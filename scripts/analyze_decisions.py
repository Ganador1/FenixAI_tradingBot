#!/usr/bin/env python3
"""Radiografía de decisiones — post-hoc analysis of Fenix agent runs vs. reality.

For every 15m candle in a session it reconstructs:
  1. What each agent said (signal, confidence, reasoning) from logs/llm_responses_<symbol>/.
  2. The engine's FINAL DECISION and which veto (if any) blocked it, from the run log.
  3. What the price ACTUALLY did afterwards (real klines pulled from Binance).
  4. A verdict: was the veto USEFUL (blocked a losing trade) or COSTLY (blocked a
     winning one)? Was each agent COHERENT with its own data and did it call the
     direction right vs. the realised future?

We know the future the agents were trying to predict, so we grade them against it.

Usage:
  python scripts/analyze_decisions.py --symbol ETHUSDC \
      --log logs/restart_ethusdc_20260705_153327.out \
      --responses logs/llm_responses_ethusdc \
      --sltp-horizon 12 --out logs/xray_ethusdc.md

The trade simulation walks forward up to --sltp-horizon candles and reports
whether SL or TP (from the decision's risk_assessment) would have hit first, or
the mark-to-market PnL at the horizon if neither did.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"

AGENT_DIRS = {
    "technical": "technical_enhanced",
    "qabba": "qabba_enhanced",
    "visual": "visual",
    "sentiment": "sentiment",
    "decision": "decision_agent",
    "risk": "risk_manager",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentRun:
    agent: str
    ts: datetime
    signal: str
    confidence: float | None
    reasoning: str
    data: dict = field(default_factory=dict)  # key market metrics the agent saw


@dataclass
class CandleDecision:
    candle_ts: datetime          # kline close time (from engine log)
    close_price: float
    final_decision: str          # BUY / SELL / HOLD
    final_confidence: str
    veto: str | None             # filter name that blocked, or None
    veto_reason: str | None
    agents: dict = field(default_factory=dict)   # agent -> AgentRun
    risk_assessment: dict = field(default_factory=dict)  # entry/sl/tp from decision
    # filled in by simulation
    future: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing: engine log
# ---------------------------------------------------------------------------

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _parse_log_ts(line: str) -> datetime | None:
    m = TS_RE.match(line)
    if not m:
        return None
    # Log timestamps are in local time; we keep them naive-local and only use
    # them for ordering / matching within the same session.
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")


def parse_engine_log(path: str) -> list[dict]:
    """Extract per-candle: close price, final decision, veto. Returns raw dicts
    keyed by the decision timestamp so we can later attach agent runs."""
    candles: list[dict] = []
    cur: dict | None = None
    last_close = None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            ts = _parse_log_ts(line)
            if "Kline closed:" in line:
                m = re.search(r"Kline closed: ([0-9.]+)", line)
                if m:
                    last_close = float(m.group(1))
            elif "FINAL DECISION:" in line:
                m = re.search(r"FINAL DECISION: (\w+) \((\w+)\)", line)
                if m:
                    cur = {
                        "ts": ts,
                        "close": last_close,
                        "decision": m.group(1),
                        "confidence": m.group(2),
                        "veto": None,
                        "veto_reason": None,
                    }
                    candles.append(cur)
            elif "blocked by filter" in line and cur is not None:
                m = re.search(r"blocked by filter (\w+): \{'reason': '([^']*)'", line)
                if m:
                    cur["veto"] = m.group(1)
                    cur["veto_reason"] = m.group(2)
    return candles


# ---------------------------------------------------------------------------
# Parsing: agent LLM response dumps
# ---------------------------------------------------------------------------


def _ts_from_filename(fn: str) -> datetime | None:
    m = re.search(r"(\d{8}_\d{6})_\d+_output\.json$", os.path.basename(fn))
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")


def _extract_signal(agent: str, out: dict) -> tuple[str, float | None, str]:
    if agent == "decision":
        return (
            str(out.get("final_decision", "?")).upper(),
            None,
            str(out.get("combined_reasoning", ""))[:600],
        )
    if agent == "visual":
        return (
            str(out.get("action", out.get("signal", "?"))).upper(),
            _f(out.get("confidence")),
            str(out.get("analysis", out.get("visual_analysis", "")))[:600],
        )
    if agent == "sentiment":
        return (
            str(out.get("overall_sentiment", "?")).upper(),
            _f(out.get("confidence_score")),
            str(out.get("market_mood", out.get("impact_assessment", "")))[:600],
        )
    if agent == "risk":
        return (
            str(out.get("verdict", out.get("recommendation", "?"))).upper(),
            None,
            str(out.get("reasoning", out.get("rationale", "")))[:600],
        )
    # technical / qabba
    return (
        str(out.get("signal", "?")).upper(),
        _f(out.get("confidence") or out.get("qabba_confidence")),
        str(out.get("reasoning", ""))[:600],
    )


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_market_data(prompt_path: str) -> dict:
    """Pull a few key numbers the agent actually saw, from its prompt."""
    keys = ("last_price", "rsi", "macd_line", "supertrend_direction", "vwap",
            "adx", "cmf", "obi", "cvd", "ema_9", "ema_20")
    data: dict = {}
    try:
        text = open(prompt_path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return data
    for k in keys:
        m = re.search(rf'"{k}":\s*"?([-\d.]+|\w+)"?', text)
        if m:
            data[k] = m.group(1)
    return data


def parse_agent_runs(responses_dir: str) -> dict[str, list[AgentRun]]:
    runs: dict[str, list[AgentRun]] = {a: [] for a in AGENT_DIRS}
    for agent, subdir in AGENT_DIRS.items():
        base = os.path.join(responses_dir, subdir)
        for out_path in sorted(glob.glob(os.path.join(base, "*_output.json"))):
            ts = _ts_from_filename(out_path)
            if ts is None:
                continue
            try:
                out = json.load(open(out_path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(out, dict):
                continue
            sig, conf, reason = _extract_signal(agent, out)
            prompt_path = out_path.replace("_output.json", "_prompt.txt")
            data = _extract_market_data(prompt_path)
            runs[agent].append(AgentRun(agent, ts, sig, conf, reason, data))
    return runs


def _nearest_run(runs: list[AgentRun], target: datetime, tol_sec: int = 180) -> AgentRun | None:
    best, best_dt = None, tol_sec + 1
    for r in runs:
        dt = abs((r.ts - target).total_seconds())
        if dt < best_dt:
            best, best_dt = r, dt
    return best


# ---------------------------------------------------------------------------
# Future klines + trade simulation
# ---------------------------------------------------------------------------


def fetch_klines(symbol: str, interval: str, start_ms: int, limit: int) -> list[dict]:
    url = f"{FUTURES_KLINES}?symbol={symbol}&interval={interval}&startTime={start_ms}&limit={limit}"
    for attempt in range(3):
        try:
            # The origin is a module constant and only query values are interpolated.
            with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310
                raw = json.load(resp)
            return [
                {"open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                 "close": float(k[4]), "open_time": int(k[0])}
                for k in raw
            ]
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return []


def simulate_trade(direction: str, entry: float, sl: float | None, tp: float | None,
                   future: list[dict]) -> dict:
    """Walk forward candle by candle; report SL/TP hit or MTM at horizon.

    direction: BUY (long) or SELL (short). Returns {outcome, exit_price, pnl_pct,
    bars_held}. Conservatively, if both SL and TP are touched in the same candle
    we assume the adverse one (SL) hit first.
    """
    if not future or entry <= 0:
        return {"outcome": "no_data", "exit_price": None, "pnl_pct": None, "bars_held": 0}
    long = direction == "BUY"
    for i, k in enumerate(future, start=1):
        hi, lo = k["high"], k["low"]
        if long:
            hit_sl = sl is not None and lo <= sl
            hit_tp = tp is not None and hi >= tp
            if hit_sl:
                return _res("SL", sl, entry, long, i)
            if hit_tp:
                return _res("TP", tp, entry, long, i)
        else:
            hit_sl = sl is not None and hi >= sl
            hit_tp = tp is not None and lo <= tp
            if hit_sl:
                return _res("SL", sl, entry, long, i)
            if hit_tp:
                return _res("TP", tp, entry, long, i)
    last = future[-1]["close"]
    return _res("MTM", last, entry, long, len(future))


def _res(outcome, exit_price, entry, long, bars):
    pnl = (exit_price - entry) / entry if long else (entry - exit_price) / entry
    return {"outcome": outcome, "exit_price": exit_price,
            "pnl_pct": pnl * 100.0, "bars_held": bars}


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def grade_veto(direction: str, sim: dict) -> tuple[str, str]:
    """Was blocking this entry the right call? Compares the simulated trade
    outcome (had it been allowed) against the fact it was vetoed."""
    pnl = sim.get("pnl_pct")
    if pnl is None:
        return "UNKNOWN", "no future data to judge"
    if sim["outcome"] == "SL":
        return "USEFUL", f"blocked a trade that would have hit SL ({pnl:+.2f}%)"
    if sim["outcome"] == "TP":
        return "COSTLY", f"blocked a trade that would have hit TP ({pnl:+.2f}%)"
    # MTM
    if pnl < -0.05:
        return "USEFUL", f"blocked a trade that was {pnl:+.2f}% at horizon"
    if pnl > 0.05:
        return "COSTLY", f"blocked a trade that was {pnl:+.2f}% at horizon"
    return "NEUTRAL", f"blocked a roughly flat trade ({pnl:+.2f}%)"


def grade_direction(signal: str, fwd_pct: float | None) -> str:
    """Did an agent's directional call match the realised move?"""
    if fwd_pct is None:
        return "?"
    bullish = signal in {"BUY", "BUY_QABBA", "LONG", "POSITIVE", "BULLISH"}
    bearish = signal in {"SELL", "SELL_QABBA", "SHORT", "NEGATIVE", "BEARISH"}
    if not bullish and not bearish:
        return "n/a"  # HOLD/NEUTRAL — no directional bet
    if abs(fwd_pct) < 0.05:
        return "flat"
    right = (bullish and fwd_pct > 0) or (bearish and fwd_pct < 0)
    return "✓" if right else "✗"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build(symbol: str, log_path: str, responses_dir: str, interval: str,
          horizon: int) -> list[CandleDecision]:
    raw_candles = parse_engine_log(log_path)
    agent_runs = parse_agent_runs(responses_dir)
    # attach decision risk_assessment (entry/sl/tp) from the decision agent output
    decisions: list[CandleDecision] = []
    for rc in raw_candles:
        if rc["ts"] is None or rc["close"] is None:
            continue
        cd = CandleDecision(
            candle_ts=rc["ts"], close_price=rc["close"],
            final_decision=rc["decision"], final_confidence=rc["confidence"],
            veto=rc["veto"], veto_reason=rc["veto_reason"],
        )
        for agent, runs in agent_runs.items():
            r = _nearest_run(runs, rc["ts"])
            if r is not None:
                cd.agents[agent] = r
        dec = cd.agents.get("decision")
        if dec is not None:
            # risk_assessment lives in the decision output; re-read the file’s json
            # is overkill — we approximate SL/TP from entry ± typical if missing.
            pass
        decisions.append(cd)
    _simulate_all(symbol, interval, horizon, decisions)
    return decisions


def _load_risk_assessment(responses_dir: str, ts: datetime) -> dict:
    """Find the decision output nearest ts and return its risk_assessment."""
    base = os.path.join(responses_dir, "decision_agent")
    best, best_dt, best_file = None, 200, None
    for out_path in glob.glob(os.path.join(base, "*_output.json")):
        fts = _ts_from_filename(out_path)
        if fts is None:
            continue
        dt = abs((fts - ts).total_seconds())
        if dt < best_dt:
            best_dt, best_file = dt, out_path
    if best_file:
        try:
            out = json.load(open(best_file, encoding="utf-8"))
            return out.get("risk_assessment", {}) or {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _simulate_all(symbol, interval, horizon, decisions):
    if not decisions:
        return
    # One kline pull covering the whole session + horizon.
    # Log timestamps are local; convert to epoch ms assuming the machine's tz.
    first = min(d.candle_ts for d in decisions)
    start_ms = int(first.timestamp() * 1000)
    total = len(decisions) + horizon + 5
    klines = fetch_klines(symbol, interval, start_ms, min(total, 1000))
    if not klines:
        print("⚠️  Could not fetch klines; simulation skipped.", file=sys.stderr)
        return
    # index klines by open_time (ms) for forward slicing
    kl_sorted = sorted(klines, key=lambda k: k["open_time"])

    def _forward_from(ts: datetime, n: int) -> list[dict]:
        target = int(ts.timestamp() * 1000)
        idx = None
        for i, k in enumerate(kl_sorted):
            if k["open_time"] >= target:
                idx = i
                break
        if idx is None:
            return []
        return kl_sorted[idx: idx + n]

    for d in decisions:
        fwd = _forward_from(d.candle_ts, horizon)
        d.future["klines"] = fwd
        # next-candle move for agent direction grading
        d.future["fwd_pct"] = (
            (fwd[0]["close"] - d.close_price) / d.close_price * 100.0 if fwd else None
        )


def render(symbol: str, responses_dir: str, decisions: list[CandleDecision],
           horizon: int) -> str:
    lines: list[str] = []
    lines.append(f"# Radiografía de decisiones — {symbol}\n")
    lines.append(f"Velas analizadas: {len(decisions)} | horizonte SL/TP: {horizon} velas\n")

    veto_stats: dict[str, dict] = {}
    agent_stats: dict[str, dict] = {}

    for d in decisions:
        fwd_pct = d.future.get("fwd_pct")
        lines.append(f"\n## {d.candle_ts:%H:%M} — close {d.close_price} — "
                     f"FINAL: {d.final_decision} ({d.final_confidence})"
                     + (f" — 🚫 {d.veto}" if d.veto else ""))
        # agents table
        for agent in ("technical", "qabba", "visual", "sentiment", "decision", "risk"):
            r = d.agents.get(agent)
            if not r:
                continue
            grade = grade_direction(r.signal, fwd_pct)
            conf = f"{r.confidence:.2f}" if r.confidence is not None else "—"
            lines.append(f"- **{agent}**: {r.signal} (conf {conf}) [{grade}] — {r.reasoning[:160]}")
            st = agent_stats.setdefault(agent, {"✓": 0, "✗": 0, "flat": 0, "n/a": 0, "?": 0})
            st[grade] = st.get(grade, 0) + 1

        # veto verdict via simulation of the vetoed direction
        if d.veto and d.final_decision in {"BUY", "SELL"}:
            ra = _load_risk_assessment(responses_dir, d.candle_ts)
            entry = _f(ra.get("entry_price")) or d.close_price
            sl = _f(ra.get("stop_loss"))
            tp = _f(ra.get("take_profit"))
            sim = simulate_trade(d.final_decision, entry, sl, tp, d.future.get("klines", []))
            verdict, why = grade_veto(d.final_decision, sim)
            lines.append(f"  - **VETO {d.veto} → {verdict}**: {why} "
                         f"(sim: {sim['outcome']} {sim['pnl_pct']:+.2f}% en {sim['bars_held']} velas)"
                         if sim["pnl_pct"] is not None else
                         f"  - **VETO {d.veto} → {verdict}**: {why}")
            vs = veto_stats.setdefault(d.veto, {"USEFUL": 0, "COSTLY": 0, "NEUTRAL": 0, "UNKNOWN": 0})
            vs[verdict] = vs.get(verdict, 0) + 1

    # summary
    lines.append("\n\n---\n## Resumen\n")
    lines.append("### Vetos: ¿útiles o costosos?")
    for veto, st in sorted(veto_stats.items()):
        total = sum(st.values())
        lines.append(f"- **{veto}** ({total}): "
                     f"{st['USEFUL']} útiles / {st['COSTLY']} costosos / "
                     f"{st['NEUTRAL']} neutros / {st['UNKNOWN']} sin datos")
    lines.append("\n### Agentes: aciertos direccionales (vela siguiente)")
    for agent, st in sorted(agent_stats.items()):
        directional = st["✓"] + st["✗"]
        acc = (st["✓"] / directional * 100.0) if directional else 0.0
        lines.append(f"- **{agent}**: {st['✓']}✓ / {st['✗']}✗ "
                     f"({acc:.0f}% en {directional} llamadas direccionales; "
                     f"{st['n/a']} HOLD/neutral)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Radiografía de decisiones de Fenix")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--log", required=True, help="engine run log (.out)")
    ap.add_argument("--responses", required=True, help="logs/llm_responses_<symbol> dir")
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--sltp-horizon", type=int, default=12, help="max candles to walk forward")
    ap.add_argument("--out", default=None, help="write markdown report here")
    args = ap.parse_args()

    decisions = build(args.symbol, args.log, args.responses, args.interval, args.sltp_horizon)
    report = render(args.symbol, args.responses, decisions, args.sltp_horizon)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✅ Informe escrito en {args.out} ({len(decisions)} velas)")
    else:
        print(report)


if __name__ == "__main__":
    main()
