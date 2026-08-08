from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String

from src.config.database import Base


def _utc_now_naive() -> datetime:
    """UTC timestamp compatible with the project's timezone-naive DB columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(String, default="trader")  # admin, trader, analyst
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utc_now_naive)


class PasswordResetToken(Base):
    """Server-side record for a short-lived, single-use password reset secret."""

    __tablename__ = "password_reset_tokens"

    id = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    purpose = Column(String(16), nullable=False)
    created_by_admin_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=_utc_now_naive, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
