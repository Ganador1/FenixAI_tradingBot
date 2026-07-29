"""Safe, bounded loading for local dotenv files that contain credentials."""

from __future__ import annotations

import io
import os
import re
import stat
from pathlib import Path

from dotenv import dotenv_values

_MAX_DOTENV_BYTES = 1024 * 1024
_MAX_VALUE_BYTES = 64 * 1024
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FORBIDDEN_PROCESS_ENV = {
    "BASH_ENV",
    "ENV",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "PATH",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
}


class DotenvSecurityError(RuntimeError):
    """Raised when a credential-bearing dotenv file is unsafe to read."""


def read_secure_dotenv(path: str | Path) -> dict[str, str]:
    """Read a private regular dotenv file without executing any of its content."""
    dotenv_path = Path(path)
    if dotenv_path.is_symlink():
        raise DotenvSecurityError("dotenv file cannot be a symbolic link")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(dotenv_path, flags)
    except OSError as exc:
        raise DotenvSecurityError("dotenv file could not be opened safely") from exc

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise DotenvSecurityError("dotenv path must be a regular file")
        if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
            raise DotenvSecurityError("dotenv file must be owned by the current user")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise DotenvSecurityError(
                "dotenv permissions must not allow group or other access; run chmod 600 .env"
            )
        if file_stat.st_size > _MAX_DOTENV_BYTES:
            raise DotenvSecurityError("dotenv file exceeds the 1 MiB safety limit")

        chunks: list[bytes] = []
        remaining = _MAX_DOTENV_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)

    if len(raw) > _MAX_DOTENV_BYTES:
        raise DotenvSecurityError("dotenv file exceeds the 1 MiB safety limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DotenvSecurityError("dotenv file must be valid UTF-8") from exc

    parsed = dotenv_values(stream=io.StringIO(text))
    values: dict[str, str] = {}
    for key, value in parsed.items():
        if not key or not _ENV_NAME.fullmatch(key):
            raise DotenvSecurityError("dotenv contains an invalid environment variable name")
        if key in _FORBIDDEN_PROCESS_ENV or key.startswith("DYLD_"):
            raise DotenvSecurityError(
                f"dotenv cannot set process-loader variable {key}"
            )
        if value is None:
            continue
        if "\x00" in value or len(value.encode("utf-8")) > _MAX_VALUE_BYTES:
            raise DotenvSecurityError(f"dotenv value for {key} is invalid or too large")
        values[key] = value
    return values


def secure_load_dotenv(
    path: str | Path,
    *,
    override: bool = False,
    required: bool = False,
) -> bool:
    """Load validated dotenv values into the current process environment."""
    if os.getenv("FENIX_SKIP_DOTENV", "").strip().lower() in _TRUE_VALUES:
        return False
    dotenv_path = Path(path)
    if not dotenv_path.exists() and not dotenv_path.is_symlink():
        if required:
            raise DotenvSecurityError("required dotenv file does not exist")
        return False

    for key, value in read_secure_dotenv(dotenv_path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return True
