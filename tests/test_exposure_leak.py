"""Regression tests for the phantom-exposure leak (2026-07-04).

Overnight bug: opening a trade registered exposure TWICE (open_trade already
calls update_open_position internally, and the engine called it again), while
closing released it only once. After two round-trips the circuit breaker
blocked a flat account with 'Total exposure would exceed limit' — rejecting a
3/3 HIGH consensus entry at 02:15.
"""

import pytest

from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord
from datetime import datetime, timezone


def _record(trade_id: str, size: float = 100.0, symbol: str = "ETHUSDC") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        timestamp=datetime.now(timezone.utc),
        symbol=symbol,
        decision="BUY",
        entry_price=1750.0,
        exit_price=None,
        pnl=0.0,
        pnl_pct=0.0,
        success=True,
        size=size,
    )


@pytest.mark.unit
def test_open_close_roundtrip_releases_all_exposure():
    rm = RuntimeRiskManager()
    rm.update_balance(400.0)

    rm.open_trade(_record("t1", size=100.0))
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(100.0)

    rm.close_trade("t1", exit_price=1760.0, pnl=0.5, pnl_pct=0.5, success=True, symbol="ETHUSDC")
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(0.0)


@pytest.mark.unit
def test_double_registration_would_leak_without_flatten():
    """Reproduce the overnight leak: extra update_open_position on open."""
    rm = RuntimeRiskManager()
    rm.update_balance(400.0)

    # Old engine behaviour: open_trade + redundant update_open_position.
    rm.open_trade(_record("t1", size=100.0))
    rm.update_open_position("ETHUSDC", size=100.0, notional=100.0, side="buy")
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(200.0)

    # Single close released only half — phantom 100 USD remained.
    rm.close_trade("t1", symbol="ETHUSDC")
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(100.0)

    # flatten_symbol (called on full close now) purges the phantom.
    rm.flatten_symbol("ETHUSDC")
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(0.0)
    assert rm.get_total_exposure()["positions_count"] == 0


@pytest.mark.unit
def test_flatten_symbol_removes_all_pyramid_records():
    """Pyramid adds create multiple TradeRecords; one close must free all."""
    rm = RuntimeRiskManager()
    rm.update_balance(400.0)

    rm.open_trade(_record("base", size=100.0))
    rm.open_trade(_record("add1", size=50.0))
    rm.open_trade(_record("add2", size=25.0))
    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(175.0)

    # Exchange close is a single event → close one record + flatten.
    rm.close_trade("base", symbol="ETHUSDC")
    rm.flatten_symbol("ETHUSDC")

    assert rm.get_total_exposure()["total_exposure"] == pytest.approx(0.0)
    # No stale active trades left for the symbol.
    assert all(t.symbol.upper() != "ETHUSDC" for t in rm._active_trades.values())


@pytest.mark.unit
def test_flatten_symbol_ignores_other_symbols():
    rm = RuntimeRiskManager()
    rm.update_balance(400.0)
    rm.open_trade(_record("eth", size=100.0, symbol="ETHUSDC"))
    rm.open_trade(_record("btc", size=200.0, symbol="BTCUSDT"))

    rm.flatten_symbol("ETHUSDC")

    exposure = rm.get_total_exposure()
    assert exposure["total_exposure"] == pytest.approx(200.0)
    assert "btc" in rm._active_trades
