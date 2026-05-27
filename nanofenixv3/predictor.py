"""
NanoFenix v3 — Dual-Horizon LightGBM Predictor.

Key innovations over V2:
1. TWO models: 30-bar (30s) horizon + 120-bar (2min) horizon
   - Consensus: only signal when BOTH agree on direction
   - This filters out noise (short-term model catches reversals,
     long-term model confirms trend — disagreement = HOLD)
2. Better confidence calibration:
   - Confidence is empirically calibrated to match what Fenix expects
   - Uses validation accuracy + prediction magnitude + model agreement
   - Result: fewer but higher-quality signals (less HOLD when edge exists)
3. Adaptive threshold:
   - In high-volatility regimes: lower threshold (bigger moves to catch)
   - In low-volatility: higher threshold (small predictions unreliable)
4. Anti-overfitting: Huber loss, validation split, sample weighting

LightGBM was chosen over neural networks because:
- Trains in 50ms vs 10s for PyTorch (critical for online learning)
- No segfault risk on macOS ARM64 / Accelerate framework
- Comparable accuracy for tabular financial data (Citadel, Two Sigma use it)
- No GPU needed, no BLAS threading issues
"""

from __future__ import annotations

import logging
import os
import pickle
import time
from collections import deque
from math import ceil, floor
from pathlib import Path

import numpy as np
import pandas as pd

from .adaptive_fusion import AdaptiveDualHorizonFusion
from .feature_engine import FEATURE_NAMES

os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

logger = logging.getLogger("NanoFenixV3.Predictor")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


# ── Configuration ──
MAX_BARS = _env_int("NANOFENIXV3_MAX_BARS", 7200)
MIN_SAMPLES = _env_int("NANOFENIXV3_MIN_SAMPLES", 600)
RETRAIN_EVERY = _env_int("NANOFENIXV3_RETRAIN_EVERY", 800)  # Was 300 — more stable

# Dual horizons
HORIZON_SHORT = _env_int("NANOFENIXV3_HORIZON_SHORT", 30)  # 30 bars = 30 seconds
HORIZON_LONG = _env_int("NANOFENIXV3_HORIZON_LONG", 120)  # 120 bars = 2 minutes

# Gap between train and val to prevent leakage
TRAIN_VAL_GAP = _env_int("NANOFENIXV3_TRAIN_VAL_GAP", 60)  # NEW: prevents lookahead

# Prediction thresholds (adaptive — these are base values)
BASE_MIN_BPS = _env_float("NANOFENIXV3_BASE_MIN_BPS", 1.5)  # Lower than V2's 2.5!
HIGH_VOL_MIN_BPS = _env_float("NANOFENIXV3_HIGH_VOL_MIN_BPS", 1.0)
LOW_VOL_MIN_BPS = _env_float(
    "NANOFENIXV3_LOW_VOL_MIN_BPS", 0.5
)  # V3.5: lowered from 2.0 to allow signals in low-vol

# Direction accuracy tracking
DIR_ACCURACY_WINDOW = 200  # Larger window for stable metric
MIN_DIR_ACCURACY_SAMPLES = 10
CALIBRATION_BINS = _env_int("NANOFENIXV3_CALIBRATION_BINS", 8)
CALIBRATION_PRIOR_ALPHA = _env_float("NANOFENIXV3_CALIBRATION_PRIOR_ALPHA", 1.5)
CALIBRATION_PRIOR_BETA = _env_float("NANOFENIXV3_CALIBRATION_PRIOR_BETA", 1.5)
CALIBRATION_MIN_SAMPLES = _env_int("NANOFENIXV3_CALIBRATION_MIN_SAMPLES", 12)
META_MIN_MOVE_BPS = _env_float("NANOFENIXV3_META_MIN_MOVE_BPS", 0.8)
META_MOVE_PRED_RATIO = _env_float("NANOFENIXV3_META_MOVE_PRED_RATIO", 0.45)
EVENT_WEIGHT_FLOOR = _env_float("NANOFENIXV3_EVENT_WEIGHT_FLOOR", 0.35)
EVENT_WEIGHT_CAP = _env_float("NANOFENIXV3_EVENT_WEIGHT_CAP", 2.5)
EVENT_KEEP_THRESHOLD = _env_float("NANOFENIXV3_EVENT_KEEP_THRESHOLD", 1.05)
EVENT_KEEP_RECENT = _env_int("NANOFENIXV3_EVENT_KEEP_RECENT", 220)
MAX_TRAIN_VAL_GAP = _env_float(
    "NANOFENIXV3_MAX_TRAIN_VAL_GAP", 0.25
)  # V3.5: was 0.15 — allow more overfit for noisy short model
COMPANION_MIN_DIR_ACC = _env_float(
    "NANOFENIXV3_COMPANION_MIN_DIRECTION_ACCURACY",
    0.48,  # V3.5: was 0.55
)
COMPANION_MIN_DIR_SAMPLES = _env_int("NANOFENIXV3_COMPANION_MIN_DIRECTION_SAMPLES", 80)
COMPANION_MIN_CALIBRATION_SAMPLES = _env_int("NANOFENIXV3_COMPANION_MIN_CALIBRATION_SAMPLES", 80)
ENABLE_ADAPTIVE_FUSION = os.getenv("NANOFENIXV3_ENABLE_ADAPTIVE_FUSION", "0").strip() == "1"
ADAPTIVE_FUSION_BASE_THRESHOLD = _env_float(
    "NANOFENIXV3_ADAPTIVE_FUSION_BASE_THRESHOLD",
    0.35,  # V3.5: was 0.52
)
ADAPTIVE_FUSION_MIN_MARGIN = _env_float(
    "NANOFENIXV3_ADAPTIVE_FUSION_MIN_MARGIN",
    0.08,  # V3.5: was 0.12
)
POLICY_MIN_ACTIONABLE_EDGE_BPS = _env_float("NANOFENIXV3_MIN_ACTIONABLE_EDGE_BPS", 0.8)
POLICY_MIN_CALIBRATION_HEALTH = _env_float("NANOFENIXV3_MIN_CALIBRATION_HEALTH", 0.5)
POLICY_BIAS_DEADZONE_BPS = _env_float("NANOFENIXV3_BIAS_DEADZONE_BPS", 0.15)
POLICY_MIN_ERROR_SAMPLES = _env_int("NANOFENIXV3_POLICY_MIN_ERROR_SAMPLES", 4)
POLICY_FEE_BUFFER_BPS = _env_float("NANOFENIXV3_POLICY_FEE_BUFFER_BPS", 0.35)


def _normalize_fusion_regime(regime: str) -> str:
    normalized = (regime or "").strip().upper()
    if normalized == "RANGING":
        return "CHOP"
    if normalized in {"TRENDING", "CHOP", "DEAD", "VOLATILE"}:
        return normalized
    return "CHOP"


class OnlineConfidenceCalibrator:
    """Bucketed online calibrator with smoothed beta posteriors."""

    def __init__(
        self,
        bins: int = CALIBRATION_BINS,
        alpha: float = CALIBRATION_PRIOR_ALPHA,
        beta: float = CALIBRATION_PRIOR_BETA,
        min_samples: int = CALIBRATION_MIN_SAMPLES,
    ):
        self._bins = max(4, bins)
        self._alpha = float(alpha)
        self._beta = float(beta)
        self._min_samples = max(1, min_samples)
        self._success = np.full(self._bins, self._alpha, dtype=np.float32)
        self._failure = np.full(self._bins, self._beta, dtype=np.float32)
        self._updates = 0

    @property
    def samples(self) -> int:
        return self._updates

    def _bucket_index(self, confidence: float) -> int:
        clipped = min(0.999999, max(0.0, float(confidence)))
        return min(self._bins - 1, int(clipped * self._bins))

    def _bucket_mean(self, index: int) -> float:
        success = float(self._success[index])
        failure = float(self._failure[index])
        return success / max(success + failure, 1e-9)

    def update(self, confidence: float, success: bool) -> None:
        index = self._bucket_index(confidence)
        if success:
            self._success[index] += 1.0
        else:
            self._failure[index] += 1.0
        self._updates += 1

    def calibrate(self, confidence: float) -> float:
        clipped = min(0.999999, max(0.0, float(confidence)))
        pos = clipped * (self._bins - 1)
        lower = int(floor(pos))
        upper = int(ceil(pos))
        if lower == upper:
            posterior = self._bucket_mean(lower)
        else:
            upper_weight = pos - lower
            lower_weight = 1.0 - upper_weight
            posterior = lower_weight * self._bucket_mean(lower) + upper_weight * self._bucket_mean(
                upper
            )

        # Shrink toward the heuristic confidence until enough live evidence accumulates.
        trust = min(1.0, self._updates / float(self._min_samples * 2))
        return float((1.0 - trust) * clipped + trust * posterior)

    def export_state(self) -> dict[str, object]:
        return {
            "bins": int(self._bins),
            "alpha": float(self._alpha),
            "beta": float(self._beta),
            "min_samples": int(self._min_samples),
            "success": self._success.astype(float).tolist(),
            "failure": self._failure.astype(float).tolist(),
            "updates": int(self._updates),
        }

    def restore_state(self, state: dict[str, object] | None) -> None:
        if not state:
            return
        try:
            success = np.asarray(state.get("success", []), dtype=np.float32)
            failure = np.asarray(state.get("failure", []), dtype=np.float32)
            if len(success) != self._bins or len(failure) != self._bins:
                return
            self._success = success
            self._failure = failure
            self._updates = max(0, int(state.get("updates", 0)))
        except Exception as exc:
            logger.debug("Failed to restore calibrator state: %s", exc)


def _event_weight_from_frame(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    """Event/volatility-aware sample priority using only contemporaneous features."""
    vol_col = "volatility_120" if horizon >= HORIZON_LONG else "volatility_30"
    obi_col = "obi_zscore_120" if horizon >= HORIZON_LONG else "obi_zscore_30"

    range_term = np.clip(frame["range_bps_30"].to_numpy(dtype=np.float32) / 18.0, 0.0, 2.2)
    vol_term = np.clip(frame[vol_col].to_numpy(dtype=np.float32) / 3.0, 0.0, 2.0)
    flow_term = np.clip(
        (
            np.abs(frame[obi_col].to_numpy(dtype=np.float32))
            + np.abs(frame["buy_vol_ratio_30"].to_numpy(dtype=np.float32))
            + np.abs(frame["trade_dir_10"].to_numpy(dtype=np.float32))
        )
        / 3.0,
        0.0,
        2.0,
    )
    trend_term = np.clip(frame["trend_consistency"].to_numpy(dtype=np.float32), 0.0, 1.0)
    alignment_term = np.clip(frame["flow_price_agree"].to_numpy(dtype=np.float32), 0.0, 1.0)
    spread_penalty = np.clip(
        np.maximum(0.0, frame["spread_zscore_60"].to_numpy(dtype=np.float32)),
        0.0,
        2.5,
    )

    weight = (
        0.72
        + 0.34 * range_term
        + 0.26 * vol_term
        + 0.28 * flow_term
        + 0.12 * trend_term
        + 0.10 * alignment_term
        - 0.10 * spread_penalty
    )

    dead_mask = (
        (frame["range_bps_30"].to_numpy(dtype=np.float32) < 6.0)
        & (frame[vol_col].to_numpy(dtype=np.float32) < 1.5)
        & (np.abs(frame[obi_col].to_numpy(dtype=np.float32)) < 0.75)
        & (np.abs(frame["buy_vol_ratio_30"].to_numpy(dtype=np.float32)) < 0.18)
    )
    weight[dead_mask] *= 0.55
    return np.clip(weight, EVENT_WEIGHT_FLOOR, EVENT_WEIGHT_CAP)


def _event_keep_mask(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    weights = _event_weight_from_frame(frame, horizon)
    keep = weights >= EVENT_KEEP_THRESHOLD
    if len(keep) <= EVENT_KEEP_RECENT:
        return np.ones(len(keep), dtype=bool)
    keep[-EVENT_KEEP_RECENT:] = True
    return keep


def _barrier_thresholds_bps(horizon: int) -> tuple[float, float]:
    """Keep online evaluation aligned with the training triple-barrier labels."""
    if horizon <= 60:
        return 5.0, 4.0
    return 10.0, 8.0


# ═══════════════════════════════════════════════════════════════════════════════
# Single-Horizon Model
# ═══════════════════════════════════════════════════════════════════════════════


class HorizonModel:
    """One LightGBM model predicting returns at a specific horizon."""

    def __init__(self, horizon: int, name: str):
        self.horizon = horizon
        self.name = name
        self._model = None
        self._trained = False
        self._last_mae: float = 999.0
        self._last_val_acc: float = 0.0
        self._last_train_acc: float = 0.0
        self._last_train_samples: int = 0
        self._bars_since_retrain: int = 0

    @property
    def trained(self) -> bool:
        return self._trained

    @property
    def val_accuracy(self) -> float:
        return self._last_val_acc

    def should_retrain(self, total_samples: int) -> bool:
        usable = total_samples - self.horizon
        if usable < MIN_SAMPLES:
            return False
        # Minimum interval between attempts: MIN_SAMPLES for untrained, RETRAIN_EVERY for trained
        min_interval = MIN_SAMPLES if not self._trained else RETRAIN_EVERY
        return self._bars_since_retrain >= min_interval

    def retrain(self, features: list[np.ndarray], closes: list[float]) -> float | None:
        """Train LightGBM huber regression. Returns val MAE or None."""
        try:
            import lightgbm as lgbm
        except ImportError:
            logger.warning("lightgbm not installed — pip install lightgbm")
            return None

        n = len(features)
        usable = n - self.horizon
        if usable < 100:
            return None

        X = pd.DataFrame(features[:usable], columns=FEATURE_NAMES).astype(np.float32)

        # DIRECT future return — NO smoothing (V3.1 fix)
        # Smoothing was blurring the signal, especially for 30-bar horizon.
        # Clean point-to-point return is what the executor actually trades.
        # TRIPLE-BARRIER METHOD (HFT Target creation)
        # Instead of just looking at the final horizon (which ignores if we hit a stop loss along the way),
        # we check the path. If we hit TP or SL before the horizon, that defines the trade outcome.
        y = np.zeros(usable, dtype=np.float32)

        # Adaptive barriers based on horizon (e.g., 30 bars vs 120 bars)
        # For 30s we might expect ~2-3bps noise, so TP=5, SL=4
        tp_bps = 5.0 if self.horizon <= 60 else 10.0
        sl_bps = 4.0 if self.horizon <= 60 else 8.0

        for i in range(usable):
            current = closes[i]
            if current <= 0:
                continue

            hit_barrier = False
            # Check path from i+1 to i+horizon (Vertical barrier)
            for step in range(1, self.horizon + 1):
                path_price = closes[i + step]
                ret_bps = (path_price - current) / current * 10000

                if ret_bps >= tp_bps:  # Upper barrier (Take Profit)
                    y[i] = tp_bps
                    hit_barrier = True
                    break
                elif ret_bps <= -sl_bps:  # Lower barrier (Stop Loss)
                    y[i] = -sl_bps
                    hit_barrier = True
                    break

            if not hit_barrier:
                # Vertical barrier (Time expiration)
                future = closes[i + self.horizon]
                y[i] = (future - current) / current * 10000  # bps

        # Clean data (remove outliers > 200bps = 2% in 30s/120s)
        valid = np.isfinite(y) & (np.abs(y) < 200)
        if valid.sum() < 100:
            return None
        X, y = X[valid].reset_index(drop=True), y[valid]

        # Event/volatility-aware sampling: keep recent data plus bars where
        # microstructure, range, or volatility suggest actual opportunity.
        keep_mask = _event_keep_mask(X, self.horizon)
        if int(np.sum(keep_mask)) >= MIN_SAMPLES:
            X = X.loc[keep_mask].reset_index(drop=True)
            y = y[keep_mask]

        # NO dead zone removal (V3.1 fix)
        # Previously removed |y| < 0.5bps which biased the model to only
        # predict large moves. The model should learn from ALL data
        # including noise — it needs to know when there's nothing to predict.

        t0 = time.monotonic()

        try:
            # GAPPED train/val split (V3.1 fix)
            # Without gap, val targets can see data from train period
            # creating artificially high val accuracy.
            split_end_train = int(len(X) * 0.75)
            val_start = min(split_end_train + TRAIN_VAL_GAP, len(X) - 50)
            if val_start >= len(X) - 10:
                val_start = split_end_train  # Fallback: no gap if not enough data

            X_train = X.iloc[:split_end_train]
            y_train = y[:split_end_train]
            X_val = X.iloc[val_start:]
            y_val = y[val_start:]

            # Sample weighting: recent data gets more weight (exponential decay)
            # This helps the model adapt to changing market conditions.
            n_train = len(X_train)
            recency_weights = np.exp(np.linspace(-2.0, 0.0, n_train)).astype(np.float32)
            event_weights = _event_weight_from_frame(X_train, self.horizon).astype(np.float32)
            sample_weights = recency_weights * event_weights

            params = {
                "objective": "huber",
                "learning_rate": 0.05,  # Slightly lower for stability
                "num_leaves": 12,  # Simpler (was 16) — less overfitting
                "n_estimators": 100,  # More trees but simpler
                "subsample": 0.75,
                "colsample_bytree": 0.65,  # Stronger feature dropout
                "min_child_samples": 20,  # More conservative splits (was 15)
                "reg_alpha": 0.2,
                "reg_lambda": 0.5,
                "verbose": -1,
            }
            model = lgbm.LGBMRegressor(**params)
            model.fit(X_train, y_train, sample_weight=sample_weights)
            dt = time.monotonic() - t0

            # Evaluate — only on val (out-of-sample with gap)
            preds_val = model.predict(X_val)
            mae_val = float(np.mean(np.abs(preds_val - y_val)))
            # Direction accuracy: only count samples where actual moved meaningfully
            meaningful_val = np.abs(y_val) >= 0.3  # Don't count tiny noise as misses
            if meaningful_val.sum() >= 10:
                dir_acc_val = float(
                    np.mean(np.sign(preds_val[meaningful_val]) == np.sign(y_val[meaningful_val]))
                )
            else:
                dir_acc_val = float(np.mean(np.sign(preds_val) == np.sign(y_val)))

            preds_train = model.predict(X_train)
            meaningful_train = np.abs(y_train) >= 0.3
            if meaningful_train.sum() >= 10:
                dir_acc_train = float(
                    np.mean(
                        np.sign(preds_train[meaningful_train]) == np.sign(y_train[meaningful_train])
                    )
                )
            else:
                dir_acc_train = float(np.mean(np.sign(preds_train) == np.sign(y_train)))

            # QUALITY GATE (V3.1 fix): reject model if val accuracy < 48%
            # A model worse than random does active harm — even on first train.
            if dir_acc_val < 0.48:
                prev_info = (
                    f"(keeping old model with {self._last_val_acc * 100:.1f}%)"
                    if self._trained
                    else "(no model yet — will retry with more data)"
                )
                logger.warning(
                    f"⚠️  [{self.name}] Rejecting retrain: val_acc={dir_acc_val * 100:.1f}% < 48% "
                    f"{prev_info}"
                )
                self._bars_since_retrain = 0  # Don't try again immediately
                return None

            if (dir_acc_train - dir_acc_val) > MAX_TRAIN_VAL_GAP:
                prev_info = (
                    f"(keeping old model with {self._last_val_acc * 100:.1f}%)"
                    if self._trained
                    else "(no model yet — will retry with more data)"
                )
                logger.warning(
                    f"⚠️  [{self.name}] Rejecting retrain: overfit gap "
                    f"{(dir_acc_train - dir_acc_val) * 100:.1f}pp > {MAX_TRAIN_VAL_GAP * 100:.1f}pp "
                    f"{prev_info}"
                )
                self._bars_since_retrain = 0
                return None

            self._model = model
            self._trained = True
            self._last_mae = mae_val
            self._last_val_acc = dir_acc_val
            self._last_train_acc = dir_acc_train
            self._last_train_samples = len(X)
            self._bars_since_retrain = 0

            # Top features
            imp = model.feature_importances_
            top = sorted(zip(FEATURE_NAMES, imp, strict=False), key=lambda x: x[1], reverse=True)[
                :5
            ]

            logger.info(
                f"✅ [{self.name}] Trained ({dt:.2f}s) | "
                f"MAE(val)={mae_val:.2f}bps | "
                f"Acc train={dir_acc_train * 100:.1f}% val={dir_acc_val * 100:.1f}% | "
                f"Samples: {len(X)} (gap={TRAIN_VAL_GAP}) | Horizon: {self.horizon}s"
            )
            logger.info(f"   [{self.name}] Top features: {top}")
            return mae_val

        except Exception as e:
            logger.error(f"⚠️  [{self.name}] Training error: {e}")
            return None

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        """Returns (predicted_bps, raw_confidence)."""
        if not self._trained or self._model is None:
            return 0.0, 0.0

        try:
            feat_df = pd.DataFrame([features], columns=FEATURE_NAMES)
            pred_bps = float(self._model.predict(feat_df)[0])
        except Exception:
            return 0.0, 0.0

        # Raw confidence: based on prediction magnitude
        raw_conf = min(0.95, 0.5 + abs(pred_bps) / 10.0)
        return pred_bps, raw_conf

    def tick(self) -> None:
        """Call once per bar to track retrain cadence."""
        self._bars_since_retrain += 1


# ═══════════════════════════════════════════════════════════════════════════════
# Dual-Horizon Ensemble Predictor
# ═══════════════════════════════════════════════════════════════════════════════


class DualHorizonPredictor:
    """
    Runs two LightGBM models at different horizons and combines predictions.

    Key insight: When short-term and long-term models AGREE on direction,
    the signal is much more reliable than either model alone. When they
    DISAGREE, it's likely noise → HOLD.

    Confidence calibration:
    - Both agree + strong magnitude → HIGH confidence (0.70-0.95)
    - Both agree + weak magnitude → MEDIUM confidence (0.55-0.70)
    - Disagree → HOLD (confidence 0.0)
    """

    def __init__(self, model_path: str | None = None):
        self._short = HorizonModel(HORIZON_SHORT, "SHORT_30s")
        self._long = HorizonModel(HORIZON_LONG, "LONG_120s")

        # Feature + close buffer (shared between both models)
        self._feat_buf: deque[np.ndarray] = deque(maxlen=MAX_BARS)
        self._close_buf: deque[float] = deque(maxlen=MAX_BARS)

        # Track each horizon on its own clock.
        # The 120s model must not be judged with a 30s realized return.
        self._pending_short_evals: deque[tuple[int, float, float, float]] = deque(maxlen=500)
        self._pending_long_evals: deque[tuple[int, float, float, float]] = deque(maxlen=500)
        self._short_dir_correct: deque[int] = deque(maxlen=DIR_ACCURACY_WINDOW)
        self._long_dir_correct: deque[int] = deque(maxlen=DIR_ACCURACY_WINDOW)
        self._recent_signed_errors: deque[float] = deque(maxlen=DIR_ACCURACY_WINDOW)
        self._recent_abs_errors: deque[float] = deque(maxlen=DIR_ACCURACY_WINDOW)
        self._short_calibrator = OnlineConfidenceCalibrator()
        self._long_calibrator = OnlineConfidenceCalibrator()
        self._adaptive_fusion_enabled = ENABLE_ADAPTIVE_FUSION
        self._adaptive_fusion_min_margin = ADAPTIVE_FUSION_MIN_MARGIN
        self._adaptive_fusion = (
            AdaptiveDualHorizonFusion() if self._adaptive_fusion_enabled else None
        )
        if self._adaptive_fusion is not None:
            self._adaptive_fusion.BASE_THRESHOLD = ADAPTIVE_FUSION_BASE_THRESHOLD

        # Load pre-trained if available
        if model_path:
            self._load_pretrained(model_path)

    @property
    def trained(self) -> bool:
        return self._short.trained and self._long.trained

    @property
    def either_trained(self) -> bool:
        return self._short.trained or self._long.trained

    @property
    def direction_accuracy(self) -> float:
        acc, _ = self._effective_direction_accuracy(use_short=True, use_long=True)
        return acc

    @property
    def short_direction_accuracy(self) -> float:
        if len(self._short_dir_correct) < MIN_DIR_ACCURACY_SAMPLES:
            return 0.5
        return float(np.mean(list(self._short_dir_correct)))

    @property
    def long_direction_accuracy(self) -> float:
        if len(self._long_dir_correct) < MIN_DIR_ACCURACY_SAMPLES:
            return 0.5
        return float(np.mean(list(self._long_dir_correct)))

    @property
    def short_direction_samples(self) -> int:
        return len(self._short_dir_correct)

    @property
    def long_direction_samples(self) -> int:
        return len(self._long_dir_correct)

    @property
    def direction_samples(self) -> int:
        return self.short_direction_samples + self.long_direction_samples

    @property
    def short_calibration_samples(self) -> int:
        return self._short_calibrator.samples

    @property
    def long_calibration_samples(self) -> int:
        return self._long_calibrator.samples

    @property
    def calibration_samples(self) -> int:
        return self.short_calibration_samples + self.long_calibration_samples

    @property
    def val_accuracy(self) -> float:
        """Average validation accuracy of both models."""
        if self._short.trained and self._long.trained:
            return (self._short.val_accuracy + self._long.val_accuracy) / 2
        if self._short.trained:
            return self._short.val_accuracy
        if self._long.trained:
            return self._long.val_accuracy
        return 0.0

    @property
    def val_accuracy_safe(self) -> float:
        if not self.either_trained:
            return 0.0
        return self.val_accuracy

    def companion_readiness(
        self,
        *,
        min_direction_accuracy: float = COMPANION_MIN_DIR_ACC,
        min_direction_samples: int = COMPANION_MIN_DIR_SAMPLES,
        min_calibration_samples: int = COMPANION_MIN_CALIBRATION_SAMPLES,
    ) -> dict[str, object]:
        return self._build_readiness(
            use_short=True,
            use_long=True,
            min_direction_accuracy=min_direction_accuracy,
            min_direction_samples=min_direction_samples,
            min_calibration_samples=min_calibration_samples,
        )

    def short_companion_readiness(
        self,
        *,
        min_direction_accuracy: float = COMPANION_MIN_DIR_ACC,
        min_direction_samples: int = COMPANION_MIN_DIR_SAMPLES,
        min_calibration_samples: int = COMPANION_MIN_CALIBRATION_SAMPLES,
    ) -> dict[str, object]:
        return self._build_readiness(
            use_short=True,
            use_long=False,
            min_direction_accuracy=min_direction_accuracy,
            min_direction_samples=min_direction_samples,
            min_calibration_samples=min_calibration_samples,
        )

    def long_companion_readiness(
        self,
        *,
        min_direction_accuracy: float = COMPANION_MIN_DIR_ACC,
        min_direction_samples: int = COMPANION_MIN_DIR_SAMPLES,
        min_calibration_samples: int = COMPANION_MIN_CALIBRATION_SAMPLES,
    ) -> dict[str, object]:
        return self._build_readiness(
            use_short=False,
            use_long=True,
            min_direction_accuracy=min_direction_accuracy,
            min_direction_samples=min_direction_samples,
            min_calibration_samples=min_calibration_samples,
        )

    def _build_readiness(
        self,
        *,
        use_short: bool,
        use_long: bool,
        min_direction_accuracy: float,
        min_direction_samples: int,
        min_calibration_samples: int,
    ) -> dict[str, object]:
        dir_acc, dir_samples = self._effective_direction_accuracy(
            use_short=use_short,
            use_long=use_long,
        )
        calibration_samples = 0
        if use_short:
            calibration_samples += self.short_calibration_samples
        if use_long:
            calibration_samples += self.long_calibration_samples
        reasons: list[str] = []

        if not self.either_trained:
            reasons.append("no_trained_model")
        if dir_samples < min_direction_samples:
            reasons.append("low_direction_samples")
        if calibration_samples < min_calibration_samples:
            reasons.append("low_calibration_samples")
        if dir_acc < min_direction_accuracy:
            reasons.append("low_direction_accuracy")

        sample_factor = min(
            1.0,
            dir_samples / max(float(min_direction_samples), 1.0),
            calibration_samples / max(float(min_calibration_samples), 1.0),
        )
        quality_span = max(min_direction_accuracy - 0.5, 1e-6)
        quality_factor = max(0.0, min(1.0, (dir_acc - 0.5) / quality_span))
        utility_score = round(sample_factor * quality_factor, 3)

        return {
            "ready": not reasons,
            "reasons": reasons,
            "utility_score": utility_score,
            "direction_accuracy": float(dir_acc),
            "direction_samples": int(dir_samples),
            "calibration_samples": int(calibration_samples),
        }

    def buffer_size(self) -> int:
        return len(self._feat_buf)

    def estimate_event_intensity(self, features: np.ndarray, horizon: int | None = None) -> float:
        frame = pd.DataFrame([features], columns=FEATURE_NAMES).astype(np.float32)
        horizon_value = horizon or self._long.horizon
        return float(_event_weight_from_frame(frame, horizon_value)[0])

    def _effective_direction_accuracy(
        self, *, use_short: bool, use_long: bool
    ) -> tuple[float, int]:
        metrics: list[tuple[float, float, int]] = []

        if use_short and self.short_direction_samples >= MIN_DIR_ACCURACY_SAMPLES:
            metrics.append((0.4, self.short_direction_accuracy, self.short_direction_samples))
        if use_long and self.long_direction_samples >= MIN_DIR_ACCURACY_SAMPLES:
            metrics.append((0.6, self.long_direction_accuracy, self.long_direction_samples))

        if not metrics:
            sample_count = 0
            if use_short:
                sample_count += self.short_direction_samples
            if use_long:
                sample_count += self.long_direction_samples
            return 0.5, sample_count

        total_weight = sum(weight for weight, _, _ in metrics)
        accuracy = sum(weight * acc for weight, acc, _ in metrics) / max(total_weight, 1e-9)
        sample_count = sum(samples for _, _, samples in metrics)
        return float(accuracy), sample_count

    def _effective_meta_probability(
        self,
        *,
        use_short: bool,
        use_long: bool,
        short_conf: float,
        long_conf: float,
    ) -> tuple[float, int]:
        metrics: list[tuple[float, float, int]] = []

        if use_short:
            metrics.append(
                (0.4, self._short_calibrator.calibrate(short_conf), self.short_calibration_samples)
            )
        if use_long:
            metrics.append(
                (0.6, self._long_calibrator.calibrate(long_conf), self.long_calibration_samples)
            )

        if not metrics:
            return 0.5, 0

        total_weight = sum(weight for weight, _, _ in metrics)
        probability = sum(weight * prob for weight, prob, _ in metrics) / max(total_weight, 1e-9)
        sample_count = sum(samples for _, _, samples in metrics)
        return float(probability), sample_count

    def _min_bps_for_volatility(self, volatility_state: str) -> float:
        if volatility_state == "HIGH":
            return HIGH_VOL_MIN_BPS
        if volatility_state == "LOW":
            return LOW_VOL_MIN_BPS
        return BASE_MIN_BPS

    def _effective_horizon_weights(
        self,
        *,
        regime: str,
        use_short: bool,
        use_long: bool,
    ) -> tuple[float, float, float]:
        normalized_regime = _normalize_fusion_regime(regime)
        if self._adaptive_fusion is not None:
            weights = self._adaptive_fusion.compute_effective_weights(normalized_regime)
            drift_score = float(self._adaptive_fusion.estimate_drift_score())
        else:
            base_weights = AdaptiveDualHorizonFusion.REGIME_WEIGHTS.get(
                normalized_regime,
                {"short": 0.5, "long": 0.5},
            )
            short_acc = (
                self.short_direction_accuracy
                if self.short_direction_samples >= MIN_DIR_ACCURACY_SAMPLES
                else 0.5
            )
            long_acc = (
                self.long_direction_accuracy
                if self.long_direction_samples >= MIN_DIR_ACCURACY_SAMPLES
                else 0.5
            )
            short_rel = float(np.clip(1.0 + (short_acc - 0.55) * 3.5, 0.2, 1.35))
            long_rel = float(np.clip(1.0 + (long_acc - 0.55) * 3.5, 0.2, 1.35))
            weights = {
                "short": float(base_weights["short"]) * short_rel,
                "long": float(base_weights["long"]) * long_rel,
            }
            total = max(float(weights["short"]) + float(weights["long"]), 1e-9)
            weights = {
                "short": float(weights["short"]) / total,
                "long": float(weights["long"]) / total,
            }
            disagreement = abs(short_acc - long_acc)
            weakness = max(0.0, 0.58 - min(short_acc, long_acc))
            drift_score = float(np.clip(disagreement * 0.8 + weakness * 1.4, 0.0, 1.0))

        short_weight = float(weights["short"]) if use_short else 0.0
        long_weight = float(weights["long"]) if use_long else 0.0
        total = max(short_weight + long_weight, 1e-9)
        return short_weight / total, long_weight / total, drift_score

    def _estimate_bias_correction_bps(self) -> float:
        if len(self._recent_signed_errors) < POLICY_MIN_ERROR_SAMPLES:
            return 0.0
        bias = float(np.mean(list(self._recent_signed_errors)))
        if abs(bias) < POLICY_BIAS_DEADZONE_BPS:
            return 0.0
        return bias

    def _estimate_uncertainty_bps(self, min_bps: float) -> float:
        samples: list[float]
        if len(self._recent_abs_errors) >= POLICY_MIN_ERROR_SAMPLES:
            samples = list(self._recent_abs_errors)
        elif len(self._recent_signed_errors) >= POLICY_MIN_ERROR_SAMPLES:
            samples = [abs(v) for v in self._recent_signed_errors]
        else:
            return float(max(min_bps * 0.5, 0.25))
        return float(max(0.0, np.percentile(np.asarray(samples, dtype=float), 65)))

    def _calibration_health(self, direction_accuracy: float, meta_probability: float) -> float:
        return float(np.clip(direction_accuracy * 0.65 + meta_probability * 0.35, 0.0, 1.0))

    def _size_multiplier_hint(
        self,
        *,
        edge_net_bps: float,
        uncertainty_bps: float,
        calibration_health: float,
    ) -> float:
        if edge_net_bps <= 0:
            return 0.0
        edge_ratio = min(1.0, edge_net_bps / max(POLICY_MIN_ACTIONABLE_EDGE_BPS * 2.0, 1e-6))
        uncertainty_penalty = max(
            0.35, 1.0 - (uncertainty_bps / max(POLICY_MIN_ACTIONABLE_EDGE_BPS * 3.0, 1e-6)) * 0.5
        )
        calibration_factor = max(0.5, calibration_health)
        return float(
            np.clip((0.45 + 0.55 * edge_ratio) * uncertainty_penalty * calibration_factor, 0.2, 1.0)
        )

    def _allow_add_to_position(
        self,
        *,
        edge_net_bps: float,
        uncertainty_bps: float,
        drift_score: float,
        calibration_health: float,
    ) -> bool:
        return bool(
            edge_net_bps >= max(POLICY_MIN_ACTIONABLE_EDGE_BPS * 1.5, uncertainty_bps * 1.25)
            and drift_score <= 0.45
            and calibration_health >= 0.6
        )

    def _resolve_disagreement_with_fusion(
        self,
        *,
        short_bps: float,
        short_conf: float,
        long_bps: float,
        long_conf: float,
        min_bps: float,
        regime: str,
    ) -> tuple[str, float, float] | None:
        if not self._adaptive_fusion_enabled or self._adaptive_fusion is None:
            return None

        norm = max(min_bps * 2.5, 1e-6)
        short_strength = min(1.0, abs(short_bps) / norm)
        long_strength = min(1.0, abs(long_bps) / norm)
        short_dir = "UP" if short_bps > 0 else "DOWN"
        long_dir = "UP" if long_bps > 0 else "DOWN"

        fused = self._adaptive_fusion.fuse_predictions(
            {
                "direction": short_dir,
                "confidence": short_strength,
                "expected_return_bps": short_bps,
                "raw_confidence": short_conf,
            },
            {
                "direction": long_dir,
                "confidence": long_strength,
                "expected_return_bps": long_bps,
                "raw_confidence": long_conf,
            },
            _normalize_fusion_regime(regime),
        )
        if fused.get("signal") == "HOLD":
            return None

        raw_score = abs(float(fused.get("raw_score", 0.0)))
        if raw_score < self._adaptive_fusion_min_margin:
            return None

        fused_signal = "LONG" if fused.get("signal") == "UP" else "SHORT"
        fused_bps = float(fused.get("expected_return_bps", 0.0))
        if abs(fused_bps) < 1e-6:
            fused_bps = long_bps if abs(long_bps) >= abs(short_bps) else short_bps

        fused_conf_hint = float(fused.get("confidence", 0.0))
        return fused_signal, fused_bps, fused_conf_hint

    @staticmethod
    def _is_meta_success(pred_bps: float, actual_ret: float) -> bool:
        required_move = max(META_MIN_MOVE_BPS, abs(pred_bps) * META_MOVE_PRED_RATIO)
        return np.sign(pred_bps) == np.sign(actual_ret) and abs(actual_ret) >= required_move

    def _queue_horizon_eval(
        self,
        *,
        horizon: int,
        bar_idx: int,
        pred_bps: float,
        close: float,
        raw_conf: float,
    ) -> None:
        if (
            bar_idx <= 0 or close <= 0 or abs(pred_bps) < 0.1
        ):  # V3.5: lowered from BASE_MIN_BPS*0.5 to capture small signals for dir_acc
            return
        if horizon == HORIZON_LONG:
            self._pending_long_evals.append((bar_idx, pred_bps, close, raw_conf))
            return
        self._pending_short_evals.append((bar_idx, pred_bps, close, raw_conf))

    def _evaluate_horizon_queue(
        self,
        *,
        pending: deque[tuple[int, float, float, float]],
        dest: deque[int],
        horizon: int,
        current_bar_idx: int,
        current_close: float,
        calibrator: OnlineConfidenceCalibrator,
    ) -> None:
        while pending:
            bar_idx, pred_bps, pred_close, raw_conf = pending[0]
            bars_elapsed = current_bar_idx - bar_idx
            if bars_elapsed < horizon:
                break
            pending.popleft()
            if pred_close <= 0:
                continue
            actual_ret = self._realized_horizon_return_bps(
                pred_bar_idx=bar_idx,
                pred_close=pred_close,
                horizon=horizon,
                current_bar_idx=current_bar_idx,
                current_close=current_close,
            )
            forecast_error = float(pred_bps - actual_ret)
            self._recent_signed_errors.append(forecast_error)
            self._recent_abs_errors.append(abs(forecast_error))
            if abs(actual_ret) > 0.3:
                correct = int(np.sign(pred_bps) == np.sign(actual_ret))
                dest.append(correct)
                if self._adaptive_fusion is not None:
                    self._adaptive_fusion.update_accuracy_tracking(
                        "long" if horizon == HORIZON_LONG else "short",
                        bool(correct),
                    )
            calibrator.update(raw_conf, self._is_meta_success(pred_bps, actual_ret))

    def _realized_horizon_return_bps(
        self,
        *,
        pred_bar_idx: int,
        pred_close: float,
        horizon: int,
        current_bar_idx: int,
        current_close: float,
    ) -> float:
        """Evaluate realized outcome with the same barrier logic used in training."""
        if pred_close <= 0:
            return 0.0

        closes = list(self._close_buf)
        if closes:
            oldest_bar_idx = current_bar_idx - len(closes) + 1
            start_pos = pred_bar_idx - oldest_bar_idx
            end_pos = start_pos + horizon
            if 0 <= start_pos < len(closes) and end_pos < len(closes):
                tp_bps, sl_bps = _barrier_thresholds_bps(horizon)
                for step in range(start_pos + 1, end_pos + 1):
                    path_price = closes[step]
                    ret_bps = (path_price - pred_close) / pred_close * 10000
                    if ret_bps >= tp_bps:
                        return tp_bps
                    if ret_bps <= -sl_bps:
                        return -sl_bps
                return (closes[end_pos] - pred_close) / pred_close * 10000

        # Fallback when the historical slice is no longer available in the live buffer.
        return (current_close - pred_close) / pred_close * 10000

    def store(self, features: np.ndarray, close: float) -> None:
        self._feat_buf.append(features.copy())
        self._close_buf.append(close)
        self._short.tick()
        self._long.tick()

    def should_retrain(self) -> bool:
        n = len(self._feat_buf)
        return self._short.should_retrain(n) or self._long.should_retrain(n)

    def retrain(self) -> None:
        """Retrain whichever model(s) need it."""
        feats = list(self._feat_buf)
        closes = list(self._close_buf)
        n = len(feats)

        if self._short.should_retrain(n):
            self._short.retrain(feats, closes)

        if self._long.should_retrain(n):
            self._long.retrain(feats, closes)

    def predict_with_policy(
        self,
        features: np.ndarray,
        volatility_state: str = "MEDIUM",
        bar_idx: int = 0,
        close: float = 0.0,
        regime: str = "RANGING",
    ) -> dict[str, object]:
        min_bps = self._min_bps_for_volatility(volatility_state)
        default_policy = {
            "signal": "HOLD",
            "pred_bps": 0.0,
            "expected_bps": 0.0,
            "confidence": 0.0,
            "uncertainty_bps": 0.0,
            "bias_correction_bps": 0.0,
            "edge_net_bps": 0.0,
            "actionable_edge_bps": 0.0,
            "calibration_health": 0.0,
            "fast_weight": 0.5,
            "slow_weight": 0.5,
            "drift_score": 0.0,
            "allow_execute": False,
            "allow_add_to_position": False,
            "size_multiplier_hint": 0.0,
            "require_reversal_confirmation": True,
            "source": "none",
            "event_intensity": self.estimate_event_intensity(features),
        }
        if not self.either_trained:
            return default_policy

        short_bps, short_conf = self._short.predict(features)
        long_bps, long_conf = self._long.predict(features)
        short_dir = (
            np.sign(short_bps) if self._short.trained and abs(short_bps) >= min_bps * 0.5 else 0
        )
        long_dir = np.sign(long_bps) if self._long.trained and abs(long_bps) >= min_bps * 0.5 else 0
        event_intensity = self.estimate_event_intensity(features)

        use_short = False
        use_long = False
        signal = "HOLD"
        expected_bps = 0.0
        source = "none"

        short_weight, long_weight, drift_score = self._effective_horizon_weights(
            regime=regime,
            use_short=short_dir != 0,
            use_long=long_dir != 0,
        )

        if short_dir == 0 and long_dir == 0:
            pass
        elif short_dir != 0 and long_dir != 0 and short_dir == long_dir:
            use_short = True
            use_long = True
            signal = "LONG" if short_dir > 0 else "SHORT"
            expected_bps = short_bps * short_weight + long_bps * long_weight
            source = "consensus"
        elif short_dir != 0 and long_dir == 0:
            use_short = True
            signal = "LONG" if short_dir > 0 else "SHORT"
            expected_bps = short_bps
            source = "short_only"
            short_weight, long_weight = 1.0, 0.0
        elif long_dir != 0 and short_dir == 0:
            use_long = True
            signal = "LONG" if long_dir > 0 else "SHORT"
            expected_bps = long_bps
            source = "long_only"
            short_weight, long_weight = 0.0, 1.0
        elif short_dir != 0 and long_dir != 0:
            if self._adaptive_fusion_enabled and self._adaptive_fusion is not None:
                fused = self._resolve_disagreement_with_fusion(
                    short_bps=short_bps,
                    short_conf=short_conf,
                    long_bps=long_bps,
                    long_conf=long_conf,
                    min_bps=min_bps,
                    regime=regime,
                )
                if fused is not None:
                    signal, expected_bps, _ = fused
                    use_short = True
                    use_long = True
                    source = "fused"
            else:
                short_strength = abs(short_bps) * short_weight
                long_strength = abs(long_bps) * long_weight
                if short_strength >= long_strength * 1.35:
                    use_short = True
                    signal = "LONG" if short_dir > 0 else "SHORT"
                    expected_bps = short_bps
                    source = "short_only"
                    short_weight, long_weight = 1.0, 0.0
                elif long_strength >= short_strength * 1.35:
                    use_long = True
                    signal = "LONG" if long_dir > 0 else "SHORT"
                    expected_bps = long_bps
                    source = "long_only"
                    short_weight, long_weight = 0.0, 1.0

        if self._short.trained:
            self._queue_horizon_eval(
                horizon=self._short.horizon,
                bar_idx=bar_idx,
                pred_bps=short_bps,
                close=close,
                raw_conf=short_conf,
            )
        if self._long.trained:
            self._queue_horizon_eval(
                horizon=self._long.horizon,
                bar_idx=bar_idx,
                pred_bps=long_bps,
                close=close,
                raw_conf=long_conf,
            )

        if signal == "HOLD" or abs(expected_bps) < min_bps:
            return {
                **default_policy,
                "fast_weight": float(short_weight),
                "slow_weight": float(long_weight),
                "drift_score": float(drift_score),
                "event_intensity": float(event_intensity),
            }

        dir_acc, dir_samples = self._effective_direction_accuracy(
            use_short=use_short, use_long=use_long
        )
        meta_prob, meta_samples = self._effective_meta_probability(
            use_short=use_short,
            use_long=use_long,
            short_conf=short_conf,
            long_conf=long_conf,
        )

        if dir_acc < 0.38 and dir_samples >= 30:
            return {
                **default_policy,
                "pred_bps": round(expected_bps, 2),
                "expected_bps": round(expected_bps, 2),
                "fast_weight": float(short_weight),
                "slow_weight": float(long_weight),
                "drift_score": float(drift_score),
                "event_intensity": float(event_intensity),
            }
        if meta_prob < 0.35 and meta_samples >= 20:
            return {
                **default_policy,
                "pred_bps": round(expected_bps, 2),
                "expected_bps": round(expected_bps, 2),
                "fast_weight": float(short_weight),
                "slow_weight": float(long_weight),
                "drift_score": float(drift_score),
                "event_intensity": float(event_intensity),
            }

        agreement_bonus = 0.06 if source in {"consensus", "fused"} else 0.0
        dir_component = max(0.0, (dir_acc - 0.48) * 1.2)
        mag_component = min(0.15, abs(expected_bps) / 20.0)
        val_component = max(0.0, (self.val_accuracy - 0.52) * 0.5)
        base_confidence = min(
            0.90, 0.48 + dir_component + mag_component + val_component + agreement_bonus
        )
        if dir_samples < 20:
            base_confidence = min(base_confidence, 0.55)
        calibration_factor = min(1.10, max(0.55, 0.90 + (meta_prob - 0.5) * 1.6))
        event_factor = min(1.08, max(0.72, 0.84 + (event_intensity - 1.0) * 0.28))
        confidence = min(0.90, base_confidence * calibration_factor * event_factor)

        bias_correction_bps = self._estimate_bias_correction_bps()
        uncertainty_bps = self._estimate_uncertainty_bps(min_bps)
        corrected_expected_bps = expected_bps - bias_correction_bps
        edge_net_bps = abs(corrected_expected_bps) - uncertainty_bps - POLICY_FEE_BUFFER_BPS
        calibration_health = self._calibration_health(dir_acc, meta_prob)
        allow_execute = bool(
            edge_net_bps >= POLICY_MIN_ACTIONABLE_EDGE_BPS
            and calibration_health >= POLICY_MIN_CALIBRATION_HEALTH
            and confidence > 0.0
        )
        size_multiplier_hint = (
            self._size_multiplier_hint(
                edge_net_bps=edge_net_bps,
                uncertainty_bps=uncertainty_bps,
                calibration_health=calibration_health,
            )
            if allow_execute
            else 0.0
        )

        return {
            "signal": signal,
            "pred_bps": round(expected_bps, 2),
            "expected_bps": round(expected_bps, 2),
            "confidence": round(confidence, 3),
            "uncertainty_bps": round(uncertainty_bps, 3),
            "bias_correction_bps": round(bias_correction_bps, 3),
            "edge_net_bps": round(edge_net_bps, 3),
            "actionable_edge_bps": round(max(edge_net_bps, 0.0), 3),
            "calibration_health": round(calibration_health, 3),
            "fast_weight": round(short_weight, 3),
            "slow_weight": round(long_weight, 3),
            "drift_score": round(drift_score, 3),
            "allow_execute": allow_execute,
            "allow_add_to_position": self._allow_add_to_position(
                edge_net_bps=edge_net_bps,
                uncertainty_bps=uncertainty_bps,
                drift_score=drift_score,
                calibration_health=calibration_health,
            )
            if allow_execute
            else False,
            "size_multiplier_hint": round(size_multiplier_hint, 3),
            "require_reversal_confirmation": bool(
                uncertainty_bps
                > max(POLICY_MIN_ACTIONABLE_EDGE_BPS, abs(corrected_expected_bps) * 0.6)
                or calibration_health < 0.62
            ),
            "source": source,
            "event_intensity": float(round(event_intensity, 3)),
        }

    def predict(
        self,
        features: np.ndarray,
        volatility_state: str = "MEDIUM",
        bar_idx: int = 0,
        close: float = 0.0,
        regime: str = "RANGING",
    ) -> tuple[str, float, float]:
        """
        Dual-horizon prediction with calibrated confidence.

        bar_idx/close: used to auto-queue raw predictions for direction
        accuracy tracking (fixes the vicious HOLD cycle where dir_acc
        never updates because we only queued non-HOLD signals).

        Returns:
            (signal: "LONG"|"SHORT"|"HOLD",
             pred_bps: float,
             confidence: float calibrated for Fenix companion)
        """
        if not self.either_trained:
            return "HOLD", 0.0, 0.0

        # Get predictions from both horizons
        short_bps, short_conf = self._short.predict(features)
        long_bps, long_conf = self._long.predict(features)

        # DEBUG: log raw predictions every 30 bars
        if bar_idx % 30 == 0:
            logger.info(
                "\U0001f4ca RAW_PRED #%d | short_bps=%.2f short_conf=%.3f | long_bps=%.2f long_conf=%.3f | vol=%s regime=%s",
                bar_idx,
                short_bps,
                short_conf,
                long_bps,
                long_conf,
                volatility_state,
                regime,
            )

        # Adaptive threshold based on volatility
        if volatility_state == "HIGH":
            min_bps = HIGH_VOL_MIN_BPS
        elif volatility_state == "LOW":
            min_bps = LOW_VOL_MIN_BPS
        else:
            min_bps = BASE_MIN_BPS
        event_intensity = self.estimate_event_intensity(features)

        # If only one model is trained, use it alone
        if not self._long.trained:
            return self._single_model_signal(
                short_bps, short_conf, min_bps, self._short.horizon, bar_idx, close
            )
        if not self._short.trained:
            return self._single_model_signal(
                long_bps, long_conf, min_bps, self._long.horizon, bar_idx, close
            )

        # ── Dual-model consensus / adaptive fusion ──
        short_dir = np.sign(short_bps) if abs(short_bps) >= min_bps * 0.5 else 0
        long_dir = np.sign(long_bps) if abs(long_bps) >= min_bps * 0.5 else 0
        fusion_conf_hint = 0.0

        # Both HOLD → HOLD
        if short_dir == 0 and long_dir == 0:
            return "HOLD", 0.0, 0.0

        if short_dir != 0 and long_dir != 0 and short_dir != long_dir:
            fused = self._resolve_disagreement_with_fusion(
                short_bps=short_bps,
                short_conf=short_conf,
                long_bps=long_bps,
                long_conf=long_conf,
                min_bps=min_bps,
                regime=regime,
            )
            if fused is None:
                return "HOLD", 0.0, 0.0
            signal, blend_bps, fusion_conf_hint = fused
            direction = 1 if signal == "LONG" else -1
            agreement_bonus = -0.01
        elif short_dir != 0 and long_dir != 0:
            # Both agree — high quality signal
            agreement_bonus = 0.08
            direction = short_dir
            signal = "LONG" if direction > 0 else "SHORT"
            blend_bps = short_bps * 0.4 + long_bps * 0.6
        elif short_dir != 0:
            # Only short has signal — lower confidence
            agreement_bonus = 0.0
            direction = short_dir
            signal = "LONG" if direction > 0 else "SHORT"
            blend_bps = short_bps * 0.4 + long_bps * 0.6
        else:
            # Only long has signal
            agreement_bonus = 0.02
            direction = long_dir
            signal = "LONG" if direction > 0 else "SHORT"
            blend_bps = short_bps * 0.4 + long_bps * 0.6

        # Queue each model on its own horizon clock.
        self._queue_horizon_eval(
            horizon=self._short.horizon,
            bar_idx=bar_idx,
            pred_bps=short_bps,
            close=close,
            raw_conf=short_conf,
        )
        self._queue_horizon_eval(
            horizon=self._long.horizon,
            bar_idx=bar_idx,
            pred_bps=long_bps,
            close=close,
            raw_conf=long_conf,
        )

        # Final threshold check
        if abs(blend_bps) < min_bps:
            return "HOLD", 0.0, 0.0

        # ── V3.1 Confidence calibration (redesigned) ──
        # KEY FIX: Direction accuracy GATES confidence.
        # If we're predicting worse than random, confidence must stay low
        # regardless of prediction magnitude or val accuracy.
        dir_acc, dir_samples = self._effective_direction_accuracy(
            use_short=short_dir != 0,
            use_long=long_dir != 0,
        )
        meta_prob, meta_samples = self._effective_meta_probability(
            use_short=short_dir != 0,
            use_long=long_dir != 0,
            short_conf=short_conf,
            long_conf=long_conf,
        )

        # HARD GATE: if dir accuracy < 38%, emit HOLD (V3.5: lowered from 45%)
        # But still expose pred_bps so companion file shows the raw prediction
        if dir_acc < 0.38 and dir_samples >= 30:
            return "HOLD", round(blend_bps, 2), 0.0
        if meta_prob < 0.35 and meta_samples >= 20:
            return "HOLD", round(blend_bps, 2), 0.0

        # Components (redesigned with dir_acc as primary driver):
        # 1. Direction accuracy is the main signal quality indicator
        # 2. Magnitude is secondary (strong predictions only matter if accurate)
        # 3. Agreement bonus only if both models trained

        # Dir accuracy component: 0 at 50%, scales up from there
        dir_component = max(0.0, (dir_acc - 0.48) * 1.2)  # 0 at 48%, 0.14 at 60%

        # Magnitude component: capped lower to prevent overconfidence on big wrong predictions
        mag_component = min(0.15, abs(blend_bps) / 20.0)

        # Val accuracy: minimal influence (can be misleading with autocorrelated data)
        val_acc = self.val_accuracy
        val_component = max(0.0, (val_acc - 0.52) * 0.5)  # 0 at 52%

        base_confidence = min(
            0.90,  # Cap at 90% (was 95% — overconfident)
            0.48 + dir_component + mag_component + val_component + agreement_bonus,
        )

        # Extra penalty if we have no dir_acc data yet (< 20 samples)
        if dir_samples < 20:
            base_confidence = min(base_confidence, 0.55)  # Uncertain → conservative

        calibration_factor = min(1.10, max(0.55, 0.90 + (meta_prob - 0.5) * 1.6))
        event_factor = min(1.08, max(0.72, 0.84 + (event_intensity - 1.0) * 0.28))
        fusion_factor = 1.0
        if fusion_conf_hint > 0:
            fusion_factor = min(1.08, max(0.78, 0.82 + fusion_conf_hint * 0.35))
        confidence = min(0.90, base_confidence * calibration_factor * event_factor * fusion_factor)
        return signal, round(blend_bps, 2), round(confidence, 3)

    def queue_direction_eval(
        self,
        bar_idx: int,
        pred_bps: float,
        close: float,
        horizon: int = HORIZON_SHORT,
        raw_conf: float = 0.5,
    ) -> None:
        """Queue a prediction for deferred direction accuracy evaluation."""
        self._queue_horizon_eval(
            horizon=horizon,
            bar_idx=bar_idx,
            pred_bps=pred_bps,
            close=close,
            raw_conf=raw_conf,
        )

    def evaluate_direction(self, current_bar_idx: int, current_close: float) -> None:
        """Evaluate each model at the horizon it actually targets."""
        self._evaluate_horizon_queue(
            pending=self._pending_short_evals,
            dest=self._short_dir_correct,
            horizon=HORIZON_SHORT,
            current_bar_idx=current_bar_idx,
            current_close=current_close,
            calibrator=self._short_calibrator,
        )
        self._evaluate_horizon_queue(
            pending=self._pending_long_evals,
            dest=self._long_dir_correct,
            horizon=HORIZON_LONG,
            current_bar_idx=current_bar_idx,
            current_close=current_close,
            calibrator=self._long_calibrator,
        )

    def _single_model_signal(
        self,
        pred_bps: float,
        conf: float,
        min_bps: float,
        horizon: int,
        bar_idx: int = 0,
        close: float = 0.0,
    ) -> tuple[str, float, float]:
        """Signal from a single model — uses same V3.1 calibration."""

        # Always queue raw prediction for direction tracking
        self._queue_horizon_eval(
            horizon=horizon,
            bar_idx=bar_idx,
            pred_bps=pred_bps,
            close=close,
            raw_conf=conf,
        )

        if abs(pred_bps) < min_bps:
            return "HOLD", 0.0, 0.0
        signal = "LONG" if pred_bps > 0 else "SHORT"

        # Use V3.1 calibration with dir_acc as primary driver
        use_short = horizon == HORIZON_SHORT
        dir_acc, dir_samples = self._effective_direction_accuracy(
            use_short=use_short,
            use_long=not use_short,
        )
        calibrator = self._short_calibrator if use_short else self._long_calibrator
        meta_prob = calibrator.calibrate(conf)
        meta_samples = calibrator.samples

        # HARD GATE: if dir accuracy < 38% with enough samples, emit HOLD (V3.5: lowered from 45%)
        if dir_acc < 0.38 and dir_samples >= 30:
            return "HOLD", 0.0, 0.0
        if meta_prob < 0.35 and meta_samples >= 20:
            return "HOLD", 0.0, 0.0

        # Dir accuracy component
        dir_component = max(0.0, (dir_acc - 0.48) * 1.2)
        # Magnitude: capped low
        mag_component = min(0.15, abs(pred_bps) / 20.0)
        # Val accuracy: minimal influence
        val_acc = max(self._short.val_accuracy, self._long.val_accuracy)
        val_component = max(0.0, (val_acc - 0.52) * 0.5)

        # Single model → lower cap than dual (0.72 vs 0.90)
        base_confidence = min(
            0.72,
            0.48 + dir_component + mag_component + val_component,
        )

        # Extra penalty if we have no dir_acc data yet (< 20 samples)
        if dir_samples < 20:
            base_confidence = min(base_confidence, 0.55)  # Uncertain → conservative

        event_intensity = self.estimate_event_intensity(
            np.asarray(self._feat_buf[-1], dtype=np.float32)
            if self._feat_buf
            else np.zeros(len(FEATURE_NAMES), dtype=np.float32),
            horizon=horizon,
        )
        calibration_factor = min(1.10, max(0.55, 0.90 + (meta_prob - 0.5) * 1.6))
        event_factor = min(1.08, max(0.72, 0.84 + (event_intensity - 1.0) * 0.28))
        confidence = min(0.72, base_confidence * calibration_factor * event_factor)

        return signal, round(pred_bps, 2), round(confidence, 3)

    def _load_pretrained(self, model_path: str) -> None:
        p = Path(model_path)
        if not p.exists():
            logger.warning(f"Pre-trained model not found: {model_path}")
            return
        try:
            with open(p, "rb") as f:
                data = pickle.load(f)
            if "short_model" in data:
                self._short._model = data["short_model"]
                self._short._trained = True
                self._short._last_val_acc = data.get("short_val_acc", 0.55)
            if "long_model" in data:
                self._long._model = data["long_model"]
                self._long._trained = True
                self._long._last_val_acc = data.get("long_val_acc", 0.55)
            self._short._last_mae = float(data.get("short_mae", self._short._last_mae))
            self._long._last_mae = float(data.get("long_mae", self._long._last_mae))
            self._short._last_train_acc = float(
                data.get("short_train_acc", self._short._last_train_acc)
            )
            self._long._last_train_acc = float(
                data.get("long_train_acc", self._long._last_train_acc)
            )
            self._short._last_train_samples = int(
                data.get("short_train_samples", self._short._last_train_samples)
            )
            self._long._last_train_samples = int(
                data.get("long_train_samples", self._long._last_train_samples)
            )
            # Buffer warmup
            for feat in data.get("buffer_features", []):
                self._feat_buf.append(feat)
            for c in data.get("buffer_closes", []):
                self._close_buf.append(c)
            self._short_dir_correct.extend(
                int(v) for v in data.get("short_dir_correct", [])[-DIR_ACCURACY_WINDOW:]
            )
            self._long_dir_correct.extend(
                int(v) for v in data.get("long_dir_correct", [])[-DIR_ACCURACY_WINDOW:]
            )
            self._short_calibrator.restore_state(data.get("short_calibrator"))
            self._long_calibrator.restore_state(data.get("long_calibrator"))
            self._recent_signed_errors.extend(
                float(v) for v in data.get("recent_signed_errors", [])[-DIR_ACCURACY_WINDOW:]
            )
            self._recent_abs_errors.extend(
                float(v) for v in data.get("recent_abs_errors", [])[-DIR_ACCURACY_WINDOW:]
            )
            self._sync_adaptive_fusion_history()
            logger.info(
                f"📦 Pre-trained V3 model loaded | "
                f"short_acc={self._short.val_accuracy * 100:.1f}% "
                f"long_acc={self._long.val_accuracy * 100:.1f}% | "
                f"buffer={len(self._feat_buf)} bars"
            )
        except Exception as e:
            logger.warning(f"Failed to load pre-trained model: {e}")

    def _sync_adaptive_fusion_history(self) -> None:
        if self._adaptive_fusion is None:
            return
        self._adaptive_fusion.short_accuracy_history.clear()
        self._adaptive_fusion.long_accuracy_history.clear()
        self._adaptive_fusion.short_accuracy_history.extend(self._short_dir_correct)
        self._adaptive_fusion.long_accuracy_history.extend(self._long_dir_correct)

    def save_model(self, path: str) -> None:
        """Save both models and buffer for warmup."""
        try:
            data = {
                "short_model": self._short._model,
                "long_model": self._long._model,
                "short_val_acc": self._short.val_accuracy,
                "long_val_acc": self._long.val_accuracy,
                "short_mae": self._short._last_mae,
                "long_mae": self._long._last_mae,
                "short_train_acc": self._short._last_train_acc,
                "long_train_acc": self._long._last_train_acc,
                "short_train_samples": self._short._last_train_samples,
                "long_train_samples": self._long._last_train_samples,
                "buffer_features": list(self._feat_buf)[-500:],
                "buffer_closes": list(self._close_buf)[-500:],
                "short_dir_correct": list(self._short_dir_correct),
                "long_dir_correct": list(self._long_dir_correct),
                "short_calibrator": self._short_calibrator.export_state(),
                "long_calibrator": self._long_calibrator.export_state(),
                "recent_signed_errors": list(self._recent_signed_errors),
                "recent_abs_errors": list(self._recent_abs_errors),
            }
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump(data, f)
            logger.info(f"💾 Model saved to {path}")
        except Exception as e:
            logger.warning(f"Failed to save model: {e}")
