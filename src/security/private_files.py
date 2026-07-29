"""Private filesystem helpers for runtime logs and state."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import cast, TextIO


def _ensure_file_parent(path: Path) -> None:
    """Create a missing file parent privately without chmodding an existing parent."""
    parent = path.parent
    if parent == Path("."):
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Private file parent must be a real directory")
        return

    existed = parent.exists()
    if parent.is_symlink():
        raise ValueError(f"{parent} cannot be a symbolic link")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_stat = parent.lstat()
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode):
        raise ValueError(f"{parent} must be a real directory")
    if not existed:
        os.chmod(parent, 0o700)


def ensure_private_directory(path: Path) -> Path:
    """Create a 0700 directory and reject a symlink at the requested path."""
    if path == Path("."):
        raise ValueError("Refusing to change permissions on the current working directory")
    if path.is_symlink():
        raise ValueError(f"{path} cannot be a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path_stat = path.lstat()
    if not stat.S_ISDIR(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
        raise ValueError(f"{path} must be a real directory")
    os.chmod(path, 0o700)
    return path


def open_private_text(path: Path, mode: str = "a", *, buffering: int = -1) -> TextIO:
    """Open a 0600 regular text file for append or truncate without symlink following."""
    if mode not in {"a", "w", "r+"}:
        raise ValueError("Unsupported private text file mode")
    _ensure_file_parent(path)
    flags = os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_RDWR if mode == "r+" else os.O_WRONLY
    if mode == "a":
        flags |= os.O_APPEND
    elif mode == "w":
        flags |= os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"{path} cannot be opened safely") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} must be a regular file")
        os.fchmod(fd, 0o600)
        return cast(
            TextIO,
            os.fdopen(fd, mode, encoding="utf-8", buffering=buffering),
        )
    except Exception:
        os.close(fd)
        raise


def write_private_text(path: Path, value: str) -> None:
    """Atomically write a 0600 text file without following symlinks."""
    _ensure_file_parent(path)
    if path.is_symlink():
        raise ValueError(f"{path} cannot be a symbolic link")

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"{path} cannot be written safely") from exc
    try:
        data = value.encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("Private file write made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def read_private_text(
    path: Path,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    require_owner_private: bool = False,
) -> str:
    """Read a bounded regular text file without following its final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path} cannot be read safely") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} must be a regular file")
        if require_owner_private:
            current_uid = getattr(os, "geteuid", lambda: file_stat.st_uid)()
            if file_stat.st_uid != current_uid or stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise ValueError(f"{path} must be owned by the current user with mode 0600")
        if file_stat.st_size > max_bytes:
            raise ValueError(f"{path} exceeds the {max_bytes}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{path} exceeds the {max_bytes}-byte limit")
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} must contain UTF-8 text") from exc


def write_private_bytes(path: Path, value: bytes) -> None:
    """Atomically write a 0600 binary file without following symlinks."""
    _ensure_file_parent(path)
    if path.is_symlink():
        raise ValueError(f"{path} cannot be a symbolic link")

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"{path} cannot be written safely") from exc
    try:
        offset = 0
        while offset < len(value):
            written = os.write(fd, value[offset:])
            if written <= 0:
                raise OSError("Private file write made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def read_private_last_line(path: Path, *, max_line_bytes: int = 64 * 1024) -> str:
    """Read the final non-empty line without loading an unbounded append-only file."""
    tail_bytes = max_line_bytes + 1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path} cannot be read safely") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} must be a regular file")
        start = max(0, file_stat.st_size - tail_bytes)
        os.lseek(fd, start, os.SEEK_SET)
        data = os.read(fd, tail_bytes)
    finally:
        os.close(fd)

    if start:
        separator = data.find(b"\n")
        if separator < 0:
            raise ValueError(f"{path} has a line exceeding {max_line_bytes} bytes")
        data = data[separator + 1 :]
    lines = [line for line in data.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path} does not contain a state record")
    last_line = lines[-1]
    if len(last_line) > max_line_bytes:
        raise ValueError(f"{path} has a line exceeding {max_line_bytes} bytes")
    try:
        return last_line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} must contain UTF-8 text") from exc
