import os
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.user import User

# Load .env so JWT_SECRET works regardless of how the server is started
# (run_fenix.py, uvicorn, etc.). Without this, users must export it manually.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Security Config
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

router = APIRouter(prefix="/api/auth", tags=["auth"])

import logging

logger = logging.getLogger("FenixAuth")

if not SECRET_KEY:
    logger.warning("JWT_SECRET no está configurado; los endpoints de auth devolverán error 500.")


# --- Login rate limiting (in-memory, per client IP, failed attempts only) ---
LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("FENIX_LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("FENIX_LOGIN_RATE_LIMIT_WINDOW", "60"))
_failed_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP exceeded the allowed FAILED login attempts."""
    client_ip = _client_ip(request)
    now = time.monotonic()
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
class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class UserProfile(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    department: str | None = None


class UserSettings(BaseModel):
    notifications_enabled: bool = True
    two_factor_enabled: bool = False
    theme: str = "auto"


class UserAdminPayload(BaseModel):
    email: str
    username: str
    role: str
    status: str
    profile: UserProfile | None = None
    settings: UserSettings | None = None


class RoleInfo(BaseModel):
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

USER_STORE: dict[str, dict[str, Any]] = {
    "1": {
        "id": "1",
        "email": "admin@fenix.ai",
        "username": "admin",
        "role": "admin",
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
        "last_login": None,
        "permissions": ROLE_STORE["admin"].permissions,
        "profile": {"first_name": "Admin", "last_name": "User"},
        "settings": {"notifications_enabled": True, "two_factor_enabled": False, "theme": "auto"},
    },
    "2": {
        "id": "2",
        "email": "trader@fenix.ai",
        "username": "trader",
        "role": "trader",
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
        "last_login": None,
        "permissions": ROLE_STORE["trader"].permissions,
        "profile": {"first_name": "Trader", "last_name": "User"},
        "settings": {"notifications_enabled": True, "two_factor_enabled": False, "theme": "auto"},
    },
}


# --- Helpers ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


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


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    if not SECRET_KEY:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not configured")
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    if not SECRET_KEY:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not configured")
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
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
        raise HTTPException(status_code=400, detail="Inactive user")
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
    - Without JWT_SECRET: only loopback clients are allowed (local dev), and a
      warning is logged. Remote clients are always rejected.
    """
    client_host = request.client.host if request.client else ""
    if not SECRET_KEY:
        if client_host in _LOOPBACK_HOSTS:
            logger.warning(
                "Control endpoint %s accessed without auth (JWT_SECRET not set, loopback client). "
                "Set JWT_SECRET to enforce authentication.",
                request.url.path,
            )
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET is not configured; control endpoints are disabled for remote clients",
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
async def list_users(_: User = Depends(get_current_active_user)):
    return list(USER_STORE.values())


@router.post("/users", response_model=dict[str, Any])
async def create_user(payload: UserAdminPayload, _: User = Depends(get_current_admin_user)):
    user_id = uuid.uuid4().hex
    role_permissions = ROLE_STORE.get(payload.role, ROLE_STORE["viewer"]).permissions
    user_data = {
        "id": user_id,
        "email": payload.email,
        "username": payload.username,
        "role": payload.role,
        "status": payload.status,
        "created_at": datetime.utcnow().isoformat(),
        "last_login": None,
        "permissions": role_permissions,
        "profile": payload.profile.dict() if payload.profile else {},
        "settings": payload.settings.dict()
        if payload.settings
        else {
            "notifications_enabled": True,
            "two_factor_enabled": False,
            "theme": "auto",
        },
    }
    USER_STORE[user_id] = user_data
    return user_data


@router.put("/users/{user_id}", response_model=dict[str, Any])
async def update_user(
    user_id: str, payload: UserAdminPayload, _: User = Depends(get_current_admin_user)
):
    if user_id not in USER_STORE:
        raise HTTPException(status_code=404, detail="User not found")
    role_permissions = ROLE_STORE.get(payload.role, ROLE_STORE["viewer"]).permissions
    user_data = USER_STORE[user_id]
    user_data.update(
        {
            "email": payload.email,
            "username": payload.username,
            "role": payload.role,
            "status": payload.status,
            "permissions": role_permissions,
            "profile": payload.profile.dict() if payload.profile else user_data.get("profile", {}),
            "settings": payload.settings.dict()
            if payload.settings
            else user_data.get("settings", {}),
        }
    )
    return user_data


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(user_id: str, _: User = Depends(get_current_admin_user)):
    if user_id not in USER_STORE:
        raise HTTPException(status_code=404, detail="User not found")
    USER_STORE.pop(user_id, None)
    return {"success": True}


@router.post("/users/{user_id}/reset-password", response_model=dict)
async def reset_password(user_id: str, _: User = Depends(get_current_admin_user)):
    if user_id not in USER_STORE:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "message": "Password reset initiated"}


class TwoFactorPayload(BaseModel):
    enabled: bool


@router.put("/users/{user_id}/two-factor", response_model=dict[str, Any])
async def toggle_two_factor(
    user_id: str, payload: TwoFactorPayload, _: User = Depends(get_current_active_user)
):
    if user_id not in USER_STORE:
        raise HTTPException(status_code=404, detail="User not found")
    user_data = USER_STORE[user_id]
    settings = user_data.get("settings", {})
    settings["two_factor_enabled"] = bool(payload.enabled)
    user_data["settings"] = settings
    return user_data


# --- Routes ---


@router.post(
    "/login", response_model=dict
)  # Changed response model to dict to match frontend expectation
async def login_for_access_token(
    form_data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    # Note: Frontend sends JSON body {email, password}, not Form data
    _check_login_rate_limit(request)
    logger.info(
        f"Login attempt for email={form_data.email} from={request.client.host if request.client else 'unknown'}"
    )
    result = await db.execute(select(User).where(User.email == form_data.email))
    user = result.scalar_one_or_none()
    logger.debug(f"User lookup: {user.email if user else 'not found'}")

    if not user:
        logger.warning(f"Authentication failed: user not found for email={form_data.email}")
        _record_failed_login(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.hashed_password):
        logger.warning(
            f"Authentication failed: password verification failed for email={form_data.email}"
        )
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


# Standard OAuth2 route (good for swagger UI)
@router.post("/token", response_model=Token)
async def login_swagger(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    # This endpoint is kept for Swagger UI compatibility
    _check_login_rate_limit(request)
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        _record_failed_login(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _clear_failed_logins(request)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}
