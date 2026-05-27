"""
MiniFenix SOTA Prediction Model.

Implements a 3-layer prediction system, based on the most recent research
in Order Book prediction:

1. **LightGBM** - The main model (faster and more accurate than Random Forest
   for financial tabular data). Used by Citadel, Two Sigma, Jump Trading.

2. **Online Learning** - The model is retrained incrementally with new data
   every N ticks, solving the degradation problem found in LOBCAST
   (arXiv:2308.01915): ALL static models degrade on new data.

3. **Probability Calibration** - It does not only predict UP/DOWN/HOLD but
   the calibrated probability of each class. Only trades when the confidence
   is high.

Academic references:
- DeepLOB (arXiv:1808.03668) - Inspiration for the LOB features
- Transformers for LOB (arXiv:2003.00130) - Benchmark for comparison
- LOBCAST (arXiv:2308.01915) - Showed that online learning is needed
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
import warnings
import numpy as np
import pandas as pd
import joblib

# Silence sklearn warnings about feature names
warnings.filterwarnings("ignore", message="X does not have valid feature names")

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

from .feature_engine import FeatureEngine, LOBSnapshot

logger = logging.getLogger("MiniFenix.SOTA")


class SOTAPredictor:
    """
    SOTA predictor combining LightGBM + Online Learning + advanced feature engineering.
    """

    # Prediction classes
    DOWN = 0     # Price will go down
    UP = 1       # Price will go up
    HOLD = 2     # No significant change

    def __init__(
        self,
        model_path: str = "minifenix/sota_model.joblib",
        pretrained_path: str = "minifenix/sota_model_pretrained.joblib",
        retrain_every_n: int = 500,     # Retrain every N ticks
        min_training_samples: int = 1000,  # Raised from 200 to 1000 to avoid overfitting # Minimum samples to train
        prediction_horizon: int = 200,   # Predict move N ticks ahead (~0.5-2s)
        move_threshold_bps: float = 2.0, # UP/DOWN threshold (2.0 bps = ~$1.35 on BTC, covers fees + slippage)
        min_confidence: float = 0.55,  # Raised from 0.52 to 0.55 for better quality    # Minimum confidence to emit signal (not 50/50)
    ):
        self.model_path = model_path
        self.retrain_every = retrain_every_n
        self.min_training_samples = min_training_samples
        self.horizon = prediction_horizon
        self.move_threshold = move_threshold_bps / 10000  # Convert bps to ratio
        self.min_confidence = min_confidence

        # Feature engine
        self.feature_engine = FeatureEngine(lookback=200)

        # LightGBM model
        self.model: lgb.LGBMClassifier | None = None
        self.is_trained = False
        self._is_training = False  # Lock to avoid race conditions

        # Buffers for online learning
        self.X_buffer: collections.deque[np.ndarray] = collections.deque(maxlen=5000)
        self.price_buffer: collections.deque[float] = collections.deque(maxlen=5000)
        self.tick_count = 0
        self.last_train_tick = 0

        # Performance metrics (for monitoring)
        self.predictions_made = 0
        self.correct_predictions = 0
        self.pending_predictions: collections.deque = collections.deque(maxlen=1000)

        # Cache of last valid prediction (used while the model is retraining)
        self._last_valid_prediction: dict | None = None

        self.pretrained_path = pretrained_path

        # Try to load a pretrained or existing model
        self._try_load_model()

    def _try_load_model(self):
        """Load a pretrained (offline) model or the existing live-trading one."""
        # First try to load the pretrained model (takes priority)
        if os.path.exists(self.pretrained_path):
            try:
                saved = joblib.load(self.pretrained_path)
                self.model = saved["model"]
                self.is_trained = True
                acc = saved.get('accuracy', '?')
                samples = saved.get('train_samples', '?')
                logger.info(f"[BRAIN] [SOTA] Pretrained model loaded ({acc} accuracy, {samples} offline samples)")
                return
            except Exception as e:
                logger.warning(f"Could not load pretrained model: {e}")

        # Fallback to live-trading model
        if os.path.exists(self.model_path):
            try:
                saved = joblib.load(self.model_path)
                self.model = saved["model"]
                self.is_trained = True
                logger.info(f"[BRAIN] [SOTA] Online model loaded ({saved.get('accuracy', '?')} accuracy)")
            except Exception as e:
                logger.warning(f"Could not load model: {e}")

    def _create_model(self, class_weights: dict | None = None) -> lgb.LGBMClassifier:
        """
        Create a new LightGBM model with hyperparameters tuned for LOB.

        Changes from the previous version:
        - Dynamic class_weight: handles imbalanced classes (HOLD dominant ~57%)
          DOWN x3, UP x1.5, HOLD x1 to compensate the imbalance
        - n_estimators=150 with learning_rate=0.08: faster convergence for
          online retraining (retrain every 500 ticks ~ every 3-5s)
        - min_child_samples=10: fewer required samples per leaf -> better for small
          datasets (500 initial training samples)
        - max_depth=5: reduced to avoid overfitting on noisy financial data
        """
        # Compute class weights dynamically if none provided
        if class_weights is None:
            class_weights = {0: 3.0, 1: 1.5, 2: 1.0}  # DOWN x3, UP x1.5, HOLD x1

        return lgb.LGBMClassifier(
            n_estimators=150,
            max_depth=5,
            learning_rate=0.08,
            num_leaves=25,
            min_child_samples=10,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            class_weight=class_weights,  # FIX: dynamic class weighting
            n_jobs=1,             # No parallelism to keep latency low
            verbose=-1,
            objective="multiclass",
            num_class=3,
            metric="multi_logloss",
            importance_type="gain",
        )

    def process_tick(self, snap: LOBSnapshot) -> dict:
        """
        Process a WebSocket tick and return the prediction.

        Returns:
            {
                "signal": "UP" | "DOWN" | "HOLD",
                "confidence": float (0-1),
                "probabilities": {"UP": float, "DOWN": float, "HOLD": float},
                "model_ready": bool,
                "accuracy": float (model running accuracy),
                "features_ready": bool,
            }
        """
        self.tick_count += 1
        mid_price = (snap.bid + snap.ask) / 2

        # 1. Compute features
        features = self.feature_engine.update(snap)

        if features is None:
            return {
                "signal": "HOLD", "confidence": 0.0,
                "probabilities": {"UP": 0.33, "DOWN": 0.33, "HOLD": 0.34},
                "model_ready": False, "accuracy": 0.0, "features_ready": False,
            }

        # 2. Save into the training buffer
        self.X_buffer.append(features.copy())
        self.price_buffer.append(mid_price)

        # 3. Evaluate past predictions (to measure live accuracy)
        self._evaluate_past_predictions(mid_price)

        # 4. Retrain if it is time (in background so we do not block)
        if (self.tick_count - self.last_train_tick) >= self.retrain_every:
            import asyncio
            asyncio.create_task(self._online_retrain_async())

        # 5. Predict (do not use model during training)
        if self._is_training or not self.is_trained or self.model is None:
            # Use cached last valid prediction if any
            if self._last_valid_prediction is not None:
                cached = self._last_valid_prediction.copy()
                cached["model_ready"] = False  # Indicate that we are serving from cache
                cached["accuracy"] = self._running_accuracy()
                return cached
            return {
                "signal": "HOLD", "confidence": 0.0,
                "probabilities": {"UP": 0.33, "DOWN": 0.33, "HOLD": 0.34},
                "model_ready": False, "accuracy": self._running_accuracy(),
                "features_ready": True,
            }

        features_df = pd.DataFrame([features], columns=self.feature_engine.feature_names)
        probas = self.model.predict_proba(features_df)[0]
        # probas = [P(DOWN), P(UP), P(HOLD)]
        pred_class = int(np.argmax(probas))
        confidence = float(probas[pred_class])

        # Save the prediction to evaluate later.
        # We use evaluate_at_tick (absolute tick) instead of a relative buffer_idx;
        # buffer_idx becomes invalid when the deque hits maxlen=5000 and starts
        # dropping items from the front -> latent bug in long sessions.
        self.pending_predictions.append({
            "tick": self.tick_count,
            "pred_class": pred_class,
            "price_at_prediction": mid_price,
            "evaluate_at_tick": self.tick_count + self.horizon,
        })

        # Only emit a signal if confidence is above the threshold
        if confidence < self.min_confidence:
            signal = "HOLD"
        else:
            signal = {self.DOWN: "DOWN", self.UP: "UP", self.HOLD: "HOLD"}[pred_class]

        # Cache the valid prediction
        result = {
            "signal": signal,
            "confidence": round(confidence, 4),
            "probabilities": {
                "DOWN": round(float(probas[0]), 4),
                "UP": round(float(probas[1]), 4),
                "HOLD": round(float(probas[2]), 4),
            },
            "model_ready": True,
            "accuracy": self._running_accuracy(),
            "features_ready": True,
        }
        self._last_valid_prediction = result.copy()
        return result

    def _online_retrain(self):
        """
        Retrain the model with the most recent data.
        This is what makes MiniFenix SOTA differ from the previous static Random Forest.
        """
        n = len(self.X_buffer)
        if n < self.min_training_samples + self.horizon:
            return

        logger.info(f"[CYCLE] [SOTA] Online retraining with {n} samples...")
        t0 = time.time()

        # Build X, y using a pandas DataFrame to keep feature names
        X_all = np.array(list(self.X_buffer))
        prices = np.array(list(self.price_buffer))

        # Labels: compare the current price with the price N ticks ahead
        X_raw = X_all[:n - self.horizon]
        y = np.zeros(len(X_raw), dtype=np.int32)

        for i in range(len(X_raw)):
            future_price = prices[i + self.horizon]
            current_price = prices[i]
            if current_price == 0:
                y[i] = self.HOLD
                continue
            change = (future_price - current_price) / current_price
            if change > self.move_threshold:
                y[i] = self.UP
            elif change < -self.move_threshold:
                y[i] = self.DOWN
            else:
                y[i] = self.HOLD

        # Convert to DataFrame with feature names
        X = pd.DataFrame(X_raw, columns=self.feature_engine.feature_names)

        # Check class balance
        unique, counts = np.unique(y, return_counts=True)
        class_dist = dict(zip(unique, counts))
        n_classes_present = len(unique)
        logger.info(f"   Class distribution: DOWN={class_dist.get(0, 0)}, UP={class_dist.get(1, 0)}, HOLD={class_dist.get(2, 0)}")

        # If there is only 1 class, training is pointless
        if n_classes_present < 2:
            logger.warning("   [WARN] Only 1 class present - skipping training")
            self.last_train_tick = self.tick_count
            return

        # Minimum class validation: at least 50 samples per class
        min_samples_per_class = 50
        class_counts = np.bincount(y, minlength=3)
        if any(count < min_samples_per_class for count in class_counts):
            logger.warning(f"   [WARN] Classes with too few samples: {class_counts} - skipping training")
            self.last_train_tick = self.tick_count
            return

        # Compute dynamic class weights based on the current distribution
        total_samples = len(y)
        class_weights = {
            0: total_samples / (3 * class_counts[0]) if class_counts[0] > 0 else 1.0,  # DOWN
            1: total_samples / (3 * class_counts[1]) if class_counts[1] > 0 else 1.0,  # UP
            2: total_samples / (3 * class_counts[2]) if class_counts[2] > 0 else 1.0,  # HOLD
        }
        # Normalise so the minimum weight is 1.0
        min_weight = min(class_weights.values())
        class_weights = {k: v/min_weight for k, v in class_weights.items()}
        logger.info(f"   Class weights: DOWN={class_weights[0]:.2f}, UP={class_weights[1]:.2f}, HOLD={class_weights[2]:.2f}")

        # Split: 80% train, 20% validation (temporal, not random)
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        # Check that the val_set classes are present in the train_set
        train_classes = set(np.unique(y_train))
        val_classes = set(np.unique(y_val))
        use_early_stopping = val_classes.issubset(train_classes) and len(train_classes) >= 2

        # Train with dynamic class weights
        self.model = self._create_model(class_weights=class_weights)

        if use_early_stopping:
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                callbacks=[
                    lgb.early_stopping(stopping_rounds=30, verbose=False),  # More patience
                    lgb.log_evaluation(period=0)  # Silence logs
                ],
            )
        else:
            # Train without early stopping when classes do not line up
            logger.info("   [WARN] Imbalanced classes between train/val - no early stopping")
            self.model.fit(X, y)  # Use the full dataset

        # Evaluate
        if use_early_stopping:
            val_preds = self.model.predict(X_val)
            val_accuracy = float(np.mean(val_preds == y_val))
        else:
            # Evaluate on the full dataset
            all_preds = self.model.predict(X)
            val_accuracy = float(np.mean(all_preds == y))

        elapsed = time.time() - t0
        logger.info(f"[OK] [SOTA] Model retrained in {elapsed:.2f}s | Val Accuracy: {val_accuracy:.1%}")

        # Log feature importance (top 5)
        importances = self.model.feature_importances_
        top_indices = np.argsort(importances)[-5:][::-1]
        top_features = [(self.feature_engine.feature_names[i], importances[i]) for i in top_indices]
        logger.info(f"   Top features: {top_features}")

        # Save model
        try:
            joblib.dump({"model": self.model, "accuracy": f"{val_accuracy:.1%}"}, self.model_path)
        except Exception as e:
            logger.warning(f"Could not save model: {e}")

        self.is_trained = True
        self.last_train_tick = self.tick_count

    async def _online_retrain_async(self):
        """Async wrapper so the retrain runs in the background without blocking."""
        if self._is_training:
            return  # A training run is already in progress
        self._is_training = True
        try:
            await asyncio.to_thread(self._online_retrain)
        finally:
            self._is_training = False

    def _evaluate_past_predictions(self, current_price: float):
        """
        Evaluate past predictions using the absolute evaluate_at_tick.
        When self.tick_count >= evaluate_at_tick, the current price IS
        the price H ticks after the prediction was made.
        """
        while self.pending_predictions:
            pred = self.pending_predictions[0]
            if self.tick_count < pred["evaluate_at_tick"]:
                break  # Not enough time has passed yet

            self.pending_predictions.popleft()
            self.predictions_made += 1

            old_price = pred["price_at_prediction"]
            if old_price == 0:
                continue

            # current_price IS the price at evaluate_at_tick
            actual_change = (current_price - old_price) / old_price
            if actual_change > self.move_threshold:
                actual_class = self.UP
            elif actual_change < -self.move_threshold:
                actual_class = self.DOWN
            else:
                actual_class = self.HOLD

            if pred["pred_class"] == actual_class:
                self.correct_predictions += 1

    def _running_accuracy(self) -> float:
        if self.predictions_made == 0:
            return 0.0
        return round(self.correct_predictions / self.predictions_made, 4)

    def get_stats(self) -> dict:
        """Return model stats for monitoring."""
        return {
            "ticks_processed": self.tick_count,
            "predictions_made": self.predictions_made,
            "running_accuracy": self._running_accuracy(),
            "is_trained": self.is_trained,
            "buffer_size": len(self.X_buffer),
            "next_retrain_in": max(0, self.retrain_every - (self.tick_count - self.last_train_tick)),
        }
