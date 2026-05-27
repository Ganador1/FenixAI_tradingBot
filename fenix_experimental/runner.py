from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fast_trigger import FastTrigger
from .models import ExperimentalRegime, TriggerDecision
from .slow_brain import SlowBrain


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class _Position:
    side: str
    entry_price: float
    quantity: float
    entry_time: float
    trade_id: str


class ExperimentalPaperRunner:
    """Paper runner for the experimental slow-brain/fast-trigger line."""

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        run_tag: str,
        output_dir: str | Path = "logs",
        slot_name: str | None = None,
        position_size_usd: float = 100.0,
        stop_loss_pct: float = 0.0030,
        take_profit_pct: float = 0.0040,
        close_open_on_finalize: bool = True,
        use_nanofenix: bool = True,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.run_tag = run_tag
        self.slot_name = slot_name or f"experimental-{self.symbol.lower()}-{self.timeframe}"
        self.position_size_usd = float(position_size_usd)
        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)
        self.close_open_on_finalize = close_open_on_finalize

        self.slow_brain = SlowBrain(ttl_seconds=15)
        self.fast_trigger = FastTrigger(use_nanofenix=use_nanofenix)

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_path = self.output_dir / f"live_slot_events_{self.slot_name}_{self.run_tag}.jsonl"
        self.summary_path = self.output_dir / f"live_slot_summary_{self.slot_name}_{self.run_tag}.json"

        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.last_price: float | None = None
        self.position: _Position | None = None
        self._trade_seq = 0
        self._closed_trades: list[dict[str, Any]] = []
        self._event_counts: Counter[str] = Counter()
        self._decision_counts: Counter[str] = Counter()

    def on_agent_snapshot(
        self,
        *,
        technical_report: dict[str, Any] | None,
        qabba_report: dict[str, Any] | None,
        visual_report: dict[str, Any] | None = None,
        sentiment_report: dict[str, Any] | None = None,
        web3_report: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> ExperimentalRegime:
        ts = float(timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp())
        self._touch_start(ts)
        regime = self.slow_brain.publish_regime(
            technical_report=technical_report,
            qabba_report=qabba_report,
            timestamp=ts,
        )
        self.fast_trigger.update_regime(regime)
        self._emit(
            "regime_update",
            {
                "regime": regime.to_dict(),
                "technical_report": dict(technical_report or {}),
                "qabba_report": dict(qabba_report or {}),
                "visual_report": dict(visual_report or {}),
                "sentiment_report": dict(sentiment_report or {}),
                "web3_report": dict(web3_report or {}),
            },
            ts,
        )
        return regime

    def on_book_ticker(
        self,
        *,
        bid: float,
        ask: float,
        bid_qty: float,
        ask_qty: float,
        timestamp: float | None = None,
    ) -> TriggerDecision | None:
        ts = float(timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp())
        self._touch_start(ts)
        self.last_price = (float(bid) + float(ask)) / 2.0
        self.update_market_price(price=self.last_price, timestamp=ts)
        decision = self.fast_trigger.on_book_ticker(
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            timestamp=ts,
        )
        if decision is not None:
            self.apply_trigger_decision(decision)
        return decision

    def on_agg_trade(
        self,
        *,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        timestamp: float | None = None,
    ) -> None:
        ts = float(timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp())
        self._touch_start(ts)
        self.fast_trigger.on_agg_trade(
            price=price,
            qty=qty,
            is_buyer_maker=is_buyer_maker,
            timestamp=ts,
        )

    def apply_trigger_decision(self, decision: TriggerDecision) -> None:
        ts = float(decision.timestamp)
        self._touch_start(ts)
        engine_decision = decision.to_engine_decision()
        self._decision_counts[engine_decision] += 1
        self._emit(
            "final_decision",
            {
                "decision": engine_decision,
                "confidence": decision.confidence,
                "reasoning": decision.reason,
                "full_data": decision.to_payload(),
            },
            ts,
        )

        if decision.action == "HOLD":
            return

        side = "LONG" if decision.action == "LONG" else "SHORT"
        if self.position is None:
            self._open_position(side=side, price=decision.price, timestamp=ts)
            return

        if self.position.side == side:
            self._emit(
                "position:skip_same_side",
                {
                    "side": side,
                    "entry_price": self.position.entry_price,
                    "quantity": self.position.quantity,
                    "reason": "Same side experimental position already open",
                },
                ts,
            )
            return

        self._close_position(price=decision.price, timestamp=ts, reason="experimental_flip", notes=f"Opposite signal: {engine_decision}")
        self._open_position(side=side, price=decision.price, timestamp=ts)

    def update_market_price(self, *, price: float, timestamp: float | None = None) -> None:
        ts = float(timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp())
        self._touch_start(ts)
        self.last_price = float(price)
        if self.position is None:
            return
        pnl_pct = self._position_pnl_pct(self.position, self.last_price)
        if pnl_pct <= -self.stop_loss_pct:
            self._close_position(price=self.last_price, timestamp=ts, reason="stop_loss", notes="Experimental stop loss")
        elif pnl_pct >= self.take_profit_pct:
            self._close_position(price=self.last_price, timestamp=ts, reason="take_profit", notes="Experimental take profit")

    def finalize(self, *, status: str = "completed") -> dict[str, Any]:
        end_ts = datetime.now(timezone.utc).timestamp()
        if self.position is not None and self.close_open_on_finalize and self.last_price:
            self._close_position(
                price=self.last_price,
                timestamp=end_ts,
                reason="slot_end",
                notes="Closed on runner finalize",
            )
        self.ended_at = end_ts

        wins = sum(1 for trade in self._closed_trades if float(trade["pnl"]) > 0)
        losses = sum(1 for trade in self._closed_trades if float(trade["pnl"]) <= 0)
        total_pnl = sum(float(trade["pnl"]) for trade in self._closed_trades)
        gross_wins = sum(float(trade["pnl"]) for trade in self._closed_trades if float(trade["pnl"]) > 0)
        gross_losses = abs(sum(float(trade["pnl"]) for trade in self._closed_trades if float(trade["pnl"]) < 0))
        event_counts = dict(self._event_counts)

        summary = {
            "event": "slot_summary",
            "status": status,
            "run_tag": self.run_tag,
            "slot_name": self.slot_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "mode": "paper",
            "started_at": _iso(self.started_at or end_ts),
            "ended_at": _iso(end_ts),
            "duration_sec": round(end_ts - (self.started_at or end_ts), 2),
            "event_log_path": str(self.event_log_path),
            "summary_path": str(self.summary_path),
            "events": {
                "total_events": sum(event_counts.values()),
                "event_counts": event_counts,
                "decision_counts": dict(self._decision_counts),
                "risk_blocked": 0,
                "judge_blocked": 0,
                "filter_blocked": 0,
                "fast_decisions": event_counts.get("final_decision", 0),
                "position_closed": event_counts.get("position:closed", 0),
            },
            "risk_status": {
                "mode": "NORMAL",
                "risk_bias": 1.0,
                "block_trading": False,
                "reason": "Experimental paper runner",
                "exposure": {
                    "total_exposure": 0.0 if self.position is None else self.position_size_usd,
                    "max_exposure": self.position_size_usd,
                    "utilization_pct": 0.0 if self.position is None else 100.0,
                    "positions_count": 0 if self.position is None else 1,
                    "positions": {} if self.position is None else {
                        self.symbol: {
                            "side": self.position.side,
                            "entry_price": self.position.entry_price,
                            "quantity": self.position.quantity,
                        }
                    },
                },
                "total_trades": len(self._closed_trades),
                "wins": wins,
                "winning_trades": wins,
                "losses": losses,
                "win_rate": (wins / len(self._closed_trades)) if self._closed_trades else 0.0,
                "total_pnl": round(total_pnl, 8),
                "avg_pnl": round(total_pnl / len(self._closed_trades), 8) if self._closed_trades else 0.0,
                "avg_win": round(gross_wins / wins, 8) if wins else 0.0,
                "avg_loss": round(-gross_losses / losses, 8) if losses else 0.0,
                "loss_streak": 0,
                "drawdown_pct": 0.0,
                "daily_pnl": round(total_pnl, 8),
                "daily_loss_pct": 0.0,
                "peak_balance": self.position_size_usd + max(total_pnl, 0.0),
                "current_balance": self.position_size_usd + total_pnl,
            },
            "engine_status": {
                "running": False,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "paper_trading": True,
                "kline_count": self.fast_trigger.completed_bars,
                "consecutive_holds": 0,
                "last_decision_time": None if not self._decision_counts else _iso(end_ts),
                "current_price": self.last_price,
                "langgraph_available": False,
            },
            "closed_trades": self._closed_trades,
            "error": None,
        }
        self.summary_path.write_text(json.dumps(summary, indent=2))
        return summary

    def _touch_start(self, timestamp: float) -> None:
        if self.started_at is None:
            self.started_at = timestamp

    def _emit(self, event: str, payload: dict[str, Any], timestamp: float) -> None:
        self._event_counts[event] += 1
        envelope = {
            "timestamp": _iso(timestamp),
            "event": event,
            "payload": payload,
            "run_tag": self.run_tag,
            "slot_name": self.slot_name,
            "slot_index": 1,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "mode": "paper",
            "experiment": "fenix_experimental",
            "experiment_id": 1,
        }
        with self.event_log_path.open("a") as handle:
            handle.write(json.dumps(envelope) + "\n")

    def _open_position(self, *, side: str, price: float, timestamp: float) -> None:
        self._trade_seq += 1
        quantity = self.position_size_usd / price if price > 0 else 0.0
        self.position = _Position(
            side=side,
            entry_price=price,
            quantity=quantity,
            entry_time=timestamp,
            trade_id=f"{self.run_tag}-{self._trade_seq}",
        )
        self._emit(
            "position:opened",
            {
                "symbol": self.symbol,
                "side": side,
                "entry_price": round(price, 6),
                "quantity": round(quantity, 8),
                "entry_time": _iso(timestamp),
                "trade_id": self.position.trade_id,
            },
            timestamp,
        )

    def _close_position(self, *, price: float, timestamp: float, reason: str, notes: str) -> None:
        if self.position is None:
            return
        pnl_pct = self._position_pnl_pct(self.position, price)
        pnl_usd = self.position_size_usd * pnl_pct
        payload = {
            "symbol": self.symbol,
            "side": self.position.side,
            "entry_price": round(self.position.entry_price, 6),
            "exit_price": round(price, 6),
            "quantity": round(self.position.quantity, 8),
            "entry_time": _iso(self.position.entry_time),
            "exit_time": _iso(timestamp),
            "pnl": round(pnl_usd, 8),
            "pnl_pct": round(pnl_pct * 100.0, 8),
            "exit_reason": reason,
            "exit_notes": notes,
            "duration_minutes": round((timestamp - self.position.entry_time) / 60.0, 8),
            "trade_id": self.position.trade_id,
            "timestamp": _iso(timestamp),
        }
        self._closed_trades.append(payload)
        self.position = None
        self._emit("position:closed", payload, timestamp)

    @staticmethod
    def _position_pnl_pct(position: _Position, price: float) -> float:
        if position.entry_price <= 0:
            return 0.0
        if position.side == "LONG":
            return (price - position.entry_price) / position.entry_price
        return (position.entry_price - price) / position.entry_price
