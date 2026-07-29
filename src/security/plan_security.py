"""Security primitives for local experiment plan files.

Plans are configuration, not trusted code. These helpers bound their size,
reject symlinks and ambiguous boolean coercion, and fail before a subprocess
can inherit unsafe settings.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping

MAX_PLAN_BYTES = 1_048_576
MAX_PLAN_SLOTS = 1_000


def read_bounded_json(path: Path, *, max_bytes: int = MAX_PLAN_BYTES) -> Any:
    """Read JSON from a bounded regular file without following its final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Plan file cannot be opened safely") from exc

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Plan file must be a regular file")
        if file_stat.st_size > max_bytes:
            raise ValueError(f"Plan file exceeds the {max_bytes}-byte limit")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Plan file exceeds the {max_bytes}-byte limit")
    finally:
        os.close(fd)

    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Plan file must contain valid UTF-8 JSON") from exc


def strict_bool(
    item: Mapping[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    """Return a JSON boolean without treating strings or integers as booleans."""
    value = item.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"Plan field {key!r} must be a JSON boolean")
    return value


def strict_optional_bool(item: Mapping[str, Any], key: str) -> bool | None:
    """Return a strict optional JSON boolean."""
    value = item.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"Plan field {key!r} must be a JSON boolean or null")
    return value


def strict_text(
    item: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
    maximum: int = 512,
) -> str | None:
    """Return bounded text and reject container coercion and control characters."""
    value = item.get(key, default)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"Plan field {key!r} must be bounded single-line text")
    return value.strip()


def strict_number(
    item: Mapping[str, Any],
    key: str,
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
    default: int | float | None = None,
) -> int | float | None:
    """Return a finite JSON number within explicit safety bounds."""
    value = item.get(key, default)
    if value is None:
        return None
    allowed_types = (int,) if integer else (int, float)
    if type(value) not in allowed_types:
        kind = "integer" if integer else "number"
        raise ValueError(f"Plan field {key!r} must be a JSON {kind}")
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise ValueError(
            f"Plan field {key!r} must be between {minimum:g} and {maximum:g}"
        )
    return int(value) if integer else numeric


def require_slot_count(raw: Any) -> list[dict[str, Any]]:
    """Validate the top-level plan shape and cap process fan-out."""
    if not isinstance(raw, list):
        raise ValueError("Plan file must contain a JSON list")
    if len(raw) > MAX_PLAN_SLOTS:
        raise ValueError(f"Plan contains more than {MAX_PLAN_SLOTS} slots")
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid plan item at index {index}: expected object")
    return raw
