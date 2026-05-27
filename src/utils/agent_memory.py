from datetime import datetime
from typing import Any

from src.memory.trade_memory import TradeMemory

trade_memory = TradeMemory()

# Debes asegurarte de que trade_memory esté inicializado antes de usar esta función


def get_last_agent_decision(
    agent_name: str, symbol: str, max_age_minutes: int = 10
) -> tuple[dict[str, Any] | None, datetime | None]:
    """
    Devuelve la última decisión de un agente para un símbolo, solo si es reciente (por ejemplo, <10 minutos).
    """
    recent_trades = trade_memory.get_recent_trades(hours=1)
    now = datetime.now()
    for trade in reversed(recent_trades):
        if trade.get("symbol") == symbol:
            decision_ctx = trade.get("decision_context", {})
            agent_decision = decision_ctx.get(agent_name, {})
            timestamp_str = trade.get("timestamp")
            if timestamp_str:
                try:
                    ts = datetime.fromisoformat(timestamp_str)
                    age_minutes = (now - ts).total_seconds() / 60
                    if age_minutes <= max_age_minutes:
                        return agent_decision, ts
                except Exception:
                    continue
    return None, None
