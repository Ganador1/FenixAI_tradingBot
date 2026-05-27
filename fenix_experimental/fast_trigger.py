from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .models import ExperimentalRegime, MarketTick, TriggerDecision


@dataclass
class _CompletedBar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    bid_qty: float
    ask_qty: float
    buy_vol: float
    sell_vol: float
    spread_sum: float

    @property
    def spread_bps(self) -> float:
        return self.spread_sum / max(1, self.tick_count)

    @property
    def ofi(self) -> float:
        total = self.bid_qty + self.ask_qty
        if total <= 0:
            return 0.0
        return (self.bid_qty - self.ask_qty) / total


class _NanoV3Adapter:
    def __init__(self, model_path: str | None = None):
        self.available = False
        self._bar_idx = 0
        try:
            from nanofenixv3.feature_engine import Bar, MultiScaleFeatureEngine
            from nanofenixv3.predictor import DualHorizonPredictor
        except Exception:
            return

        self._Bar = Bar
        self._feature_engine = MultiScaleFeatureEngine()
        self._predictor = DualHorizonPredictor(model_path=model_path)
        self.available = True

    def on_bar(self, bar: _CompletedBar, regime: ExperimentalRegime) -> dict[str, Any] | None:
        if not self.available:
            return None

        self._bar_idx += 1
        nano_bar = self._Bar(
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            tick_count=bar.tick_count,
            obi_sum=bar.ofi * bar.tick_count,
            spread_sum=bar.spread_sum,
            buy_vol=bar.buy_vol,
            sell_vol=bar.sell_vol,
            buy_count=int(bar.buy_vol > 0),
            sell_count=int(bar.sell_vol > 0),
            vwap_num=(bar.buy_vol + bar.sell_vol) * bar.close,
            vwap_den=(bar.buy_vol + bar.sell_vol),
        )
        self._feature_engine.add_bar(nano_bar)
        features = self._feature_engine.compute_features()
        if features is None:
            return None

        self._predictor.evaluate_direction(self._bar_idx, bar.close)
        signal, pred_bps, confidence = self._predictor.predict(
            features,
            volatility_state=self._volatility_state(),
            bar_idx=self._bar_idx,
            close=bar.close,
        )
        self._predictor.store(features, bar.close)
        if self._predictor.should_retrain():
            self._predictor.retrain()

        direction_accuracy = float(self._predictor.direction_accuracy)
        if signal == "LONG" and regime.bias != "LONG":
            signal = "HOLD"
        elif signal == "SHORT" and regime.bias != "SHORT":
            signal = "HOLD"

        return {
            "signal": signal,
            "pred_bps": float(pred_bps),
            "confidence": float(confidence),
            "direction_accuracy": direction_accuracy,
            "source": "nanofenixv3",
        }

    def _volatility_state(self) -> str:
        bar_count = int(getattr(self._feature_engine, "bar_count", 0) or 0)
        if bar_count < 60:
            return "MEDIUM"
        try:
            features = self._feature_engine.compute_features()
            if features is None:
                return "MEDIUM"
            vol_30 = float(features[18])
            if vol_30 >= 4.0:
                return "HIGH"
            if vol_30 <= 1.5:
                return "LOW"
        except Exception:
            pass
        return "MEDIUM"


class FastTrigger:
    """Quantitative trigger reading bookTicker + aggTrade under a fresh regime."""

    def __init__(self, *, use_nanofenix: bool = True, nanofenix_model_path: str | None = None):
        self.current_regime: ExperimentalRegime | None = None
        self._current_bar: dict[str, Any] | None = None
        self._last_tick: MarketTick | None = None
        self._recent_closes: deque[float] = deque(maxlen=120)
        self._recent_ofi: deque[float] = deque(maxlen=120)
        self._recent_spreads: deque[float] = deque(maxlen=120)
        self._latest_trade_direction: float = 0.0
        self._latest_trade_volume: float = 0.0
        self._completed_bars = 0
        self._nanofenix = _NanoV3Adapter(model_path=nanofenix_model_path) if use_nanofenix else None

    @property
    def completed_bars(self) -> int:
        return self._completed_bars

    def update_regime(self, regime: ExperimentalRegime) -> None:
        self.current_regime = regime

    def on_book_ticker(
        self,
        *,
        bid: float,
        ask: float,
        bid_qty: float,
        ask_qty: float,
        timestamp: float | None = None,
    ) -> TriggerDecision | None:
        ts = float(timestamp if timestamp is not None else time.time())
        tick = MarketTick(
            bid=float(bid),
            ask=float(ask),
            bid_qty=float(bid_qty),
            ask_qty=float(ask_qty),
            timestamp=ts,
        )
        self._last_tick = tick
        decision = None

        current_second = math.floor(ts)
        if self._current_bar is None:
            self._current_bar = self._new_bar(current_second, tick)
            return None

        bar_second = int(self._current_bar["second"])
        if current_second != bar_second:
            completed = self._finish_bar()
            self._current_bar = self._new_bar(current_second, tick)
            if completed is not None:
                decision = self._evaluate_bar(completed)
        else:
            self._update_bar(self._current_bar, tick)

        return decision

    def on_agg_trade(
        self,
        *,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        timestamp: float | None = None,
    ) -> None:
        del timestamp
        if self._current_bar is None:
            return
        qty = float(qty)
        if qty <= 0:
            return
        if is_buyer_maker:
            self._current_bar["sell_vol"] += qty
            self._latest_trade_direction = -1.0
        else:
            self._current_bar["buy_vol"] += qty
            self._latest_trade_direction = 1.0
        self._latest_trade_volume = qty

    def _new_bar(self, second: int, tick: MarketTick) -> dict[str, Any]:
        return {
            "second": second,
            "timestamp": tick.timestamp,
            "open": tick.mid,
            "high": tick.mid,
            "low": tick.mid,
            "close": tick.mid,
            "tick_count": 1,
            "bid_qty": tick.bid_qty,
            "ask_qty": tick.ask_qty,
            "buy_vol": 0.0,
            "sell_vol": 0.0,
            "spread_sum": tick.spread_bps,
        }

    def _update_bar(self, bar: dict[str, Any], tick: MarketTick) -> None:
        mid = tick.mid
        bar["close"] = mid
        bar["high"] = max(float(bar["high"]), mid)
        bar["low"] = min(float(bar["low"]), mid)
        bar["tick_count"] = int(bar["tick_count"]) + 1
        bar["bid_qty"] = float(tick.bid_qty)
        bar["ask_qty"] = float(tick.ask_qty)
        bar["spread_sum"] = float(bar["spread_sum"]) + tick.spread_bps

    def _finish_bar(self) -> _CompletedBar | None:
        if self._current_bar is None:
            return None
        bar = _CompletedBar(
            timestamp=float(self._current_bar["timestamp"]),
            open=float(self._current_bar["open"]),
            high=float(self._current_bar["high"]),
            low=float(self._current_bar["low"]),
            close=float(self._current_bar["close"]),
            tick_count=int(self._current_bar["tick_count"]),
            bid_qty=float(self._current_bar["bid_qty"]),
            ask_qty=float(self._current_bar["ask_qty"]),
            buy_vol=float(self._current_bar["buy_vol"]),
            sell_vol=float(self._current_bar["sell_vol"]),
            spread_sum=float(self._current_bar["spread_sum"]),
        )
        self._recent_closes.append(bar.close)
        self._recent_ofi.append(bar.ofi)
        self._recent_spreads.append(bar.spread_bps)
        self._completed_bars += 1
        return bar

    def _evaluate_bar(self, bar: _CompletedBar) -> TriggerDecision | None:
        regime = self.current_regime
        if regime is None or not regime.is_fresh(bar.timestamp):
            return None
        if regime.bias == "NEUTRAL":
            return None
        if bar.spread_bps > regime.max_spread_bps:
            return None

        nano_result = self._nanofenix.on_bar(bar, regime) if self._nanofenix else None
        if nano_result and nano_result["signal"] in {"LONG", "SHORT"}:
            return TriggerDecision(
                action=str(nano_result["signal"]),
                confidence=min(0.95, max(regime.confidence, float(nano_result["confidence"]))),
                price=bar.close,
                timestamp=bar.timestamp,
                reason=f"NanoFenixV3 agreed with fresh {regime.bias} regime",
                regime_bias=regime.bias,
                ofi=bar.ofi,
                spread_bps=bar.spread_bps,
                pred_bps=float(nano_result["pred_bps"]),
                direction_accuracy=float(nano_result["direction_accuracy"]),
                source=str(nano_result["source"]),
            )

        return self._heuristic_decision(bar, regime)

    def _heuristic_decision(
        self,
        bar: _CompletedBar,
        regime: ExperimentalRegime,
    ) -> TriggerDecision | None:
        if abs(bar.ofi) < regime.min_ofi_required:
            return None
        if len(self._recent_closes) < 2:
            return None

        prev_close = self._recent_closes[-2]
        if prev_close <= 0:
            return None
        ret_bps = (bar.close - prev_close) / prev_close * 10000.0
        micro_alignment = 1.0 if (bar.ofi > 0 and ret_bps >= -0.5) or (bar.ofi < 0 and ret_bps <= 0.5) else 0.0

        if regime.bias == "LONG" and bar.ofi > 0 and micro_alignment > 0:
            confidence = min(0.92, 0.48 + regime.confidence * 0.25 + min(abs(bar.ofi), 1.0) * 0.15 + min(max(ret_bps, 0.0) / 8.0, 0.12))
            return TriggerDecision(
                action="LONG",
                confidence=confidence,
                price=bar.close,
                timestamp=bar.timestamp,
                reason="Fresh slow-brain LONG regime confirmed by OFI and 1s momentum",
                regime_bias=regime.bias,
                ofi=bar.ofi,
                spread_bps=bar.spread_bps,
                pred_bps=max(ret_bps, 0.0),
                direction_accuracy=0.55,
                source="heuristic",
            )

        if regime.bias == "SHORT" and bar.ofi < 0 and micro_alignment > 0:
            confidence = min(0.92, 0.48 + regime.confidence * 0.25 + min(abs(bar.ofi), 1.0) * 0.15 + min(max(-ret_bps, 0.0) / 8.0, 0.12))
            return TriggerDecision(
                action="SHORT",
                confidence=confidence,
                price=bar.close,
                timestamp=bar.timestamp,
                reason="Fresh slow-brain SHORT regime confirmed by OFI and 1s momentum",
                regime_bias=regime.bias,
                ofi=bar.ofi,
                spread_bps=bar.spread_bps,
                pred_bps=min(ret_bps, 0.0),
                direction_accuracy=0.55,
                source="heuristic",
            )

        return None
