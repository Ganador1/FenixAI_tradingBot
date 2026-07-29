"""Encrypted, integrity-protected local secret storage.

The vault key is derived from ``FENIX_MASTER_PASSWORD`` and a random local
salt. The derived key is never written to disk beside the encrypted vault.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

_KDF_ITERATIONS = 600_000
_MAX_VAULT_BYTES = 5 * 1024 * 1024
_singleton_lock = threading.Lock()


def _read_private_regular_file(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise RuntimeError(f"{path.name} cannot be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"{path.name} could not be opened safely") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"{path.name} must be a regular file")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise RuntimeError(f"{path.name} permissions must not allow group or other access")
        if file_stat.st_size > max_bytes:
            raise RuntimeError(f"{path.name} exceeds its size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise RuntimeError(f"{path.name} exceeds its size limit")
        return data
    finally:
        os.close(fd)


def _atomic_private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise RuntimeError(f"{path.parent.name} cannot be a symlink")
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        logger.debug("Could not tighten permissions on %s", path.parent, exc_info=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            logger.debug("Could not tighten permissions on %s", path, exc_info=True)
    finally:
        temporary_path.unlink(missing_ok=True)


class EncryptedVault:
    """Small encrypted JSON vault with atomic writes and corruption fail-close."""

    def __init__(self, encryption_key: bytes, vault_file: Path | None = None):
        self.fernet = Fernet(encryption_key)
        configured = os.getenv("FENIX_VAULT_PATH", "").strip()
        self.vault_file = vault_file or (
            Path(configured) if configured else Path("security/.vault.enc")
        )
        self.vault_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._load_error: Exception | None = None
        self._vault_data = self._load_vault()

    def _load_vault(self) -> dict[str, Any]:
        if not self.vault_file.exists():
            return {}
        try:
            encrypted_data = _read_private_regular_file(
                self.vault_file,
                max_bytes=_MAX_VAULT_BYTES,
            )
            decrypted_data = self.fernet.decrypt(encrypted_data)
            raw = json.loads(decrypted_data.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("vault payload must be a JSON object")
            return raw
        except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._load_error = exc
            logger.error("Encrypted vault could not be loaded; writes are disabled: %s", exc)
            return {}

    def _save_vault(self) -> None:
        if self._load_error is not None:
            raise RuntimeError("refusing to overwrite an unreadable encrypted vault")
        payload = json.dumps(
            self._vault_data,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        _atomic_private_write(self.vault_file, self.fernet.encrypt(payload))

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_value: str) -> str:
        return self.fernet.decrypt(encrypted_value.encode("ascii")).decode("utf-8")

    def store(self, key: str, encrypted_value: str, ttl: int | None = None) -> None:
        with self._lock:
            expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl)
                if ttl is not None
                else None
            )
            self._vault_data[key] = {
                "value": encrypted_value,
                "expiry": expiry.isoformat() if expiry else None,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            self._save_vault()

    def retrieve(self, key: str) -> str | None:
        with self._lock:
            entry = self._vault_data.get(key)
            if not isinstance(entry, dict):
                return None
            expiry_raw = entry.get("expiry")
            if expiry_raw:
                expiry = datetime.fromisoformat(str(expiry_raw))
                if datetime.now(timezone.utc) > expiry:
                    self._vault_data.pop(key, None)
                    self._save_vault()
                    return None
            value = entry.get("value")
            return str(value) if value is not None else None

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._vault_data
            if existed:
                self._vault_data.pop(key, None)
                self._save_vault()
            return existed


class SecureSecretsManager:
    """Local encrypted secret manager backed by a password-derived Fernet key."""

    def __init__(self, master_password: str | None = None):
        password = master_password or os.getenv("FENIX_MASTER_PASSWORD")
        if not password:
            raise RuntimeError(
                "FENIX_MASTER_PASSWORD is required for encrypted secret persistence"
            )
        if len(password) < 16:
            raise RuntimeError("FENIX_MASTER_PASSWORD must contain at least 16 characters")

        vault_path_raw = os.getenv("FENIX_VAULT_PATH", "").strip()
        vault_path = Path(vault_path_raw) if vault_path_raw else Path("security/.vault.enc")
        salt_path_raw = os.getenv("FENIX_VAULT_SALT_PATH", "").strip()
        self.salt_file = (
            Path(salt_path_raw)
            if salt_path_raw
            else vault_path.parent / ".salt"
        )
        salt = self._load_or_create_salt()
        self.encryption_key = self._derive_key(password, salt, _KDF_ITERATIONS)
        self._migrate_legacy_adjacent_key(vault_path)
        self.vault = EncryptedVault(self.encryption_key, vault_file=vault_path)
        self.rotation_schedule: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

    def _load_or_create_salt(self) -> bytes:
        if self.salt_file.exists():
            salt = _read_private_regular_file(self.salt_file, max_bytes=16)
            if len(salt) != 16:
                raise RuntimeError("encrypted vault salt has an invalid length")
            return salt
        salt = os.urandom(16)
        _atomic_private_write(self.salt_file, salt)
        return salt

    def _migrate_legacy_adjacent_key(self, vault_path: Path) -> None:
        """Re-encrypt the legacy vault whose raw Fernet key lived beside it."""
        legacy_key_path = vault_path.parent / ".key"
        if not vault_path.exists() or not legacy_key_path.exists():
            return
        try:
            legacy_key = _read_private_regular_file(legacy_key_path, max_bytes=128)
            legacy_vault = EncryptedVault(legacy_key, vault_file=vault_path)
            if legacy_vault._load_error is not None:
                return

            migrated_vault = EncryptedVault(self.encryption_key, vault_file=vault_path)
            migrated_vault._load_error = None
            migrated_vault._vault_data = dict(legacy_vault._vault_data)
            migrated_vault._save_vault()
            # The raw adjacent key was the weakness being removed. It is safe
            # to delete only after the re-encrypted vault has been persisted.
            legacy_key_path.unlink()
            logger.warning("Migrated legacy adjacent-key vault to password-derived encryption")
        except Exception:
            logger.error("Legacy vault migration failed; the original files were preserved", exc_info=True)

    def store_secret(
        self,
        key: str,
        value: str,
        ttl_seconds: int | None = None,
        auto_rotate: bool = False,
    ) -> bool:
        try:
            if not key or len(key) > 128 or any(char.isspace() for char in key):
                raise ValueError("secret key is invalid")
            if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
                raise ValueError("secret value exceeds the supported limit")
            if ttl_seconds is not None and ttl_seconds <= 0:
                raise ValueError("secret TTL must be positive")
            encrypted_value = self.vault.encrypt(value)
            self.vault.store(key, encrypted_value, ttl_seconds)
            if auto_rotate and ttl_seconds:
                self.rotation_schedule[key] = {
                    "interval": max(1, ttl_seconds // 2),
                    "last_rotation": datetime.now(timezone.utc).isoformat(),
                }
            logger.info("Secret stored in encrypted vault: %s", key)
            return True
        except Exception:
            logger.error("Could not store secret %s", key, exc_info=True)
            return False

    def get_secret(self, key: str) -> str | None:
        try:
            encrypted_value = self.vault.retrieve(key)
            return self.vault.decrypt(encrypted_value) if encrypted_value is not None else None
        except Exception:
            logger.error("Could not retrieve secret %s", key, exc_info=True)
            return None

    def delete_secret(self, key: str) -> bool:
        try:
            return self.vault.delete(key)
        except Exception:
            logger.error("Could not delete secret %s", key, exc_info=True)
            return False

    def rotate_credentials(self, service: str) -> bool:
        logger.warning("Automatic credential rotation is not implemented for %s", service)
        return False

    def check_rotation_schedule(self) -> None:
        current_time = datetime.now(timezone.utc)
        for key, schedule in list(self.rotation_schedule.items()):
            last_rotation = datetime.fromisoformat(str(schedule["last_rotation"]))
            interval = timedelta(seconds=int(schedule["interval"]))
            if current_time - last_rotation > interval:
                self.rotate_credentials(key.split("_", 1)[0])

    def validate_integrity(self) -> bool:
        return self.vault._load_error is None

    def emergency_lockdown(self) -> None:
        self.vault._vault_data.clear()
        self.rotation_schedule.clear()
        logger.warning("Emergency lockdown cleared decrypted secret material from memory")


_secrets_manager: SecureSecretsManager | None = None


def get_secrets_manager() -> SecureSecretsManager:
    global _secrets_manager
    with _singleton_lock:
        if _secrets_manager is None:
            _secrets_manager = SecureSecretsManager()
        return _secrets_manager


def reset_secrets_manager_for_tests() -> None:
    """Clear the singleton so tests can isolate paths and passwords."""
    global _secrets_manager
    with _singleton_lock:
        _secrets_manager = None


def migrate_env_secrets(secrets_manager: SecureSecretsManager) -> int:
    secret_env_vars = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_SECRET_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DATABASE_PASSWORD",
        "JWT_SECRET",
        "ENCRYPTION_KEY",
    ]
    migrated_count = 0
    for env_var in secret_env_vars:
        value = os.getenv(env_var)
        if value and secrets_manager.store_secret(
            key=env_var.lower(),
            value=value,
            ttl_seconds=None,
        ):
            migrated_count += 1
    logger.info("Migrated %d environment secrets into the encrypted vault", migrated_count)
    return migrated_count


def init_secrets() -> SecureSecretsManager:
    return get_secrets_manager()
