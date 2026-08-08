import hashlib
import hmac
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, NoReturn

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis import asyncio as redis_async
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.user import PasswordResetToken, User

from src.security.dotenv_security import secure_load_dotenv

# Uvicorn can import the API without going through run_fenix.py.
secure_load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Security Config
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_ISSUER = os.getenv("FENIX_JWT_ISSUER", "fenix-api")
JWT_AUDIENCE = os.getenv("FENIX_JWT_AUDIENCE", "fenix-dashboard")

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated=["bcrypt"],
    pbkdf2_sha256__default_rounds=600_000,
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter(prefix="/api/auth", tags=["auth"])

import logging

logger = logging.getLogger("FenixAuth")

if not SECRET_KEY:
    logger.warning("JWT_SECRET is not configured; authenticated endpoints are disabled.")
elif len(SECRET_KEY.encode("utf-8")) < 32:
    logger.error("JWT_SECRET is too short; use at least 32 random bytes.")


# --- Login rate limiting (shared when configured; bounded local fallback) ---
def _bounded_numeric_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Ignoring invalid numeric setting %s", name)
        return default
    if not minimum <= value <= maximum:
        logger.warning("Ignoring out-of-range numeric setting %s", name)
        return default
    return value


LOGIN_RATE_LIMIT_ATTEMPTS = int(_bounded_numeric_env("FENIX_LOGIN_RATE_LIMIT_ATTEMPTS", 5, 1, 100))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = _bounded_numeric_env("FENIX_LOGIN_RATE_LIMIT_WINDOW", 60, 1, 3600)
_failed_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_MAX_RATE_LIMIT_BUCKETS = 10_000
_login_rate_redis: redis_async.Redis | None = None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enabled_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _shared_login_rate_limit_required() -> bool:
    return _enabled_env(
        "FENIX_REQUIRE_SHARED_LOGIN_RATE_LIMIT",
        default=_enabled_env("ALLOW_EXPOSE_API"),
    )


def _rate_limit_key(scope: str, value: str) -> str:
    secret_key = _require_valid_jwt_configuration()
    digest = hmac.new(
        secret_key.encode("utf-8"),
        f"{scope}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"fenix:auth:failed:{scope}:{digest}"


async def _shared_rate_limit_client() -> redis_async.Redis | None:
    global _login_rate_redis
    redis_url = os.getenv("REDIS_URL", os.getenv("FENIX_REDIS_URL", "")).strip()
    if not redis_url:
        return None
    if _login_rate_redis is None:
        _login_rate_redis = redis_async.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    await _login_rate_redis.ping()
    return _login_rate_redis


def _check_local_login_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP exceeded the allowed FAILED login attempts."""
    client_ip = _client_ip(request)
    now = time.monotonic()
    if (
        client_ip not in _failed_login_attempts
        and len(_failed_login_attempts) >= _MAX_RATE_LIMIT_BUCKETS
    ):
        stale_before = now - LOGIN_RATE_LIMIT_WINDOW_SECONDS
        for key, values in list(_failed_login_attempts.items()):
            if not values or values[-1] < stale_before:
                _failed_login_attempts.pop(key, None)
        if len(_failed_login_attempts) >= _MAX_RATE_LIMIT_BUCKETS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Login service is temporarily rate limited",
                headers={"Retry-After": str(int(LOGIN_RATE_LIMIT_WINDOW_SECONDS))},
            )
    attempts = _failed_login_attempts[client_ip]
    while attempts and (now - attempts[0]) > LOGIN_RATE_LIMIT_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= LOGIN_RATE_LIMIT_ATTEMPTS:
        logger.warning("Login rate limit exceeded for ip=%s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(int(LOGIN_RATE_LIMIT_WINDOW_SECONDS))},
        )


async def _check_login_rate_limit(request: Request, email: str) -> None:
    try:
        client = await _shared_rate_limit_client()
        if client is not None:
            keys = (
                _rate_limit_key("ip", _client_ip(request)),
                _rate_limit_key("account", email),
            )
            counts = await client.mget(keys)
            if any(int(value or 0) >= LOGIN_RATE_LIMIT_ATTEMPTS for value in counts):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts. Try again later.",
                    headers={"Retry-After": str(int(LOGIN_RATE_LIMIT_WINDOW_SECONDS))},
                )
            return
    except HTTPException:
        raise
    except Exception:
        logger.exception("Shared login rate limiter is unavailable")
        if _shared_login_rate_limit_required():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is temporarily unavailable",
            )

    if _shared_login_rate_limit_required():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable",
        )
    _check_local_login_rate_limit(request)


def _record_failed_login(request: Request) -> None:
    _failed_login_attempts[_client_ip(request)].append(time.monotonic())


async def _record_shared_failed_login(request: Request, email: str) -> None:
    try:
        client = await _shared_rate_limit_client()
        if client is None:
            if _shared_login_rate_limit_required():
                raise RuntimeError("shared login rate limiter is required")
            _record_failed_login(request)
            return
        keys = (
            _rate_limit_key("ip", _client_ip(request)),
            _rate_limit_key("account", email),
        )
        async with client.pipeline(transaction=True) as pipeline:
            for key in keys:
                pipeline.incr(key)
                pipeline.expire(key, int(LOGIN_RATE_LIMIT_WINDOW_SECONDS))
            await pipeline.execute()
    except Exception:
        logger.exception("Failed to record shared login rate limit")
        if _shared_login_rate_limit_required():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is temporarily unavailable",
            )
        _record_failed_login(request)


# --- Schemas ---
class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Token(_StrictInput):
    access_token: str
    token_type: str


class TokenData(_StrictInput):
    username: str | None = None


class UserResponse(_StrictInput):
    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool


class LoginRequest(_StrictInput):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or any(char.isspace() for char in normalized):
            raise ValueError("invalid email address")
        return normalized


class UserProfile(_StrictInput):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    department: str | None = None


class UserSettings(_StrictInput):
    notifications_enabled: bool = True
    theme: str = "auto"


class AdminReauthentication(_StrictInput):
    admin_password: str = Field(min_length=1, max_length=1024)


class UserAdminPayload(AdminReauthentication):
    email: str = Field(min_length=3, max_length=254)
    username: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    role: Literal["admin", "trader", "viewer"]
    status: Literal["active", "inactive"]
    profile: UserProfile | None = None
    settings: UserSettings | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or any(char.isspace() for char in normalized):
            raise ValueError("invalid email address")
        return normalized


class UserCreatePayload(UserAdminPayload):
    pass


class PasswordResetRequest(AdminReauthentication):
    pass


class PasswordResetCompletion(_StrictInput):
    token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)


class RoleInfo(_StrictInput):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool


# Static role definitions; user records themselves are database-backed.
ROLE_STORE: dict[str, RoleInfo] = {
    "admin": RoleInfo(
        id="admin",
        name="Admin",
        description="Full system access",
        permissions=["read:trading", "write:trading", "read:users", "write:users"],
        is_system=True,
    ),
    "trader": RoleInfo(
        id="trader",
        name="Trader",
        description="Trading operations",
        permissions=["read:trading", "write:trading"],
        is_system=True,
    ),
    "viewer": RoleInfo(
        id="viewer",
        name="Viewer",
        description="Read-only access",
        permissions=["read:trading"],
        is_system=True,
    ),
}


# --- Helpers ---
def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    """Hash a password and return the string.

    This function prefers pbkdf2_sha256 (no external non-py dependency), and falls back
    to the configured pwd_context if necessary. This avoids issues when the system's
    bcrypt library is missing or incompatible (e.g. missing __about__ attribute).
    """
    try:
        # Try with the default context (pbkdf2_sha256 if configured as first scheme).
        return pwd_context.hash(password)
    except Exception as e:
        logger = logging.getLogger("FenixAuth")
        logger.warning(
            f"Password hashing failed with default scheme: {e}. Trying explicit pbkdf2_sha256."
        )
        # Explicit fallback to pbkdf2_sha256
        try:
            return pwd_context.hash(password, scheme="pbkdf2_sha256")
        except Exception as ex:
            logger.error(f"Fallback hashing with pbkdf2_sha256 also failed: {ex}")
            # Re-raise to let the caller handle failure and to avoid silently storing plain text
            raise


_DUMMY_PASSWORD_HASH = get_password_hash(secrets.token_urlsafe(32))


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    secret_key = _require_valid_jwt_configuration()
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=15))
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "nbf": now,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "jti": uuid.uuid4().hex,
        }
    )
    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def _require_valid_jwt_configuration() -> str:
    if not SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )
    if len(SECRET_KEY.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication configuration is invalid",
        )
    return SECRET_KEY


def _authentication_state(user: User) -> str:
    """Bind a JWT to mutable server-side authentication state.

    Password changes alter this keyed fingerprint, which immediately revokes
    every access token issued with the previous password hash.
    """
    secret_key = _require_valid_jwt_configuration()
    material = f"{user.id}:{user.hashed_password}".encode()
    return hmac.new(secret_key.encode("utf-8"), material, hashlib.sha256).hexdigest()


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    secret_key = _require_valid_jwt_configuration()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if len(token) > 8192:
        raise credentials_exception
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "jti", "sub", "auth_state"]},
        )
        username = payload.get("sub")
        token_id = payload.get("jti")
        token_auth_state = payload.get("auth_state")
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 254
            or not isinstance(token_id, str)
            or not 1 <= len(token_id) <= 128
            or not isinstance(token_auth_state, str)
            or len(token_auth_state) != 64
        ):
            raise credentials_exception
        token_data = TokenData(username=username)
    except jwt.InvalidTokenError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.email == token_data.username))
    user = result.scalar_one_or_none()
    if user is None or not hmac.compare_digest(token_auth_state, _authentication_state(user)):
        raise credentials_exception
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_active_user)):
    if getattr(current_user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return current_user


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


async def require_control_access(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Guard for trading/engine control endpoints.

    - With JWT_SECRET configured: requires a valid bearer token of an active
      user with role 'admin' or 'trader'.
    - Without a strong JWT_SECRET: access is denied by default. Local
      unauthenticated control requires an explicit development-only opt-in.
    """
    client_host = request.client.host if request.client else ""
    if not SECRET_KEY or len(SECRET_KEY.encode("utf-8")) < 32:
        allow_unsafe_loopback = os.getenv(
            "FENIX_ALLOW_UNAUTHENTICATED_LOOPBACK_CONTROL", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if client_host in _LOOPBACK_HOSTS and allow_unsafe_loopback:
            logger.warning(
                "Development-only unauthenticated loopback control used for %s",
                request.url.path,
            )
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Control endpoints require a strong JWT_SECRET",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for control endpoints",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header.split(" ", 1)[1].strip()
    user = await get_current_user(token=token, db=db)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    if getattr(user, "role", None) not in ("admin", "trader"):
        raise HTTPException(status_code=403, detail="Role 'admin' or 'trader' required")


# --- Database-backed admin endpoints for the Users page ---


def _public_user(user: User) -> dict[str, Any]:
    raw_role = str(user.role or "viewer")
    role = raw_role if raw_role in ROLE_STORE else "viewer"
    full_name = str(user.full_name or "").strip()
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.email.split("@", 1)[0],
        "role": role,
        "status": "active" if user.is_active else "inactive",
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": None,
        "permissions": ROLE_STORE.get(role, ROLE_STORE["viewer"]).permissions,
        "profile": {"first_name": full_name, "last_name": ""},
        "settings": {
            "notifications_enabled": True,
            "theme": "auto",
        },
    }


def _payload_full_name(payload: UserAdminPayload) -> str:
    profile = payload.profile
    if profile is None:
        return ""
    return " ".join(
        part.strip() for part in (profile.first_name or "", profile.last_name or "") if part.strip()
    )[:255]


async def _lock_active_admin_ids(db: AsyncSession) -> set[str]:
    """Serialize last-admin checks on databases that support row locking."""
    result = await db.execute(
        select(User.id)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.id)
        .with_for_update()
    )
    return {str(user_id) for user_id in result.scalars().all()}


async def _load_managed_user(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _require_admin_reauthentication(
    admin_password: str,
    current_admin: User,
    request: Request,
) -> None:
    """Require the administrator's password for account-takeover-sensitive actions."""
    await _check_login_rate_limit(request, current_admin.email)
    if verify_password(admin_password, current_admin.hashed_password):
        return
    logger.warning(
        "Admin reauthentication failed actor=%s ip=%s",
        current_admin.id,
        _client_ip(request),
    )
    await _record_shared_failed_login(request, current_admin.email)
    raise HTTPException(status_code=403, detail="Administrator reauthentication failed")


def _would_remove_active_admin(user: User, *, role: str, active: bool) -> bool:
    return bool(user.role == "admin" and user.is_active and (role != "admin" or not active))


def _new_password_reset_record(
    *,
    user_id: str,
    admin_id: str,
    purpose: Literal["setup", "reset"],
    now: datetime,
) -> tuple[str, PasswordResetToken]:
    raw_token = secrets.token_urlsafe(48)
    return raw_token, PasswordResetToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        purpose=purpose,
        created_by_admin_id=admin_id,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )


@router.get("/roles", response_model=list[RoleInfo])
async def list_roles(_: User = Depends(get_current_active_user)):
    return list(ROLE_STORE.values())


@router.get("/users", response_model=list[dict[str, Any]])
async def list_users(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.email))
    return [_public_user(user) for user in result.scalars().all()]


@router.post("/users", response_model=dict[str, Any])
async def create_user(
    payload: UserCreatePayload,
    request: Request,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin_reauthentication(payload.admin_password, current_admin, request)
    user = User(
        id=str(uuid.uuid4()),
        email=payload.email,
        # The administrator never chooses or learns the account's final password.
        hashed_password=get_password_hash(secrets.token_urlsafe(48)),
        full_name=_payload_full_name(payload),
        role=payload.role,
        is_active=payload.status == "active",
    )
    db.add(user)
    try:
        # Flush the parent first so the reset-token foreign key is valid on SQLite.
        await db.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        setup_token, setup_record = _new_password_reset_record(
            user_id=str(user.id),
            admin_id=str(current_admin.id),
            purpose="setup",
            now=now,
        )
        db.add(setup_record)
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="A user with this email already exists"
        ) from exc
    logger.info("Admin user_created actor=%s target=%s", current_admin.id, user.id)
    return {
        **_public_user(user),
        "setup_token": setup_token,
        "expires_at": setup_record.expires_at.replace(tzinfo=timezone.utc).isoformat(),
    }


@router.put("/users/{user_id}", response_model=dict[str, Any])
async def update_user(
    user_id: str,
    payload: UserAdminPayload,
    request: Request,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin_reauthentication(payload.admin_password, current_admin, request)
    user = await _load_managed_user(db, user_id)
    active = payload.status == "active"
    if str(user.id) == str(current_admin.id) and not active:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account")
    if _would_remove_active_admin(user, role=payload.role, active=active):
        if len(await _lock_active_admin_ids(db)) <= 1:
            raise HTTPException(status_code=409, detail="At least one active admin is required")

    user.email = payload.email
    user.full_name = _payload_full_name(payload)
    user.role = payload.role
    user.is_active = active
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="A user with this email already exists"
        ) from exc
    logger.info("Admin user_updated actor=%s target=%s", current_admin.id, user.id)
    return _public_user(user)


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: str,
    payload: AdminReauthentication,
    request: Request,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin_reauthentication(payload.admin_password, current_admin, request)
    user = await _load_managed_user(db, user_id)
    if str(user.id) == str(current_admin.id):
        raise HTTPException(status_code=409, detail="You cannot delete your own account")
    if user.role == "admin" and user.is_active:
        if len(await _lock_active_admin_ids(db)) <= 1:
            raise HTTPException(status_code=409, detail="At least one active admin is required")
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
    await db.delete(user)
    await db.commit()
    logger.info("Admin user_deleted actor=%s target=%s", current_admin.id, user_id)
    return {"success": True, "id": user_id}


@router.post("/users/{user_id}/reset-password", response_model=dict)
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin_reauthentication(payload.admin_password, current_admin, request)
    user = await _load_managed_user(db, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
    raw_token, reset_record = _new_password_reset_record(
        user_id=str(user.id),
        admin_id=str(current_admin.id),
        purpose="reset",
        now=now,
    )
    db.add(reset_record)
    await db.commit()
    logger.info("Admin password_reset_issued actor=%s target=%s", current_admin.id, user.id)
    return {
        "success": True,
        "id": str(user.id),
        "reset_token": raw_token,
        "expires_at": reset_record.expires_at.replace(tzinfo=timezone.utc).isoformat(),
    }


@router.post("/password-reset/complete", response_model=dict)
async def complete_password_reset(
    payload: PasswordResetCompletion,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consume a reset token exactly once without exposing it in a URL."""
    await _check_login_rate_limit(request, "password-reset")

    async def reject_reset() -> NoReturn:
        await _record_shared_failed_login(request, "password-reset")
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    reset_record = result.scalar_one_or_none()
    if reset_record is None or reset_record.used_at is not None or reset_record.expires_at <= now:
        await reject_reset()

    user = await db.get(User, reset_record.user_id)
    if user is None:
        await reject_reset()

    replacement_hash = get_password_hash(payload.new_password)
    consume_result = await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == reset_record.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
    )
    if consume_result.rowcount != 1:
        await db.rollback()
        await reject_reset()

    user.hashed_password = replacement_hash
    await db.commit()
    logger.info("Password reset completed target=%s", user.id)
    return {"success": True}


# --- Routes ---


@router.post(
    "/login", response_model=dict
)  # Changed response model to dict to match frontend expectation
async def login_for_access_token(
    form_data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    # Note: Frontend sends JSON body {email, password}, not Form data
    await _check_login_rate_limit(request, form_data.email)
    logger.info("Login attempt from ip=%s", _client_ip(request))
    result = await db.execute(select(User).where(User.email == form_data.email))
    user = result.scalar_one_or_none()
    password_hash = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(form_data.password, password_hash)

    if not user or not password_valid:
        logger.warning("Authentication failed from ip=%s", _client_ip(request))
        await _record_shared_failed_login(request, form_data.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        await _record_shared_failed_login(request, form_data.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "role": user.role,
            "userId": user.id,
            "auth_state": _authentication_state(user),
        },
        expires_delta=access_token_expires,
    )

    # Return structure matching what frontend expects (referencing api/src/routes/auth.ts lines 100-108)
    return {
        "success": True,
        "token": access_token,  # Frontend expects 'token' or 'accessToken' in root or data? Checking authStore.ts line 54: data.token
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
        },
    }


@router.get("/me", response_model=dict)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return {
        "success": True,
        "data": {
            "user": {
                "id": current_user.id,
                "email": current_user.email,
                "name": current_user.full_name,
                "role": current_user.role,
                "is_active": current_user.is_active,
            },
            # Mock permissions based on role
            "permissions": (
                ["read:trading", "write:trading"]
                if current_user.role == "admin"
                else ["read:trading"]
            ),
        },
    }
