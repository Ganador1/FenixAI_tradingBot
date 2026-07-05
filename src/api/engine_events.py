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
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger("FenixEngineEvents")

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]


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

    async def handle_engine_event(event_type: str, data: dict[str, Any]) -> None:
        try:
            data = data or {}
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
                    "confidence": inner.get("confidence", 0.0),
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
