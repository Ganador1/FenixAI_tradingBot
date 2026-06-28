import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace


def _build_minimal_engine(*, symbol: str = "BTCUSDT", timeframe: str = "1m"):
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = symbol
    engine.timeframe = timeframe
    engine.use_testnet = True
    engine.paper_trading = False
    engine.allow_live_trading = True
    engine.enable_trading = True
    engine._running = False
    engine._stopping = False
    engine._stopped = False
    engine._engine_cleanup_on_stop = False
    engine._consecutive_holds = 0
    engine._last_decision_time = None
    engine._fast_last_trade_ts = None
    engine._engine_leverage = 10.0
    engine._fast_scout_fraction = 0.25
    engine._fast_scout_max_notional_usd = 100.0
    engine._fast_require_sltp = False
    engine._add_position_reserve_pct = 0.0
    engine._short_tf_mode = timeframe in {"1m", "3m", "5m"}
    engine._nanofenix_companion_enabled = False
    engine._nanofenix_timing_trigger_enabled = False
    engine._nanofenix_timing_trigger_ttl_sec = 75.0
    engine._nanofenix_timing_trigger_min_fast_score = 1.2
    engine._nanofenix_timing_trigger_require_regime = True
    engine._nanofenix_timing_trigger_allow_countertrend = False
    engine._nanofenix_timing_regime = None
    engine._nanofenix_min_conf = 0.62
    engine._nanofenix_min_pred_bps = 2.5
    engine._nanofenix_min_direction_accuracy = 0.55
    engine._nanofenix_require_for_opposite_exit = True
    engine._nanofenix_force_reversal_exit = False
    engine._nanofenix_min_actionable_edge_bps = 0.8
    engine._nanofenix_max_uncertainty_bps = 3.0
    engine._nanofenix_uncertainty_size_reduce_threshold = 1.5
    engine._nanofenix_require_allow_execute = False
    engine._nanofenix_hard_veto_reasons = set()
    engine._nanofenix_strong_reversal_override = False
    engine._nanofenix_strong_reversal_override_score = 0.80
    engine._nanofenix_strong_reversal_override_confidence = "HIGH"
    engine._engine_enforce_llm_risk = False
    engine._fast_reversal_exit_enabled = True
    engine._fast_reversal_exit_score = 1.9
    engine._fast_reversal_exit_min_adverse_pct = 0.12
    engine._filter_qabba_min_conf = 0.70
    engine._filter_require_qabba_alignment = False
    engine._filter_qabba_opposite_veto_conf = 0.80
    engine._filter_qabba_hold_veto_conf = 0.95
    engine._filter_obi_buy = 1.25
    engine._filter_obi_sell = 0.80
    engine._filter_volume_imb_th = 0.15
    engine._filter_chop_size_mult = 0.90
    engine._filter_chop_size_mult_short = 0.85
    engine._filter_chop_size_mult_short_low_conf = 0.70
    engine._filter_vpin_high = 0.90
    engine._filter_vpin_size_mult = 0.35
    engine._filter_rsi_overbought = 80.0
    engine._filter_rsi_oversold = 20.0
    engine._filter_sr_prox_pct = 0.02
    engine._filter_sr_prox_pct_short = 0.005
    engine._filter_rebound_guard_enabled = False
    engine._filter_rebound_percent_b_max = 0.15
    engine._filter_rebound_rsi_max = 35.0
    engine._filter_rebound_wick_ratio_min = 1.10
    engine._filter_rebound_micro_obi_min = 1.05
    engine._filter_rebound_micro_wdi_min = 0.05
    engine._filter_rebound_micro_imb_min = 0.03
    engine._filter_rejection_percent_b_min = 0.85
    engine._filter_rejection_rsi_min = 65.0
    engine._filter_rejection_wick_ratio_min = 1.10
    engine._filter_rejection_micro_obi_max = 0.95
    engine._filter_rejection_micro_wdi_max = -0.05
    engine._filter_rejection_micro_imb_max = -0.03
    engine._long_confluence_guard = False
    engine._long_confluence_qabba_min_conf = 0.70
    engine._long_confluence_allow_high_conf = True
    engine._short_confluence_guard = False
    engine._short_confluence_qabba_min_conf = 0.70
    engine._short_confluence_allow_high_conf = True
    engine._filter_min_buy_directional_score = 0.0
    engine._filter_min_sell_directional_score = 0.0
    engine._medium_buy_strong_edge_enabled = False
    engine._medium_sell_strong_edge_enabled = False
    engine._medium_buy_strong_edge_score = 0.60
    engine._medium_sell_strong_edge_score = 0.60
    engine._filter_block_trend_conflict_non_high = False
    engine._eth3m_long_max_entries = 2
    engine._eth3m_require_qabba_for_long_add = True
    engine._eth3m_long_add_qabba_min_conf = 0.85
    engine._eth3m_block_long_add_in_low_regime = True
    engine._eth3m_block_long_add_on_trend_conflict = True
    engine._eth3m_long_trend_guard = True
    engine._eth3m_long_size_mult = 0.5
    engine.market_data = MagicMock()
    engine.market_data.current_price = 100.0
    engine.market_data.stop = AsyncMock()
    engine.market_data.get_microstructure_metrics = MagicMock(return_value=MagicMock())
    engine.executor = MagicMock()
    engine.trade_manager = MagicMock()
    engine.reasoning_bank = MagicMock()
    engine.risk_manager = None
    engine.on_agent_event = None
    engine._stop_chart_refresher = AsyncMock()
    engine._stop_sentiment_refresher = AsyncMock()
    engine._stop_balance_refresher = AsyncMock()
    engine._stop_fast_loop = AsyncMock()
    engine._chart_service = MagicMock()
    engine._filter_block_counts = {}
    engine._filter_adjust_counts = {}
    engine._get_cached_balance_usdt = MagicMock(return_value=None)
    engine._hydrate_tracked_position_from_exchange = AsyncMock()
    engine._apply_fast_reversal_exit_policy = MagicMock(side_effect=lambda **kwargs: (kwargs.get("new_signal"), None))
    engine._apply_nanofenix_exit_policy = MagicMock(side_effect=lambda **kwargs: (kwargs.get("new_signal"), None))
    engine._log_signal = MagicMock()
    return engine


def test_timeframe_to_seconds_supports_live_watchdog_intervals():
    from src.trading.engine import TradingEngine

    assert TradingEngine._timeframe_to_seconds("3m") == 180
    assert TradingEngine._timeframe_to_seconds("15m") == 900
    assert TradingEngine._timeframe_to_seconds("1h") == 3600
    assert TradingEngine._timeframe_to_seconds("bad") == 900


def test_default_kline_watchdog_grace_is_one_interval_plus_small_buffer():
    from src.trading.engine import TradingEngine

    assert TradingEngine._default_kline_watchdog_grace_sec("5m") == pytest.approx(345.0)
    assert TradingEngine._default_kline_watchdog_grace_sec("15m") == pytest.approx(1020.0)


@pytest.mark.asyncio
async def test_hydrate_tracked_position_from_exchange_registers_live_position(monkeypatch):
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine.paper_trading = False
    engine.trade_manager.get_position.return_value = None
    engine.trade_manager.open_position.return_value = SimpleNamespace(
        trade_id="hydrated:SOLUSDT:test"
    )
    engine.executor.get_position.return_value = {
        "positionAmt": "-0.06",
        "entryPrice": "86.17",
        "markPrice": "86.05",
    }
    engine.executor.service = MagicMock()
    engine.executor.service.get_open_algo_orders.return_value = [
        {"algoId": 3000001353880337, "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "85.29"},
        {"algoId": 3000001353880306, "orderType": "STOP_MARKET", "triggerPrice": "86.78"},
    ]
    engine.risk_manager = MagicMock()
    engine.risk_manager.open_trade.return_value = None
    engine.on_agent_event = AsyncMock()
    persist_open_position = AsyncMock()
    monkeypatch.setattr(engine_module, "persist_open_position", persist_open_position, raising=False)

    hydrated = await TradingEngine._hydrate_tracked_position_from_exchange(engine)

    assert hydrated is True
    engine.trade_manager.open_position.assert_called_once()
    kwargs = engine.trade_manager.open_position.call_args.kwargs
    assert kwargs["symbol"] == "SOLUSDT"
    assert kwargs["side"] == "SHORT"
    assert kwargs["entry_price"] == pytest.approx(86.17)
    assert kwargs["quantity"] == pytest.approx(0.06)
    assert kwargs["stop_loss"] == pytest.approx(86.78)
    assert kwargs["take_profit"] == pytest.approx(85.29)
    assert kwargs["sl_order_id"] == 3000001353880306
    assert kwargs["tp_order_id"] == 3000001353880337
    engine.risk_manager.open_trade.assert_called_once()
    engine.on_agent_event.assert_awaited_once()
    assert engine.on_agent_event.await_args.args[0] == "position:hydrated"
    persist_open_position.assert_awaited_once()
    assert persist_open_position.await_args.kwargs["symbol"] == "SOLUSDT"
    assert persist_open_position.await_args.kwargs["side"] == "SHORT"
    assert persist_open_position.await_args.kwargs["quantity"] == pytest.approx(0.06)


def test_rest_kline_payload_is_converted_to_closed_kline_data():
    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")

    converted = engine._rest_kline_to_kline_data(
        {
            "timestamp": 1710000000000,
            "close_time": 1710000899999,
            "open": 85.1,
            "high": 85.5,
            "low": 84.9,
            "close": 85.3,
            "volume": 123.4,
        }
    )

    assert converted["symbol"] == "SOLUSDT"
    assert converted["timeframe"] == "15m"
    assert converted["open_time"] == 1710000000000
    assert converted["close_time"] == 1710000899999
    assert converted["close"] == 85.3
    assert converted["is_closed"] is True
    assert converted["source"] == "rest_kline_watchdog"


@pytest.mark.asyncio
async def test_rest_kline_watchdog_injects_only_unseen_closed_klines(monkeypatch):
    from src.trading import engine as engine_mod

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._last_closed_kline_open_time = 1710000000000
    engine._on_kline_received = AsyncMock()

    class _FakeClient:
        async def connect(self):
            return True

        async def get_klines(self, symbol, interval, limit):
            assert symbol == "SOLUSDT"
            assert interval == "15m"
            assert limit == 3
            return [
                {
                    "timestamp": 1710000000000,
                    "close_time": 1710000899999,
                    "open": 85.1,
                    "high": 85.5,
                    "low": 84.9,
                    "close": 85.3,
                    "volume": 123.4,
                },
                {
                    "timestamp": 1710000900000,
                    "close_time": 1710001799999,
                    "open": 85.3,
                    "high": 85.8,
                    "low": 85.0,
                    "close": 85.7,
                    "volume": 234.5,
                },
            ]

        async def close(self):
            return None

    monkeypatch.setattr(engine_mod, "BinanceClient", lambda testnet: _FakeClient())

    injected = await engine._poll_closed_kline_fallback(limit=3)

    assert injected == 1
    engine._on_kline_received.assert_awaited_once()
    payload = engine._on_kline_received.await_args.args[0]
    assert payload["open_time"] == 1710000900000
    assert payload["source"] == "rest_kline_watchdog"


@pytest.mark.asyncio
async def test_rest_kline_watchdog_without_last_seen_injects_only_latest_closed(monkeypatch):
    from src.trading import engine as engine_mod

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._last_closed_kline_open_time = None
    engine._on_kline_received = AsyncMock()

    class _FakeClient:
        async def connect(self):
            return True

        async def get_klines(self, symbol, interval, limit):
            assert symbol == "SOLUSDT"
            assert interval == "15m"
            assert limit == 3
            return [
                {
                    "timestamp": 1710000000000,
                    "close_time": 1710000899999,
                    "open": 85.1,
                    "high": 85.5,
                    "low": 84.9,
                    "close": 85.3,
                    "volume": 123.4,
                },
                {
                    "timestamp": 1710000900000,
                    "close_time": 1710001799999,
                    "open": 85.3,
                    "high": 85.8,
                    "low": 85.0,
                    "close": 85.7,
                    "volume": 234.5,
                },
                {
                    "timestamp": 1710001800000,
                    "close_time": 999999999999999,
                    "open": 85.7,
                    "high": 85.9,
                    "low": 85.2,
                    "close": 85.4,
                    "volume": 345.6,
                },
            ]

        async def close(self):
            return None

    monkeypatch.setattr(engine_mod, "BinanceClient", lambda testnet: _FakeClient())

    injected = await engine._poll_closed_kline_fallback(limit=3)

    assert injected == 1
    engine._on_kline_received.assert_awaited_once()
    payload = engine._on_kline_received.await_args.args[0]
    assert payload["open_time"] == 1710000900000


@pytest.mark.asyncio
async def test_rest_kline_watchdog_skips_current_open_rest_kline(monkeypatch):
    from src.trading import engine as engine_mod

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._last_closed_kline_open_time = 1710000000000
    engine._on_kline_received = AsyncMock()

    class _FakeClient:
        async def connect(self):
            return True

        async def get_klines(self, symbol, interval, limit):
            return [
                {
                    "timestamp": 1710000900000,
                    "close_time": 999999999999999,
                    "open": 85.3,
                    "high": 85.8,
                    "low": 85.0,
                    "close": 85.7,
                    "volume": 234.5,
                },
            ]

        async def close(self):
            return None

    monkeypatch.setattr(engine_mod, "BinanceClient", lambda testnet: _FakeClient())

    injected = await engine._poll_closed_kline_fallback(limit=3)

    assert injected == 0
    engine._on_kline_received.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_kline_duplicate_is_not_ingested_or_analyzed(monkeypatch):
    from src.trading import engine as engine_mod
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._kline_count = 5
    engine._min_klines_to_start = 1
    engine._last_closed_kline_open_time = 1710000000000
    engine._run_analysis_cycle = AsyncMock()
    add_kline = MagicMock()
    monkeypatch.setattr(engine_mod, "add_kline", add_kline)

    await TradingEngine._on_kline_received(
        engine,
        {
            "open_time": 1710000000000,
            "open": 85.1,
            "high": 85.5,
            "low": 84.9,
            "close": 85.3,
            "volume": 123.4,
            "is_closed": True,
        },
    )

    add_kline.assert_not_called()
    engine._run_analysis_cycle.assert_not_awaited()
    assert engine._kline_count == 5


@pytest.mark.asyncio
async def test_closed_kline_duplicate_after_first_close_runs_analysis_once(monkeypatch):
    from src.trading import engine as engine_mod
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._kline_count = 0
    engine._min_klines_to_start = 1
    engine._last_closed_kline_open_time = None
    engine._run_analysis_cycle = AsyncMock()
    add_kline = MagicMock()
    monkeypatch.setattr(engine_mod, "add_kline", add_kline)
    payload = {
        "open_time": 1710000900000,
        "open": 85.3,
        "high": 85.8,
        "low": 85.0,
        "close": 85.7,
        "volume": 234.5,
        "is_closed": True,
    }

    await TradingEngine._on_kline_received(engine, dict(payload))
    await TradingEngine._on_kline_received(engine, dict(payload))

    assert add_kline.call_count == 1
    engine._run_analysis_cycle.assert_awaited_once()
    assert engine._last_closed_kline_open_time == 1710000900000


@pytest.mark.asyncio
async def test_analysis_cycle_skips_when_previous_cycle_is_running():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine._analysis_cycle_lock = asyncio.Lock()
    await engine._analysis_cycle_lock.acquire()
    engine.on_agent_event = AsyncMock()

    try:
        await TradingEngine._run_analysis_cycle(engine)
    finally:
        engine._analysis_cycle_lock.release()

    engine.on_agent_event.assert_awaited_once()
    assert engine.on_agent_event.await_args.args[0] == "analysis_cycle_skipped"


@pytest.mark.asyncio
async def test_execute_trade_skips_same_side_position_by_default(monkeypatch):
    """
    Safety: if a same-side position exists, the engine should not pyramid by default.
    This prevents repeated entries when running on short TF with noisy/stale decisions.
    """
    monkeypatch.delenv("FENIX_ALLOW_ADD_TO_POSITION", raising=False)

    engine = _build_minimal_engine(timeframe="1m")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.50", "entryPrice": "99.90"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0}
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_trade_blocks_same_side_add_when_nanofenix_policy_disallows(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(timeframe="15m")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.50", "entryPrice": "99.90"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
        "nanofenix_policy": {
            "allow_add_to_position": False,
            "reason": "nanofenix_policy_blocks_same_side_add",
        },
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_trade_reconciles_stale_local_position_before_opening_live_trade():
    engine = _build_minimal_engine(timeframe="15m")

    stale_position = SimpleNamespace(side="SHORT", quantity=0.4, entry_price=101.2)
    state = {"tracked_position": stale_position}

    engine.trade_manager = MagicMock()
    engine.trade_manager.get_position.side_effect = lambda *_args, **_kwargs: state["tracked_position"]
    engine.trade_manager.open_position = MagicMock()

    async def _clear_stale_position():
        state["tracked_position"] = None

    engine._reconcile_tracked_position_with_exchange = AsyncMock(side_effect=_clear_stale_position)

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            executed_qty=0.5,
            entry_price=100.0,
            order_id="stale-cleared-1",
        )
    )
    engine.executor = executor

    decision_data = {
        "position_size": 50.0,
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    engine._reconcile_tracked_position_with_exchange.assert_awaited_once()
    executor.execute_market_order.assert_awaited_once()
    engine.trade_manager.open_position.assert_called_once()


@pytest.mark.asyncio
async def test_execute_trade_blocks_when_stale_local_position_survives_reconciliation():
    engine = _build_minimal_engine(timeframe="15m")
    engine.on_agent_event = AsyncMock()

    stale_position = SimpleNamespace(side="SHORT", quantity=0.4, entry_price=101.2)
    engine.trade_manager = MagicMock()
    engine.trade_manager.get_position.return_value = stale_position
    engine.trade_manager.open_position = MagicMock()
    engine._reconcile_tracked_position_with_exchange = AsyncMock()

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "position_size": 50.0,
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    engine._reconcile_tracked_position_with_exchange.assert_awaited_once()
    executor.execute_market_order.assert_not_awaited()
    engine.trade_manager.open_position.assert_not_called()
    assert any(
        call.args[0] == "risk:blocked"
        and call.args[1].get("reason") == "local_position_stale_after_reconciliation"
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_applies_nanofenix_size_multiplier_hint():
    engine = _build_minimal_engine(timeframe="15m")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            executed_qty=0.2,
            entry_price=100.0,
            order_id="123",
        )
    )
    engine.executor = executor

    decision_data = {
        "position_size": 50.0,
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
        "nanofenix_policy": {
            "size_multiplier_hint": 0.4,
            "allow_execute": True,
        },
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    kwargs = executor.execute_market_order.await_args.kwargs
    assert kwargs["quantity"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_execute_trade_caps_marginal_short_size_from_nanofenix_policy():
    engine = _build_minimal_engine(timeframe="15m")
    engine._nanofenix_marginal_short_size_cap = 0.35
    engine._nanofenix_marginal_short_max_edge_bps = 0.5
    engine._nanofenix_marginal_short_max_pred_bps = 2.0

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            executed_qty=0.175,
            entry_price=100.0,
            order_id="456",
        )
    )
    engine.executor = executor

    decision_data = {
        "position_size": 50.0,
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 101.0, "take_profit": 98.0},
        "nanofenix_policy": {
            "allow_execute": True,
            "size_multiplier_hint": 1.0,
            "source": "consensus",
            "pred_bps": -1.6,
            "edge_net_bps": 0.3,
        },
    }

    await engine._execute_trade("SELL", "HIGH", decision_data)

    kwargs = executor.execute_market_order.await_args.kwargs
    assert kwargs["quantity"] == pytest.approx(0.175)


def test_fast_reversal_hold_exit_requires_nanofenix_confirmation(monkeypatch):
    from src.trading.engine import TradingEngine
    import src.trading.engine as engine_module

    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "HOLD",
            "confidence": 0.0,
            "pred_bps": 0.0,
            "direction_accuracy": 0.5,
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("SELL", 2.1, "micro sell"))
    engine.market_data.get_microstructure_metrics.return_value = MagicMock()
    monkeypatch.setattr(engine_module, "get_current_indicators", lambda: {"rsi": 45.0})

    new_signal, policy = TradingEngine._apply_fast_reversal_exit_policy(
        engine,
        position_side="LONG",
        entry_price=100.0,
        current_price=99.8,
        decision="HOLD",
        new_signal=None,
        qabba_signal="SELL",
        qabba_confidence=0.95,
    )

    assert new_signal is None
    assert policy is not None
    assert policy["blocked"] is True
    assert policy["reason"] == "hold_exit_not_confirmed_by_nanofenix"


def test_fast_reversal_hold_exit_allows_with_qabba_and_nanofenix(monkeypatch):
    from src.trading.engine import TradingEngine
    import src.trading.engine as engine_module

    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "SELL",
            "confidence": 0.9,
            "pred_bps": -3.1,
            "direction_accuracy": 0.77,
            "companion_ready": True,
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("SELL", 2.1, "micro sell"))
    engine.market_data.get_microstructure_metrics.return_value = MagicMock()
    monkeypatch.setattr(engine_module, "get_current_indicators", lambda: {"rsi": 45.0})

    new_signal, policy = TradingEngine._apply_fast_reversal_exit_policy(
        engine,
        position_side="LONG",
        entry_price=100.0,
        current_price=99.8,
        decision="HOLD",
        new_signal=None,
        qabba_signal="SELL",
        qabba_confidence=0.95,
    )

    assert new_signal == "SELL"
    assert policy is not None
    assert policy["blocked"] is False
    assert policy["forced_signal"] == "SELL"


def test_nanofenix_exit_policy_allows_strong_reversal_without_fresh_companion():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="15m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_require_for_opposite_exit = True
    engine._nanofenix_force_reversal_exit = True
    engine._nanofenix_strong_reversal_override = True
    engine._nanofenix_strong_reversal_override_score = 0.80
    engine._nanofenix_strong_reversal_override_confidence = "HIGH"
    engine._read_nanofenix_companion_signal = MagicMock(return_value=None)

    new_signal, policy = TradingEngine._apply_nanofenix_exit_policy(
        engine,
        position_side="LONG",
        decision="SELL",
        new_signal="SELL",
        confidence="HIGH",
        directional_score=-0.85,
    )

    assert new_signal == "SELL"
    assert policy is not None
    assert policy["blocked"] is False
    assert policy["reason"] == "strong_reversal_override_without_companion"


def test_fast_reversal_short_tf_uses_short_horizon_companion_quality(monkeypatch):
    from src.trading.engine import TradingEngine
    import src.trading.engine as engine_module

    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "SELL",
            "confidence": 0.60,
            "pred_bps": -2.1,
            "direction_accuracy": 0.42,
            "short_direction_accuracy": 0.64,
            "companion_ready": False,
            "short_companion_ready": True,
            "version": "v3",
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("SELL", 2.1, "micro sell"))
    engine.market_data.get_microstructure_metrics.return_value = MagicMock()
    monkeypatch.setattr(engine_module, "get_current_indicators", lambda: {"rsi": 45.0})

    new_signal, policy = TradingEngine._apply_fast_reversal_exit_policy(
        engine,
        position_side="LONG",
        entry_price=100.0,
        current_price=99.8,
        decision="HOLD",
        new_signal=None,
        qabba_signal="SELL",
        qabba_confidence=0.95,
    )

    assert new_signal == "SELL"
    assert policy is not None
    assert policy["blocked"] is False


def test_build_nanofenix_policy_payload_short_tf_uses_short_horizon_readiness():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_signal_path = "logs/test.json"
    engine._nanofenix_max_signal_age_sec = 25.0
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "_signal_age_sec": 0.0,
            "symbol": "BTCUSDT",
            "signal": "SELL",
            "confidence": 0.81,
            "pred_bps": -3.2,
            "direction_accuracy": 0.32,
            "short_direction_accuracy": 0.68,
            "companion_ready": False,
            "short_companion_ready": True,
        }
    )

    policy = TradingEngine._build_nanofenix_policy_payload(engine, "SELL")

    assert policy is not None
    assert policy["allow_execute"] is True
    assert policy["reason"] == "ok"
    assert policy["companion_ready"] is True
    assert policy["direction_accuracy"] == pytest.approx(0.68)
    assert policy["action"] == "SELL"


def test_build_nanofenix_policy_payload_maps_long_signal_to_buy():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="15m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_signal_path = "logs/test.json"
    engine._nanofenix_max_signal_age_sec = 25.0
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "_signal_age_sec": 0.0,
            "symbol": "BTCUSDT",
            "signal": "LONG",
            "confidence": 0.83,
            "pred_bps": 3.4,
            "direction_accuracy": 0.22,
            "long_direction_accuracy": 0.74,
            "companion_ready": False,
            "long_companion_ready": True,
        }
    )

    policy = TradingEngine._build_nanofenix_policy_payload(engine, "BUY")

    assert policy is not None
    assert policy["action"] == "BUY"
    assert policy["allow_execute"] is True
    assert policy["companion_ready"] is True
    assert policy["direction_accuracy"] == pytest.approx(0.74)


def test_build_nanofenix_policy_payload_blocks_direction_mismatch():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="15m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_signal_path = "logs/test.json"
    engine._nanofenix_max_signal_age_sec = 25.0
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "_signal_age_sec": 0.0,
            "symbol": "BTCUSDT",
            "signal": "SELL",
            "confidence": 0.82,
            "pred_bps": -3.2,
            "direction_accuracy": 0.72,
            "companion_ready": True,
        }
    )

    policy = TradingEngine._build_nanofenix_policy_payload(engine, "BUY")

    assert policy is not None
    assert policy["allow_execute"] is False
    assert "direction_mismatch" in policy["reasons"] or "direction_not_confirmed" in policy["reasons"]


def test_build_nanofenix_policy_payload_blocks_unexpected_run_id():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="15m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_signal_path = "logs/test.json"
    engine._nanofenix_max_signal_age_sec = 25.0
    engine._nanofenix_expected_run_id = "expected-run"
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "_signal_age_sec": 0.0,
            "symbol": "BTCUSDT",
            "signal": "LONG",
            "action": "BUY",
            "confidence": 0.82,
            "pred_bps": 3.2,
            "direction_accuracy": 0.72,
            "companion_ready": True,
            "run_id": "stale-run",
            "producer_pid": 99999,
        }
    )

    policy = TradingEngine._build_nanofenix_policy_payload(engine, "BUY")

    assert policy is not None
    assert policy["allow_execute"] is False
    assert "run_id_mismatch" in policy["reasons"]
    assert policy["run_id"] == "stale-run"
    assert policy["producer_pid"] == 99999


def test_normalize_technical_report_adds_numeric_confidence():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(timeframe="15m")
    normalized = engine._normalize_technical_report({"signal": "SELL", "confidence_level": "MEDIUM"})

    assert normalized["confidence"] == pytest.approx(0.60)
    assert normalized["confidence_level"] == "MEDIUM"


def test_update_nanofenix_timing_regime_tracks_strong_tech_qabba_alignment():
    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_timing_trigger_enabled = True

    engine._update_nanofenix_timing_regime(
        technical_report={"signal": "BUY", "confidence": 0.82},
        qabba_report={"signal": "BUY", "confidence": 0.91},
        decision_data={"final_decision": "BUY", "confidence_in_decision": "MEDIUM"},
    )

    regime = engine._get_active_nanofenix_timing_regime()
    assert regime is not None
    assert regime["bias"] == "LONG"
    assert regime["source"] == "tech_qabba_alignment"
    assert regime["qabba_signal"] == "BUY"


def test_compute_nanofenix_fast_trigger_requires_fresh_regime_and_alignment():
    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_timing_trigger_enabled = True
    engine._nanofenix_timing_regime = {
        "bias": "LONG",
        "source": "tech_qabba_alignment",
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "BUY",
            "confidence": 0.76,
            "pred_bps": 3.4,
            "direction_accuracy": 0.40,
            "short_direction_accuracy": 0.69,
            "companion_ready": False,
            "short_companion_ready": True,
            "short_utility_score": 1.0,
            "version": "v3",
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("BUY", 1.5, "ofi+micro aligned"))

    decision, score, reason = engine._compute_nanofenix_fast_trigger({}, MagicMock())

    assert decision == "BUY"
    assert score >= 1.5
    assert "FAST_NANO_TRIGGER" in reason


def test_compute_nanofenix_fast_trigger_short_tf_ignores_bad_long_acc_if_short_is_good():
    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_timing_trigger_enabled = True
    engine._nanofenix_timing_regime = {
        "bias": "LONG",
        "source": "tech_qabba_alignment",
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "BUY",
            "confidence": 0.78,
            "pred_bps": 3.6,
            "direction_accuracy": 0.39,
            "short_direction_accuracy": 0.67,
            "long_companion_direction_accuracy": 0.12,
            "companion_ready": False,
            "short_companion_ready": True,
            "short_utility_score": 1.0,
            "version": "v3",
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("BUY", 1.6, "ofi+micro aligned"))

    decision, score, reason = engine._compute_nanofenix_fast_trigger({}, MagicMock())

    assert decision == "BUY"
    assert score >= 1.6
    assert "FAST_NANO_TRIGGER" in reason


def test_compute_nanofenix_fast_trigger_blocks_countertrend_without_override():
    engine = _build_minimal_engine(timeframe="3m")
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_timing_trigger_enabled = True
    engine._nanofenix_timing_regime = {
        "bias": "SHORT",
        "source": "qabba_lead",
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }
    engine._read_nanofenix_companion_signal = MagicMock(
        return_value={
            "action": "BUY",
            "confidence": 0.90,
            "pred_bps": 4.2,
            "direction_accuracy": 0.45,
            "short_direction_accuracy": 0.72,
            "companion_ready": False,
            "short_companion_ready": True,
            "short_utility_score": 1.0,
            "version": "v3",
        }
    )
    engine._compute_fast_signal = MagicMock(return_value=("BUY", 1.7, "ofi+micro aligned"))

    decision, score, reason = engine._compute_nanofenix_fast_trigger({}, MagicMock())

    assert decision == "HOLD"
    assert score == 0.0
    assert "opposes slow regime" in reason


@pytest.mark.asyncio
async def test_process_decision_hard_vetoes_entry_when_nanofenix_disallows():
    engine = _build_minimal_engine(timeframe="15m")
    engine._execute_trade = AsyncMock()
    engine._manage_open_position = AsyncMock()
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_require_allow_execute = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": False,
            "reason": "direction_not_confirmed,high_uncertainty",
            "reasons": ["direction_not_confirmed", "high_uncertainty"],
            "action": "SELL",
        }
    )

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "risk_assessment": {},
        "technical_report": {"signal": "SELL", "confidence": 0.8},
        "qabba_report": {"signal": "SELL", "confidence": 0.8},
        "indicators": {},
    }

    await engine._process_decision(result)

    engine._execute_trade.assert_not_awaited()
    engine._manage_open_position.assert_awaited_once_with(new_signal="HOLD")
    assert result["final_trade_decision"]["effective_decision"] == "HOLD"
    assert result["final_trade_decision"]["hold_reason"].startswith("nanofenix_hard_veto")
    assert engine._filter_block_counts.get("NANOFENIX", 0) == 1


@pytest.mark.asyncio
async def test_process_decision_relaxed_nanofenix_veto_allows_soft_edge_reasons():
    engine = _build_minimal_engine(timeframe="15m")
    engine._execute_trade = AsyncMock()
    engine._manage_open_position = AsyncMock()
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_require_allow_execute = True
    engine._nanofenix_hard_veto_reasons = {"direction_mismatch", "high_uncertainty", "companion_not_ready"}
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": False,
            "reason": "low_actionable_edge,low_pred_bps",
            "reasons": ["low_actionable_edge", "low_pred_bps"],
            "action": "BUY",
        }
    )

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "risk_assessment": {},
        "technical_report": {"signal": "BUY", "confidence": 0.8},
        "qabba_report": {"signal": "BUY", "confidence": 0.8},
        "indicators": {},
    }

    await engine._process_decision(result)

    engine._execute_trade.assert_awaited_once()
    assert result["final_trade_decision"]["effective_decision"] == "BUY"
    assert engine._filter_block_counts.get("NANOFENIX", 0) == 0


@pytest.mark.asyncio
async def test_process_decision_relaxed_nanofenix_veto_still_blocks_direction_mismatch():
    engine = _build_minimal_engine(timeframe="15m")
    engine._execute_trade = AsyncMock()
    engine._manage_open_position = AsyncMock()
    engine._nanofenix_companion_enabled = True
    engine._nanofenix_require_allow_execute = True
    engine._nanofenix_hard_veto_reasons = {"direction_mismatch", "high_uncertainty", "companion_not_ready"}
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": False,
            "reason": "direction_mismatch,low_actionable_edge",
            "reasons": ["direction_mismatch", "low_actionable_edge"],
            "action": "SELL",
        }
    )

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "risk_assessment": {},
        "technical_report": {"signal": "BUY", "confidence": 0.8},
        "qabba_report": {"signal": "BUY", "confidence": 0.8},
        "indicators": {},
    }

    await engine._process_decision(result)

    engine._execute_trade.assert_not_awaited()
    engine._manage_open_position.assert_awaited_once_with(new_signal="HOLD")
    assert result["final_trade_decision"]["effective_decision"] == "HOLD"
    assert result["final_trade_decision"]["hold_reason"].startswith("nanofenix_hard_veto")


@pytest.mark.asyncio
async def test_execute_trade_paper_uses_valid_entry_price_when_market_price_is_zero():
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="5m")
    engine.paper_trading = True
    engine.market_data.current_price = 0.0
    engine.executor.get_balance.return_value = 10000.0
    engine.on_agent_event = AsyncMock()

    await TradingEngine._execute_trade(
        engine,
        "BUY",
        "MEDIUM",
        {
            "risk_assessment": {
                "entry_price": 86.42,
                "stop_loss": 85.9,
                "take_profit": 87.5,
            }
        },
    )

    simulated = [
        call.args[1]
        for call in engine.on_agent_event.await_args_list
        if call.args and call.args[0] == "trade:simulated"
    ]
    assert simulated
    assert simulated[-1]["price"] == pytest.approx(86.42)


@pytest.mark.asyncio
async def test_execute_trade_paper_uses_balance_fallback_when_executor_balance_is_zero(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_BALANCE_FALLBACK_USDT", "240")
    monkeypatch.setenv("FENIX_MAX_RISK_PER_TRADE", "0.003")
    monkeypatch.setenv("FENIX_LEVERAGE", "10")

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine.paper_trading = True
    engine.market_data.current_price = 86.42
    engine.executor.get_balance.return_value = 0.0
    engine.on_agent_event = AsyncMock()

    await TradingEngine._execute_trade(
        engine,
        "SELL",
        "HIGH",
        {
            "risk_assessment": {
                "entry_price": 86.42,
                "stop_loss": 87.1,
                "take_profit": 85.3,
            }
        },
    )

    engine.executor.get_balance.assert_not_called()
    simulated = [
        call.args[1]
        for call in engine.on_agent_event.await_args_list
        if call.args and call.args[0] == "trade:simulated"
    ]
    assert simulated
    assert simulated[-1]["notional_usd"] == pytest.approx(7.2)


@pytest.mark.asyncio
async def test_execute_trade_does_not_record_no_exposure_failure_as_loss():
    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine.market_data.current_price = 86.42

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock())
    risk_manager.get_adjusted_size.return_value = 25.0
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 50.0
    executor.min_notional = 5.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = MagicMock(step_size=0.1, min_notional=5.0)
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(
            success=False,
            status="NO_ORDER_ID",
            order_id=None,
            entry_price=0.0,
            executed_qty=0.0,
            message="Market order failed to return order ID",
        )
    )
    engine.executor = executor

    await engine._execute_trade(
        "BUY",
        "HIGH",
        {
            "risk_assessment": {
                "entry_price": 86.42,
                "stop_loss": 85.9,
                "take_profit": 87.5,
            }
        },
    )

    executor.execute_market_order.assert_awaited_once()
    risk_manager.record_trade.assert_not_called()


@pytest.mark.asyncio
async def test_process_decision_does_not_execute_blocked_flip(monkeypatch):
    from src.trading.trade_manager import OpenPosition

    monkeypatch.setenv("FENIX_MIN_FLIP_CONFIDENCE", "HIGH")

    engine = _build_minimal_engine(timeframe="1m")
    engine._execute_trade = AsyncMock()
    engine.trade_manager = MagicMock()
    engine.trade_manager.has_position.return_value = True
    engine.trade_manager.get_position.return_value = OpenPosition(
        symbol="BTCUSDT",
        side="SHORT",
        entry_price=101.0,
        quantity=1.0,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
    )
    engine.trade_manager.check_exit_conditions.return_value = None

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "risk_assessment": {},
        "indicators": {},
    }

    await engine._process_decision(result)

    engine._execute_trade.assert_not_awaited()
    assert result["final_trade_decision"]["effective_decision"] == "HOLD"


@pytest.mark.asyncio
async def test_process_decision_does_not_execute_when_companion_blocks_reverse(monkeypatch):
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(timeframe="3m")
    engine._execute_trade = AsyncMock()
    engine._nanofenix_companion_enabled = True
    engine._apply_nanofenix_exit_policy = MagicMock(
        return_value=(
            None,
            {
                "policy": "nanofenix_companion",
                "blocked": True,
                "reason": "opposite_exit_not_confirmed",
            },
        )
    )
    engine.trade_manager = MagicMock()
    engine.trade_manager.has_position.return_value = True
    engine.trade_manager.get_position.return_value = OpenPosition(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=99.0,
        quantity=1.0,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
    )
    engine.trade_manager.check_exit_conditions.return_value = None

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "risk_assessment": {},
        "indicators": {},
    }

    await engine._process_decision(result)

    engine._execute_trade.assert_not_awaited()
    assert result["final_trade_decision"]["effective_decision"] == "HOLD"


@pytest.mark.asyncio
async def test_process_decision_refreshes_exchange_protection_after_trailing_update():
    from src.trading.trade_manager import TradeManager

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.trade_manager = TradeManager(
        trailing_stop_pct=0.02,
        trailing_tp_enabled=True,
        trailing_tp_pct=0.004,
        trailing_tp_arm_pct=0.0015,
    )
    engine.trade_manager.open_position(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=100.0,
        quantity=1.0,
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
        stop_loss=98.0,
        take_profit=110.0,
        trade_id="trade-123",
        protection_position_id="pos-123",
        sl_order_id="sl-old",
        tp_order_id="tp-old",
    )
    engine.market_data.current_price = 103.0
    engine.executor = MagicMock()
    engine.executor.refresh_position_protection = AsyncMock(
        return_value=MagicMock(
            success=True,
            position_id="pos-123",
            sl_order_id="sl-new",
            tp_order_id="tp-new",
            message="ok",
        )
    )
    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "HOLD",
            "confidence_in_decision": "LOW",
            "combined_reasoning": "hold position",
        },
        "risk_assessment": {},
        "qabba_report": {},
        "indicators": {},
    }

    await engine._process_decision(result)

    position = engine.trade_manager.get_position("ETHUSDT")
    assert position is not None
    assert position.protection_refresh_pending is False
    assert position.sl_order_id == "sl-new"
    assert position.tp_order_id == "tp-new"
    engine.executor.refresh_position_protection.assert_awaited_once()
    assert any(call.args[0] == "position:protection_refreshed" for call in engine.on_agent_event.await_args_list)


@pytest.mark.asyncio
async def test_fast_loop_refreshes_trailing_protection_without_waiting_for_slow_candle(monkeypatch):
    from src.trading.trade_manager import TradeManager
    import src.trading.engine as engine_module

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine._fast_loop_live = True
    engine.on_agent_event = AsyncMock()
    engine.trade_manager = TradeManager(
        trailing_stop_pct=0.02,
        trailing_tp_enabled=True,
        trailing_tp_pct=0.004,
        trailing_tp_arm_pct=0.0015,
    )
    engine.trade_manager.open_position(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=100.0,
        quantity=1.0,
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
        stop_loss=98.0,
        take_profit=110.0,
        trade_id="trade-fast-1",
        protection_position_id="pos-fast-1",
        sl_order_id="sl-old",
        tp_order_id="tp-old",
    )
    engine.market_data.current_price = 103.0
    engine.executor = MagicMock()
    engine.executor.refresh_position_protection = AsyncMock(
        return_value=MagicMock(
            success=True,
            position_id="pos-fast-1",
            sl_order_id="sl-fast-new",
            tp_order_id="tp-fast-new",
            message="ok",
        )
    )
    monkeypatch.setattr(engine_module, "get_current_indicators", lambda: {})

    await engine._run_fast_decision_cycle()

    position = engine.trade_manager.get_position("ETHUSDT")
    assert position is not None
    assert position.protection_refresh_pending is False
    engine.executor.refresh_position_protection.assert_awaited_once()
    assert any(call.args[0] == "position:protection_refreshed" for call in engine.on_agent_event.await_args_list)


@pytest.mark.asyncio
async def test_reconcile_tracked_position_with_exchange_closes_stale_local_state():
    from src.trading.trade_manager import OpenPosition
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "2068.73"}
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        return_value=({"positionAmt": "0.000", "markPrice": "2068.73"}, 0.0, True)
    )
    engine.trade_manager.has_position.return_value = True
    engine.trade_manager.get_position.return_value = OpenPosition(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2074.52,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stale-1",
        reasoning_digest="digest-stale-1",
        decision_agent_name="decision_agent",
        protection_position_id="pos-stale-1",
    )
    engine.trade_manager.close_position.return_value = {
        "trade_id": "trade-stale-1",
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "reasoning_digest": "digest-stale-1",
        "decision_agent_name": "decision_agent",
        "pnl": 0.21,
        "pnl_pct": 0.1,
    }
    engine.executor.cancel_position_protection = AsyncMock(return_value=True)
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)
    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True

    await TradingEngine._reconcile_tracked_position_with_exchange(engine)

    engine.executor.cancel_position_protection.assert_awaited_once_with("pos-stale-1")
    engine.executor.cancel_all_orders.assert_not_awaited()
    engine.trade_manager.close_position.assert_called_once()
    engine.risk_manager.close_trade.assert_called_once()
    engine.reasoning_bank.update_entry_outcome.assert_called_once()
    assert any(call.args[0] == "position:closed" for call in engine.on_agent_event.await_args_list)


@pytest.mark.asyncio
async def test_reconcile_tracked_position_with_exchange_uses_exchange_fill_price_and_pnl():
    from src.trading.trade_manager import OpenPosition
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2074.52,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stale-fill-1",
        reasoning_digest="digest-stale-fill-1",
        decision_agent_name="decision_agent",
        protection_position_id="pos-stale-fill-1",
    )
    engine.trade_manager.has_position.return_value = True
    engine.trade_manager.get_position.return_value = tracked_position
    engine.executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "2068.73"}
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        return_value=({"positionAmt": "0.000", "markPrice": "2068.73"}, 0.0, True)
    )
    engine.executor.get_recent_trades = MagicMock(
        return_value=[
            {
                "side": "BUY",
                "price": "2069.10",
                "qty": "0.037",
                "realizedPnl": "0.20",
                "commission": "0.01",
                "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]
    )
    engine.trade_manager.close_position.return_value = {
        "trade_id": "trade-stale-fill-1",
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "reasoning_digest": "digest-stale-fill-1",
        "decision_agent_name": "decision_agent",
        "exit_price": 2068.73,
        "pnl": 0.21,
        "pnl_pct": 0.1,
    }
    engine.executor.cancel_position_protection = AsyncMock(return_value=True)
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)
    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True

    await TradingEngine._reconcile_tracked_position_with_exchange(engine)

    engine.risk_manager.close_trade.assert_called_once_with(
        "trade-stale-fill-1",
        exit_price=2069.10,
        pnl=0.20,
        pnl_pct=pytest.approx((2074.52 - 2069.10) / 2074.52 * 100.0),
        success=True,
        symbol="ETHUSDT",
    )
    payloads = [
        call.args[1]
        for call in engine.on_agent_event.await_args_list
        if call.args and call.args[0] == "position:closed"
    ]
    assert payloads
    assert payloads[-1]["exit_price"] == pytest.approx(2069.10)
    assert payloads[-1]["pnl"] == pytest.approx(0.20)
    assert payloads[-1]["exchange_fill_reconciled"] is True


@pytest.mark.asyncio
async def test_close_position_record_passes_realized_metrics_to_risk_manager(monkeypatch):
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="1m")
    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True
    engine.on_agent_event = AsyncMock()
    persist_position_close = AsyncMock()
    monkeypatch.setattr(engine_module, "persist_position_close", persist_position_close, raising=False)

    close_result = {
        "trade_id": "trade-win-1",
        "exit_price": 2371.34,
        "pnl": 0.01494,
        "pnl_pct": 0.06995,
    }

    await TradingEngine._close_position_record(engine, close_result)

    engine.risk_manager.close_trade.assert_called_once_with(
        "trade-win-1",
        exit_price=2371.34,
        pnl=0.01494,
        pnl_pct=0.06995,
        success=True,
        symbol="ETHUSDT",
    )
    persist_position_close.assert_awaited_once()
    assert persist_position_close.await_args.kwargs["symbol"] == "ETHUSDT"
    assert persist_position_close.await_args.kwargs["close_result"] is close_result


@pytest.mark.asyncio
async def test_reconcile_tracked_position_with_exchange_skips_unconfirmed_flat_snapshot():
    from src.trading.trade_manager import OpenPosition
    from src.trading.engine import TradingEngine

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "2068.73"}
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        return_value=({"positionAmt": "0.250", "entryPrice": "2071.0", "markPrice": "2072.1"}, 0.25, False)
    )
    engine.trade_manager.has_position.return_value = True
    engine.trade_manager.get_position.return_value = OpenPosition(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2074.52,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stale-2",
        reasoning_digest="digest-stale-2",
        decision_agent_name="decision_agent",
        protection_position_id="pos-stale-2",
    )
    engine.trade_manager.close_position.return_value = {"trade_id": "trade-stale-2"}
    engine.executor.cancel_position_protection = AsyncMock(return_value=True)
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)

    await TradingEngine._reconcile_tracked_position_with_exchange(engine)

    engine.executor.cancel_position_protection.assert_not_awaited()
    engine.trade_manager.close_position.assert_not_called()
    assert not any(call.args[0] == "position:closed" for call in engine.on_agent_event.await_args_list)


@pytest.mark.asyncio
async def test_manage_open_position_executes_live_reduce_only_exit_before_local_close():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition, ExitReason

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()

    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=84.10,
        quantity=0.06,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=30),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-live-exit-1",
        decision_agent_name="Decision Agent",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.check_exit_conditions.return_value = {
        "symbol": "ETHUSDT",
        "side": "SHORT",
        "entry_price": 84.10,
        "exit_price": 84.32,
        "quantity": 0.06,
        "entry_time": tracked_position.entry_time.isoformat(),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pnl": -0.0132,
        "pnl_pct": -0.26,
        "exit_reason": ExitReason.OPPOSITE_SIGNAL.value,
        "exit_notes": "Opposite signal: BUY",
        "trade_id": "trade-live-exit-1",
    }
    engine.trade_manager.close_position.return_value = {
        "symbol": "ETHUSDT",
        "side": "SHORT",
        "entry_price": 84.10,
        "exit_price": 85.29,
        "quantity": 0.06,
        "entry_time": tracked_position.entry_time.isoformat(),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pnl": -0.0714,
        "pnl_pct": ((84.10 - 85.29) / 84.10) * 100.0,
        "exit_reason": ExitReason.OPPOSITE_SIGNAL.value,
        "exit_notes": "Opposite signal: BUY",
        "trade_id": "trade-live-exit-1",
    }

    engine.executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            executed_qty=0.06,
            entry_price=85.29,
            order_id="close-live-1",
        )
    )
    engine.executor.get_recent_trades = MagicMock(
        return_value=[
            {
                "side": "BUY",
                "price": "85.29",
                "qty": "0.06",
                "realizedPnl": "-0.0714",
                "commission": "0.0025587",
                "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]
    )
    engine._close_position_record = AsyncMock()

    close_result = await TradingEngine._manage_open_position(engine, new_signal="BUY")

    engine.executor.execute_market_order.assert_awaited_once_with(
        side="BUY",
        quantity=0.06,
        reduce_only=True,
    )
    engine._close_position_record.assert_awaited_once()
    recorded = engine._close_position_record.await_args.args[0]
    assert recorded["exit_price"] == pytest.approx(85.29)
    assert recorded["pnl"] == pytest.approx(-0.0714)
    assert recorded["exchange_fill_reconciled"] is True
    assert close_result["exit_price"] == pytest.approx(85.29)


@pytest.mark.asyncio
async def test_manage_open_position_executes_live_reduce_only_for_rule_based_exit():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition, ExitReason

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    engine.on_agent_event = AsyncMock()

    tracked_position = OpenPosition(
        symbol="SOLUSDT",
        side="SHORT",
        entry_price=84.19,
        quantity=0.06,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=30),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-live-time-exit-1",
        decision_agent_name="Exchange Hydration",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.check_exit_conditions.return_value = {
        "symbol": "SOLUSDT",
        "side": "SHORT",
        "entry_price": 84.19,
        "exit_price": 85.20,
        "quantity": 0.06,
        "entry_time": tracked_position.entry_time.isoformat(),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pnl": -0.0606,
        "pnl_pct": -1.1997,
        "exit_reason": ExitReason.TIME_EXIT.value,
        "exit_notes": "time_exit 1 consecutive HOLDs",
        "trade_id": "trade-live-time-exit-1",
    }
    engine.executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            executed_qty=0.06,
            entry_price=85.20,
            order_id="close-time-exit-1",
        )
    )
    # Pre-check: position still open on exchange (not flat) -> live close
    # order must be sent. Post-close: exchange confirms flat.
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        side_effect=[
            ({"positionAmt": "-0.060", "markPrice": "85.20"}, -0.06, False),
            ({"positionAmt": "0.000", "markPrice": "85.20"}, 0.0, True),
        ]
    )
    engine.executor.get_recent_trades = MagicMock(
        return_value=[
            {
                "side": "BUY",
                "price": "85.20",
                "qty": "0.06",
                "realizedPnl": "-0.0606",
                "commission": "0.002556",
                "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]
    )
    engine._close_position_record = AsyncMock()

    close_result = await TradingEngine._manage_open_position(engine)

    engine.executor.execute_market_order.assert_awaited_once_with(
        side="BUY",
        quantity=0.06,
        reduce_only=True,
    )
    # Awaited twice: pre-close flat check + post-close confirmation.
    assert engine._confirm_exchange_flat_snapshot.await_count == 2
    engine._hydrate_tracked_position_from_exchange.assert_not_awaited()
    engine._close_position_record.assert_awaited_once()
    recorded = engine._close_position_record.await_args.args[0]
    assert recorded["exit_price"] == pytest.approx(85.20)
    assert recorded["pnl"] == pytest.approx(-0.0606)
    assert recorded["exchange_fill_reconciled"] is True
    assert close_result["exit_price"] == pytest.approx(85.20)


@pytest.mark.asyncio
async def test_manage_open_position_rehydrates_when_rule_based_live_exit_not_flat():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition, ExitReason

    engine = _build_minimal_engine(symbol="SOLUSDT", timeframe="15m")
    tracked_position = OpenPosition(
        symbol="SOLUSDT",
        side="SHORT",
        entry_price=84.19,
        quantity=0.06,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=30),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-live-time-exit-not-flat",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.check_exit_conditions.return_value = {
        "symbol": "SOLUSDT",
        "side": "SHORT",
        "entry_price": 84.19,
        "exit_price": 85.20,
        "quantity": 0.06,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pnl": -0.0606,
        "pnl_pct": -1.1997,
        "exit_reason": ExitReason.TIME_EXIT.value,
        "trade_id": "trade-live-time-exit-not-flat",
    }
    engine.executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(success=True, executed_qty=0.06, entry_price=85.20)
    )
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        return_value=({"positionAmt": "-0.060", "markPrice": "85.20"}, 0.06, False)
    )
    engine._close_position_record = AsyncMock()

    close_result = await TradingEngine._manage_open_position(engine)

    assert close_result is None
    engine.executor.execute_market_order.assert_awaited_once_with(
        side="BUY",
        quantity=0.06,
        reduce_only=True,
    )
    engine._hydrate_tracked_position_from_exchange.assert_awaited_once()
    engine._close_position_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_manage_open_position_attempts_exchange_reconciliation_when_live_exit_is_rejected():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=85.69,
        quantity=0.09,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=8),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-live-exit-rejected",
        decision_agent_name="Decision Agent",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.executor.execute_market_order = AsyncMock(
        return_value=SimpleNamespace(
            success=False,
            executed_qty=0.0,
            entry_price=0.0,
            order_id=None,
            message="APIError(code=-2022): ReduceOnly Order is rejected.",
        )
    )
    engine._reconcile_tracked_position_with_exchange = AsyncMock()
    engine._close_position_record = AsyncMock()

    close_result = await TradingEngine._manage_open_position(engine, new_signal="SELL")

    assert close_result is None
    engine.executor.execute_market_order.assert_awaited_once_with(
        side="SELL",
        quantity=0.09,
        reduce_only=True,
    )
    engine._reconcile_tracked_position_with_exchange.assert_awaited_once()
    engine.trade_manager.close_position.assert_not_called()
    engine._close_position_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_flat_symbol_orders_falls_back_to_symbol_scope_when_targeted_cleanup_fails():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(symbol="BTCUSDC", timeframe="15m")
    tracked_position = OpenPosition(
        symbol="BTCUSDC",
        side="SHORT",
        entry_price=67084.9,
        quantity=0.006,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        protection_position_id="pos-flat-1",
    )
    engine.executor.cancel_position_protection = AsyncMock(return_value=False)
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)

    cleaned = await TradingEngine._cleanup_flat_symbol_orders(
        engine,
        tracked_position=tracked_position,
        source="test cleanup",
    )

    assert cleaned is True
    engine.executor.cancel_position_protection.assert_awaited_once_with("pos-flat-1")
    engine.executor.cancel_all_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_trade_allows_add_when_flag_enabled(monkeypatch):
    import src.trading.engine as engine_module

    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(timeframe="1m")
    engine.on_agent_event = AsyncMock()
    persist_order_fill = AsyncMock()
    persist_open_position = AsyncMock()
    monkeypatch.setattr(engine_module, "persist_order_fill", persist_order_fill, raising=False)
    monkeypatch.setattr(engine_module, "persist_open_position", persist_open_position, raising=False)

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.50", "entryPrice": "99.90"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(
            success=True,
            executed_qty=0.2,
            entry_price=100.0,
            order_id=123,
            message="ok",
        )
    )
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0}
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    assert executor.execute_market_order.await_count == 1
    assert any(call.args[0] == "trade_executed" for call in engine.on_agent_event.await_args_list)
    assert any(call.args[0] == "position:opened" for call in engine.on_agent_event.await_args_list)
    persist_order_fill.assert_awaited_once()
    persist_open_position.assert_awaited_once()
    assert persist_order_fill.await_args.kwargs["symbol"] == "BTCUSDT"
    assert persist_order_fill.await_args.kwargs["side"] == "BUY"
    assert persist_order_fill.await_args.kwargs["order_id"] == "123"


@pytest.mark.asyncio
async def test_execute_trade_applies_exchange_min_qty_floor_when_margin_and_risk_allow(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR", "1")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD", "28")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD", "1.0")

    engine = _build_minimal_engine(symbol="BTCUSDC", timeframe="15m")
    engine._engine_leverage = 3.0

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock())
    risk_manager.get_adjusted_size.return_value = 24.0
    risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 90.0,
        "positions": {},
    }
    risk_manager.update_open_position.return_value = None
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 111.0
    executor.min_notional = 10.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = MagicMock(step_size=0.001, min_notional=10.0)
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(
            success=True,
            executed_qty=0.001,
            entry_price=66000.0,
            order_id=1234,
            message="ok",
            protection_position_id=None,
            sl_order_id=None,
            tp_order_id=None,
        )
    )
    engine.executor = executor

    decision_data = {
        "position_size": 24.0,
        "risk_assessment": {
            "entry_price": 66000.0,
            "stop_loss": 65100.0,
            "take_profit": 67800.0,
        },
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_awaited_once()
    _, kwargs = executor.execute_market_order.await_args
    assert kwargs["quantity"] == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_execute_trade_blocks_exchange_min_qty_floor_when_exposure_is_insufficient(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR", "1")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD", "28")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD", "1.0")

    engine = _build_minimal_engine(symbol="BTCUSDC", timeframe="15m")
    engine._engine_leverage = 3.0
    engine.on_agent_event = AsyncMock()

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock())
    risk_manager.get_adjusted_size.return_value = 24.0
    risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 40.0,
        "positions": {},
    }
    risk_manager.update_open_position.return_value = None
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 111.0
    executor.min_notional = 10.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = MagicMock(step_size=0.001, min_notional=10.0)
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "position_size": 24.0,
        "risk_assessment": {
            "entry_price": 66000.0,
            "stop_loss": 65100.0,
            "take_profit": 67800.0,
        },
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "risk:blocked" and call.args[1]["reason"] == "exchange_min_qty_floor_blocked"
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_applies_exchange_floor_to_meet_min_notional(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR", "1")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD", "8")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD", "2")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="1m")
    engine._engine_leverage = 3.0

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock())
    risk_manager.get_adjusted_size.return_value = 2.5
    risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 80.0,
        "positions": {},
    }
    risk_manager.update_open_position.return_value = None
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 76.0
    executor.min_notional = 5.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = MagicMock(step_size=0.001, min_notional=20.0)
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(
            success=True,
            executed_qty=0.009,
            entry_price=2227.61,
            order_id=4321,
            message="ok",
            protection_position_id=None,
            sl_order_id=None,
            tp_order_id=None,
        )
    )
    engine.executor = executor

    decision_data = {
        "position_size": 2.5,
        "risk_assessment": {
            "entry_price": 2227.61,
            "stop_loss": 2228.8,
            "take_profit": 2221.0,
        },
    }

    await engine._execute_trade("SELL", "HIGH", decision_data)

    executor.execute_market_order.assert_awaited_once()
    _, kwargs = executor.execute_market_order.await_args
    assert kwargs["quantity"] == pytest.approx(0.009)


@pytest.mark.asyncio
async def test_execute_trade_blocks_exchange_floor_when_fee_budget_is_exceeded(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR", "1")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD", "16")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD", "4")
    monkeypatch.setenv("FENIX_MIN_QTY_FLOOR_MAX_FEES_USD", "0.01")
    monkeypatch.setenv("FENIX_ESTIMATED_ROUND_TRIP_FEE_PCT", "0.0008")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="1m")
    engine._engine_leverage = 10.0
    engine.on_agent_event = AsyncMock()

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock())
    risk_manager.get_adjusted_size.return_value = 6.0
    risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 600.0,
        "positions": {},
    }
    risk_manager.update_open_position.return_value = None
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 60.0
    executor.min_notional = 5.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = MagicMock(step_size=0.001, min_notional=20.0)
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "position_size": 6.0,
        "risk_assessment": {
            "entry_price": 2360.0,
            "stop_loss": 2356.0,
            "take_profit": 2372.0,
        },
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "risk:blocked" and call.args[1]["reason"] == "exchange_min_qty_floor_blocked"
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_reserves_headroom_for_future_adds(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(timeframe="3m")
    engine._add_position_reserve_pct = 0.25

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.0", "entryPrice": "0.0"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(
            success=True,
            executed_qty=0.375,
            entry_price=100.0,
            order_id=321,
            message="ok",
        )
    )
    engine.executor = executor
    engine.risk_manager = MagicMock()
    engine.risk_manager.update_balance.return_value = None
    engine.risk_manager.check_trade_allowed.return_value = (True, MagicMock(risk_bias=1.0))
    engine.risk_manager.get_adjusted_size.return_value = 50.0
    engine.risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 50.0,
    }
    engine.risk_manager.update_open_position.return_value = None

    monkeypatch.setenv("FENIX_MAX_RISK_PER_TRADE", "0.1")

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0}
    }

    await engine._execute_trade("BUY", "MEDIUM", decision_data)

    assert executor.execute_market_order.await_count == 1
    _, kwargs = executor.execute_market_order.await_args
    assert kwargs["quantity"] == pytest.approx(0.375)


@pytest.mark.asyncio
async def test_execute_trade_blocks_third_eth3m_long_entry(monkeypatch):
    from src.trading.trade_manager import TradeManager

    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.trade_manager = TradeManager()
    engine.trade_manager.open_position("ETHUSDT", "LONG", 100.0, 0.10, "t1")
    engine.trade_manager.open_position("ETHUSDT", "LONG", 101.0, 0.10, "t2")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.20", "entryPrice": "100.50"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 102.0, "stop_loss": 100.0, "take_profit": 105.0},
        "_execution_qabba_signal": "BUY",
        "_execution_qabba_confidence": 0.95,
        "_execution_market_condition": "NORMAL",
        "_execution_chop_regime": "TREND",
        "_execution_trend_conflict": False,
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "position:skip_same_side" and "entry cap reached" in call.args[1]["reason"]
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_blocks_eth3m_long_add_without_qabba_buy(monkeypatch):
    from src.trading.trade_manager import TradeManager

    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.trade_manager = TradeManager()
    engine.trade_manager.open_position("ETHUSDT", "LONG", 100.0, 0.10, "t1")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.10", "entryPrice": "100.00"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 101.0, "stop_loss": 99.0, "take_profit": 104.0},
        "_execution_qabba_signal": "HOLD",
        "_execution_qabba_confidence": 0.65,
        "_execution_market_condition": "NORMAL",
        "_execution_chop_regime": "TREND",
        "_execution_trend_conflict": False,
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "position:skip_same_side" and "requires fresh QABBA BUY" in call.args[1]["reason"]
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_blocks_eth3m_long_add_in_low_vol_transition(monkeypatch):
    from src.trading.trade_manager import TradeManager

    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine.trade_manager = TradeManager()
    engine.trade_manager.open_position("ETHUSDT", "LONG", 100.0, 0.10, "t1")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "0.10", "entryPrice": "100.00"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 101.0, "stop_loss": 99.0, "take_profit": 104.0},
        "_execution_qabba_signal": "BUY",
        "_execution_qabba_confidence": 0.95,
        "_execution_market_condition": "LOW_VOLATILITY",
        "_execution_chop_regime": "TRANSITION",
        "_execution_trend_conflict": True,
    }

    await engine._execute_trade("BUY", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "position:skip_same_side" and "low-vol" in call.args[1]["reason"]
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_blocks_same_side_short_add_when_exchange_exposure_cap_is_full(monkeypatch):
    monkeypatch.setenv("FENIX_ALLOW_ADD_TO_POSITION", "1")

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine.on_agent_event = AsyncMock()
    engine._engine_leverage = 6.0

    risk_manager = MagicMock()
    risk_manager.update_balance.return_value = None
    risk_manager.check_trade_allowed.return_value = (True, MagicMock(risk_bias=1.0))
    risk_manager.get_adjusted_size.return_value = 205.0
    risk_manager.get_total_exposure.return_value = {
        "total_exposure": 0.0,
        "max_exposure": 420.0,
        "positions": {},
    }
    risk_manager.update_open_position.return_value = None
    engine.risk_manager = risk_manager

    executor = MagicMock()
    executor.get_balance.return_value = 141.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.return_value = {"positionAmt": "-0.216", "entryPrice": "1969.00"}
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "position_size": 205.0,
        "risk_assessment": {"entry_price": 1973.15, "stop_loss": 1981.26, "take_profit": 1956.72},
        "_execution_qabba_signal": "SELL",
        "_execution_qabba_confidence": 0.95,
        "_execution_market_condition": "NORMAL",
        "_execution_chop_regime": "TREND",
        "_execution_trend_conflict": False,
    }

    await engine._execute_trade("SELL", "HIGH", decision_data)

    executor.execute_market_order.assert_not_awaited()
    assert any(
        call.args[0] == "risk:blocked" and "Live exposure cap reached" in call.args[1]["reason"]
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_execute_trade_skips_when_position_check_fails(monkeypatch):
    """
    Safety: if we can't confirm position state, do not open a new trade.
    """
    monkeypatch.delenv("FENIX_ALLOW_ADD_TO_POSITION", raising=False)

    engine = _build_minimal_engine(timeframe="1m")

    executor = MagicMock()
    executor.get_balance.return_value = 100.0
    executor.min_notional = 1.0
    executor.service = MagicMock()
    executor.service.get_symbol_config.return_value = None
    executor.get_position.side_effect = Exception("boom")
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.execute_market_order = AsyncMock()
    engine.executor = executor

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0}
    }

    await engine._execute_trade("BUY", "MEDIUM", decision_data)

    executor.execute_market_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_cleanup_closes_exchange_and_syncs_local_state():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine._engine_cleanup_on_stop = True

    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2075.17,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stop-1",
        reasoning_digest="digest-stop-1",
        decision_agent_name="decision_agent",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.close_position.return_value = {
        "trade_id": "trade-stop-1",
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "reasoning_digest": "digest-stop-1",
        "decision_agent_name": "decision_agent",
        "pnl": 1.25,
        "pnl_pct": 0.6,
    }

    executor = MagicMock()
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.get_position.side_effect = [
        {"positionAmt": "-0.037", "markPrice": "2078.10"},
        {"positionAmt": "0.000", "markPrice": "2078.05"},
    ]
    executor.execute_market_order = AsyncMock(
        return_value=MagicMock(success=True, entry_price=2078.02, status="FILLED", message="ok")
    )
    executor.service = MagicMock()
    executor.format_quantity.return_value = "0.037"
    engine.executor = executor

    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True

    await TradingEngine.stop(engine)

    executor.cancel_all_orders.assert_awaited_once()
    executor.execute_market_order.assert_awaited_once()
    engine.trade_manager.close_position.assert_called_once()
    engine.risk_manager.close_trade.assert_called_once()
    engine.reasoning_bank.update_entry_outcome.assert_called_once()
    engine.market_data.stop.assert_awaited_once()
    assert engine._stopped is True


@pytest.mark.asyncio
async def test_stop_cleanup_reconciles_stale_local_state_when_exchange_is_already_flat():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine._engine_cleanup_on_stop = True

    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=2085.12,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stop-2",
        reasoning_digest="digest-stop-2",
        decision_agent_name="decision_agent",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.close_position.return_value = {
        "trade_id": "trade-stop-2",
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "reasoning_digest": "digest-stop-2",
        "decision_agent_name": "decision_agent",
        "pnl": -0.11,
        "pnl_pct": -0.05,
    }

    executor = MagicMock()
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "2081.94"}
    executor.execute_market_order = AsyncMock()
    executor.service = MagicMock()
    engine.executor = executor

    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True

    await TradingEngine.stop(engine)

    executor.execute_market_order.assert_not_awaited()
    engine.trade_manager.close_position.assert_called_once()
    engine.risk_manager.close_trade.assert_called_once()
    assert engine._stopped is True


@pytest.mark.asyncio
async def test_stop_cleanup_uses_exchange_fill_for_flat_reconciliation():
    from src.trading.engine import TradingEngine
    from src.trading.trade_manager import OpenPosition

    engine = _build_minimal_engine(symbol="ETHUSDT", timeframe="3m")
    engine._engine_cleanup_on_stop = True
    engine.on_agent_event = AsyncMock()

    tracked_position = OpenPosition(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=2085.12,
        quantity=0.037,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=15),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-stop-fill-1",
        reasoning_digest="digest-stop-fill-1",
        decision_agent_name="decision_agent",
    )
    engine.trade_manager.get_position.return_value = tracked_position
    engine.trade_manager.close_position.return_value = {
        "trade_id": "trade-stop-fill-1",
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "reasoning_digest": "digest-stop-fill-1",
        "decision_agent_name": "decision_agent",
        "exit_price": 2081.94,
        "pnl": -0.11,
        "pnl_pct": -0.05,
    }

    executor = MagicMock()
    executor.cancel_all_orders = AsyncMock(return_value=True)
    executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "2081.94"}
    executor.execute_market_order = AsyncMock()
    executor.service = MagicMock()
    executor.get_recent_trades = MagicMock(
        return_value=[
            {
                "side": "SELL",
                "price": "2082.55",
                "qty": "0.037",
                "realizedPnl": "-0.095",
                "commission": "0.01",
                "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]
    )
    engine.executor = executor

    engine.risk_manager = MagicMock()
    engine.risk_manager.close_trade.return_value = True

    await TradingEngine.stop(engine)

    engine.risk_manager.close_trade.assert_called_once_with(
        "trade-stop-fill-1",
        exit_price=2082.55,
        pnl=-0.095,
        pnl_pct=pytest.approx((2082.55 - 2085.12) / 2085.12 * 100.0),
        success=False,
        symbol="ETHUSDT",
    )
    payloads = [
        call.args[1]
        for call in engine.on_agent_event.await_args_list
        if call.args and call.args[0] == "position:closed"
    ]
    assert payloads
    assert payloads[-1]["exit_price"] == pytest.approx(2082.55)
    assert payloads[-1]["exchange_fill_reconciled"] is True
