from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOG_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_FLIP_BLOCK_RE = re.compile(r"FLIP BLOCKED: (?P<position>[A-Z]+)→(?P<decision>[A-Z]+)")
_NANOFENIX_BLOCK_RE = re.compile(
    r"NanoFenix companion policy \((?P<reason>[^)]+)\): side=(?P<side>[A-Z]+) decision=(?P<decision>[A-Z]+) effective_signal=None"
)
_TRADE_EXEC_RE = re.compile(r"Trade executed: (?P<decision>BUY|SELL)\b")


@dataclass
class RuntimeRegression:
    blocked_line: int
    executed_line: int
    blocked_decision: str
    reason: str
    blocked_timestamp: str
    executed_timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked_line": self.blocked_line,
            "executed_line": self.executed_line,
            "blocked_decision": self.blocked_decision,
            "reason": self.reason,
            "blocked_timestamp": self.blocked_timestamp,
            "executed_timestamp": self.executed_timestamp,
        }


def _parse_log_entries(log_path: str | Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(Path(log_path).read_text().splitlines(), start=1):
        ts_match = _LOG_TS_RE.match(raw_line)
        ts = ts_match.group("ts") if ts_match else ""
        trade_match = _TRADE_EXEC_RE.search(raw_line)
        flip_match = _FLIP_BLOCK_RE.search(raw_line)
        nano_match = _NANOFENIX_BLOCK_RE.search(raw_line)
        entries.append(
            {
                "line": idx,
                "raw": raw_line,
                "timestamp": ts,
                "trade_decision": trade_match.group("decision") if trade_match else None,
                "flip_block": flip_match.groupdict() if flip_match else None,
                "nano_block": nano_match.groupdict() if nano_match else None,
            }
        )
    return entries


def _find_block_followed_by_execution(
    entries: list[dict[str, Any]],
    *,
    block_key: str,
    reason_prefix: str,
    max_window_lines: int = 80,
) -> list[RuntimeRegression]:
    regressions: list[RuntimeRegression] = []
    for idx, entry in enumerate(entries):
        block = entry.get(block_key)
        if not block:
            continue
        blocked_decision = str(block["decision"]).upper()
        for later in entries[idx + 1 : idx + 1 + max_window_lines]:
            trade_decision = later.get("trade_decision")
            if trade_decision is None:
                continue
            if str(trade_decision).upper() != blocked_decision:
                continue
            regressions.append(
                RuntimeRegression(
                    blocked_line=int(entry["line"]),
                    executed_line=int(later["line"]),
                    blocked_decision=blocked_decision,
                    reason=f"{reason_prefix}:{block.get('reason', 'blocked')}",
                    blocked_timestamp=str(entry["timestamp"]),
                    executed_timestamp=str(later["timestamp"]),
                )
            )
            break
    return regressions


def detect_flip_blocked_then_trade(log_path: str | Path) -> list[dict[str, Any]]:
    entries = _parse_log_entries(log_path)
    return [
        reg.to_dict()
        for reg in _find_block_followed_by_execution(
            entries, block_key="flip_block", reason_prefix="flip_blocked"
        )
    ]


def detect_nanofenix_blocked_then_trade(log_path: str | Path) -> list[dict[str, Any]]:
    entries = _parse_log_entries(log_path)
    return [
        reg.to_dict()
        for reg in _find_block_followed_by_execution(
            entries, block_key="nano_block", reason_prefix="nanofenix_policy"
        )
    ]


def compare_summary_vs_closed_trades(
    summary_path: str | Path, event_path: str | Path
) -> dict[str, Any]:
    summary = json.loads(Path(summary_path).read_text())
    risk_status = dict(summary.get("risk_status") or {})
    actual_wins = 0
    actual_losses = 0
    actual_total_pnl = 0.0
    closed_trades = 0

    with Path(event_path).open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("event") != "position:closed":
                continue
            payload = dict(row.get("payload") or {})
            pnl = float(payload.get("pnl") or 0.0)
            closed_trades += 1
            actual_total_pnl += pnl
            if pnl > 0:
                actual_wins += 1
            else:
                actual_losses += 1

    summary_trades = int(risk_status.get("total_trades") or 0)
    summary_wins = int(risk_status.get("wins") or 0)
    summary_losses = int(risk_status.get("losses") or 0)
    summary_pnl = float(risk_status.get("total_pnl") or 0.0)

    mismatches: list[str] = []
    if summary_trades != closed_trades:
        mismatches.append("total_trades")
    if summary_wins != actual_wins:
        mismatches.append("wins")
    if summary_losses != actual_losses:
        mismatches.append("losses")
    if round(summary_pnl, 8) != round(actual_total_pnl, 8):
        mismatches.append("total_pnl")

    return {
        "summary_total_trades": summary_trades,
        "summary_wins": summary_wins,
        "summary_losses": summary_losses,
        "summary_total_pnl": round(summary_pnl, 8),
        "actual_total_trades": closed_trades,
        "actual_wins": actual_wins,
        "actual_losses": actual_losses,
        "actual_total_pnl": round(actual_total_pnl, 8),
        "mismatch_fields": mismatches,
    }


def analyze_runtime_regressions(
    *,
    raw_log_path: str | Path,
    summary_path: str | Path,
    event_path: str | Path,
) -> dict[str, Any]:
    return {
        "flip_blocked_then_trade": detect_flip_blocked_then_trade(raw_log_path),
        "nanofenix_blocked_then_trade": detect_nanofenix_blocked_then_trade(raw_log_path),
        "summary_vs_closed_trades": compare_summary_vs_closed_trades(summary_path, event_path),
    }


def main() -> None:
    raw_log = Path("logs/fenix_20260305_192946.log")
    summary = Path("logs/live_slot_summary_nanov3.json")
    events = Path("logs/live_slot_events_nanov3.jsonl")
    report = analyze_runtime_regressions(
        raw_log_path=raw_log,
        summary_path=summary,
        event_path=events,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
