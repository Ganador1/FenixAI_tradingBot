from __future__ import annotations

import json
import os
import types

import numpy as np

from nanofenixv3 import core as core_module
from nanofenixv3.feature_engine import Bar, FEATURE_LOOKBACK, MultiScaleFeatureEngine


def test_core_uses_default_pretrained_path(monkeypatch):
    captured: dict[str, object] = {}

    class _FakePredictor:
        def __init__(self, model_path=None):
            captured["model_path"] = model_path
            self.either_trained = False
            self.val_accuracy = 0.0

    monkeypatch.setattr(core_module, "DualHorizonPredictor", _FakePredictor)
    monkeypatch.setattr(core_module, "BarAggregator", lambda interval=1.0: object())
    monkeypatch.setattr(core_module, "MultiScaleFeatureEngine", lambda: object())
    monkeypatch.setattr(core_module, "PaperExecutor", lambda balance=0.0: object())

    bot = core_module.NanoFenixV3(symbol="ETHUSDT", balance=1000.0)

    assert captured["model_path"] == "nanofenixv3/pretrained_ethusdt.pkl"
    assert bot._model_save_path == "nanofenixv3/pretrained_ethusdt.pkl"
    assert str(bot._runtime_state_path) == "nanofenixv3/runtime_ethusdt_1s.pkl"


def test_core_ws_url_uses_raw_trade_feed_for_futures():
    assert (
        core_module.NanoFenixV3._build_ws_url("SOLUSDT", "wss://fstream.binance.com")
        == "wss://fstream.binance.com/public/stream?streams=solusdt@bookTicker/solusdt@trade"
    )


def test_feature_engine_state_round_trip_preserves_warm_features():
    engine = MultiScaleFeatureEngine()

    for idx in range(FEATURE_LOOKBACK + 8):
        price = 100.0 + idx * 0.05
        engine.add_bar(
            Bar(
                timestamp=float(idx),
                open=price - 0.02,
                high=price + 0.05,
                low=price - 0.07,
                close=price,
                tick_count=10 + (idx % 4),
                obi_sum=1.5 + (idx % 3) * 0.2,
                spread_sum=0.8 + (idx % 5) * 0.05,
                buy_vol=5.0 + idx * 0.1,
                sell_vol=4.0 + idx * 0.08,
                buy_count=3,
                sell_count=2,
                vwap_num=price * 9.0,
                vwap_den=9.0,
            )
        )

    original = engine.compute_features()
    assert original is not None

    restored = MultiScaleFeatureEngine()
    restored.restore_state(engine.export_state())
    replay = restored.compute_features()

    assert restored.is_warm is True
    assert restored.bar_count == engine.bar_count
    assert replay is not None
    assert np.allclose(original, replay)


def test_core_runtime_state_round_trip_avoids_feature_warmup(tmp_path, monkeypatch):
    runtime_path = tmp_path / "runtime_state.pkl"
    model_path = tmp_path / "model.pkl"
    monkeypatch.setenv("NANOFENIXV3_RUNTIME_STATE_PATH", str(runtime_path))

    bot = core_module.NanoFenixV3(
        symbol="ETHUSDT",
        balance=1000.0,
        model_path=str(model_path),
    )
    for idx in range(FEATURE_LOOKBACK + 5):
        price = 2000.0 + idx * 0.1
        bot.features.add_bar(
            Bar(
                timestamp=float(idx),
                open=price - 0.03,
                high=price + 0.04,
                low=price - 0.06,
                close=price,
                tick_count=12,
                obi_sum=1.0 + idx * 0.01,
                spread_sum=0.9,
                buy_vol=6.0,
                sell_vol=4.5,
                buy_count=4,
                sell_count=3,
                vwap_num=price * 10.5,
                vwap_den=10.5,
            )
        )
    bot._tick_count = 12345
    bot._persist_runtime_snapshot()

    restored = core_module.NanoFenixV3(
        symbol="ETHUSDT",
        balance=1000.0,
        model_path=str(model_path),
    )

    assert restored.features.is_warm is True
    assert restored.features.bar_count == bot.features.bar_count
    assert restored._tick_count == 12345
    assert restored.features.compute_features() is not None


def test_unique_tmp_path_is_not_shared_between_writers(tmp_path):
    target = tmp_path / "signal.json"

    first = core_module._unique_tmp_path(target)
    second = core_module._unique_tmp_path(target)

    assert first != second
    assert first.parent == target.parent
    assert second.parent == target.parent
    assert first.name.startswith("signal.json.")
    assert second.name.startswith("signal.json.")
    assert first.name.endswith(".tmp")
    assert second.name.endswith(".tmp")


def test_companion_signal_includes_producer_identity(tmp_path, monkeypatch):
    signal_path = tmp_path / "companion.json"
    monkeypatch.setenv("NANOFENIX_SIGNAL_STATE_PATH", str(signal_path))
    monkeypatch.setenv("NANOFENIXV3_RUN_ID", "run-test-123")

    bot = core_module.NanoFenixV3(
        symbol="ETHUSDC",
        balance=1000.0,
        model_path=str(tmp_path / "model.pkl"),
    )

    bot._publish_companion_signal(
        bar_idx=1,
        close=2000.0,
        signal="SHORT",
        pred_bps=-3.5,
        confidence=0.72,
        ema_trend=-2.1,
        range_bps=4.2,
        buy_vol_ratio=0.44,
        regime_ctx={"regime": "TRENDING", "volatility": "LOW", "trend": "BEAR"},
        event_intensity=1.1,
        readiness={"ready": True, "reasons": [], "utility_score": 0.7},
        short_readiness={
            "ready": True,
            "reasons": [],
            "utility_score": 0.8,
            "direction_accuracy": 0.61,
            "direction_samples": 50,
            "calibration_samples": 80,
        },
        long_readiness={
            "ready": False,
            "reasons": ["low_direction_accuracy"],
            "utility_score": 0.2,
            "direction_accuracy": 0.49,
            "direction_samples": 50,
            "calibration_samples": 80,
        },
        policy={"allow_execute": True, "source": "short_only"},
    )

    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-test-123"
    assert payload["producer_pid"] == os.getpid()


def test_core_can_restore_from_interval_runtime_fallback_when_custom_target_is_empty(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    model_path = tmp_path / "model.pkl"

    bot = core_module.NanoFenixV3(
        symbol="ETHUSDT",
        balance=1000.0,
        model_path=str(model_path),
    )
    for idx in range(FEATURE_LOOKBACK + 5):
        price = 2000.0 + idx * 0.1
        bot.features.add_bar(
            Bar(
                timestamp=float(idx),
                open=price - 0.03,
                high=price + 0.04,
                low=price - 0.06,
                close=price,
                tick_count=12,
                obi_sum=1.0 + idx * 0.01,
                spread_sum=0.9,
                buy_vol=6.0,
                sell_vol=4.5,
                buy_count=4,
                sell_count=3,
                vwap_num=price * 10.5,
                vwap_den=10.5,
            )
        )
    bot._tick_count = 9876
    bot._persist_runtime_snapshot()

    custom_target = tmp_path / "custom" / "missing_runtime.pkl"
    monkeypatch.setenv("NANOFENIXV3_RUNTIME_STATE_PATH", str(custom_target))
    restored = core_module.NanoFenixV3(
        symbol="ETHUSDT",
        balance=1000.0,
        model_path=str(model_path),
    )

    assert str(restored._runtime_state_path) == str(custom_target)
    assert restored.features.is_warm is True
    assert restored._tick_count == 9876
    assert restored.features.compute_features() is not None


def test_core_publishes_policy_fields_in_companion_payload(tmp_path):
    signal_path = tmp_path / "companion.json"
    model_path = tmp_path / "model.pkl"

    bot = core_module.NanoFenixV3(
        symbol="ETHUSDT",
        balance=1000.0,
        model_path=str(model_path),
    )
    bot._signal_state_file = signal_path
    bot.predictor = types.SimpleNamespace(
        direction_accuracy=0.61,
        short_direction_accuracy=0.72,
        long_direction_accuracy=0.55,
        direction_samples=200,
        short_direction_samples=120,
        long_direction_samples=80,
        calibration_samples=300,
        short_calibration_samples=170,
        long_calibration_samples=130,
        val_accuracy_safe=0.58,
    )
    bot.executor = types.SimpleNamespace(
        position=None,
        n_trades=0,
        win_rate=0.0,
        total_pnl=0.0,
    )

    bot._publish_companion_signal(
        bar_idx=42,
        close=2000.0,
        signal="LONG",
        pred_bps=3.5,
        confidence=0.74,
        ema_trend=2.2,
        range_bps=12.0,
        buy_vol_ratio=0.61,
        regime_ctx={"regime": "TRENDING", "volatility": "MEDIUM", "trend": "BULL", "vol_30s": 4.1},
        event_intensity=1.4,
        readiness={"ready": True, "reasons": [], "utility_score": 1.0},
        short_readiness={
            "ready": True,
            "reasons": [],
            "utility_score": 1.0,
            "direction_accuracy": 0.72,
            "direction_samples": 120,
            "calibration_samples": 170,
        },
        long_readiness={
            "ready": True,
            "reasons": [],
            "utility_score": 0.7,
            "direction_accuracy": 0.55,
            "direction_samples": 80,
            "calibration_samples": 130,
        },
        policy={
            "expected_bps": 3.5,
            "uncertainty_bps": 0.8,
            "bias_correction_bps": 0.3,
            "edge_net_bps": 2.4,
            "actionable_edge_bps": 2.4,
            "calibration_health": 0.81,
            "fast_weight": 0.65,
            "slow_weight": 0.35,
            "drift_score": 0.22,
            "allow_add_to_position": False,
            "size_multiplier_hint": 0.55,
            "require_reversal_confirmation": True,
            "source": "short_only",
        },
    )

    payload = json.loads(signal_path.read_text())
    assert payload["edge_net_bps"] == 2.4
    assert payload["uncertainty_bps"] == 0.8
    assert payload["allow_add_to_position"] is False
    assert payload["size_multiplier_hint"] == 0.55
    assert payload["source"] == "short_only"


def test_core_observer_only_publishes_without_paper_execution(tmp_path):
    signal_path = tmp_path / "companion.json"

    bot = core_module.NanoFenixV3.__new__(core_module.NanoFenixV3)
    bot.symbol = "SOLUSDT"
    bot._signal_state_file = signal_path
    bot._observer_only = True
    bot._last_model_save_bar = 0
    bot._autosave_every = 999999

    bot.features = types.SimpleNamespace(
        bar_count=10,
        add_bar=lambda _bar: None,
        compute_features=lambda: np.ones(28, dtype=float),
        get_regime_context=lambda: {
            "regime": "TRENDING",
            "volatility": "LOW",
            "trend": "BULL",
            "vol_30s": 1.2,
        },
    )
    bot.predictor = types.SimpleNamespace(
        direction_accuracy=0.61,
        short_direction_accuracy=0.62,
        long_direction_accuracy=0.6,
        direction_samples=400,
        short_direction_samples=200,
        long_direction_samples=200,
        calibration_samples=600,
        short_calibration_samples=300,
        long_calibration_samples=300,
        val_accuracy_safe=0.58,
        either_trained=True,
        store=lambda _feat, _close: None,
        evaluate_direction=lambda _bar_idx, _close: None,
        should_retrain=lambda: False,
        predict_with_policy=lambda *_args, **_kwargs: {
            "signal": "SHORT",
            "pred_bps": -2.2,
            "confidence": 0.72,
            "allow_execute": True,
        },
        estimate_event_intensity=lambda _feat: 1.0,
        companion_readiness=lambda: {"ready": True, "reasons": [], "utility_score": 1.0},
        short_companion_readiness=lambda: {
            "ready": True,
            "reasons": [],
            "utility_score": 1.0,
            "direction_accuracy": 0.62,
            "direction_samples": 200,
            "calibration_samples": 300,
        },
        long_companion_readiness=lambda: {
            "ready": True,
            "reasons": [],
            "utility_score": 1.0,
            "direction_accuracy": 0.6,
            "direction_samples": 200,
            "calibration_samples": 300,
        },
    )
    on_bar_calls = {"called": False}

    bot.executor = types.SimpleNamespace(
        position=None,
        n_trades=0,
        win_rate=0.0,
        total_pnl=0.0,
    )

    def _mark_called(**_kwargs):
        on_bar_calls["called"] = True

    bot.executor.on_bar = _mark_called

    bot._on_bar(
        Bar(
            timestamp=1.0,
            open=84.0,
            high=84.2,
            low=83.9,
            close=84.1,
            tick_count=12,
            obi_sum=0.0,
            spread_sum=0.0,
            buy_vol=1.0,
            sell_vol=1.0,
            buy_count=1,
            sell_count=1,
            vwap_num=84.1,
            vwap_den=1.0,
        )
    )

    assert on_bar_calls["called"] is False
    payload = json.loads(signal_path.read_text())
    assert payload["action"] == "SELL"
    assert payload["has_position"] is False
