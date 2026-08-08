"""Shared engine-event handling for the API server and the live CLI process.

Both processes must translate raw ``TradingEngine`` events into the event
names/payloads the frontend expects, and persist agent outputs to the shared
SQLite database so REST endpoints (Agents page, Reasoning Bank, history)
survive page reloads.

Previously the API server had this logic inline and the live process
(`run_fenix.py`) forwarded RAW engine events through Redis with a partial
event-name map. Result: the dashboard rendered live events incorrectly and
nothing from the live session was persisted. This module is now the single
source of truth used by both sides.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("FenixEngineEvents")

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_CONF_LABEL_MAP = {"HIGH": 0.8, "MEDIUM": 0.55, "LOW": 0.35}
_ALERT_COOLDOWN_SECONDS = {
    "trade:error": 60.0,
    "risk:blocked": 60.0,
    "kline_watchdog": 300.0,
}


def _bounded_text(value: Any, default: str, limit: int) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        mapped = _CONF_LABEL_MAP.get(value.upper())
        if mapped is not None:
            return mapped
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    return 0.5


async def _persist_agent_output(payload: dict[str, Any], agent_name: str) -> None:
    """Persist an agent output row into the shared SQLite database."""
    try:
        from src.config.database import SessionLocal
        from src.models.db_models import AgentOutput

        async with SessionLocal() as db_session:
            db_output = AgentOutput(
                id=payload["id"],
                agent_id=agent_name.replace(" ", "_"),
                agent_name=agent_name,
                timestamp=datetime.utcnow(),
                reasoning=payload["reasoning"],
                decision=payload["decision"],
                confidence=payload["confidence"],
                input_summary=payload["input_summary"],
            )
            db_session.add(db_output)
            await db_session.commit()
    except Exception as db_err:
        logger.debug("Could not persist agent output to DB: %s", db_err)


async def _persist_system_alert(payload: dict[str, Any]) -> None:
    """Persist a bounded dashboard alert without blocking engine execution."""
    try:
        from sqlalchemy import delete, desc, select

        from src.config.database import SessionLocal
        from src.models.db_models import SystemAlert

        async with SessionLocal() as db_session:
            try:
                created_at = (
                    datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
            except (AttributeError, TypeError, ValueError):
                created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db_session.add(
                SystemAlert(
                    id=_bounded_text(payload.get("id"), str(uuid.uuid4()), 128),
                    type=_bounded_text(payload.get("type"), "warning", 32),
                    title=_bounded_text(payload.get("title"), "System alert", 160),
                    message=_bounded_text(payload.get("message"), "No details available.", 500),
                    component=_bounded_text(payload.get("component"), "system", 64),
                    severity=_bounded_text(payload.get("severity"), "medium", 16),
                    created_at=created_at,
                    resolved=bool(payload.get("resolved", False)),
                )
            )
            stale_ids = select(SystemAlert.id).order_by(desc(SystemAlert.created_at)).offset(1_000)
            await db_session.execute(delete(SystemAlert).where(SystemAlert.id.in_(stale_ids)))
            # Insert and retention are one atomic transaction and one fsync boundary.
            await db_session.commit()
    except Exception as db_err:
        logger.debug("Could not persist system alert to DB: %s", db_err)


def _system_alert_for_event(event_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = _bounded_text(data.get("timestamp"), datetime.now(timezone.utc).isoformat(), 64)
    symbol = _bounded_text(data.get("symbol"), "position", 32)
    if event_type == "position:closed":
        reason = _bounded_text(
            data.get("exit_reason") or data.get("reason"), "position_closed", 80
        ).lower()
        if "stop_loss" in reason:
            title, alert_type, severity = "Stop loss hit", "warning", "high"
        elif "take_profit" in reason:
            title, alert_type, severity = "Take profit hit", "info", "medium"
        else:
            title, alert_type, severity = "Position closed", "info", "low"
        message = f"{symbol} closed ({reason.replace('_', ' ')})."
    elif event_type == "trade:error":
        title, alert_type, severity = "Trade execution error", "error", "critical"
        message = str(data.get("message") or "The order could not be executed.")[:500]
    elif event_type == "risk:blocked":
        title, alert_type, severity = "Trade blocked by risk controls", "warning", "high"
        reason = str(data.get("reason") or "risk policy")
        message = f"{symbol}: {reason.replace('_', ' ')}."[:500]
    elif event_type.startswith("kline_watchdog") and "recovered" not in event_type:
        title, alert_type, severity = "Market data watchdog warning", "warning", "high"
        message = str(data.get("reason") or "Market data freshness check failed.")[:500]
    else:
        return None

    return {
        "id": str(uuid.uuid4()),
        "type": alert_type,
        "title": _bounded_text(title, "System alert", 160),
        "message": _bounded_text(message, "No details available.", 500),
        "component": "trading" if not event_type.startswith("kline_watchdog") else "market_data",
        "severity": severity,
        "created_at": timestamp,
        "resolved": False,
    }


def create_engine_event_handler(
    emit: EmitFn,
    *,
    persist: bool = True,
) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Build an ``on_agent_event`` callback for a TradingEngine.

    Args:
        emit: Async function used to broadcast Socket.IO events. In the API
            process this is ``sio.emit``; in the live CLI process it is
            ``RedisBridge.emit`` (which relays to the API server's clients).
        persist: Whether to write agent outputs to the shared database.

    Returns:
        Async callback compatible with ``TradingEngine.on_agent_event``.
    """
    # Keys are limited to the fixed alert categories above, so this cannot grow
    # with attacker-controlled symbols or messages.
    alert_last_emitted_at: dict[str, float] = {}

    async def handle_engine_event(event_type: str, data: dict[str, Any]) -> None:
        try:
            data = data or {}
            system_alert = _system_alert_for_event(event_type, data)
            if system_alert is not None:
                cooldown_key = (
                    "kline_watchdog" if event_type.startswith("kline_watchdog") else event_type
                )
                cooldown = _ALERT_COOLDOWN_SECONDS.get(cooldown_key, 0.0)
                now = time.monotonic()
                last_emitted = alert_last_emitted_at.get(cooldown_key, float("-inf"))
                if now - last_emitted >= cooldown:
                    alert_last_emitted_at[cooldown_key] = now
                    await emit("system:alert", system_alert)
                    if persist:
                        await _persist_system_alert(system_alert)

            if event_type == "agent_output":
                agent_name = data.get("agent_name", "unknown")
                inner = data.get("data", {}) or {}
                payload = {
                    "id": str(uuid.uuid4()),
                    "agent_name": agent_name,
                    "timestamp": data.get("timestamp"),
                    "reasoning": inner.get("reasoning", "")
                    or inner.get("visual_analysis", "")
                    or inner.get("analysis", "")
                    or "No reasoning",
                    "decision": inner.get("signal") or inner.get("action") or "HOLD",
                    "confidence": _coerce_confidence(
                        inner.get("confidence") or inner.get("confidence_in_decision")
                    ),
                    "input_summary": "Live Analysis",
                }
                # Include social and Fear&Greed data for sentiment agent
                if data.get("social_data"):
                    payload["social_data"] = data.get("social_data")
                if data.get("fear_greed_value"):
                    payload["fear_greed_value"] = data.get("fear_greed_value")
                await emit("agentOutput", payload)

                if persist:
                    await _persist_agent_output(payload, agent_name)

            elif event_type == "cycle_summary":
                dec = data.get("decision", "HOLD")
                conf = data.get("confidence", 0.35)
                conf_label = data.get("confidence_label", "LOW")
                holds = data.get("consecutive_holds", 0)
                elapsed = data.get("elapsed_s", 0)
                reasoning = data.get("reasoning", "")
                # One feed line that summarises the full cycle
                summary = (
                    f"📋 {dec} ({conf_label}) | {reasoning} | " f"holds={holds} | ⏱ {elapsed:.1f}s"
                )
                await emit(
                    "agentOutput",
                    {
                        "id": str(uuid.uuid4()),
                        "agent_name": "── Cycle Summary ──",
                        "timestamp": data.get("timestamp"),
                        "reasoning": summary,
                        "decision": dec,
                        "confidence": conf,
                        "input_summary": "cycle",
                    },
                )
                # Separate event so the frontend can show a countdown timer
                await emit("cycle:summary", data)
            elif event_type == "final_decision":
                payload = {
                    "decision": data.get("decision"),
                    "confidence": data.get("confidence"),
                    "reasoning": data.get("reasoning"),
                    "timestamp": data.get("timestamp"),
                }
                await emit("trade:signal", payload)
            elif event_type == "news_update":
                payload = {
                    "news": data.get("news_data", []),
                    "timestamp": data.get("timestamp"),
                }
                await emit("news:update", payload)
                # Backward-compatible alias
                await emit("newsUpdate", payload)
            elif event_type == "reasoning:new":
                payload = {
                    "agent_name": data.get("agent_name"),
                    "prompt_digest": data.get("prompt_digest"),
                    "timestamp": data.get("timestamp"),
                }
                await emit("reasoning:new", payload)
                # Backwards-compatible event names for frontend
                await emit("agent:reasoning", payload)
                await emit("reasoningUpdate", payload)
            elif event_type in ("filter:blocked", "filter:adjusted"):
                # Entry filters (MTF veto, directional score, min confidence…).
                # Without these the dashboard cannot explain why a BUY/SELL
                # decision did not become an order.
                await emit(event_type, data)
            elif event_type == "risk:blocked":
                await emit("risk:blocked", data)
            elif event_type == "nanofenix:policy":
                await emit("nanofenix:policy", data)
            elif event_type in ("trade_executed", "trade:simulated"):
                payload = {"simulated": event_type == "trade:simulated", **data}
                await emit("trade:executed", payload)
                # camelCase alias the Trading page already listened to.
                await emit("tradeExecuted", payload)
            elif event_type.startswith("position:"):
                payload = {"kind": event_type, **data}
                await emit("position:update", payload)
                await emit("positionUpdate", payload)
            elif event_type.startswith("analysis_cycle"):
                await emit("engine:cycle", {"kind": event_type, **data})
            elif event_type.startswith("kline_watchdog"):
                await emit("engine:watchdog", {"kind": event_type, **data})

        except Exception as e:
            logger.error("Error handling engine event %s: %s", event_type, e)

    return handle_engine_event
