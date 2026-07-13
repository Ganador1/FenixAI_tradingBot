"""Fee-aware win/loss classification and the live account preflight.

Two regressions:

1. `_close_position_record` classified success/loss from gross realized PnL,
   so a trade that made money on price but lost it to round-trip commission
   counted as a "win" for loss_streak/win_rate/ReasoningBank. Classification
   must use PnL net of exchange commission.
2. Nothing verified the Binance account was in one-way position mode before
   starting live trading. Fenix never sends positionSide, so a hedge-mode
   account would reject every single order with -4061; the engine must
   refuse to start rather than discover this on the first live entry.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_close_record_engine():
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "ETHUSDT"
    engine.paper_trading = False
    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True
    engine.reasoning_bank = MagicMock()
    engine.executor = SimpleNamespace(get_balance=lambda: 500.0)
    engine.on_agent_event = AsyncMock()
    engine._register_post_stopout_block = MagicMock()
    engine._append_live_ledger_record = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_close_position_record_treats_commission_eaten_win_as_loss(monkeypatch):
    """A gross-positive trade that loses money net of commission is a loss."""
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    engine = _build_close_record_engine()
    monkeypatch.setattr(engine_module, "persist_position_close", AsyncMock(), raising=False)

    close_result = {
        "trade_id": "trade-fee-eaten",
        "exit_price": 2000.0,
        "pnl": 0.05,
        "pnl_pct": 0.02,
        "exchange_commission": 0.08,
    }

    await TradingEngine._close_position_record(engine, close_result)

    engine.risk_manager.close_trade.assert_called_once_with(
        "trade-fee-eaten",
        exit_price=2000.0,
        pnl=0.05,
        pnl_pct=0.02,
        success=False,
        symbol="ETHUSDT",
    )


@pytest.mark.asyncio
async def test_close_position_record_keeps_win_when_commission_does_not_flip_sign(monkeypatch):
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    engine = _build_close_record_engine()
    monkeypatch.setattr(engine_module, "persist_position_close", AsyncMock(), raising=False)

    close_result = {
        "trade_id": "trade-real-win",
        "exit_price": 2000.0,
        "pnl": 5.0,
        "pnl_pct": 2.0,
        "exchange_commission": 0.08,
    }

    await TradingEngine._close_position_record(engine, close_result)

    engine.risk_manager.close_trade.assert_called_once_with(
        "trade-real-win",
        exit_price=2000.0,
        pnl=5.0,
        pnl_pct=2.0,
        success=True,
        symbol="ETHUSDT",
    )


@pytest.mark.asyncio
async def test_close_position_record_reasoning_bank_reuses_net_classification(monkeypatch):
    """ReasoningBank must not recompute success independently from gross pnl."""
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    engine = _build_close_record_engine()
    monkeypatch.setattr(engine_module, "persist_position_close", AsyncMock(), raising=False)

    close_result = {
        "trade_id": "trade-fee-eaten-2",
        "exit_price": 2000.0,
        "pnl": 0.03,
        "pnl_pct": 0.01,
        "exchange_commission": 0.10,
        "reasoning_digest": "digest-fee-eaten",
        "decision_agent_name": "decision_agent",
    }

    await TradingEngine._close_position_record(engine, close_result)

    engine.reasoning_bank.update_entry_outcome.assert_called_once_with(
        agent_name="decision_agent",
        prompt_digest="digest-fee-eaten",
        success=False,
        reward=0.03,
        trade_id="trade-fee-eaten-2",
    )


def _build_preflight_engine(service):
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "SOLUSDT"
    engine.executor = SimpleNamespace(service=service)
    return engine


@pytest.mark.asyncio
async def test_preflight_blocks_startup_on_hedge_mode(monkeypatch):
    from src.trading.engine import TradingEngine

    service = MagicMock()
    service.get_position_mode.return_value = True
    engine = _build_preflight_engine(service)

    import src.risk.safety_alerts as safety_alerts

    alerts: list[tuple[str, dict | None]] = []

    async def record_alert(event_type, message, context=None):
        alerts.append((event_type, context))
        return True

    monkeypatch.setattr(safety_alerts, "alert_safety_event", record_alert)

    result = await TradingEngine._run_account_preflight(engine)

    assert result is False
    service.validate_permissions.assert_not_called()
    assert alerts and alerts[0][0] == "RECONCILIATION_FAILURE"


@pytest.mark.asyncio
async def test_preflight_passes_on_one_way_mode_with_trade_permission():
    from src.trading.engine import TradingEngine

    service = MagicMock()
    service.get_position_mode.return_value = False
    service.validate_permissions.return_value = (True, [])
    engine = _build_preflight_engine(service)

    result = await TradingEngine._run_account_preflight(engine)

    assert result is True


@pytest.mark.asyncio
async def test_preflight_blocks_startup_when_trading_permission_missing():
    from src.trading.engine import TradingEngine

    service = MagicMock()
    service.get_position_mode.return_value = False
    service.validate_permissions.return_value = (False, ["API key does not have trading permission"])
    engine = _build_preflight_engine(service)

    result = await TradingEngine._run_account_preflight(engine)

    assert result is False


@pytest.mark.asyncio
async def test_preflight_does_not_block_startup_on_transient_network_error():
    """A preflight that could not be verified must not block live trading."""
    from src.trading.engine import TradingEngine

    service = MagicMock()
    service.get_position_mode.side_effect = TimeoutError("network blip")
    service.validate_permissions.side_effect = TimeoutError("network blip")
    engine = _build_preflight_engine(service)

    result = await TradingEngine._run_account_preflight(engine)

    assert result is True
