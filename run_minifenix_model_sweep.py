#!/usr/bin/env python3
"""
MiniFenix model sweep runner.

Runs `minifenix_optimized_for_comparison.py` sequentially across multiple
Ollama models and extracts comparable metrics from each log.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_CANDIDATES = [
    "glm-5:cloud",
    "deepseek-v3.2:cloud",
    "cogito-2.1:671b-cloud",
    "nemotron-3-nano:30b-cloud",
    "ministral-3:14b-cloud",
    "qwen3-coder-next:cloud",
    "devstral-small-2:24b-cloud",
    "rnj-1:8b-cloud",
]


@dataclass
class SweepResult:
    model: str
    log_file: str
    duration_s: int
    exit_code: int
    pnl: float
    trades: int
    win_rate_pct: float
    ticks: int
    balance: float | None
    equity: float | None
    equity_pnl: float
    brain_calls: int
    brain_avg_latency_s: float | None
    brain_p95_latency_s: float | None
    brain_timeouts: int
    brain_fallback_success: int
    brain_errors: int
    run_timed_out: bool
    score: float = 0.0


def _safe_slug(raw: str) -> str:
    text = raw.replace(":", "-")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def _extract_last_float(pattern: str, text: str) -> float | None:
    values = re.findall(pattern, text)
    if not values:
        return None
    return float(values[-1].replace(",", ""))


def _extract_last_int(pattern: str, text: str) -> int:
    values = re.findall(pattern, text)
    if not values:
        return 0
    return int(values[-1].replace(",", ""))


def _percent_values(pattern: str, text: str) -> list[float]:
    return [float(x) for x in re.findall(pattern, text)]


def _latency_stats(latencies: Sequence[float]) -> tuple[float | None, float | None]:
    if not latencies:
        return None, None
    if len(latencies) == 1:
        return latencies[0], latencies[0]
    avg = statistics.fmean(latencies)
    idx = max(0, math.ceil(len(latencies) * 0.95) - 1)
    p95 = sorted(latencies)[idx]
    return avg, p95


def parse_minifenix_log(content: str) -> dict[str, float | int | None]:
    pnl = _extract_last_float(r"P&L combinado:\s*\$([+-]?[\d,]+(?:\.\d+)?)", content) or 0.0
    trades = _extract_last_int(r"Total trades:\s*([\d,]+)", content)
    ticks = _extract_last_int(r"Ticks:\s*([\d,]+)", content)

    wr_values = _percent_values(r"Win Rate:\s*([\d.]+)%", content)
    win_rate_pct = wr_values[-1] if wr_values else 0.0

    balance_values = re.findall(
        r"Balance:\s*\$([+-]?[\d,]+(?:\.\d+)?)\s*/\s*\$([+-]?[\d,]+(?:\.\d+)?)",
        content,
    )
    balance = float(balance_values[-1][0].replace(",", "")) if balance_values else None
    equity = float(balance_values[-1][1].replace(",", "")) if balance_values else None

    latencies = [float(x) for x in re.findall(r"\[BRAIN\]\s*✅.*? en ([\d.]+)s", content)]
    avg_latency, p95_latency = _latency_stats(latencies)

    timeouts = len(re.findall(r"\[BRAIN\]\s*⏱️ Timeout", content))
    fallback_success = len(re.findall(r"\(fallback\)", content))
    errors = len(
        re.findall(
            r"\[BRAIN\]\s*Error LLM|\[BRAIN\]\s*Fallback también falló|Error inesperado en Brain",
            content,
        )
    )

    return {
        "pnl": pnl,
        "trades": trades,
        "win_rate_pct": win_rate_pct,
        "ticks": ticks,
        "balance": balance,
        "equity": equity,
        "brain_calls": len(latencies) + fallback_success,
        "brain_avg_latency_s": avg_latency,
        "brain_p95_latency_s": p95_latency,
        "brain_timeouts": timeouts,
        "brain_fallback_success": fallback_success,
        "brain_errors": errors,
    }


def list_ollama_models() -> list[str]:
    try:
        out = subprocess.run(
            ["ollama", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    models: list[str] = []
    for line in out.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        if first and first != "NAME":
            models.append(first)
    return models


def default_models_from_ollama(max_models: int) -> list[str]:
    available = set(list_ollama_models())
    filtered = [m for m in DEFAULT_CANDIDATES if m in available]
    if not filtered:
        filtered = list(available)
    # Avoid vision-first variants for this text-only benchmark.
    filtered = [m for m in filtered if "-vl" not in m.lower()]
    return filtered[:max_models]


def run_single_model(
    model: str,
    duration_s: int,
    symbol: str,
    brain_interval: int,
    logs_dir: Path,
    fallback_model: str,
) -> SweepResult:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _safe_slug(model)
    log_file = logs_dir / f"minifenix_sweep_{ts}_{slug}.log"

    env = os.environ.copy()
    env["MINIFENIX_LLM_MODEL"] = model
    env["MINIFENIX_LLM_FALLBACK"] = fallback_model

    cmd = [
        sys.executable,
        "minifenix_optimized_for_comparison.py",
        "--symbols",
        symbol,
        "--brain-interval",
        str(brain_interval),
        "--duration",
        str(duration_s),
    ]

    hard_timeout_s = duration_s + max(90, brain_interval * 4)
    run_timed_out = False
    exit_code = 0
    with log_file.open("w", encoding="utf-8") as fp:
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                stdout=fp,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=hard_timeout_s,
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            run_timed_out = True
            exit_code = 124
            fp.write(
                f"\n[Sweep] HARD_TIMEOUT after {hard_timeout_s}s for model={model}. "
                "Process terminated.\n"
            )

    text = log_file.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_minifenix_log(text)

    return SweepResult(
        model=model,
        log_file=str(log_file),
        duration_s=duration_s,
        exit_code=exit_code,
        pnl=float(parsed["pnl"] or 0.0),
        trades=int(parsed["trades"] or 0),
        win_rate_pct=float(parsed["win_rate_pct"] or 0.0),
        ticks=int(parsed["ticks"] or 0),
        balance=float(parsed["balance"]) if parsed["balance"] is not None else None,
        equity=float(parsed["equity"]) if parsed["equity"] is not None else None,
        equity_pnl=(
            float(parsed["equity"]) - 10_000.0
            if parsed["equity"] is not None
            else float(parsed["pnl"] or 0.0)
        ),
        brain_calls=int(parsed["brain_calls"] or 0),
        brain_avg_latency_s=float(parsed["brain_avg_latency_s"])
        if parsed["brain_avg_latency_s"] is not None
        else None,
        brain_p95_latency_s=float(parsed["brain_p95_latency_s"])
        if parsed["brain_p95_latency_s"] is not None
        else None,
        brain_timeouts=int(parsed["brain_timeouts"] or 0),
        brain_fallback_success=int(parsed["brain_fallback_success"] or 0),
        brain_errors=int(parsed["brain_errors"] or 0),
        run_timed_out=run_timed_out,
    )


def _minmax_norm(values: Iterable[float], invert: bool = False) -> list[float]:
    seq = list(values)
    if not seq:
        return []
    lo, hi = min(seq), max(seq)
    if math.isclose(lo, hi):
        return [0.5 for _ in seq]
    out = [(x - lo) / (hi - lo) for x in seq]
    if invert:
        out = [1.0 - x for x in out]
    return out


def score_results(results: list[SweepResult]) -> None:
    pnl_norm = _minmax_norm([r.pnl for r in results])
    equity_norm = _minmax_norm([r.equity_pnl for r in results])
    wr_norm = _minmax_norm([r.win_rate_pct for r in results])
    trades_norm = _minmax_norm([r.trades for r in results])
    latency_values = [r.brain_avg_latency_s if r.brain_avg_latency_s is not None else 999.0 for r in results]
    latency_norm = _minmax_norm(latency_values, invert=True)
    stability_signal = [
        r.brain_timeouts + r.brain_errors + (0.5 * r.brain_fallback_success) + (5 if r.run_timed_out else 0)
        for r in results
    ]
    timeout_norm = _minmax_norm(stability_signal, invert=True)

    for idx, res in enumerate(results):
        res.score = (
            0.30 * pnl_norm[idx]
            + 0.25 * equity_norm[idx]
            + 0.15 * wr_norm[idx]
            + 0.10 * trades_norm[idx]
            + 0.15 * latency_norm[idx]
            + 0.05 * timeout_norm[idx]
        )


def render_table(results: Sequence[SweepResult]) -> str:
    headers = [
        "Model",
        "PnL",
        "EqPnL",
        "Trades",
        "WinRate",
        "Ticks",
        "Brain avg",
        "Timeouts",
        "Fallbacks",
        "Errors",
        "RunTO",
        "Score",
    ]
    lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for r in results:
        lat = f"{r.brain_avg_latency_s:.2f}s" if r.brain_avg_latency_s is not None else "n/a"
        lines.append(
            " | ".join(
                [
                    r.model,
                    f"${r.pnl:+.2f}",
                    f"${r.equity_pnl:+.2f}",
                    str(r.trades),
                    f"{r.win_rate_pct:.1f}%",
                    f"{r.ticks:,}",
                    lat,
                    str(r.brain_timeouts),
                    str(r.brain_fallback_success),
                    str(r.brain_errors),
                    "Y" if r.run_timed_out else "N",
                    f"{r.score:.3f}",
                ]
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniFenix model sweep")
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated models. If empty, uses available defaults from ollama list.",
    )
    parser.add_argument("--max-models", type=int, default=6, help="Max models when auto-selecting.")
    parser.add_argument("--duration", type=int, default=300, help="Duration per model in seconds.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol for MiniFenix.")
    parser.add_argument("--brain-interval", type=int, default=30, help="Brain interval in seconds.")
    parser.add_argument(
        "--fallback-model",
        default="mistral-large-3:675b-cloud",
        help="Fallback model used when the primary model times out.",
    )
    args = parser.parse_args()

    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    if args.models.strip():
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = default_models_from_ollama(args.max_models)

    if not models:
        print("No models selected. Pass --models or ensure `ollama list` returns models.")
        return 1

    print(f"Sweep models ({len(models)}): {', '.join(models)}")
    print(f"Duration/model: {args.duration}s | Symbol: {args.symbol} | Brain interval: {args.brain_interval}s")
    print()

    results: list[SweepResult] = []
    for i, model in enumerate(models, start=1):
        print(f"[{i}/{len(models)}] Running {model} ...")
        res = run_single_model(
            model=model,
            duration_s=args.duration,
            symbol=args.symbol,
            brain_interval=args.brain_interval,
            logs_dir=logs_dir,
            fallback_model=args.fallback_model,
        )
        results.append(res)
        print(
            f"  -> exit={res.exit_code} pnl=${res.pnl:+.2f} trades={res.trades} "
            f"wr={res.win_rate_pct:.1f}% avg_brain={res.brain_avg_latency_s or 0:.2f}s "
            f"timeouts={res.brain_timeouts} fallbacks={res.brain_fallback_success} "
            f"errors={res.brain_errors} hard_to={'Y' if res.run_timed_out else 'N'}"
        )

    score_results(results)
    ranked = sorted(results, key=lambda x: x.score, reverse=True)

    table = render_table(ranked)
    print("\n=== Ranking ===")
    print(table)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_file = logs_dir / f"minifenix_sweep_summary_{ts}.json"
    md_file = logs_dir / f"minifenix_sweep_summary_{ts}.md"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "duration_s_per_model": args.duration,
        "symbol": args.symbol,
        "brain_interval_s": args.brain_interval,
        "fallback_model": args.fallback_model,
        "models": models,
        "results_ranked": [r.__dict__ for r in ranked],
        "winner": ranked[0].model if ranked else None,
    }
    json_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_file.write_text(
        "\n".join(
            [
                "# MiniFenix Model Sweep",
                "",
                f"- Generated: `{payload['generated_at']}`",
                f"- Duration/model: `{args.duration}s`",
                f"- Symbol: `{args.symbol}`",
                f"- Brain interval: `{args.brain_interval}s`",
                f"- Fallback model: `{args.fallback_model}`",
                "",
                "## Ranking",
                "",
                table,
                "",
                f"## Winner",
                "",
                f"`{payload['winner']}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"\nSummary JSON: {json_file}")
    print(f"Summary MD:   {md_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
