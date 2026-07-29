import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.user import User

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


# --- Login rate limiting (in-memory, per client IP, failed attempts only) ---
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


LOGIN_RATE_LIMIT_ATTEMPTS = int(
    _bounded_numeric_env("FENIX_LOGIN_RATE_LIMIT_ATTEMPTS", 5, 1, 100)
)
LOGIN_RATE_LIMIT_WINDOW_SECONDS = _bounded_numeric_env(
    "FENIX_LOGIN_RATE_LIMIT_WINDOW", 60, 1, 3600
)
_failed_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_MAX_RATE_LIMIT_BUCKETS = 10_000


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP exceeded the allowed FAILED login attempts."""
    client_ip = _client_ip(request)
    now = time.monotonic()
    if client_ip not in _failed_login_attempts and len(_failed_login_attempts) >= _MAX_RATE_LIMIT_BUCKETS:
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
def _record_failed_login(request: Request) -> None:
    _failed_login_attempts[_client_ip(request)].append(time.monotonic())


def _clear_failed_logins(request: Request) -> None:
    _failed_login_attempts.pop(_client_ip(request), None)


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
    two_factor_enabled: bool = False
    theme: str = "auto"


class UserAdminPayload(_StrictInput):
    email: str = Field(min_length=3, max_length=254)
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    role: Literal["admin", "trader", "viewer"]
    status: Literal["active", "inactive"]
    profile: UserProfile | None = None
    settings: UserSettings | None = None


class RoleInfo(_StrictInput):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool


# --- In-memory admin data (demo for UI) ---
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
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "jti", "sub"]},
        )
        username = payload.get("sub")
        token_id = payload.get("jti")
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 254
            or not isinstance(token_id, str)
            or not 1 <= len(token_id) <= 128
        ):
            raise credentials_exception
        token_data = TokenData(username=username)
    except jwt.InvalidTokenError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.email == token_data.username))
    user = result.scalar_one_or_none()
    if user is None:
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
        allow_unsafe_loopback = (
            os.getenv("FENIX_ALLOW_UNAUTHENTICATED_LOOPBACK_CONTROL", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
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


# --- Admin endpoints for Users page (demo) ---


@router.get("/roles", response_model=list[RoleInfo])
async def list_roles(_: User = Depends(get_current_active_user)):
    return list(ROLE_STORE.values())


@router.get("/users", response_model=list[dict[str, Any]])
async def list_users(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.email))
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "username": user.email.split("@", 1)[0],
            "role": user.role,
            "status": "active" if user.is_active else "inactive",
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": None,
            "permissions": ROLE_STORE.get(str(user.role), ROLE_STORE["viewer"]).permissions,
            "profile": {"first_name": user.full_name or "", "last_name": ""},
            "settings": {
                "notifications_enabled": True,
                "two_factor_enabled": False,
                "theme": "auto",
            },
        }
        for user in result.scalars().all()
    ]


@router.post("/users", response_model=dict[str, Any])
async def create_user(payload: UserAdminPayload, _: User = Depends(get_current_admin_user)):
    raise HTTPException(
        status_code=501,
        detail="User creation requires a secure invitation and initial-password workflow",
    )


@router.put("/users/{user_id}", response_model=dict[str, Any])
async def update_user(
    user_id: str, payload: UserAdminPayload, _: User = Depends(get_current_admin_user)
):
    raise HTTPException(
        status_code=501,
        detail="User mutation is disabled until database-backed audit logging is implemented",
    )


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(user_id: str, _: User = Depends(get_current_admin_user)):
    raise HTTPException(
        status_code=501,
        detail="User deletion is disabled until last-admin and self-delete safeguards are implemented",
    )


@router.post("/users/{user_id}/reset-password", response_model=dict)
async def reset_password(user_id: str, _: User = Depends(get_current_admin_user)):
    raise HTTPException(
        status_code=501,
        detail="Password reset is unavailable until a one-time, expiring reset flow is implemented",
    )


class TwoFactorPayload(_StrictInput):
    enabled: bool


@router.put("/users/{user_id}/two-factor", response_model=dict[str, Any])
async def toggle_two_factor(
    user_id: str,
    payload: TwoFactorPayload,
    current_user: User = Depends(get_current_active_user),
):
    if getattr(current_user, "role", None) != "admin" and str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's settings")
    raise HTTPException(
        status_code=501,
        detail="Two-factor authentication is not implemented and no setting was changed",
    )


# --- Routes ---


@router.post(
    "/login", response_model=dict
)  # Changed response model to dict to match frontend expectation
async def login_for_access_token(
    form_data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    # Note: Frontend sends JSON body {email, password}, not Form data
    _check_login_rate_limit(request)
    logger.info("Login attempt from ip=%s", _client_ip(request))
    result = await db.execute(select(User).where(User.email == form_data.email))
    user = result.scalar_one_or_none()
    password_hash = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(form_data.password, password_hash)

    if not user or not password_valid:
        logger.warning("Authentication failed from ip=%s", _client_ip(request))
        _record_failed_login(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        _record_failed_login(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _clear_failed_logins(request)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email, "role": user.role, "userId": user.id},
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
            "permissions": ["read:trading", "write:trading"]
            if current_user.role == "admin"
            else ["read:trading"],
        },
    }
