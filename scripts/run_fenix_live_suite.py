#!/usr/bin/env python3
"""
Fenix Live Suite Runner.

Runs controlled slot experiments using the real TradingEngine lifecycle via
`scripts/run_fenix_live_slot.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SUPPORTED_PROVIDER_CHOICES = (
    "ollama_cloud",
    "ollama_local",
    "huggingface_mlx",
    "huggingface_inference",
    "groq",
    "openai",
    "anthropic",
)


def _load_dotenv_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except Exception:
        pass

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class LiveSuiteSlot:
    name: str
    mode: str = "individual"  # individual | team
    base_model: str | None = None
    team_models: str | None = None
    base_vision_model: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    run_minutes: int | None = None
    engine_mode: str | None = None  # paper | testnet | live
    description: str = ""
    experiment: str = ""
    experiment_id: int = 0
    disable_reasoning_bank: bool = False
    disable_risk_manager: bool = False
    disable_judge: bool = False
    monolithic_mode: bool = False
    lite_pipeline: bool = False
    no_visual: bool = False
    no_sentiment: bool = False
    disable_trading: bool = False
    max_risk_per_trade: float | None = None
    balance_fallback_usdt: float | None = None
    lite_consensus_mode: str | None = None
    lite_node_timeout_sec: float | None = None
    strict_mtf_bias_timeframe: str | None = None
    strict_mtf_opposing_veto_conf: float | None = None
    strict_mtf_bias_cache_sec: float | None = None
    lite_mtf_confirm_conf: float | None = None
    lite_mtf_qabba_min_conf: float | None = None
    lite_allow_mtf_qabba_when_tech_hold: bool | None = None


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def _load_plan(path: Path) -> list[LiveSuiteSlot]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("Plan file must contain a JSON list")

    slots: list[LiveSuiteSlot] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid plan item at index {idx}: expected object")

        mode = str(item.get("mode", "individual")).strip().lower()
        if mode not in {"individual", "team"}:
            raise ValueError(f"Invalid mode in slot {idx}: {mode}")

        slot = LiveSuiteSlot(
            name=str(item.get("name") or f"slot-{idx}").strip(),
            mode=mode,
            base_model=item.get("base_model"),
            team_models=item.get("team_models"),
            base_vision_model=item.get("base_vision_model"),
            symbol=item.get("symbol"),
            timeframe=item.get("timeframe") or item.get("single_timeframe"),
            run_minutes=item.get("run_minutes"),
            engine_mode=item.get("engine_mode"),
            description=str(item.get("description", "")),
            experiment=str(item.get("experiment", "")),
            experiment_id=int(item.get("experiment_id", 0) or 0),
            disable_reasoning_bank=bool(item.get("disable_reasoning_bank", False)),
            disable_risk_manager=bool(item.get("disable_risk_manager", False)),
            disable_judge=bool(item.get("disable_judge", False)),
            monolithic_mode=bool(item.get("monolithic_mode", False)),
            lite_pipeline=bool(item.get("lite_pipeline", False)),
            no_visual=bool(item.get("no_visual", False)),
            no_sentiment=bool(item.get("no_sentiment", False)),
            disable_trading=bool(item.get("disable_trading", False)),
            max_risk_per_trade=(
                float(item["max_risk_per_trade"])
                if item.get("max_risk_per_trade") is not None
                else None
            ),
            balance_fallback_usdt=(
                float(item["balance_fallback_usdt"])
                if item.get("balance_fallback_usdt") is not None
                else None
            ),
            lite_consensus_mode=(
                str(item["lite_consensus_mode"]).strip()
                if item.get("lite_consensus_mode") is not None
                else None
            ),
            lite_node_timeout_sec=(
                float(item["lite_node_timeout_sec"])
                if item.get("lite_node_timeout_sec") is not None
                else None
            ),
            strict_mtf_bias_timeframe=(
                str(item["strict_mtf_bias_timeframe"]).strip()
                if item.get("strict_mtf_bias_timeframe") is not None
                else None
            ),
            strict_mtf_opposing_veto_conf=(
                float(item["strict_mtf_opposing_veto_conf"])
                if item.get("strict_mtf_opposing_veto_conf") is not None
                else None
            ),
            strict_mtf_bias_cache_sec=(
                float(item["strict_mtf_bias_cache_sec"])
                if item.get("strict_mtf_bias_cache_sec") is not None
                else None
            ),
            lite_mtf_confirm_conf=(
                float(item["lite_mtf_confirm_conf"])
                if item.get("lite_mtf_confirm_conf") is not None
                else None
            ),
            lite_mtf_qabba_min_conf=(
                float(item["lite_mtf_qabba_min_conf"])
                if item.get("lite_mtf_qabba_min_conf") is not None
                else None
            ),
            lite_allow_mtf_qabba_when_tech_hold=(
                bool(item["lite_allow_mtf_qabba_when_tech_hold"])
                if item.get("lite_allow_mtf_qabba_when_tech_hold") is not None
                else None
            ),
        )

        if slot.mode == "team" and not slot.team_models:
            raise ValueError(f"Slot {idx} is team mode but team_models is missing")
        slots.append(slot)

    return slots


def _build_slot_command(
    *,
    slot: LiveSuiteSlot,
    args: argparse.Namespace,
    run_tag: str,
    slot_number: int,
    summary_path: Path,
    event_log_path: Path,
) -> list[str]:
    symbol = slot.symbol or args.symbol
    timeframe = slot.timeframe or args.timeframe
    run_minutes = int(slot.run_minutes or args.slot_minutes)
    engine_mode = (slot.engine_mode or args.engine_mode).strip().lower()

    cmd = [
        args.python_bin,
        "scripts/run_fenix_live_slot.py",
        "--symbol",
        symbol,
        "--timeframe",
        timeframe,
        "--run-minutes",
        str(run_minutes),
        "--mode",
        engine_mode,
        "--api-key-index",
        str(args.api_key_index),
        "--run-tag",
        run_tag,
        "--slot-name",
        slot.name,
        "--slot-index",
        str(slot_number),
        "--summary-path",
        str(summary_path),
        "--event-log-path",
        str(event_log_path),
        "--min-klines-to-start",
        str(args.min_klines_to_start),
        "--fast-loop-sec",
        str(args.fast_loop_sec),
    ]
    team_provider = getattr(args, "team_provider", "ollama_cloud")
    risk_provider = getattr(args, "risk_provider", None)
    cmd.extend(["--team-provider", str(team_provider)])
    if risk_provider:
        cmd.extend(["--risk-provider", str(risk_provider)])

    if args.allow_live:
        cmd.append("--allow-live")
    if args.use_testnet_data:
        cmd.append("--use-testnet-data")
    if args.no_analyze_on_start:
        cmd.append("--no-analyze-on-start")
    else:
        cmd.append("--analyze-on-start")
        cmd.extend(["--analyze-on-start-delay-sec", str(args.analyze_on_start_delay_sec)])
    cmd.extend(["--shutdown-timeout-sec", str(args.shutdown_timeout_sec)])

    if slot.mode == "team":
        cmd.extend(["--team-models", str(slot.team_models)])
    else:
        base_model = slot.base_model or args.base_model
        if base_model:
            cmd.extend(["--base-model", str(base_model)])

    vision_model = slot.base_vision_model or args.base_vision_model
    if vision_model:
        cmd.extend(["--base-vision-model", str(vision_model)])

    if args.model_timeout_sec is not None:
        cmd.extend(["--model-timeout-sec", str(args.model_timeout_sec)])
    if slot.experiment:
        cmd.extend(["--experiment", slot.experiment])
    if slot.experiment_id:
        cmd.extend(["--experiment-id", str(slot.experiment_id)])

    # Global toggles + slot-specific toggles.
    if args.disable_reasoning_bank or slot.disable_reasoning_bank:
        cmd.append("--disable-reasoning-bank")
    if args.disable_risk_manager or slot.disable_risk_manager:
        cmd.append("--disable-risk-manager")
    if args.disable_judge or slot.disable_judge:
        cmd.append("--disable-judge")
    if args.monolithic_mode or slot.monolithic_mode:
        cmd.append("--monolithic-mode")
    if args.lite_pipeline or slot.lite_pipeline:
        cmd.append("--lite-pipeline")
    if args.no_visual or slot.no_visual:
        cmd.append("--no-visual")
    if args.no_sentiment or slot.no_sentiment:
        cmd.append("--no-sentiment")
    if args.disable_trading or slot.disable_trading:
        cmd.append("--disable-trading")

    max_risk = slot.max_risk_per_trade
    if max_risk is None:
        max_risk = args.max_risk_per_trade
    if max_risk is not None:
        cmd.extend(["--max-risk-per-trade", str(max_risk)])

    balance_fallback = slot.balance_fallback_usdt
    if balance_fallback is None:
        balance_fallback = args.balance_fallback_usdt
    if balance_fallback is not None:
        cmd.extend(["--balance-fallback-usdt", str(balance_fallback)])

    def _slot_or_arg(slot_value: Any, arg_name: str) -> Any:
        return slot_value if slot_value is not None else getattr(args, arg_name, None)

    def _append_optional(flag: str, value: Any) -> None:
        if value is not None and str(value).strip():
            cmd.extend([flag, str(value)])

    _append_optional(
        "--lite-consensus-mode",
        _slot_or_arg(slot.lite_consensus_mode, "lite_consensus_mode"),
    )
    _append_optional(
        "--lite-node-timeout-sec",
        _slot_or_arg(slot.lite_node_timeout_sec, "lite_node_timeout_sec"),
    )
    _append_optional(
        "--strict-mtf-bias-timeframe",
        _slot_or_arg(slot.strict_mtf_bias_timeframe, "strict_mtf_bias_timeframe"),
    )
    _append_optional(
        "--strict-mtf-opposing-veto-conf",
        _slot_or_arg(slot.strict_mtf_opposing_veto_conf, "strict_mtf_opposing_veto_conf"),
    )
    _append_optional(
        "--strict-mtf-bias-cache-sec",
        _slot_or_arg(slot.strict_mtf_bias_cache_sec, "strict_mtf_bias_cache_sec"),
    )
    _append_optional(
        "--lite-mtf-confirm-conf",
        _slot_or_arg(slot.lite_mtf_confirm_conf, "lite_mtf_confirm_conf"),
    )
    _append_optional(
        "--lite-mtf-qabba-min-conf",
        _slot_or_arg(slot.lite_mtf_qabba_min_conf, "lite_mtf_qabba_min_conf"),
    )

    allow_mtf_qabba = (
        slot.lite_allow_mtf_qabba_when_tech_hold
        if slot.lite_allow_mtf_qabba_when_tech_hold is not None
        else getattr(args, "lite_allow_mtf_qabba_when_tech_hold", False)
    )
    if allow_mtf_qabba:
        cmd.append("--lite-allow-mtf-qabba-when-tech-hold")

    return cmd


def _slot_alerts(summary_payload: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    events = summary_payload.get("events", {}) or {}
    decisions = events.get("decision_counts", {}) or {}

    buy_count = int(decisions.get("BUY", 0) or 0)
    sell_count = int(decisions.get("SELL", 0) or 0)
    hold_count = int(decisions.get("HOLD", 0) or 0)
    risk_blocked = int(events.get("risk_blocked", 0) or 0)
    judge_blocked = int(events.get("judge_blocked", 0) or 0)

    action_decisions = buy_count + sell_count
    if action_decisions == 0:
        alerts.append("NO_ACTION_DECISIONS")
    if hold_count > 0 and action_decisions == 0:
        alerts.append("ONLY_HOLD_DECISIONS")
    if action_decisions > 0 and risk_blocked > action_decisions:
        alerts.append("HIGH_RISK_BLOCK_RATE")
    if action_decisions > 0 and judge_blocked > action_decisions:
        alerts.append("HIGH_JUDGE_BLOCK_RATE")
    if summary_payload.get("status") not in {"completed", "engine_stopped_early"}:
        alerts.append(f"STATUS_{str(summary_payload.get('status')).upper()}")
    return alerts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a multi-slot live-like suite using the real Fenix TradingEngine."
    )
    parser.add_argument("--plan-json", required=True, help="Path to suite plan JSON file")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--slot-minutes", type=int, default=60)
    parser.add_argument("--engine-mode", choices=["paper", "testnet", "live"], default="testnet")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--use-testnet-data", action="store_true")
    parser.add_argument("--api-key-index", type=int, choices=[1, 2], default=1)
    parser.add_argument("--python-bin", default="fenix_env/bin/python")
    parser.add_argument("--run-tag-suffix", type=str, default="")
    parser.add_argument("--max-slots", type=int, default=None)
    parser.add_argument("--resume-from", type=int, default=1)

    parser.add_argument("--base-model", default=None)
    parser.add_argument("--base-vision-model", default=None)
    parser.add_argument(
        "--team-provider",
        choices=SUPPORTED_PROVIDER_CHOICES,
        default="ollama_cloud",
    )
    parser.add_argument(
        "--risk-provider",
        choices=SUPPORTED_PROVIDER_CHOICES,
        default=None,
    )
    parser.add_argument("--model-timeout-sec", type=int, default=120)

    parser.add_argument("--disable-reasoning-bank", action="store_true")
    parser.add_argument("--disable-risk-manager", action="store_true")
    parser.add_argument("--disable-judge", action="store_true")
    parser.add_argument("--monolithic-mode", action="store_true")
    parser.add_argument("--lite-pipeline", action="store_true")
    parser.add_argument("--no-visual", action="store_true")
    parser.add_argument("--no-sentiment", action="store_true")
    parser.add_argument("--disable-trading", action="store_true")

    parser.add_argument("--max-risk-per-trade", type=float, default=None)
    parser.add_argument("--balance-fallback-usdt", type=float, default=None)
    parser.add_argument("--min-klines-to-start", type=int, default=5)
    parser.add_argument("--fast-loop-sec", type=float, default=0.0)
    parser.add_argument("--analyze-on-start-delay-sec", type=float, default=2.0)
    parser.add_argument("--no-analyze-on-start", action="store_true")
    parser.add_argument("--shutdown-timeout-sec", type=float, default=25.0)
    parser.add_argument("--lite-consensus-mode", default=None)
    parser.add_argument("--lite-node-timeout-sec", type=float, default=None)
    parser.add_argument("--strict-mtf-bias-timeframe", default=None)
    parser.add_argument("--strict-mtf-opposing-veto-conf", type=float, default=None)
    parser.add_argument("--strict-mtf-bias-cache-sec", type=float, default=None)
    parser.add_argument("--lite-mtf-confirm-conf", type=float, default=None)
    parser.add_argument("--lite-mtf-qabba-min-conf", type=float, default=None)
    parser.add_argument("--lite-allow-mtf-qabba-when-tech-hold", action="store_true")

    return parser.parse_args()


def main() -> None:
    _load_dotenv_file(PROJECT_ROOT)
    args = parse_args()

    plan_path = Path(args.plan_json)
    if not plan_path.exists():
        print(f"Plan file not found: {plan_path}")
        raise SystemExit(1)

    slots = _load_plan(plan_path)
    if not slots:
        print("Plan is empty.")
        raise SystemExit(1)

    if args.max_slots:
        slots = slots[: args.max_slots]

    if not Path(args.python_bin).exists():
        print(f"Python binary not found: {args.python_bin}")
        raise SystemExit(1)

    run_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if args.run_tag_suffix:
        run_tag = f"{run_tag}{args.run_tag_suffix}"

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = logs_dir / f"live_suite_schedule_{run_tag}.jsonl"
    results_path = logs_dir / f"live_suite_results_{run_tag}.jsonl"
    master_log_path = logs_dir / f"live_suite_run_{run_tag}.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    env["PYTHONUNBUFFERED"] = "1"

    run_start_event = {
        "event": "run_start",
        "run_tag": run_tag,
        "plan_file": str(plan_path),
        "total_slots": len(slots),
        "slot_minutes_default": args.slot_minutes,
        "symbol_default": args.symbol,
        "timeframe_default": args.timeframe,
        "engine_mode_default": args.engine_mode,
        "api_key_index": args.api_key_index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl(schedule_path, run_start_event)
    _append_jsonl(results_path, run_start_event)

    total = len(slots)
    completed = 0
    failed = 0

    print("=" * 72)
    print("Fenix Live Suite Runner")
    print("=" * 72)
    print(f"plan         : {plan_path.name}")
    print(f"slots        : {total}")
    print(f"default mode : {args.engine_mode}")
    print(f"api key idx  : {args.api_key_index}")
    print(f"run tag      : {run_tag}")
    print("=" * 72)

    with master_log_path.open("a", encoding="utf-8") as master_log:
        master_log.write(
            f"[{datetime.now(timezone.utc).isoformat()}] START run_tag={run_tag} "
            f"slots={total} plan={plan_path}\n"
        )
        master_log.flush()

        for idx, slot in enumerate(slots, start=1):
            if idx < args.resume_from:
                print(f"Slot {idx}/{total} skipped (resume_from={args.resume_from})")
                continue

            slot_start = datetime.now(timezone.utc)
            slot_slug = _slug(slot.name)
            slot_log_path = logs_dir / f"live_slot_{idx:02d}_{slot_slug}_{run_tag}.log"
            slot_summary_path = logs_dir / f"live_slot_summary_{idx:02d}_{slot_slug}_{run_tag}.json"
            slot_event_path = logs_dir / f"live_slot_events_{idx:02d}_{slot_slug}_{run_tag}.jsonl"

            cmd = _build_slot_command(
                slot=slot,
                args=args,
                run_tag=run_tag,
                slot_number=idx,
                summary_path=slot_summary_path,
                event_log_path=slot_event_path,
            )

            slot_start_event = {
                "event": "slot_start",
                "slot": idx,
                "slot_name": slot.name,
                "description": slot.description,
                "experiment": slot.experiment,
                "experiment_id": slot.experiment_id,
                "mode": slot.mode,
                "symbol": slot.symbol or args.symbol,
                "timeframe": slot.timeframe or args.timeframe,
                "run_minutes": slot.run_minutes or args.slot_minutes,
                "command": cmd,
                "slot_log_path": str(slot_log_path),
                "slot_summary_path": str(slot_summary_path),
                "slot_event_path": str(slot_event_path),
                "timestamp": slot_start.isoformat(),
            }
            _append_jsonl(schedule_path, slot_start_event)
            _append_jsonl(results_path, slot_start_event)

            print(f"\nSlot {idx}/{total}: {slot.name}")
            if slot.description:
                print(f"  {slot.description}")
            print(f"  log: {slot_log_path.name}")

            timeout_sec = max(120, int((slot.run_minutes or args.slot_minutes) * 60) + 900)
            rc = None

            with slot_log_path.open("w", encoding="utf-8") as out:
                try:
                    result = subprocess.run(
                        cmd,
                        env=env,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        timeout=timeout_sec,
                        check=False,
                    )
                    rc = int(result.returncode)
                except subprocess.TimeoutExpired:
                    rc = -9
                    out.write("\n[LIVE_SUITE] Slot timeout exceeded; moving to next slot.\n")

            slot_end = datetime.now(timezone.utc)
            duration_sec = (slot_end - slot_start).total_seconds()

            summary_payload: dict[str, Any] | None = None
            if slot_summary_path.exists():
                try:
                    summary_payload = json.loads(slot_summary_path.read_text())
                except Exception:
                    summary_payload = None

            status_icon = "OK" if rc == 0 else ("TIMEOUT" if rc == -9 else f"FAIL rc={rc}")
            print(f"  {status_icon} ({duration_sec:.0f}s)")

            alerts = _slot_alerts(summary_payload or {}) if summary_payload else ["NO_SUMMARY"]
            if alerts:
                print(f"  alerts: {', '.join(alerts)}")

            slot_end_event = {
                "event": "slot_end",
                "slot": idx,
                "slot_name": slot.name,
                "status": status_icon,
                "returncode": rc,
                "duration_sec": round(duration_sec, 2),
                "alerts": alerts,
                "slot_log_path": str(slot_log_path),
                "slot_summary_path": str(slot_summary_path),
                "slot_event_path": str(slot_event_path),
                "summary": summary_payload or {},
                "timestamp": slot_end.isoformat(),
            }
            _append_jsonl(schedule_path, slot_end_event)
            _append_jsonl(results_path, slot_end_event)

            master_log.write(
                f"[{slot_end.isoformat()}] SLOT {idx}/{total} name={slot.name} "
                f"rc={rc} duration={duration_sec:.1f}s alerts={alerts}\n"
            )
            master_log.flush()

            if rc == 0:
                completed += 1
            else:
                failed += 1

        run_end_event = {
            "event": "run_end",
            "run_tag": run_tag,
            "total_slots": total,
            "completed_slots": completed,
            "failed_slots": failed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(schedule_path, run_end_event)
        _append_jsonl(results_path, run_end_event)

        master_log.write(
            f"[{datetime.now(timezone.utc).isoformat()}] END run_tag={run_tag} "
            f"completed={completed} failed={failed}\n"
        )
        master_log.flush()

    print("\n" + "=" * 72)
    print("Live suite finished")
    print("=" * 72)
    print(f"run_tag        : {run_tag}")
    print(f"completed      : {completed}/{total}")
    print(f"failed         : {failed}")
    print(f"schedule_jsonl : {schedule_path}")
    print(f"results_jsonl  : {results_path}")
    print(f"master_log     : {master_log_path}")
    print("=" * 72)

    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
