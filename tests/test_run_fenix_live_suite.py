import argparse
import json
from pathlib import Path

import scripts.run_fenix_live_suite as live_suite


def _suite_args(**overrides):
    base = dict(
        symbol="BTCUSDT",
        timeframe="5m",
        slot_minutes=30,
        engine_mode="testnet",
        allow_live=False,
        use_testnet_data=False,
        api_key_index=1,
        python_bin="fenix_env/bin/python",
        run_tag_suffix="",
        max_slots=None,
        resume_from=1,
        base_model="base-model",
        base_vision_model="vision-model",
        model_timeout_sec=120,
        disable_reasoning_bank=False,
        disable_risk_manager=False,
        disable_judge=False,
        monolithic_mode=False,
        lite_pipeline=False,
        no_visual=False,
        no_sentiment=False,
        disable_trading=False,
        max_risk_per_trade=None,
        balance_fallback_usdt=None,
        min_klines_to_start=5,
        fast_loop_sec=0.0,
        analyze_on_start_delay_sec=2.0,
        no_analyze_on_start=False,
        shutdown_timeout_sec=5.0,
        lite_consensus_mode=None,
        lite_node_timeout_sec=None,
        strict_mtf_bias_timeframe=None,
        strict_mtf_opposing_veto_conf=None,
        strict_mtf_bias_cache_sec=None,
        lite_mtf_confirm_conf=None,
        lite_mtf_qabba_min_conf=None,
        lite_allow_mtf_qabba_when_tech_hold=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_load_plan_accepts_single_timeframe_alias(tmp_path: Path):
    plan = [
        {
            "name": "slot-1",
            "mode": "individual",
            "base_model": "model-a",
            "single_timeframe": "3m",
        }
    ]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    slots = live_suite._load_plan(plan_path)

    assert len(slots) == 1
    assert slots[0].timeframe == "3m"
    assert slots[0].base_model == "model-a"


def test_build_slot_command_team_slot_includes_team_models(tmp_path: Path):
    slot = live_suite.LiveSuiteSlot(
        name="team-slot",
        mode="team",
        team_models="technical=a,qabba=b,decision=c,sentiment=d,visual=e,risk_manager=f",
        run_minutes=45,
        disable_judge=True,
    )
    args = _suite_args()
    summary_path = tmp_path / "summary.json"
    event_path = tmp_path / "events.jsonl"

    cmd = live_suite._build_slot_command(
        slot=slot,
        args=args,
        run_tag="run-tag",
        slot_number=1,
        summary_path=summary_path,
        event_log_path=event_path,
    )

    assert "scripts/run_fenix_live_slot.py" in cmd
    assert "--team-models" in cmd
    assert "--disable-judge" in cmd
    assert "--mode" in cmd and "testnet" in cmd
    assert "--api-key-index" in cmd and "1" in cmd


def test_build_slot_command_individual_slot_uses_defaults(tmp_path: Path):
    slot = live_suite.LiveSuiteSlot(name="individual-slot", mode="individual")
    args = _suite_args(disable_reasoning_bank=True, no_visual=True)
    summary_path = tmp_path / "summary.json"
    event_path = tmp_path / "events.jsonl"

    cmd = live_suite._build_slot_command(
        slot=slot,
        args=args,
        run_tag="run-tag",
        slot_number=2,
        summary_path=summary_path,
        event_log_path=event_path,
    )

    assert "--base-model" in cmd and "base-model" in cmd
    assert "--base-vision-model" in cmd and "vision-model" in cmd
    assert "--disable-reasoning-bank" in cmd
    assert "--no-visual" in cmd
    assert "--slot-index" in cmd and "2" in cmd


def test_load_plan_accepts_lite_mtf_guard_fields(tmp_path: Path):
    plan = [
        {
            "name": "mtf-slot",
            "mode": "team",
            "team_models": "technical=a,qabba=b,decision=c,risk_manager=d",
            "lite_consensus_mode": "technical_mtf_qabba_guard",
            "strict_mtf_bias_timeframe": "30m",
            "strict_mtf_opposing_veto_conf": 0.75,
            "strict_mtf_bias_cache_sec": 120,
            "lite_mtf_confirm_conf": 0.55,
            "lite_mtf_qabba_min_conf": 0.70,
            "lite_node_timeout_sec": 45,
            "lite_allow_mtf_qabba_when_tech_hold": True,
        }
    ]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    slots = live_suite._load_plan(plan_path)

    assert slots[0].lite_consensus_mode == "technical_mtf_qabba_guard"
    assert slots[0].strict_mtf_bias_timeframe == "30m"
    assert slots[0].strict_mtf_opposing_veto_conf == 0.75
    assert slots[0].strict_mtf_bias_cache_sec == 120
    assert slots[0].lite_mtf_confirm_conf == 0.55
    assert slots[0].lite_mtf_qabba_min_conf == 0.70
    assert slots[0].lite_node_timeout_sec == 45
    assert slots[0].lite_allow_mtf_qabba_when_tech_hold is True


def test_build_slot_command_includes_explicit_lite_mtf_guard_options(tmp_path: Path):
    slot = live_suite.LiveSuiteSlot(
        name="mtf-slot",
        mode="team",
        team_models="technical=a,qabba=b,decision=c,risk_manager=d",
        lite_consensus_mode="technical_mtf_qabba_guard",
        strict_mtf_bias_timeframe="30m",
        strict_mtf_opposing_veto_conf=0.75,
        strict_mtf_bias_cache_sec=120,
        lite_mtf_confirm_conf=0.55,
        lite_mtf_qabba_min_conf=0.70,
        lite_node_timeout_sec=45,
        lite_allow_mtf_qabba_when_tech_hold=True,
    )
    args = _suite_args()
    summary_path = tmp_path / "summary.json"
    event_path = tmp_path / "events.jsonl"

    cmd = live_suite._build_slot_command(
        slot=slot,
        args=args,
        run_tag="run-tag",
        slot_number=3,
        summary_path=summary_path,
        event_log_path=event_path,
    )

    assert "--lite-consensus-mode" in cmd
    assert "technical_mtf_qabba_guard" in cmd
    assert "--strict-mtf-bias-timeframe" in cmd
    assert "30m" in cmd
    assert "--strict-mtf-opposing-veto-conf" in cmd
    assert "0.75" in cmd
    assert "--strict-mtf-bias-cache-sec" in cmd
    assert "120" in cmd
    assert "--lite-mtf-confirm-conf" in cmd
    assert "0.55" in cmd
    assert "--lite-mtf-qabba-min-conf" in cmd
    assert "0.7" in cmd
    assert "--lite-node-timeout-sec" in cmd
    assert "45" in cmd
    assert "--lite-allow-mtf-qabba-when-tech-hold" in cmd
