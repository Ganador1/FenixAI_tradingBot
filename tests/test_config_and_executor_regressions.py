"""Regression tests for config loading and the limit-order cancel race.

Covers three fixes from the 2026-07 audit:
1. ``config.settings.FenixConfig.from_dict`` must tolerate unknown YAML keys
   (fenix.yaml previously failed to load with TypeError).
2. ``run_fenix.parse_args`` defaults must come from config/fenix.yaml while
   explicit CLI flags keep precedence.
3. ``OrderExecutor.execute_market_order`` must never fall back to a market
   order when a GTX limit cancel cannot be confirmed (double-position risk).
"""

import sys
from unittest.mock import MagicMock

import pytest

from config.settings import AgentSettings, FenixConfig
from src.core.trading_constants import SymbolConfig
from src.trading.executor import OrderExecutor


# ---------------------------------------------------------------------------
# 1. Config loading
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_ignores_unknown_keys():
    """Unknown YAML keys must not raise TypeError (regression)."""
    config = FenixConfig.from_dict(
        {
            "agents": {
                "technical_weight": 0.35,
                "made_up_future_key": 123,
            },
            "trading": {"symbol": "ETHUSDT", "another_unknown": "x"},
        }
    )
    assert config.agents.technical_weight == pytest.approx(0.35)
    assert config.trading.symbol == "ETHUSDT"


@pytest.mark.unit
def test_from_dict_loads_v21_agent_keys():
    """The v2.1 keys present in fenix.yaml map to real fields now."""
    config = FenixConfig.from_dict(
        {
            "agents": {
                "sentiment_timeout_short": 8,
                "sentiment_cache_ttl": 900,
                "trailing_stop_escalated": True,
            }
        }
    )
    assert config.agents.sentiment_timeout_short == 8
    assert config.agents.sentiment_cache_ttl == 900
    assert config.agents.trailing_stop_escalated is True


@pytest.mark.unit
def test_agent_settings_defaults_still_valid():
    settings = AgentSettings()
    total = (
        settings.technical_weight
        + settings.qabba_weight
        + settings.visual_weight
        + settings.sentiment_weight
    )
    assert total == pytest.approx(1.0)


@pytest.mark.unit
def test_real_fenix_yaml_loads():
    """The shipped config/fenix.yaml must load end-to-end without errors."""
    from pathlib import Path

    yaml_path = Path(__file__).resolve().parent.parent / "config" / "fenix.yaml"
    if not yaml_path.exists():
        pytest.skip("config/fenix.yaml not present")
    config = FenixConfig.from_yaml(yaml_path)
    assert config.trading.symbol
    assert 0 < config.agents.technical_weight <= 1


# ---------------------------------------------------------------------------
# 2. CLI defaults from fenix.yaml
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_defaults_come_from_yaml(monkeypatch):
    import run_fenix

    monkeypatch.setattr(sys, "argv", ["run_fenix.py"])
    args = run_fenix.parse_args()

    from config.settings import get_config

    cfg = get_config()
    assert args.symbol == cfg.trading.symbol
    assert args.timeframe == cfg.trading.timeframe
    assert args.model == cfg.llm.default_model
    assert args.interval == cfg.trading.analysis_interval_seconds


@pytest.mark.unit
def test_cli_flags_override_yaml(monkeypatch):
    import run_fenix

    monkeypatch.setattr(
        sys, "argv", ["run_fenix.py", "--symbol", "SOLUSDT", "--timeframe", "1m"]
    )
    args = run_fenix.parse_args()
    assert args.symbol == "SOLUSDT"
    assert args.timeframe == "1m"


# ---------------------------------------------------------------------------
# 3. Limit-order cancel race (double-position guard)
# ---------------------------------------------------------------------------


def _make_executor() -> tuple[OrderExecutor, MagicMock]:
    # These tests intentionally exercise exchange-write paths against a mock
    # service, so the mutation capability must be granted explicitly.
    executor = OrderExecutor(
        symbol="BTCUSDT",
        testnet=True,
        timeframe="15m",
        allow_mutations=True,
    )
    svc = MagicMock()
    svc.get_symbol_config.return_value = SymbolConfig(
        symbol="BTCUSDT",
        tick_size=0.1,
        step_size=0.001,
        min_notional=5.0,
        price_precision=1,
        quantity_precision=3,
    )
    svc.get_ticker_price.return_value = 50000.0
    svc.get_open_orders.return_value = []
    executor._service = svc
    return executor, svc


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_cancel_unconfirmed_blocks_market_fallback(monkeypatch):
    """If the cancel fails and status is unknown, do NOT place a market order."""
    monkeypatch.setenv("FENIX_USE_LIMIT_ENTRY", "1")
    executor, svc = _make_executor()

    svc.place_limit_order.return_value = {"orderId": 111, "status": "NEW"}
    svc.cancel_order.side_effect = RuntimeError("network error")
    svc.get_order.side_effect = RuntimeError("network error")

    result = await executor.execute_market_order(side="BUY", quantity=0.1)

    assert result.success is False
    assert result.status == "LIMIT_CANCEL_UNCONFIRMED"
    svc.place_market_order.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_filled_during_cancel_race_uses_limit_fill(monkeypatch):
    """If the limit filled while cancel failed, use it as entry (no market order)."""
    monkeypatch.setenv("FENIX_USE_LIMIT_ENTRY", "1")
    executor, svc = _make_executor()

    svc.place_limit_order.return_value = {"orderId": 222, "status": "NEW"}
    svc.cancel_order.side_effect = RuntimeError("cancel rejected")
    svc.get_order.return_value = {
        "orderId": 222,
        "status": "FILLED",
        "avgPrice": "49999.9",
        "executedQty": "0.1",
    }

    result = await executor.execute_market_order(side="BUY", quantity=0.1)

    assert result.success is True
    assert result.order_id == 222
    assert result.entry_price == pytest.approx(49999.9)
    svc.place_market_order.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_cancel_confirmed_falls_back_to_market(monkeypatch):
    """Normal path: cancel succeeds and the market fallback executes."""
    monkeypatch.setenv("FENIX_USE_LIMIT_ENTRY", "1")
    executor, svc = _make_executor()

    svc.place_limit_order.return_value = {"orderId": 333, "status": "NEW"}
    svc.cancel_order.return_value = {"orderId": 333, "status": "CANCELED"}
    svc.place_market_order.return_value = {"orderId": 444}
    svc.get_order.return_value = {
        "orderId": 444,
        "status": "FILLED",
        "avgPrice": "50001.0",
        "executedQty": "0.1",
    }

    result = await executor.execute_market_order(side="BUY", quantity=0.1)

    assert result.success is True
    assert result.order_id == 444
    svc.place_market_order.assert_called_once()
