#!/usr/bin/env python3
"""FenixAI v2.5 live canary monitor.

Tails a Fenix live-run log and emits structured status snapshots every
60 seconds. Designed to be safe to leave running unattended next to a
small live run — it ONLY reads the log file and prints to stdout. It
never sends orders, never calls Binance, never touches the engine.

Watches for:

  * Position openings on the exchange and the SL/TP that should follow.
  * Balance / cartera readings so you can confirm the engine knows how
    much capital it has.
  * Risk-manager and decision-agent final verdicts (so you can see
    whether the agent stack is producing coherent reasoning).
  * NanoFenix companion ticks and policy gates (allow_execute /
    hard-veto reasons).
  * Anything that looks like a CAUTION / accounting gap / error.

Emits an ALERT line within seconds if it sees an entry log that is not
followed by a protective-order log within ``--sltp-grace-seconds``.

Usage:

    python scripts/monitor_live_canary.py logs/live_canary_<ts>.log \\
        --interval 60 --sltp-grace-seconds 90

The script does not exit on its own — interrupt with Ctrl+C.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---- log patterns the monitor recognises ---------------------------------

RE_ENTRY = re.compile(
    r"(?:Order\s+(?:filled|opened|placed)|trade:executed|"
    r"OPEN_LONG|OPEN_SHORT|position[_\s]opened|"
    r"Live entry confirmed)",
    re.IGNORECASE,
)
RE_PROTECTIVE = re.compile(
    r"(?:_place_protective_orders|"
    r"protective[_\s]order|"
    r"Stop[_\s]loss\s+(?:placed|set)|"
    r"Take[_\s]profit\s+(?:placed|set)|"
    r"SL\s*[:=]|TP\s*[:=]|"
    r"sl_order|tp_order|"
    r"reduceOnly)",
    re.IGNORECASE,
)
RE_FINAL_DECISION = re.compile(r"FINAL\s+DECISION[:\s]+(\S+)", re.IGNORECASE)
RE_TECHNICAL = re.compile(r"\bTechnical[:\s]+(BUY|SELL|HOLD|HOLD_TECHNICAL)", re.IGNORECASE)
RE_QABBA = re.compile(r"\bQABBA[:\s]+(BUY|SELL|HOLD|HOLD_QABBA)", re.IGNORECASE)
RE_DECISION_AGENT = re.compile(r"decision_agent.*?(BUY|SELL|HOLD)", re.IGNORECASE)
RE_RISK = re.compile(
    r"(?:Risk[_\s]Manager|RuntimeRiskManager).*?(APPROVED|REJECTED|BLOCKED|CAUTION|NORMAL|DRAWDOWN)",
    re.IGNORECASE,
)
RE_BALANCE = re.compile(
    r"(?:balance|usable_balance|wallet_balance|available_capital).{0,40}\$?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
RE_POSITION_SIZE = re.compile(
    r"(?:position[_\s]size|qty|quantity)[\s:=]+\$?\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
RE_NANO_TICK = re.compile(r"NanoFenixV3.*?ML=(\w+)\([-+]?\d", re.IGNORECASE)
RE_NANO_POLICY = re.compile(
    r"nanofenix.*?(allow_execute|hard[_\s]veto|companion_not_ready|direction_mismatch|high_uncertainty|stale_signal)",
    re.IGNORECASE,
)
RE_ACCOUNTING_GAP = re.compile(r"completed_with_accounting_gap|accounting[_\s]gap", re.IGNORECASE)
RE_ERROR = re.compile(r"\b(?:ERROR|CRITICAL|Traceback|Exception)\b")
RE_CYCLE_DONE = re.compile(r"Analysis cycle completed in ([\d.]+)s", re.IGNORECASE)


# ---- monitor state -------------------------------------------------------


@dataclass
class MonitorState:
    last_entry_line_no: int | None = None
    last_entry_ts: float | None = None
    last_entry_text: str | None = None
    last_protective_after_entry_ts: float | None = None
    cycles: int = 0
    final_decisions: Counter = field(default_factory=Counter)
    technical_decisions: Counter = field(default_factory=Counter)
    qabba_decisions: Counter = field(default_factory=Counter)
    risk_states: Counter = field(default_factory=Counter)
    nano_signals: Counter = field(default_factory=Counter)
    nano_policy_hits: Counter = field(default_factory=Counter)
    balance_observations: deque = field(default_factory=lambda: deque(maxlen=20))
    position_size_observations: deque = field(default_factory=lambda: deque(maxlen=20))
    errors: list[str] = field(default_factory=list)
    accounting_gaps: int = 0
    cycle_durations: deque = field(default_factory=lambda: deque(maxlen=20))
    sltp_alerts: list[str] = field(default_factory=list)


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def consume_line(line: str, line_no: int, state: MonitorState, sltp_grace: float) -> list[str]:
    """Update state from a log line. Return any urgent alerts to print now."""
    alerts: list[str] = []
    now = time.time()

    if RE_ENTRY.search(line):
        # Don't flag the same entry twice on a multi-line confirmation block.
        if state.last_entry_ts is None or (now - state.last_entry_ts) > 30:
            state.last_entry_ts = now
            state.last_entry_line_no = line_no
            state.last_entry_text = line.strip()[:220]
            state.last_protective_after_entry_ts = None
            alerts.append(f"[{_stamp()}] ENTRY DETECTED on line {line_no}: {state.last_entry_text}")

    if RE_PROTECTIVE.search(line):
        # A protective-order log within the SL/TP grace window after an entry
        # is what we expect.
        if state.last_entry_ts is not None and state.last_protective_after_entry_ts is None:
            elapsed = now - state.last_entry_ts
            if elapsed <= sltp_grace:
                state.last_protective_after_entry_ts = now
                alerts.append(
                    f"[{_stamp()}] OK SL/TP signal seen {elapsed:.1f}s after entry: {line.strip()[:180]}"
                )

    if state.last_entry_ts is not None and state.last_protective_after_entry_ts is None:
        elapsed = now - state.last_entry_ts
        if elapsed > sltp_grace:
            # Fire the alert ONCE, then clear the entry so we don't spam.
            msg = (
                f"[{_stamp()}] ALERT: {elapsed:.0f}s after entry and NO protective-order line "
                f"detected. This is the bug the v2.5 release checklist flagged. "
                f"Entry was: {state.last_entry_text}"
            )
            alerts.append(msg)
            state.sltp_alerts.append(msg)
            state.last_entry_ts = None  # silence further alerts until next entry

    m = RE_FINAL_DECISION.search(line)
    if m:
        state.final_decisions[m.group(1).upper()] += 1
        state.cycles += 1

    m = RE_TECHNICAL.search(line)
    if m:
        state.technical_decisions[m.group(1).upper()] += 1

    m = RE_QABBA.search(line)
    if m:
        state.qabba_decisions[m.group(1).upper()] += 1

    m = RE_RISK.search(line)
    if m:
        state.risk_states[m.group(1).upper()] += 1

    m = RE_BALANCE.search(line)
    if m:
        try:
            state.balance_observations.append(float(m.group(1)))
        except ValueError:
            pass

    m = RE_POSITION_SIZE.search(line)
    if m:
        try:
            state.position_size_observations.append(float(m.group(1)))
        except ValueError:
            pass

    m = RE_NANO_TICK.search(line)
    if m:
        state.nano_signals[m.group(1).upper()] += 1

    m = RE_NANO_POLICY.search(line)
    if m:
        state.nano_policy_hits[m.group(1).lower().replace(" ", "_")] += 1

    if RE_ACCOUNTING_GAP.search(line):
        state.accounting_gaps += 1
        alerts.append(f"[{_stamp()}] ALERT: accounting gap mentioned: {line.strip()[:180]}")

    m = RE_CYCLE_DONE.search(line)
    if m:
        try:
            state.cycle_durations.append(float(m.group(1)))
        except ValueError:
            pass

    if RE_ERROR.search(line):
        state.errors.append(f"line {line_no}: {line.strip()[:200]}")

    return alerts


def print_snapshot(state: MonitorState) -> None:
    print("=" * 78)
    print(f"[{_stamp()}] FENIX LIVE CANARY MONITOR SNAPSHOT")
    print("-" * 78)
    print(f"Analysis cycles seen          : {state.cycles}")
    if state.cycle_durations:
        avg = sum(state.cycle_durations) / len(state.cycle_durations)
        print(f"Cycle duration (avg/last)     : {avg:.1f}s / {state.cycle_durations[-1]:.1f}s")

    print(f"Final-decision distribution   : {dict(state.final_decisions)}")
    print(f"Technical agent distribution  : {dict(state.technical_decisions)}")
    print(f"QABBA agent distribution      : {dict(state.qabba_decisions)}")
    print(f"Risk-manager states           : {dict(state.risk_states)}")

    if state.balance_observations:
        print(
            f"Balance observed (min/last)   : ${min(state.balance_observations):.2f}"
            f" / ${state.balance_observations[-1]:.2f}"
        )
    else:
        print("Balance observed              : <not seen yet>")

    if state.position_size_observations:
        print(f"Position sizes seen (last 3)  : {list(state.position_size_observations)[-3:]}")

    print(f"NanoFenix signals             : {dict(state.nano_signals)}")
    if state.nano_policy_hits:
        print(f"NanoFenix policy gate hits    : {dict(state.nano_policy_hits)}")

    if state.last_entry_ts is not None:
        elapsed = time.time() - state.last_entry_ts
        sl_tp_status = (
            "OK (SL/TP seen)"
            if state.last_protective_after_entry_ts is not None
            else f"NO SL/TP YET ({elapsed:.0f}s elapsed)"
        )
        print(f"Last entry status             : {sl_tp_status}")
    else:
        print("Last entry status             : <no entries yet>")

    print(f"Accounting gaps               : {state.accounting_gaps}")
    print(f"Errors seen                   : {len(state.errors)}")
    if state.errors:
        for e in state.errors[-3:]:
            print(f"  - {e}")
    if state.sltp_alerts:
        print("SL/TP alerts fired so far     :")
        for a in state.sltp_alerts[-3:]:
            print(f"  - {a}")
    print("=" * 78)


def follow(path: Path, sltp_grace: float, interval: float) -> None:
    state = MonitorState()
    line_no = 0
    last_snapshot = time.time()
    print(
        f"[{_stamp()}] monitoring {path} (snapshot every {interval}s, "
        f"SL/TP grace {sltp_grace}s). Ctrl+C to stop."
    )

    # First, drain any existing content so we don't double-flag.
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line_no += 1
            consume_line(line, line_no, state, sltp_grace)

    print(f"[{_stamp()}] initial drain done at line {line_no}. Following tail...")
    print_snapshot(state)

    # Now follow.
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)  # end
        while True:
            chunk = fh.readlines()
            for line in chunk:
                line_no += 1
                alerts = consume_line(line, line_no, state, sltp_grace)
                for a in alerts:
                    print(a, flush=True)
            now = time.time()
            if now - last_snapshot >= interval:
                print_snapshot(state)
                last_snapshot = now
            time.sleep(0.5)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("log_path", type=Path, help="Path to the live canary log file")
    p.add_argument(
        "--interval", type=float, default=60.0, help="Snapshot interval in seconds (default 60)"
    )
    p.add_argument(
        "--sltp-grace-seconds",
        type=float,
        default=90.0,
        help="Seconds after an entry before alerting if no SL/TP log appears (default 90)",
    )
    args = p.parse_args()
    if not args.log_path.exists():
        print(f"Waiting for {args.log_path} to appear...")
        for _ in range(120):
            if args.log_path.exists():
                break
            time.sleep(1)
        else:
            print(f"Log file did not appear: {args.log_path}", file=sys.stderr)
            return 1
    try:
        follow(args.log_path, args.sltp_grace_seconds, args.interval)
    except KeyboardInterrupt:
        print(f"[{_stamp()}] interrupted, final snapshot:")
        # Can't reach state here easily; the last snapshot is the recent one.
    return 0


if __name__ == "__main__":
    sys.exit(main())
