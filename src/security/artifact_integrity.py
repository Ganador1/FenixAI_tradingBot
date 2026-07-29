"""Integrity controls for model artifacts that require unsafe serializers.

Pickle and joblib can execute code while loading. Fenix therefore loads only
repository models with pinned SHA-256 digests or runtime artifacts carrying a
valid HMAC generated with a local, permission-restricted signing key.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import pickle
import re
import secrets
import stat
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_MODEL_ARTIFACT_BYTES = 100 * 1024 * 1024

TRUSTED_REPOSITORY_ARTIFACTS = {
    "nanofenixv3/pretrained_solusdt.pkl": (
        "74a77056366911aa7fe088e54a1220eadb8f6c18dce8b5df655acfa920c2f69c"
    ),
    "nanofenixv3/pretrained_btcusdt_5s.pkl": (
        "e049ffb60820f2a4c9b838e9c55091f2d3896dd9485b81abaf8686b294499fe8"
    ),
    "nanofenixv3/pretrained_ethusdt.pkl": (
        "8bfb193a15134f6e6055f7524e2240c9bef533a2d5ed1f41aa267b54d0d4f26d"
    ),
    "nanofenixv3/pretrained_btcusdt.pkl": (
        "f917c9b067ce488f560a49c1029d89e7d42688d714870f69cce11c8af1a89043"
    ),
    "nanofenixv3/pretrained_btcusdc.pkl": (
        "2666fd9b6587f4523b8097a6f7b87434d85b469aa7a6d0c14cfbe24627d93321"
    ),
    "minifenix/sota_model_pretrained.joblib": (
        "a2d4ed71d266c231a695cdfe5516f6a4599ae9cbd88fcb1b61368e68a553aed1"
    ),
}


class ArtifactIntegrityError(ValueError):
    """Raised when an executable model artifact is not trusted."""


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise ArtifactIntegrityError("artifact symlinks are not allowed")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ArtifactIntegrityError("artifact could not be opened safely") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ArtifactIntegrityError("artifact is not a regular file")
        if file_stat.st_size > max_bytes:
            raise ArtifactIntegrityError("artifact exceeds the configured size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise ArtifactIntegrityError("artifact exceeds the configured size limit")
        return data
    finally:
        os.close(fd)


def _artifact_env_hash(path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path.name).strip("_").upper()
    return os.getenv(f"FENIX_TRUSTED_ARTIFACT_SHA256_{slug}", "").strip().lower()


def _repository_hash(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return ""
    return TRUSTED_REPOSITORY_ARTIFACTS.get(relative, "")


def _signing_key_path() -> Path:
    configured = os.getenv("FENIX_MODEL_SIGNING_KEY_FILE", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".fenixai/model-signing.key"


def _load_signing_key(*, create: bool) -> bytes:
    supplied = os.getenv("FENIX_MODEL_SIGNING_KEY", "").encode("utf-8")
    if supplied:
        if len(supplied) < 32:
            raise ArtifactIntegrityError("FENIX_MODEL_SIGNING_KEY must be at least 32 bytes")
        return supplied

    path = _signing_key_path()
    if path.exists():
        if path.is_symlink():
            raise ArtifactIntegrityError("model signing key cannot be a symlink")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ArtifactIntegrityError("model signing key permissions must be 0600")
        key = _read_regular_file(path, max_bytes=4096)
        if len(key) < 32:
            raise ArtifactIntegrityError("model signing key is invalid")
        return key
    if not create:
        raise ArtifactIntegrityError("runtime model signing key is unavailable")

    if path.parent.is_symlink():
        raise ArtifactIntegrityError("model signing key directory cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return _load_signing_key(create=False)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    return key


def _hmac_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.hmac")


def _valid_runtime_signature(path: Path, data: bytes) -> bool:
    signature_path = _hmac_path(path)
    if not signature_path.exists():
        return False
    try:
        supplied = _read_regular_file(signature_path, max_bytes=256).decode("ascii").strip()
        key = _load_signing_key(create=False)
    except (ArtifactIntegrityError, UnicodeDecodeError):
        return False
    expected = hmac.new(key, data, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def read_verified_artifact(path: str | Path) -> bytes:
    artifact_path = Path(path)
    data = _read_regular_file(artifact_path, max_bytes=MAX_MODEL_ARTIFACT_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    trusted_digest = _repository_hash(artifact_path) or _artifact_env_hash(artifact_path)
    if trusted_digest and hmac.compare_digest(digest, trusted_digest):
        return data
    if _valid_runtime_signature(artifact_path, data):
        return data
    raise ArtifactIntegrityError(
        "model artifact is unsigned or its pinned SHA-256 digest does not match"
    )


def load_verified_pickle(path: str | Path) -> Any:
    data = read_verified_artifact(path)
    # The bytes are authenticated before deserialization. Pickle remains limited
    # to trusted model artifacts because the format itself is executable.
    return pickle.loads(data)  # nosec B301


def load_verified_joblib(path: str | Path) -> Any:
    import joblib

    data = read_verified_artifact(path)
    return joblib.load(io.BytesIO(data))


def write_signed_artifact(path: str | Path, data: bytes) -> None:
    """Atomically write an artifact and its HMAC sidecar with private modes."""
    if len(data) > MAX_MODEL_ARTIFACT_BYTES:
        raise ArtifactIntegrityError("artifact exceeds the configured size limit")
    target = Path(path)
    if target.parent.is_symlink():
        raise ArtifactIntegrityError("artifact directory cannot be a symlink")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = _load_signing_key(create=True)
    signature = hmac.new(key, data, hashlib.sha256).hexdigest().encode("ascii")

    temp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    signature_target = _hmac_path(target)
    signature_temp = signature_target.with_name(
        f".{signature_target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    try:
        for output, payload in ((temp, data), (signature_temp, signature)):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(output, flags, 0o600)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
        os.replace(temp, target)
        os.replace(signature_temp, signature_target)
        os.chmod(target, 0o600)
        os.chmod(signature_target, 0o600)
    finally:
        for leftover in (temp, signature_temp):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
