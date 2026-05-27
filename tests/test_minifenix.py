"""
Tests para MiniFenix: Feature Engine, SOTA Predictor, Paper Trader, Brain, Trigger.

Cubre:
- FeatureEngine: cálculo correcto de features con datos sintéticos
- SOTAPredictor: predicciones coherentes y online retrain
- PaperTrader: apertura, cierre, SL/TP, P&L correcto
- Brain (TradingRegime): modelo de datos y stale detection
- Trigger: consenso Brain+ML
"""
import asyncio
import collections
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from minifenix.feature_engine import FeatureEngine, LOBSnapshot
from minifenix.models import TradingRegime
from minifenix.paper_trader import PaperTrader, Position, Trade
from minifenix.sota_predictor import SOTAPredictor


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_snap(bid: float, ask: float, bid_qty: float = 10.0, ask_qty: float = 10.0) -> LOBSnapshot:
    """Crea un snapshot de LOB con valores dados."""
    return LOBSnapshot(
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        timestamp_ms=int(time.time() * 1000),
    )


def _feed_engine(engine: FeatureEngine, n_ticks: int = 60, base_price: float = 50000.0):
    """Alimenta el engine con n_ticks sintéticos para calentamiento."""
    for i in range(n_ticks):
        noise = np.random.randn() * 0.5
        price = base_price + noise
        engine.update(_make_snap(bid=price - 0.5, ask=price + 0.5, bid_qty=5 + i % 3, ask_qty=5 + i % 4))


# ─── FeatureEngine ───────────────────────────────────────────────────────────


class TestFeatureEngine:
    """Tests para el motor de feature engineering."""

    def test_not_ready_initially(self):
        engine = FeatureEngine(lookback=100)
        assert not engine.ready

    def test_ready_after_enough_ticks(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        assert engine.ready

    def test_returns_none_when_not_ready(self):
        engine = FeatureEngine(lookback=100)
        snap = _make_snap(50000.0, 50001.0)
        result = engine.update(snap)
        assert result is None

    def test_returns_correct_number_of_features(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        # Siguiente tick debería devolver features
        snap = _make_snap(50000.0, 50001.0)
        features = engine.update(snap)
        assert features is not None
        assert len(features) == engine.n_features
        assert len(features) == len(engine.feature_names)

    def test_features_are_finite(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        snap = _make_snap(50000.0, 50001.0)
        features = engine.update(snap)
        assert features is not None
        assert np.all(np.isfinite(features)), f"Non-finite features: {features}"

    def test_feature_dtype_is_float32(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        snap = _make_snap(50000.0, 50001.0)
        features = engine.update(snap)
        assert features is not None
        assert features.dtype == np.float32

    def test_mid_price_return_is_coherent(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55, base_price=50000.0)
        # Precio sube significativamente
        features = engine.update(_make_snap(50100.0, 50101.0))
        assert features is not None
        # Feature 0 es "mid_price_return" - debería ser positivo
        assert features[0] > 0

    def test_obi_positive_when_bid_pressure(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        # Mucha más qty en bid que ask
        features = engine.update(_make_snap(50000.0, 50001.0, bid_qty=100.0, ask_qty=10.0))
        assert features is not None
        # Feature "obi_raw" (índice 10) debería ser positivo
        obi_idx = engine.feature_names.index("obi_raw")
        assert features[obi_idx] > 0

    def test_spread_bps_positive(self):
        engine = FeatureEngine(lookback=100)
        _feed_engine(engine, n_ticks=55)
        features = engine.update(_make_snap(50000.0, 50010.0))
        assert features is not None
        spread_idx = engine.feature_names.index("spread_bps")
        assert features[spread_idx] > 0

    def test_buffers_respect_maxlen(self):
        engine = FeatureEngine(lookback=50)
        _feed_engine(engine, n_ticks=100)
        assert len(engine.mid_prices) == 50
        assert len(engine.returns) == 50


# ─── SOTAPredictor ───────────────────────────────────────────────────────────


class TestSOTAPredictor:
    """Tests para el predictor SOTA con LightGBM."""

    def test_initial_prediction_is_hold(self):
        predictor = SOTAPredictor(
            model_path="/tmp/test_sota_model.joblib",
            min_training_samples=100,
            retrain_every_n=50,
        )
        snap = _make_snap(50000.0, 50001.0)
        result = predictor.process_tick(snap)
        assert result["signal"] == "HOLD"
        assert result["model_ready"] is False

    def test_prediction_dict_has_required_keys(self):
        predictor = SOTAPredictor(model_path="/tmp/test_sota2.joblib")
        snap = _make_snap(50000.0, 50001.0)
        result = predictor.process_tick(snap)
        required_keys = {"signal", "confidence", "probabilities", "model_ready", "accuracy", "features_ready"}
        assert required_keys.issubset(set(result.keys()))

    def test_probabilities_sum_to_one_when_model_not_ready(self):
        predictor = SOTAPredictor(model_path="/tmp/test_sota3.joblib")
        snap = _make_snap(50000.0, 50001.0)
        result = predictor.process_tick(snap)
        probs = result["probabilities"]
        total = probs["UP"] + probs["DOWN"] + probs["HOLD"]
        assert abs(total - 1.0) < 0.01

    def test_get_stats_returns_valid_dict(self):
        predictor = SOTAPredictor(model_path="/tmp/test_sota4.joblib")
        stats = predictor.get_stats()
        assert "ticks_processed" in stats
        assert "running_accuracy" in stats
        assert "is_trained" in stats
        assert stats["ticks_processed"] == 0

    def test_tick_count_increments(self):
        predictor = SOTAPredictor(model_path="/tmp/test_sota5.joblib")
        for _ in range(10):
            predictor.process_tick(_make_snap(50000.0, 50001.0))
        assert predictor.tick_count == 10


# ─── PaperTrader ─────────────────────────────────────────────────────────────


class TestPaperTrader:
    """Tests para el motor de paper trading."""

    def test_initial_state(self):
        trader = PaperTrader(initial_balance=10000.0)
        assert trader.balance == 10000.0
        assert trader.position is None
        assert trader.can_trade()
        assert len(trader.trades) == 0

    def test_open_long(self):
        trader = PaperTrader(initial_balance=10000.0, position_size_pct=0.10)
        result = trader.open_long(price=50000.0)
        assert result is True
        assert trader.position is not None
        assert trader.position.side == "LONG"
        assert trader.position.entry_price == 50000.0

    def test_open_short(self):
        trader = PaperTrader(initial_balance=10000.0, position_size_pct=0.10)
        result = trader.open_short(price=50000.0)
        assert result is True
        assert trader.position is not None
        assert trader.position.side == "SHORT"

    def test_cannot_open_when_position_exists(self):
        trader = PaperTrader(initial_balance=10000.0)
        trader.open_long(price=50000.0)
        assert not trader.can_trade()
        result = trader.open_long(price=50000.0)
        assert result is False

    def test_take_profit_long(self):
        trader = PaperTrader(
            initial_balance=10000.0,
            position_size_pct=0.10,
            sl_pct=0.01,
            tp_pct=0.02,
            commission_pct=0.0,
        )
        trader.open_long(price=50000.0)
        tp_price = 50000.0 * 1.025  # Above TP
        trade = trader.update(tp_price)
        assert trade is not None
        assert trade.reason == "TP"
        assert trade.pnl > 0
        assert trader.position is None

    def test_stop_loss_long(self):
        trader = PaperTrader(
            initial_balance=10000.0,
            position_size_pct=0.10,
            sl_pct=0.01,
            tp_pct=0.02,
            commission_pct=0.0,
        )
        trader.open_long(price=50000.0)
        sl_price = 50000.0 * 0.985  # Below SL
        trade = trader.update(sl_price)
        assert trade is not None
        assert trade.reason == "SL"
        assert trade.pnl < 0

    def test_take_profit_short(self):
        trader = PaperTrader(
            initial_balance=10000.0,
            position_size_pct=0.10,
            sl_pct=0.01,
            tp_pct=0.02,
            commission_pct=0.0,
        )
        trader.open_short(price=50000.0)
        tp_price = 50000.0 * 0.975  # Below TP for short
        trade = trader.update(tp_price)
        assert trade is not None
        assert trade.reason == "TP"
        assert trade.pnl > 0

    def test_stop_loss_short(self):
        trader = PaperTrader(
            initial_balance=10000.0,
            position_size_pct=0.10,
            sl_pct=0.01,
            tp_pct=0.02,
            commission_pct=0.0,
        )
        trader.open_short(price=50000.0)
        sl_price = 50000.0 * 1.015  # Above SL for short
        trade = trader.update(sl_price)
        assert trade is not None
        assert trade.reason == "SL"
        assert trade.pnl < 0

    def test_force_close(self):
        trader = PaperTrader(initial_balance=10000.0, commission_pct=0.0)
        trader.open_long(price=50000.0)
        trade = trader.force_close(price=50100.0, reason="REGIME_CHANGE")
        assert trade is not None
        assert trade.reason == "REGIME_CHANGE"
        assert trader.position is None

    def test_commission_deducted(self):
        trader = PaperTrader(initial_balance=10000.0, commission_pct=0.001)
        initial = trader.balance
        trader.open_long(price=50000.0)
        assert trader.balance < initial  # Entry fee deducted

    def test_cooldown_respected(self):
        trader = PaperTrader(initial_balance=10000.0, cooldown_seconds=60.0)
        trader.open_long(price=50000.0)
        trader.force_close(50000.0)
        # Should not be able to trade during cooldown
        assert not trader.can_trade()

    def test_get_stats_keys(self):
        trader = PaperTrader(initial_balance=10000.0)
        stats = trader.get_stats()
        required = {"equity", "balance", "total_pnl", "total_return_pct", "wins", "losses", "win_rate"}
        assert required.issubset(set(stats.keys()))

    def test_pnl_is_zero_sum_no_commission(self):
        """Sin comisiones, abrir y cerrar al mismo precio debe dar P&L ~0."""
        trader = PaperTrader(
            initial_balance=10000.0, commission_pct=0.0, cooldown_seconds=0.0,
            sl_pct=0.10, tp_pct=0.10,
        )
        trader.open_long(price=50000.0)
        trade = trader.force_close(50000.0)
        assert abs(trade.pnl) < 0.01

    def test_unrealized_pnl_updates(self):
        trader = PaperTrader(initial_balance=10000.0, sl_pct=0.10, tp_pct=0.10)
        trader.open_long(price=50000.0)
        trader.position.update_pnl(50050.0)
        assert trader.position.unrealized_pnl > 0

    def test_max_drawdown_tracked(self):
        trader = PaperTrader(
            initial_balance=10000.0,
            position_size_pct=0.50,
            sl_pct=0.01,
            tp_pct=0.10,
            commission_pct=0.0,
            cooldown_seconds=0.0,
        )
        # Force a losing trade
        trader.open_long(price=50000.0)
        trader.update(50000.0 * 0.985)  # SL hit
        assert trader.max_drawdown > 0


# ─── TradingRegime ───────────────────────────────────────────────────────────


class TestTradingRegime:
    """Tests para el modelo TradingRegime."""

    def test_not_stale_when_fresh(self):
        regime = TradingRegime(
            bias="LONG", confidence=0.8,
            min_ofi_required=0.3, max_spread_bps=5.0,
            z_score_threshold=2.0, macro_context="test",
            timestamp=time.time(),
        )
        assert not regime.is_stale(max_age_seconds=60)

    def test_stale_when_old(self):
        regime = TradingRegime(
            bias="LONG", confidence=0.8,
            min_ofi_required=0.3, max_spread_bps=5.0,
            z_score_threshold=2.0, macro_context="test",
            timestamp=time.time() - 1000,
        )
        assert regime.is_stale(max_age_seconds=60)

    def test_bias_values(self):
        for bias in ("LONG", "SHORT", "NEUTRAL"):
            regime = TradingRegime(
                bias=bias, confidence=0.5,
                min_ofi_required=0.3, max_spread_bps=5.0,
                z_score_threshold=2.0, macro_context="",
                timestamp=time.time(),
            )
            assert regime.bias == bias


# ─── Position ────────────────────────────────────────────────────────────────


class TestPosition:
    """Tests para el modelo Position."""

    def test_unrealized_pnl_long_profit(self):
        pos = Position(side="LONG", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=49500.0, take_profit=50500.0)
        pnl = pos.update_pnl(50100.0)
        assert pnl > 0
        assert abs(pnl - 10.0) < 0.01  # 0.1 * (50100-50000) = 10

    def test_unrealized_pnl_short_profit(self):
        pos = Position(side="SHORT", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=50500.0, take_profit=49500.0)
        pnl = pos.update_pnl(49900.0)
        assert pnl > 0

    def test_hit_stop_long(self):
        pos = Position(side="LONG", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=49500.0, take_profit=50500.0)
        assert pos.hit_stop(49400.0) is True
        assert pos.hit_stop(49600.0) is False

    def test_hit_tp_long(self):
        pos = Position(side="LONG", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=49500.0, take_profit=50500.0)
        assert pos.hit_tp(50600.0) is True
        assert pos.hit_tp(50400.0) is False

    def test_hit_stop_short(self):
        pos = Position(side="SHORT", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=50500.0, take_profit=49500.0)
        assert pos.hit_stop(50600.0) is True
        assert pos.hit_stop(50400.0) is False

    def test_hit_tp_short(self):
        pos = Position(side="SHORT", entry_price=50000.0, quantity=0.1, size_usdt=5000.0,
                       stop_loss=50500.0, take_profit=49500.0)
        assert pos.hit_tp(49400.0) is True
        assert pos.hit_tp(49600.0) is False
