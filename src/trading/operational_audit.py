"""Durable local audit records for independently running Fenix instances."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.security.plan_security import read_bounded_json
from src.security.private_files import (
    ensure_private_directory,
    open_private_text,
    write_private_text,
)

logger = logging.getLogger("FenixOperationalAudit")

# Strong references so fire-and-forget alert tasks are not garbage collected
# mid-flight (asyncio only keeps weak references to running tasks).
_ALERT_TASKS: set = set()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip())
    return (normalized.strip(".-") or "fenix")[:80]


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class OperationalAudit:
    """Owns one instance heartbeat file and one append-only trade ledger."""

    def __init__(
        self,
        *,
        project_root: Path,
        symbol: str,
        timeframe: str,
        paper_trading: bool,
        allow_live_trading: bool,
        instance_id: str | None = None,
    ) -> None:
        configured_root = os.getenv("FENIX_OPERATIONAL_STATE_DIR", "").strip()
        state_root = (
            Path(configured_root).expanduser() if configured_root else project_root / "logs"
        )
        ensure_private_directory(state_root)
        raw_instance_id = instance_id or os.getenv("FENIX_INSTANCE_ID", "").strip()
        if not raw_instance_id:
            raw_instance_id = f"{symbol.lower()}-{timeframe}-{os.getpid()}"

        self.instance_id = _safe_component(raw_instance_id)
        self.symbol = str(symbol).upper()
        self.timeframe = str(timeframe)
        self.paper_trading = bool(paper_trading)
        self.allow_live_trading = bool(allow_live_trading)
        self.started_at = _utcnow()
        self._instance_path = state_root / "runtime_instances" / f"{self.instance_id}.json"
        self._ledger_path = state_root / "live_ledger" / f"{self.instance_id}.jsonl"

    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    def write_heartbeat(
        self,
        *,
        status: str,
        tracked_position: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> None:
        payload = {
            "schema_version": 1,
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "paper_trading": self.paper_trading,
            "allow_live_trading": self.allow_live_trading,
            "started_at": self.started_at,
            "heartbeat_at": _utcnow(),
            "status": str(status),
            "tracked_position": tracked_position,
            "detail": detail,
        }
        write_private_text(
            self._instance_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        )

    def append_ledger_record(self, record: dict[str, Any]) -> None:
        """Append one fsynced record; an audit write must survive a crash."""
        payload = {
            "schema_version": 1,
            "recorded_at": _utcnow(),
            "instance_id": self.instance_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "paper_trading": self.paper_trading,
            **record,
        }
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
            + "\n"
        )
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise ValueError("Operational audit record exceeds the 1 MiB limit")
        with open_private_text(self._ledger_path, "a") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())


def read_runtime_instances(
    project_root: Path | None = None,
    *,
    freshness_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Read local instance heartbeats without treating stale files as live."""
    configured_root = os.getenv("FENIX_OPERATIONAL_STATE_DIR", "").strip()
    root = (
        Path(configured_root).expanduser()
        if configured_root
        else (project_root or Path.cwd()) / "logs"
    )
    max_age = freshness_seconds
    if max_age is None:
        try:
            max_age = max(1.0, float(os.getenv("FENIX_INSTANCE_FRESHNESS_SEC", "20")))
        except (TypeError, ValueError):
            max_age = 20.0
    if (
        not math.isfinite(float(max_age))
        or float(max_age) <= 0
        or float(max_age) > 86_400
    ):
        max_age = 20.0

    now = datetime.now(timezone.utc)
    instances: list[dict[str, Any]] = []
    runtime_dir = root / "runtime_instances"
    if root.is_symlink() or runtime_dir.is_symlink() or not runtime_dir.is_dir():
        return instances
    for path in runtime_dir.glob("*.json"):
        try:
            payload = read_bounded_json(path, max_bytes=1_048_576)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        heartbeat = _parse_timestamp(payload.get("heartbeat_at"))
        age_seconds = None if heartbeat is None else max(0.0, (now - heartbeat).total_seconds())
        payload["heartbeat_age_seconds"] = age_seconds
        payload["fresh"] = bool(
            payload.get("status") == "running"
            and age_seconds is not None
            and age_seconds <= max_age
        )
        if not payload["fresh"] and payload.get("status") == "running":
            _alert_stale_heartbeat(payload, age_seconds)
        instances.append(payload)

    return sorted(
        instances,
        key=lambda item: (not bool(item.get("fresh")), str(item.get("instance_id", ""))),
    )


def _alert_stale_heartbeat(payload: dict[str, Any], age_seconds: float | None) -> None:
    """Fire-and-forget alert for a stale instance heartbeat."""
    try:
        import asyncio

        from src.risk.safety_alerts import alert_safety_event

        instance_id = payload.get("instance_id", "unknown")
        symbol = payload.get("symbol", "unknown")
        age_str = f"{age_seconds:.0f}s" if age_seconds is not None else "unknown"

        # Schedule without blocking the caller. get_event_loop() is unreliable
        # outside a running loop (deprecated, and it can return a loop that
        # never runs, silently dropping the alert) — require a running loop
        # and make the skip observable instead.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "Stale heartbeat for %s (%s, %s old) could not be alerted: "
                "no running event loop in this context",
                instance_id,
                symbol,
                age_str,
            )
            return

        task = loop.create_task(
            alert_safety_event(
                "STALE_HEARTBEAT",
                f"Instance {instance_id} ({symbol}) heartbeat is {age_str} old",
                {
                    "instance_id": instance_id,
                    "symbol": symbol,
                    "age_seconds": age_seconds,
                },
            )
        )
        _ALERT_TASKS.add(task)
        task.add_done_callback(_ALERT_TASKS.discard)
    except Exception:
        logger.debug("Stale heartbeat alert scheduling failed", exc_info=True)
