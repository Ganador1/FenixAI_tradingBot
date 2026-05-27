"""Kline utilities for WS-driven pipelines.

Funciones para:
- extraer timestamp de cierre de velas desde payload de Binance WS
- decidir si procesar una vela comparando con el último timestamp procesado
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any


def extract_kline_close_ts(kline_payload: Any) -> datetime | None:
    """Extrae el timestamp de cierre de la vela (`k.T`) como datetime UTC.

    Acepta payloads raw del WS de Binance o dicts similares.
    Retorna None si no se puede extraer y debe usarse fallback externo.
    """
    try:
        if isinstance(kline_payload, dict) and kline_payload.get("e") == "kline":
            k = kline_payload.get("k", {})
            if close_ms := k.get("T"):
                return datetime.utcfromtimestamp(close_ms / 1000.0)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def should_process_kline(
    tf: str, last_ts_map: MutableMapping[str, datetime | None], kline_close_ts: datetime
) -> bool:
    """Devuelve True si la vela debe procesarse según el último timestamp visto para ese TF."""
    last = last_ts_map.get(tf)
    return True if last is None else kline_close_ts > last
