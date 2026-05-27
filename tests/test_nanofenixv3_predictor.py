import numpy as np
import pandas as pd
import pytest

import nanofenixv3.predictor as predictor_module
from nanofenixv3.adaptive_fusion import AdaptiveDualHorizonFusion
from nanofenixv3.feature_engine import N_FEATURES
from nanofenixv3.predictor import (
    DualHorizonPredictor,
    OnlineConfidenceCalibrator,
    _event_weight_from_frame,
)


class _ConstantModel:
    def __init__(self, pred_bps: float):
        self.pred_bps = pred_bps

    def predict(self, frame):
        return np.array([self.pred_bps], dtype=float)


def _features() -> np.ndarray:
    return np.zeros(N_FEATURES, dtype=np.float32)


def _make_dual_predictor(short_bps: float, long_bps: float) -> DualHorizonPredictor:
    predictor = DualHorizonPredictor()
    predictor._short._trained = True
    predictor._short._model = _ConstantModel(short_bps)
    predictor._short._last_val_acc = 0.56
    predictor._long._trained = True
    predictor._long._model = _ConstantModel(long_bps)
    predictor._long._last_val_acc = 0.60
    return predictor


def test_long_horizon_is_evaluated_only_after_long_delay():
    predictor = _make_dual_predictor(short_bps=2.0, long_bps=2.0)

    signal, pred_bps, confidence = predictor.predict(
        _features(), volatility_state="MEDIUM", bar_idx=1, close=100.0
    )

    assert signal == "LONG"
    assert pred_bps > 0
    assert confidence > 0

    # After 30 bars the short model should be scored, but the 120s model must
    # still be pending. A short-lived pullback should not mark the long model wrong.
    predictor.evaluate_direction(current_bar_idx=31, current_close=99.95)
    assert list(predictor._short_dir_correct) == [0]
    assert list(predictor._long_dir_correct) == []
    assert predictor.short_direction_samples == 1
    assert predictor.long_direction_samples == 0

    # After the full long horizon the trend recovered; the long model is finally
    # evaluated against the correct horizon and scored as correct.
    predictor.evaluate_direction(current_bar_idx=121, current_close=100.25)
    assert list(predictor._short_dir_correct) == [0]
    assert list(predictor._long_dir_correct) == [1]
    assert predictor.short_direction_samples == 1
    assert predictor.long_direction_samples == 1


def test_long_horizon_direction_eval_uses_barrier_path_not_only_final_close():
    predictor = DualHorizonPredictor()
    predictor.queue_direction_eval(
        bar_idx=1,
        pred_bps=6.0,
        close=100.0,
        horizon=120,
        raw_conf=0.8,
    )

    closes = [100.0]
    closes.extend([100.12])  # +12bps hit before the horizon ends
    closes.extend([99.95] * 119)
    predictor._close_buf.extend(closes[:121])

    predictor.evaluate_direction(current_bar_idx=121, current_close=99.95)

    assert list(predictor._long_dir_correct) == [1]
    assert predictor.long_direction_samples == 1


def test_direction_accuracy_blends_short_and_long_horizon_scores():
    predictor = DualHorizonPredictor()
    predictor._short_dir_correct.extend([1] * 10)
    predictor._long_dir_correct.extend([0] * 10)

    assert predictor.short_direction_accuracy == pytest.approx(1.0)
    assert predictor.long_direction_accuracy == pytest.approx(0.0)
    assert predictor.direction_accuracy == pytest.approx(0.4)


def test_single_long_model_uses_long_horizon_accuracy_for_confidence():
    predictor = DualHorizonPredictor()
    predictor._long._trained = True
    predictor._long._model = _ConstantModel(3.0)
    predictor._long._last_val_acc = 0.60

    predictor._short_dir_correct.extend([0] * 40)
    predictor._long_dir_correct.extend([1] * 40)

    signal, pred_bps, confidence = predictor.predict(_features(), volatility_state="MEDIUM")

    assert signal == "LONG"
    assert pred_bps == pytest.approx(3.0)
    assert confidence == pytest.approx(0.57, abs=1e-3)


def test_online_calibrator_moves_confidence_with_realized_outcomes():
    calibrator = OnlineConfidenceCalibrator(min_samples=4)

    baseline = calibrator.calibrate(0.85)
    for _ in range(12):
        calibrator.update(0.85, False)
    lowered = calibrator.calibrate(0.85)
    for _ in range(24):
        calibrator.update(0.85, True)
    recovered = calibrator.calibrate(0.85)

    assert lowered < baseline
    assert recovered > lowered


def test_low_meta_probability_blocks_signal_even_with_good_direction_accuracy():
    predictor = DualHorizonPredictor()
    predictor._long._trained = True
    predictor._long._model = _ConstantModel(3.0)
    predictor._long._last_val_acc = 0.60
    predictor._long_dir_correct.extend([1] * 40)

    for _ in range(30):
        predictor._long_calibrator.update(0.8, False)

    signal, pred_bps, confidence = predictor.predict(_features(), volatility_state="MEDIUM")

    assert signal == "HOLD"
    assert pred_bps == 0.0
    assert confidence == 0.0


def test_event_weighting_prioritizes_high_signal_samples():
    low_event = pd.DataFrame(
        [
            {
                "range_bps_30": 3.0,
                "volatility_30": 0.9,
                "volatility_120": 1.0,
                "obi_zscore_30": 0.2,
                "obi_zscore_120": 0.3,
                "buy_vol_ratio_30": 0.05,
                "trade_dir_10": 0.04,
                "trend_consistency": 0.45,
                "flow_price_agree": 0.5,
                "spread_zscore_60": 1.5,
            }
        ]
    )
    high_event = pd.DataFrame(
        [
            {
                "range_bps_30": 24.0,
                "volatility_30": 4.8,
                "volatility_120": 5.2,
                "obi_zscore_30": 2.3,
                "obi_zscore_120": 1.9,
                "buy_vol_ratio_30": 0.72,
                "trade_dir_10": 0.68,
                "trend_consistency": 0.88,
                "flow_price_agree": 1.0,
                "spread_zscore_60": 0.3,
            }
        ]
    )

    low_weight = _event_weight_from_frame(low_event, horizon=30)[0]
    high_weight = _event_weight_from_frame(high_event, horizon=120)[0]

    assert low_weight < 1.0
    assert high_weight > low_weight


def test_disagreement_stays_hold_when_adaptive_fusion_is_disabled():
    predictor = _make_dual_predictor(short_bps=-0.8, long_bps=6.0)

    signal, pred_bps, confidence = predictor.predict(
        _features(), volatility_state="MEDIUM", regime="TRENDING"
    )

    assert signal == "HOLD"
    assert pred_bps == 0.0
    assert confidence == 0.0


def test_adaptive_fusion_can_resolve_dominant_disagreement():
    predictor = _make_dual_predictor(short_bps=-0.8, long_bps=6.0)
    predictor._adaptive_fusion_enabled = True
    predictor._adaptive_fusion = AdaptiveDualHorizonFusion()
    predictor._adaptive_fusion.BASE_THRESHOLD = 0.52

    signal, pred_bps, confidence = predictor.predict(
        _features(), volatility_state="MEDIUM", regime="TRENDING"
    )

    assert signal == "LONG"
    assert pred_bps > 1.5
    assert confidence > 0.0


def test_adaptive_fusion_downweights_horizon_with_poor_recent_accuracy():
    fusion = AdaptiveDualHorizonFusion()
    fusion.short_accuracy_history.extend([1.0] * 80)
    fusion.long_accuracy_history.extend([0.0] * 80)

    result = fusion.fuse_predictions(
        short_pred={"direction": "UP", "confidence": 0.7, "expected_return_bps": 2.0},
        long_pred={"direction": "DOWN", "confidence": 0.9, "expected_return_bps": -6.0},
        market_regime="TRENDING",
    )

    assert result["signal"] == "UP"
    assert result["effective_weights"]["short"] > result["effective_weights"]["long"]


def test_predictor_syncs_adaptive_fusion_history_from_loaded_state(tmp_path, monkeypatch):
    monkeypatch.setattr(predictor_module, "ENABLE_ADAPTIVE_FUSION", True)
    path = tmp_path / "nanofenix_state.pkl"

    predictor = _make_dual_predictor(short_bps=3.0, long_bps=2.0)
    predictor._adaptive_fusion_enabled = True
    predictor._adaptive_fusion = AdaptiveDualHorizonFusion()
    predictor._short_dir_correct.extend([1] * 40 + [0] * 10)
    predictor._long_dir_correct.extend([0] * 35 + [1] * 15)

    predictor.save_model(str(path))

    restored = DualHorizonPredictor(str(path))

    assert restored._adaptive_fusion is not None
    assert len(restored._adaptive_fusion.short_accuracy_history) == restored.short_direction_samples
    assert len(restored._adaptive_fusion.long_accuracy_history) == restored.long_direction_samples


def test_predictor_persists_live_state_round_trip(tmp_path):
    path = tmp_path / "nanofenix_state.pkl"

    predictor = _make_dual_predictor(short_bps=3.0, long_bps=2.0)
    predictor._short_dir_correct.extend([1] * 90)
    predictor._long_dir_correct.extend([1] * 110)
    for _ in range(40):
        predictor._short_calibrator.update(0.8, True)
    for _ in range(50):
        predictor._long_calibrator.update(0.75, True)
    predictor._feat_buf.append(_features())
    predictor._close_buf.append(100.0)

    predictor.save_model(str(path))

    restored = DualHorizonPredictor(str(path))
    readiness = restored.companion_readiness()

    assert restored.short_direction_samples == 90
    assert restored.long_direction_samples == 110
    assert restored.short_calibration_samples == 40
    assert restored.long_calibration_samples == 50
    assert readiness["ready"] is True
    assert readiness["utility_score"] > 0.0


def test_predict_with_policy_reports_actionable_edge_and_uncertainty():
    predictor = _make_dual_predictor(short_bps=4.0, long_bps=2.0)
    predictor._short_dir_correct.extend([1] * 60)
    predictor._long_dir_correct.extend([1] * 60)
    for _ in range(40):
        predictor._short_calibrator.update(0.8, True)
        predictor._long_calibrator.update(0.7, True)

    result = predictor.predict_with_policy(
        _features(),
        volatility_state="MEDIUM",
        regime="TRENDING",
    )

    assert result["signal"] == "LONG"
    assert result["expected_bps"] > 0
    assert result["uncertainty_bps"] >= 0
    assert result["edge_net_bps"] > 0
    assert "bias_correction_bps" in result
    assert "calibration_health" in result


def test_predict_with_policy_uses_single_source_when_only_short_signal_is_actionable():
    predictor = _make_dual_predictor(short_bps=4.0, long_bps=0.2)
    predictor._short_dir_correct.extend([1] * 60)
    predictor._long_dir_correct.extend([0] * 10 + [1] * 10)
    for _ in range(40):
        predictor._short_calibrator.update(0.8, True)
        predictor._long_calibrator.update(0.55, True)

    result = predictor.predict_with_policy(
        _features(),
        volatility_state="MEDIUM",
        regime="VOLATILE",
    )

    assert result["signal"] == "LONG"
    assert result["source"] == "short_only"
    assert result["fast_weight"] > result["slow_weight"]


def test_predict_with_policy_downweights_degraded_branch_and_applies_bias_correction():
    predictor = _make_dual_predictor(short_bps=3.0, long_bps=3.0)
    predictor._short_dir_correct.extend([1] * 80)
    predictor._long_dir_correct.extend([0] * 80)
    predictor._recent_signed_errors.extend([1.5, 1.0, 1.3, 1.2])

    result = predictor.predict_with_policy(
        _features(),
        volatility_state="MEDIUM",
        regime="TRENDING",
    )

    assert result["fast_weight"] > result["slow_weight"]
    assert result["bias_correction_bps"] > 0
    assert result["edge_net_bps"] < abs(result["expected_bps"])


def test_companion_readiness_reports_block_reasons():
    predictor = DualHorizonPredictor()
    predictor._short._trained = True
    predictor._short._model = _ConstantModel(2.5)
    predictor._short._last_val_acc = 0.58
    predictor._short_dir_correct.extend([0] * 60)
    for _ in range(25):
        predictor._short_calibrator.update(0.8, False)

    readiness = predictor.companion_readiness(
        min_direction_accuracy=0.55,
        min_direction_samples=40,
        min_calibration_samples=20,
    )

    assert readiness["ready"] is False
    assert "low_direction_accuracy" in readiness["reasons"]


def test_short_companion_readiness_can_be_true_when_overall_is_dragged_by_long_horizon():
    predictor = DualHorizonPredictor()
    predictor._short._trained = True
    predictor._long._trained = True
    predictor._short._model = _ConstantModel(-2.8)
    predictor._long._model = _ConstantModel(1.2)
    predictor._short._last_val_acc = 0.58
    predictor._long._last_val_acc = 0.54

    predictor._short_dir_correct.extend([1] * 70 + [0] * 10)
    predictor._long_dir_correct.extend([0] * 60 + [1] * 20)
    for _ in range(90):
        predictor._short_calibrator.update(0.7, True)
    for _ in range(90):
        predictor._long_calibrator.update(0.6, False)

    overall = predictor.companion_readiness(
        min_direction_accuracy=0.55,
        min_direction_samples=40,
        min_calibration_samples=40,
    )
    short_only = predictor.short_companion_readiness(
        min_direction_accuracy=0.55,
        min_direction_samples=40,
        min_calibration_samples=40,
    )

    assert overall["ready"] is False
    assert "low_direction_accuracy" in overall["reasons"]
    assert short_only["ready"] is True
    assert short_only["direction_accuracy"] > overall["direction_accuracy"]
