"""
Adaptive Dual-Horizon Fusion System for NanoFenix v3.5
Based on: "Learning Fast and Slow for Online Time Series Forecasting" (Pham et al., 2022)
"""

from collections import deque
from typing import Literal

import numpy as np


class AdaptiveDualHorizonFusion:
    """
    Adaptive fusion system for multi-horizon predictions.

    Replaces rigid 100% agreement consensus with weighted fusion that
    adapts short/long horizon weights by market regime.
    """

    # Weights by market regime.
    REGIME_WEIGHTS = {
        "TRENDING": {"short": 0.35, "long": 0.65},
        "CHOP": {"short": 0.70, "long": 0.30},
        "DEAD": {"short": 0.50, "long": 0.50},
        "VOLATILE": {"short": 0.60, "long": 0.40},
    }

    BASE_THRESHOLD = 0.35  # V3.5: more permissive threshold (was 0.52).
    MIN_HISTORY = 20

    def __init__(self):
        self.short_accuracy_history = deque(maxlen=200)
        self.long_accuracy_history = deque(maxlen=200)

    def _direction_to_value(self, direction: str) -> int:
        """Convert a direction label into a numeric value."""
        mapping = {"UP": 1, "DOWN": -1, "HOLD": 0}
        return mapping.get(direction, 0)

    def _value_to_direction(self, value: float) -> str:
        """Convert a numeric value into a direction label."""
        if value > 0.1:
            return "UP"
        elif value < -0.1:
            return "DOWN"
        return "HOLD"

    def _reliability_scale(self, horizon: str) -> float:
        history = self.short_accuracy_history if horizon == "short" else self.long_accuracy_history
        if len(history) < self.MIN_HISTORY:
            return 1.0
        accuracy = float(np.mean(history))
        return float(np.clip(1.0 + (accuracy - 0.55) * 3.5, 0.2, 1.35))

    def compute_effective_weights(
        self, market_regime: Literal["TRENDING", "CHOP", "DEAD", "VOLATILE"]
    ) -> dict[str, float]:
        weights = self.REGIME_WEIGHTS.get(market_regime, {"short": 0.5, "long": 0.5})
        short_weight = float(weights["short"]) * self._reliability_scale("short")
        long_weight = float(weights["long"]) * self._reliability_scale("long")
        total = max(short_weight + long_weight, 1e-8)
        return {
            "short": short_weight / total,
            "long": long_weight / total,
        }

    def estimate_drift_score(self) -> float:
        short_acc = float(self.get_recent_accuracy("short"))
        long_acc = float(self.get_recent_accuracy("long"))
        disagreement = abs(short_acc - long_acc)
        weakness = max(0.0, 0.58 - min(short_acc, long_acc))
        return float(np.clip(disagreement * 0.8 + weakness * 1.4, 0.0, 1.0))

    def fuse_predictions(
        self,
        short_pred: dict,
        long_pred: dict,
        market_regime: Literal["TRENDING", "CHOP", "DEAD", "VOLATILE"],
    ) -> dict:
        """
        Fuse two horizon predictions with adaptive weights.

        Args:
            short_pred: Short-horizon prediction (30s).
            long_pred: Long-horizon prediction (120s).
            market_regime: Current market regime.

        Returns:
            dict: Fused signal with calibrated confidence.
        """
        # 1. Get weights for the current regime.
        weights = self.REGIME_WEIGHTS.get(market_regime, {"short": 0.5, "long": 0.5})

        # 2. Convert direction labels into numeric values.
        short_dir = self._direction_to_value(short_pred.get("direction", "HOLD"))
        long_dir = self._direction_to_value(long_pred.get("direction", "HOLD"))

        # 3. Calculate weighted scores.
        short_conf = short_pred.get("confidence", 0.5)
        long_conf = long_pred.get("confidence", 0.5)

        short_reliability = self._reliability_scale("short")
        long_reliability = self._reliability_scale("long")
        short_weight = weights["short"] * short_reliability
        long_weight = weights["long"] * long_reliability

        short_score = short_dir * short_conf * short_weight
        long_score = long_dir * long_conf * long_weight

        # 4. Combined score.
        combined_score = short_score + long_score
        total_strength = abs(short_conf * short_weight) + abs(long_conf * long_weight)
        normalized_score = combined_score / max(total_strength, 1e-8)

        # 5. Check threshold.
        if abs(normalized_score) < self.BASE_THRESHOLD:
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "expected_return_bps": 0.0,
                "reason": "below_threshold",
                "raw_score": normalized_score,
            }

        # 6. Calculate final direction.
        direction = self._value_to_direction(normalized_score)

        # 7. Calculate weighted expected return.
        short_return = short_pred.get("expected_return_bps", 0)
        long_return = long_pred.get("expected_return_bps", 0)
        total_weight = max(short_weight + long_weight, 1e-8)
        weighted_return = (short_return * short_weight + long_return * long_weight) / total_weight

        # 8. Calibrate confidence.
        calibrated_conf = min(0.95, abs(normalized_score) * 1.2)

        return {
            "signal": direction,
            "confidence": calibrated_conf,
            "expected_return_bps": weighted_return,
            "regime": market_regime,
            "weights_used": weights,
            "effective_weights": {
                "short": short_weight,
                "long": long_weight,
            },
            "raw_score": normalized_score,
            "drift_score": self.estimate_drift_score(),
        }

    def update_accuracy_tracking(self, horizon: str, was_correct: bool):
        """Update per-horizon accuracy tracking."""
        if horizon == "short":
            self.short_accuracy_history.append(1.0 if was_correct else 0.0)
        else:
            self.long_accuracy_history.append(1.0 if was_correct else 0.0)

    def get_recent_accuracy(self, horizon: str) -> float:
        """Return recent accuracy for one horizon."""
        history = self.short_accuracy_history if horizon == "short" else self.long_accuracy_history
        if not history:
            return 0.5
        return np.mean(history)


class AssociativeMemory:
    """
    Recurrent pattern memory for improving predictions.

    Implements retrieval of similar historical patterns.
    """

    def __init__(self, max_size: int = 1000, similarity_threshold: float = 0.85):
        self.patterns = []
        self.outcomes = []
        self.max_size = max_size
        self.similarity_threshold = similarity_threshold

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return np.dot(a, b) / (norm_a * norm_b)

    def store(self, pattern_features: np.ndarray, outcome: dict):
        """Store a pattern with its outcome."""
        self.patterns.append(pattern_features.copy())
        self.outcomes.append(outcome)

        # FIFO eviction when the memory exceeds its size limit.
        if len(self.patterns) > self.max_size:
            self.patterns.pop(0)
            self.outcomes.pop(0)

    def retrieve_similar(self, current_features: np.ndarray, k: int = 5) -> list:
        """Retrieve the k most similar patterns."""
        if len(self.patterns) < k:
            return []

        similarities = [self._cosine_similarity(current_features, p) for p in self.patterns]

        top_k_idx = np.argsort(similarities)[-k:]

        return [
            {"similarity": similarities[i], "outcome": self.outcomes[i]}
            for i in top_k_idx
            if similarities[i] > self.similarity_threshold
        ]

    def get_expected_outcome(self, current_features: np.ndarray) -> dict:
        """
        Predict the expected outcome from similar past patterns.
        """
        similar = self.retrieve_similar(current_features)
        if not similar:
            return None

        # Weight outcomes by similarity.
        total_weight = sum(s["similarity"] for s in similar)

        weighted_return = (
            sum(s["outcome"].get("actual_return_bps", 0) * s["similarity"] for s in similar)
            / total_weight
        )

        profitable_count = sum(1 for s in similar if s["outcome"].get("was_profitable", False))

        return {
            "expected_return_bps": weighted_return,
            "probability_profitable": profitable_count / len(similar),
            "num_similar_patterns": len(similar),
            "avg_similarity": total_weight / len(similar),
        }


class OnlineConfidenceCalibrator:
    """
    Online Bayesian calibrator for prediction confidence.
    """

    def __init__(self, n_bins: int = 10, prior_alpha: float = 2.0, prior_beta: float = 2.0):
        self.n_bins = n_bins
        self.alpha = np.full(n_bins, prior_alpha)
        self.beta = np.full(n_bins, prior_beta)
        self.total_updates = 0

    def _confidence_to_bin(self, confidence: float) -> int:
        """Map confidence into a calibration bin."""
        clipped = np.clip(confidence, 0.0, 0.9999)
        return int(clipped * self.n_bins)

    def update(self, predicted_confidence: float, was_correct: bool):
        """Update the calibrator with a new observation."""
        bin_idx = self._confidence_to_bin(predicted_confidence)

        if was_correct:
            self.alpha[bin_idx] += 1
        else:
            self.beta[bin_idx] += 1

        self.total_updates += 1

    def calibrate(self, predicted_confidence: float) -> float:
        """Return calibrated confidence."""
        bin_idx = self._confidence_to_bin(predicted_confidence)

        # Beta posterior.
        posterior_mean = self.alpha[bin_idx] / (self.alpha[bin_idx] + self.beta[bin_idx])

        # Shrinkage toward the original prediction.
        trust_factor = min(1.0, self.total_updates / 100)

        return (1 - trust_factor) * predicted_confidence + trust_factor * posterior_mean
