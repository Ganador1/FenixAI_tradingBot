"""
NanoFenix v3 — "The Precision Companion"
==========================================

Complete redesign combining the best of V0, V1, V2, and MiniFenix.

Key improvements over predecessors:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
V1 problems fixed:
  - Raw bookTicker too noisy → 1-sec bar aggregation (from V2)
  - REGIME_CHANGE exit destroyed P&L → Time-based + trailing exits
  - Horizon mismatch → Dual horizons matched to hold time

V2 problems fixed:
  - 63% HOLD signals → Lower adaptive thresholds + dual-model consensus
  - Confidence calibration wrong → Empirical calibration matching Fenix thresholds
  - Only 20 features → 28 multi-scale features including V0's LOB ideas

V0 problems fixed:
  - PyTorch segfaults on macOS → Pure LightGBM, no neural networks
  - 10-12s retrain blocks → 50ms LightGBM retrain
  - Overly complex architecture → Clean modular design

MiniFenix problems fixed:
  - LLM brain adds latency → Pure quantitative, no LLM dependency
  - Classifier loses magnitude info → Regression approach

Architecture:
  bookTicker + aggTrade WebSocket (300+ tps)
    → BarAggregator (1-second bars, 300× noise reduction)
    → MultiScaleFeatureEngine (28 features at multiple lookbacks)
    → DualHorizonPredictor (30s + 120s LightGBM consensus)
    → PaperExecutor (standalone) + CompanionSignal (for Fenix)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from .executor import PaperExecutor
from .feature_engine import (
    FEATURE_LOOKBACK,
    Bar,
    BarAggregator,
    MultiScaleFeatureEngine,
)
from .predictor import DualHorizonPredictor

logger = logging.getLogger("NanoFenixV3")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_interval_tag(interval: float) -> str:
    rounded = round(interval)
    if abs(interval - rounded) < 1e-9:
        return f"{int(rounded)}s"
    return f"{interval:g}".replace(".", "_") + "s"


def _default_runtime_state_path(symbol: str, interval: float) -> Path:
    return Path(f"nanofenixv3/runtime_{symbol.lower()}_{_runtime_interval_tag(interval)}.pkl")


def _unique_tmp_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")


def _signal_to_action(signal: str) -> str:
    normalized = str(signal or "").strip().upper()
    if normalized in {"LONG", "BUY", "UP"}:
        return "BUY"
    if normalized in {"SHORT", "SELL", "DOWN"}:
        return "SELL"
    return "HOLD"


# ═══════════════════════════════════════════════════════════════════════════════
# NanoFenix V3 Bot
# ═══════════════════════════════════════════════════════════════════════════════


class NanoFenixV3:
    """
    The Precision Companion: dual-horizon LightGBM + multi-scale features.

    Can run standalone (paper trading) or as companion to Fenix engine
    (publishes rich signal per bar for Fenix to consume).
    """

    def __init__(
        self,
        symbol: str = "ETHUSDT",
        balance: float = 10_000.0,
        model_path: str | None = None,
    ):
        self.symbol = symbol.upper()
        self.aggregator = BarAggregator(
            interval=float(os.getenv("NANOFENIXV3_BAR_INTERVAL", "1.0"))
        )
        self.features = MultiScaleFeatureEngine()
        resolved_model_path = model_path or f"nanofenixv3/pretrained_{self.symbol.lower()}.pkl"
        self.predictor = DualHorizonPredictor(model_path=resolved_model_path)
        self.executor = PaperExecutor(balance=balance)

        runtime_interval = float(getattr(self.aggregator, "_interval", 1.0) or 1.0)
        default_runtime_path = _default_runtime_state_path(self.symbol, runtime_interval)
        runtime_state_path = os.getenv("NANOFENIXV3_RUNTIME_STATE_PATH", "").strip()
        if runtime_state_path:
            self._runtime_state_path = Path(runtime_state_path)
        else:
            self._runtime_state_path = default_runtime_path
        self._runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._runtime_restore_path = self._resolve_runtime_restore_path(default_runtime_path)

        self._running = False
        self._tick_count = 0
        self._start_time = 0.0
        self._run_id = os.getenv("NANOFENIXV3_RUN_ID", "").strip() or (
            f"{self.symbol.lower()}-{os.getpid()}-{int(time.time())}"
        )
        self._seen_book_ticker = False
        self._observer_only = _env_bool("NANOFENIXV3_COMPANION_OBSERVER_ONLY", False)

        # ── Companion signal output ──
        self._signal_state_file: Path | None = None
        signal_path = os.getenv("NANOFENIX_SIGNAL_STATE_PATH", "").strip()
        if signal_path:
            try:
                self._signal_state_file = Path(signal_path)
                self._signal_state_file.parent.mkdir(parents=True, exist_ok=True)
                logger.info("📡 Companion signal enabled: %s", self._signal_state_file)
            except Exception as e:
                logger.warning("Signal state init failed: %s", e)
                self._signal_state_file = None

        # ── Auto-save model periodically ──
        # Always save to THIS symbol's canonical path. When warm-starting from
        # another market's pretrained model (e.g. ETHUSDT -> ETHUSDC), the
        # borrowed file must stay read-only or we contaminate it with data
        # from a different microstructure.
        own_model_path = f"nanofenixv3/pretrained_{self.symbol.lower()}.pkl"
        if model_path and Path(model_path).resolve() != Path(own_model_path).resolve():
            logger.info(
                "Warm-start model %s is read-only; autosaves go to %s",
                model_path,
                own_model_path,
            )
        self._model_save_path = own_model_path
        self._autosave_every = max(60, _env_int("NANOFENIXV3_AUTOSAVE_EVERY", 120))
        self._last_model_save_bar = 0
        self._load_runtime_state()

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point. Connects to Binance combined stream."""
        self._running = True
        self._start_time = time.time()

        logger.info("=" * 65)
        logger.info("  NanoFenix v3 — The Precision Companion")
        logger.info("=" * 65)
        logger.info(f"  Symbol:     {self.symbol}")
        bar_interval = self.aggregator._interval
        logger.info(f"  Bar:        {bar_interval:.0f} second{'s' if bar_interval != 1 else ''}")
        logger.info("  Horizons:   30s + 120s (dual consensus)")
        logger.info("  Features:   28 multi-scale")
        logger.info("  ML:         LightGBM huber (dual-horizon)")
        logger.info(f"  Balance:    ${self.executor.balance:,.2f}")
        logger.info(f"  Companion:  {'ENABLED' if self._signal_state_file else 'disabled'}")
        if self._observer_only:
            logger.info("  Executor:   observer-only")
        if self.predictor.either_trained:
            logger.info(f"  📦 Pre-trained: val_acc={self.predictor.val_accuracy * 100:.1f}%")
        logger.info("=" * 65)

        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed — pip install websockets")
            return

        ws_base = os.getenv("NANOFENIX_WS_BASE_URL", "").strip()
        if not ws_base:
            if _env_bool("NANOFENIX_USE_TESTNET", False):
                ws_base = "wss://stream.binancefuture.com"
            else:
                ws_base = "wss://fstream.binance.com"

        url = self._build_ws_url(self.symbol, ws_base)
        logger.info(f"🔗 Connecting: {url}")

        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    logger.info("✅ WebSocket connected")
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        self._process_ws_message(raw_msg)
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"WS error: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    @staticmethod
    def _build_ws_url(symbol: str, ws_base: str) -> str:
        sym_lower = symbol.lower()
        ws_base = ws_base.rstrip("/")
        if "fstream.binance.com" in ws_base and not ws_base.endswith(
            ("/public", "/market", "/private")
        ):
            ws_base = f"{ws_base}/public"
        return f"{ws_base}/stream?streams={sym_lower}@bookTicker/{sym_lower}@trade"

    def stop(self) -> None:
        self._running = False
        logger.info("🛑 NanoFenix v3 stopped")

    # ──────────────────────────────────────────────────────────────────────
    # WebSocket Processing
    # ──────────────────────────────────────────────────────────────────────

    def _process_ws_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        stream = msg.get("stream", "")
        data = msg.get("data", msg)
        event = data.get("e", "")

        if "bookTicker" in stream or event == "bookTicker":
            self._seen_book_ticker = True
            bid = float(data.get("b", 0))
            ask = float(data.get("a", 0))
            bid_qty = float(data.get("B", 0))
            ask_qty = float(data.get("A", 0))
            if bid <= 0 or ask <= 0:
                return

            mid = (bid + ask) / 2.0
            total = bid_qty + ask_qty
            obi = (bid_qty - ask_qty) / total if total > 0 else 0.0
            spread_bps = (ask - bid) / mid * 10000
            ts = time.monotonic()

            self._tick_count += 1
            completed_bar = self.aggregator.process_tick(mid, obi, spread_bps, ts)
            if completed_bar is not None:
                self._on_bar(completed_bar)

        elif "aggTrade" in stream or "trade" in stream or event in {"aggTrade", "trade"}:
            price = float(data.get("p", 0))
            qty = float(data.get("q", 0))
            is_buyer_maker = bool(data.get("m", True))
            if price > 0 and qty > 0:
                # Some endpoints can degrade and stop emitting bookTicker updates.
                # Use aggTrade price as a fallback to keep 1s bars flowing.
                if not self._seen_book_ticker:
                    ts = time.monotonic()
                    completed_bar = self.aggregator.process_tick(
                        price,
                        obi=0.0,
                        spread_bps=0.0,
                        ts=ts,
                    )
                    if completed_bar is not None:
                        self._on_bar(completed_bar)
                self.aggregator.process_trade(price, qty, is_buyer_maker)

    # ──────────────────────────────────────────────────────────────────────
    # Bar Processing Pipeline
    # ──────────────────────────────────────────────────────────────────────

    def _on_bar(self, bar: Bar) -> None:
        """Process one completed 1-second bar through the full pipeline."""
        self.features.add_bar(bar)
        bar_idx = self.features.bar_count

        # Compute features (need warmup)
        feat = self.features.compute_features()
        if feat is None:
            if bar_idx % 60 == 0:
                logger.info(
                    f"⏳ Warmup: {bar_idx}/{FEATURE_LOOKBACK} bars | "
                    f"{self.symbol}=${bar.close:,.2f} | Ticks/bar: {bar.tick_count}"
                )
            return

        close = bar.close

        # Store features in predictor buffer
        self.predictor.store(feat, close)
        # V3.1: proper deferred direction evaluation
        self.predictor.evaluate_direction(bar_idx, close)

        # Retrain if needed
        if self.predictor.should_retrain():
            self.predictor.retrain()

        # Get regime context for adaptive prediction
        regime_ctx = self.features.get_regime_context()
        vol_state = regime_ctx.get("volatility", "MEDIUM")

        # Predict (dual-horizon consensus)
        # bar_idx/close passed so predict() auto-queues raw predictions for
        # direction tracking even when the hard gate emits HOLD
        policy = self.predictor.predict_with_policy(
            feat,
            volatility_state=vol_state,
            bar_idx=bar_idx,
            close=close,
            regime=regime_ctx.get("regime", "RANGING"),
        )
        signal = str(policy.get("signal", "HOLD"))
        pred_bps = float(policy.get("pred_bps", 0.0) or 0.0)
        confidence = float(policy.get("confidence", 0.0) or 0.0)
        event_intensity = self.predictor.estimate_event_intensity(feat)
        readiness = self.predictor.companion_readiness()
        short_readiness = self.predictor.short_companion_readiness()
        long_readiness = self.predictor.long_companion_readiness()

        # Extract executor-compatible features from vector
        ema_trend = feat[7]  # ema_cross_20_60 (index 7)
        range_bps = feat[25]  # range_bps_30 (index 25)
        buy_vol_ratio = float(feat[13]) * 0.5 + 0.5  # bvr_5 [-1,+1] → [0,1]

        # ── Publish companion signal ──
        self._publish_companion_signal(
            bar_idx=bar_idx,
            close=close,
            signal=signal,
            pred_bps=pred_bps,
            confidence=confidence,
            ema_trend=ema_trend,
            range_bps=range_bps,
            buy_vol_ratio=buy_vol_ratio,
            regime_ctx=regime_ctx,
            event_intensity=event_intensity,
            readiness=readiness,
            short_readiness=short_readiness,
            long_readiness=long_readiness,
            policy=policy,
        )

        # ── Execute on paper ──
        if not self._observer_only:
            self.executor.on_bar(
                bar_idx=bar_idx,
                close=close,
                signal=signal,
                pred_bps=pred_bps,
                ema_trend=ema_trend,
                direction_accuracy=self.predictor.direction_accuracy,
                direction_samples=self.predictor.direction_samples,
                calibration_samples=self.predictor.calibration_samples,
                companion_ready=bool(readiness["ready"]),
                val_accuracy=self.predictor.val_accuracy_safe,
                range_bps=range_bps,
                buy_vol_ratio=buy_vol_ratio,
                volatility_state=vol_state,
            )

        # ── Periodic logging ──
        if bar_idx % 30 == 0:
            self._log_status(
                bar_idx,
                close,
                signal,
                pred_bps,
                confidence,
                ema_trend,
                buy_vol_ratio,
                range_bps,
                regime_ctx,
                event_intensity,
            )

        if bar_idx % 120 == 0:
            self._log_extended_stats(bar_idx)

        # ── Auto-save model every 600 bars (10 min) ──
        if (
            bar_idx - self._last_model_save_bar >= self._autosave_every
            and self.predictor.either_trained
        ):
            self._persist_runtime_snapshot()
            self._last_model_save_bar = bar_idx

    def _resolve_runtime_restore_path(self, default_runtime_path: Path) -> Path:
        legacy_path = Path(f"nanofenixv3/runtime_{self.symbol.lower()}.pkl")
        interval_backup_path = default_runtime_path.with_name(
            f"{default_runtime_path.stem}_backup{default_runtime_path.suffix}"
        )
        candidates = [
            self._runtime_state_path,
            default_runtime_path,
            legacy_path,
            interval_backup_path,
        ]
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                return candidate
        return self._runtime_state_path

    def _load_runtime_state(self) -> None:
        path = self._runtime_restore_path
        if not path.exists():
            return
        try:
            with open(path, "rb") as f:
                state = pickle.load(f)
        except Exception as exc:
            logger.warning("Failed to load Nano runtime state %s: %s", path, exc)
            return

        try:
            self.features.restore_state(state.get("feature_engine"))
        except Exception as exc:
            logger.warning("Failed to restore feature engine state: %s", exc)
        try:
            self.aggregator.restore_state(state.get("aggregator"))
        except Exception as exc:
            logger.warning("Failed to restore aggregator state: %s", exc)
        try:
            self._tick_count = int(state.get("tick_count", self._tick_count) or self._tick_count)
            self._last_model_save_bar = int(
                state.get("last_model_save_bar", self._last_model_save_bar)
                or self._last_model_save_bar
            )
        except Exception:
            pass

        if path != self._runtime_state_path:
            logger.info(
                "♻️ Using runtime fallback %s (save target=%s)",
                path,
                self._runtime_state_path,
            )
        logger.info(
            "♻️ Runtime state restored: bars=%s warm=%s ticks=%s path=%s",
            getattr(self.features, "bar_count", 0),
            getattr(self.features, "is_warm", False),
            self._tick_count,
            path,
        )

    def _persist_runtime_snapshot(self) -> None:
        state = {
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": self.symbol,
            "tick_count": int(self._tick_count),
            "last_model_save_bar": int(self._last_model_save_bar),
            "feature_engine": self.features.export_state(),
            "aggregator": self.aggregator.export_state(),
        }
        tmp = _unique_tmp_path(self._runtime_state_path)
        try:
            with open(tmp, "wb") as f:
                pickle.dump(state, f)
            tmp.replace(self._runtime_state_path)
            if self.predictor.either_trained:
                self.predictor.save_model(self._model_save_path)
            logger.info(
                "💾 Runtime state saved to %s | bars=%s warm=%s",
                self._runtime_state_path,
                getattr(self.features, "bar_count", 0),
                getattr(self.features, "is_warm", False),
            )
        except Exception as exc:
            logger.warning("Failed to persist Nano runtime state: %s", exc)

    # ──────────────────────────────────────────────────────────────────────
    # Companion Signal Publisher
    # ──────────────────────────────────────────────────────────────────────

    def _publish_companion_signal(
        self,
        *,
        bar_idx: int,
        close: float,
        signal: str,
        pred_bps: float,
        confidence: float,
        ema_trend: float,
        range_bps: float,
        buy_vol_ratio: float,
        regime_ctx: dict,
        event_intensity: float,
        readiness: dict,
        short_readiness: dict,
        long_readiness: dict,
        policy: dict[str, object] | None = None,
    ) -> None:
        """Write rich companion signal for Fenix engine consumption."""
        if not self._signal_state_file:
            return

        try:
            action = _signal_to_action(signal)
            run_id = getattr(self, "_run_id", None)
            if not run_id:
                run_id = f"{self.symbol.lower()}-{os.getpid()}-{int(time.time())}"
                self._run_id = run_id
            payload = {
                # ── Core signal ──
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol,
                "version": "v3",
                "run_id": run_id,
                "producer_pid": os.getpid(),
                "runtime_state_path": str(getattr(self, "_runtime_state_path", "")),
                "model_save_path": str(getattr(self, "_model_save_path", "")),
                "bar_index": int(bar_idx),
                "close": float(close),
                "signal": str(signal),  # LONG / SHORT / HOLD
                "action": action,  # BUY / SELL / HOLD
                "raw_signal": str(signal),
                "pred_bps": float(pred_bps),
                "confidence": float(confidence),
                "direction_accuracy": float(self.predictor.direction_accuracy),
                "short_direction_accuracy": float(self.predictor.short_direction_accuracy),
                "long_direction_accuracy": float(self.predictor.long_direction_accuracy),
                "direction_samples": int(self.predictor.direction_samples),
                "short_direction_samples": int(self.predictor.short_direction_samples),
                "long_direction_samples": int(self.predictor.long_direction_samples),
                "calibration_samples": int(self.predictor.calibration_samples),
                "short_calibration_samples": int(self.predictor.short_calibration_samples),
                "long_calibration_samples": int(self.predictor.long_calibration_samples),
                "val_accuracy": float(self.predictor.val_accuracy_safe),
                # ── Market context (V3 exclusive) ──
                "ema_trend_bps": float(ema_trend),
                "range_bps_30": float(range_bps),
                "buy_vol_ratio_5": float(buy_vol_ratio),
                "event_intensity": float(event_intensity),
                "regime": regime_ctx.get("regime", "UNKNOWN"),
                "volatility_state": regime_ctx.get("volatility", "UNKNOWN"),
                "trend": regime_ctx.get("trend", "UNKNOWN"),
                "vol_30s": regime_ctx.get("vol_30s", 0.0),
                # ── Executor state ──
                "has_position": self.executor.position is not None,
                "position_direction": (
                    self.executor.position.direction if self.executor.position else None
                ),
                "paper_trades": self.executor.n_trades,
                "paper_win_rate": round(self.executor.win_rate, 1),
                "paper_pnl": round(self.executor.total_pnl, 4),
                "companion_ready": bool(readiness.get("ready", False)),
                "companion_block_reasons": list(readiness.get("reasons", [])),
                "utility_score": float(readiness.get("utility_score", 0.0)),
                "short_companion_ready": bool(short_readiness.get("ready", False)),
                "short_companion_block_reasons": list(short_readiness.get("reasons", [])),
                "short_utility_score": float(short_readiness.get("utility_score", 0.0)),
                "short_companion_direction_accuracy": float(
                    short_readiness.get("direction_accuracy", 0.0)
                ),
                "short_companion_direction_samples": int(
                    short_readiness.get("direction_samples", 0)
                ),
                "short_companion_calibration_samples": int(
                    short_readiness.get("calibration_samples", 0)
                ),
                "long_companion_ready": bool(long_readiness.get("ready", False)),
                "long_companion_block_reasons": list(long_readiness.get("reasons", [])),
                "long_utility_score": float(long_readiness.get("utility_score", 0.0)),
                "long_companion_direction_accuracy": float(
                    long_readiness.get("direction_accuracy", 0.0)
                ),
                "long_companion_direction_samples": int(long_readiness.get("direction_samples", 0)),
                "long_companion_calibration_samples": int(
                    long_readiness.get("calibration_samples", 0)
                ),
                "expected_bps": float((policy or {}).get("expected_bps", pred_bps) or pred_bps),
                "uncertainty_bps": float((policy or {}).get("uncertainty_bps", 0.0) or 0.0),
                "bias_correction_bps": float((policy or {}).get("bias_correction_bps", 0.0) or 0.0),
                "edge_net_bps": float((policy or {}).get("edge_net_bps", 0.0) or 0.0),
                "actionable_edge_bps": float((policy or {}).get("actionable_edge_bps", 0.0) or 0.0),
                "calibration_health": float((policy or {}).get("calibration_health", 0.0) or 0.0),
                "fast_weight": float((policy or {}).get("fast_weight", 0.5) or 0.5),
                "slow_weight": float((policy or {}).get("slow_weight", 0.5) or 0.5),
                "drift_score": float((policy or {}).get("drift_score", 0.0) or 0.0),
                "allow_execute": bool((policy or {}).get("allow_execute", False)),
                "allow_add_to_position": bool((policy or {}).get("allow_add_to_position", False)),
                "size_multiplier_hint": float(
                    (policy or {}).get("size_multiplier_hint", 0.0) or 0.0
                ),
                "require_reversal_confirmation": bool(
                    (policy or {}).get("require_reversal_confirmation", True)
                ),
                "source": str((policy or {}).get("source", "none")),
            }
            tmp = _unique_tmp_path(self._signal_state_file)
            tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            tmp.replace(self._signal_state_file)
        except Exception as e:
            logger.debug("Signal state write failed: %s", e)

    # ──────────────────────────────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────────────────────────────

    def _log_status(
        self,
        bar_idx: int,
        close: float,
        signal: str,
        pred_bps: float,
        confidence: float,
        ema_trend: float,
        buy_vol_ratio: float,
        range_bps: float,
        regime_ctx: dict,
        event_intensity: float,
    ) -> None:
        pos_str = "no position"
        if self.executor.position is not None:
            pos = self.executor.position
            upnl = pos.unrealized_pnl(close)
            held = bar_idx - pos.entry_bar
            pos_str = f"POS={pos.direction}@${pos.entry_price:,.2f} uPnL=${upnl:+.4f} held={held}s"

        regime = regime_ctx.get("regime", "?")
        vol = regime_ctx.get("volatility", "?")
        trend = regime_ctx.get("trend", "?")

        logger.info(
            f"📊 #{bar_idx} | {self.symbol}=${close:,.2f} | "
            f"ML={signal}({pred_bps:+.1f}bps conf={confidence:.0%}) | "
            f"Regime={regime} Vol={vol} Trend={trend} Ev={event_intensity:.2f} | "
            f"EMA={ema_trend:+.1f} BuyR={buy_vol_ratio:.2f} | "
            f"P&L=${self.executor.total_pnl:+.2f} | {pos_str}"
        )

    def _log_extended_stats(self, bar_idx: int) -> None:
        elapsed = (time.time() - self._start_time) / 60
        logger.info("─" * 60)
        logger.info(f"📊 STATS @ {elapsed:.1f} min | NanoFenix V3")
        logger.info(
            f"   Ticks: {self._tick_count:,} | Bars: {bar_idx} | "
            f"~{self._tick_count // max(1, bar_idx)} ticks/bar"
        )
        logger.info(
            f"   ML buffer: {self.predictor.buffer_size()} samples | "
            f"Trained: {self.predictor.trained} | "
            f"Dir Acc: {self.predictor.direction_accuracy * 100:.1f}% "
            f"(S={self.predictor.short_direction_accuracy * 100:.1f}%/"
            f"{self.predictor.short_direction_samples} "
            f"L={self.predictor.long_direction_accuracy * 100:.1f}%/"
            f"{self.predictor.long_direction_samples}) | "
            f"Calib={self.predictor.calibration_samples} "
            f"(S={self.predictor.short_calibration_samples} "
            f"L={self.predictor.long_calibration_samples}) | "
            f"Val Acc: {self.predictor.val_accuracy * 100:.1f}%"
        )
        logger.info(
            f"   Trades: {self.executor.n_trades} | "
            f"WR: {self.executor.win_rate:.0f}% "
            f"({self.executor._wins}W/{self.executor._losses}L)"
        )
        logger.info(
            f"   P&L: ${self.executor.total_pnl:+.4f} ({self.executor.total_pnl_pct:+.3f}%)"
        )
        logger.info(f"   Balance: ${self.executor.balance:,.2f}")
        logger.info("─" * 60)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main(
    symbol: str = "ETHUSDT",
    balance: float = 10_000.0,
    model_path: str | None = None,
):
    """Run NanoFenix v3."""

    # Resolve symbol from env or arg
    symbol = os.getenv("NANOFENIXV3_SYMBOL", symbol).upper()

    # Setup logging
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"nanofenixv3_{symbol}_{timestamp}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file),
        ],
    )
    logger.info(f"Log file: {log_file}")

    bot = NanoFenixV3(symbol=symbol, balance=balance, model_path=model_path)

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("🛑 Shutdown requested (signal %s)", signum)
        bot.stop()
        bot._persist_runtime_snapshot()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
    finally:
        bot._persist_runtime_snapshot()
        logger.info("=" * 60)
        logger.info("  FINAL RESULTS — NanoFenix V3")
        logger.info("=" * 60)
        logger.info(f"  Trades: {bot.executor.n_trades}")
        logger.info(f"  Win Rate: {bot.executor.win_rate:.1f}%")
        logger.info(f"  P&L: ${bot.executor.total_pnl:+.4f}")
        logger.info(f"  Balance: ${bot.executor.balance:,.2f}")
        logger.info(f"  Dir Accuracy: {bot.predictor.direction_accuracy * 100:.1f}%")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
