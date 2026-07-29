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


class _LeverageExecutor:
    """Executor stub exposing the strict class-level leverage reader."""

    def __init__(self, leverage):
        self._leverage = leverage

    def get_exchange_leverage(self):
        if isinstance(self._leverage, Exception):
            raise self._leverage
        return self._leverage


def _build_sizing_engine(executor, *, paper_trading: bool = False):
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "SOLUSDT"
    engine.paper_trading = paper_trading
    engine._engine_leverage = 10.0
    engine.executor = executor
    return engine


@pytest.mark.asyncio
async def test_sizing_leverage_prefers_exchange_value(monkeypatch):
    """Sizing must use the real 3x, not a stale FENIX_LEVERAGE=10."""
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    engine = _build_sizing_engine(_LeverageExecutor(3.0))

    assert await TradingEngine._resolve_sizing_leverage(engine) == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_sizing_leverage_falls_back_to_env_when_exchange_unknown(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LEVERAGE", "10")

    engine = _build_sizing_engine(_LeverageExecutor(None))
    assert await TradingEngine._resolve_sizing_leverage(engine) == pytest.approx(10.0)

    engine = _build_sizing_engine(_LeverageExecutor(TimeoutError("network blip")))
    assert await TradingEngine._resolve_sizing_leverage(engine) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_sizing_leverage_paper_mode_never_queries_exchange(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LEVERAGE", "10")

    class ExplodingExecutor:
        def get_exchange_leverage(self):
            raise AssertionError("paper sizing must not query the exchange")

    engine = _build_sizing_engine(ExplodingExecutor(), paper_trading=True)
    assert await TradingEngine._resolve_sizing_leverage(engine) == pytest.approx(10.0)


def _build_stagger_engine(symbol: str, *, paper_trading: bool = False):
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = symbol
    engine.paper_trading = paper_trading
    return engine


def test_analysis_stagger_is_deterministic_and_separates_live_bots(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.delenv("FENIX_ANALYSIS_STAGGER_OFFSET_SEC", raising=False)
    monkeypatch.setenv("FENIX_ANALYSIS_STAGGER_SEC", "10")

    eth = _build_stagger_engine("ETHUSDC")
    sol = _build_stagger_engine("SOLUSDT")

    eth_offset = TradingEngine._analysis_stagger_seconds(eth)
    sol_offset = TradingEngine._analysis_stagger_seconds(sol)

    assert 0.0 <= eth_offset < 10.0
    assert 0.0 <= sol_offset < 10.0
    # Stable across calls (hash-derived, not random).
    assert eth_offset == TradingEngine._analysis_stagger_seconds(eth)
    # The two live symbols land on different stable slots.
    assert eth_offset != sol_offset


def test_analysis_stagger_explicit_offset_wins(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_ANALYSIS_STAGGER_OFFSET_SEC", "2.5")
    monkeypatch.setenv("FENIX_ANALYSIS_STAGGER_SEC", "10")

    engine = _build_stagger_engine("ETHUSDC")
    assert TradingEngine._analysis_stagger_seconds(engine) == pytest.approx(2.5)


def test_analysis_stagger_disabled_in_paper_and_by_env(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.delenv("FENIX_ANALYSIS_STAGGER_OFFSET_SEC", raising=False)

    monkeypatch.delenv("FENIX_ANALYSIS_STAGGER_SEC", raising=False)
    paper = _build_stagger_engine("ETHUSDC", paper_trading=True)
    assert TradingEngine._analysis_stagger_seconds(paper) == 0.0

    monkeypatch.setenv("FENIX_ANALYSIS_STAGGER_SEC", "0")
    live = _build_stagger_engine("ETHUSDC")
    assert TradingEngine._analysis_stagger_seconds(live) == 0.0
