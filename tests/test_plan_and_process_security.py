import argparse
import json
from pathlib import Path

import pytest

import scripts.run_benchmark_suite as benchmark_suite
import scripts.run_fenix_live_suite as live_suite
from src.security.plan_security import (
    MAX_PLAN_BYTES,
    MAX_PLAN_SLOTS,
    read_bounded_json,
    require_slot_count,
)
from src.security.private_files import open_private_text
from src.security.subprocess_environment import experiment_child_environment


def _live_args(**overrides):
    values = {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "slot_minutes": 30,
        "engine_mode": "testnet",
        "allow_live": False,
        "use_testnet_data": False,
        "api_key_index": 1,
        "python_bin": ".venv/bin/python",
        "base_model": "model",
        "base_vision_model": None,
        "model_timeout_sec": 120,
        "disable_reasoning_bank": False,
        "disable_risk_manager": False,
        "disable_judge": False,
        "monolithic_mode": False,
        "lite_pipeline": False,
        "no_visual": False,
        "no_sentiment": False,
        "disable_trading": False,
        "max_risk_per_trade": None,
        "balance_fallback_usdt": None,
        "min_klines_to_start": 5,
        "fast_loop_sec": 0,
        "no_analyze_on_start": False,
        "analyze_on_start_delay_sec": 2,
        "shutdown_timeout_sec": 25,
        "team_provider": "ollama_cloud",
        "risk_provider": None,
        "lite_consensus_mode": None,
        "lite_node_timeout_sec": None,
        "strict_mtf_bias_timeframe": None,
        "strict_mtf_opposing_veto_conf": None,
        "strict_mtf_bias_cache_sec": None,
        "lite_mtf_confirm_conf": None,
        "lite_mtf_qabba_min_conf": None,
        "lite_allow_mtf_qabba_when_tech_hold": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    "loader",
    [live_suite._load_plan, benchmark_suite._load_plan],
)
def test_plan_loaders_reject_string_booleans(tmp_path, loader):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "unsafe",
                    "mode": "individual",
                    "base_model": "model",
                    "disable_risk_manager": "false",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="JSON boolean"):
        loader(path)


def test_plan_reader_rejects_symlink_and_oversized_input(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("[]", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(real)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"[" + b" " * MAX_PLAN_BYTES + b"]")

    with pytest.raises(ValueError, match="safely"):
        read_bounded_json(linked)
    with pytest.raises(ValueError, match="exceeds"):
        read_bounded_json(oversized)


def test_plan_slot_count_is_bounded():
    with pytest.raises(ValueError, match="more than"):
        require_slot_count([{}] * (MAX_PLAN_SLOTS + 1))


def test_live_suite_refuses_riskless_or_simulated_balance_mainnet(tmp_path):
    slot = live_suite.LiveSuiteSlot(name="unsafe")
    common = {
        "slot": slot,
        "run_tag": "run",
        "slot_number": 1,
        "summary_path": tmp_path / "summary.json",
        "event_log_path": tmp_path / "events.jsonl",
    }

    with pytest.raises(ValueError, match="risk manager"):
        live_suite._build_slot_command(
            args=_live_args(
                engine_mode="live",
                allow_live=True,
                disable_risk_manager=True,
            ),
            **common,
        )

    with pytest.raises(ValueError, match="simulated balance"):
        live_suite._build_slot_command(
            args=_live_args(
                engine_mode="live",
                allow_live=True,
                balance_fallback_usdt=1_000,
            ),
            **common,
        )


def test_experiment_child_environment_strips_unneeded_secrets():
    env = experiment_child_environment(
        {
            "PATH": "/bin",
            "JWT_SECRET": "jwt",
            "FENIX_MASTER_PASSWORD": "vault",
            "SMTP_PASSWORD": "mail",
            "BINANCE_API_KEY_1": "selected",
            "BINANCE_API_KEY_2": "other",
            "GROQ_API_KEY": "needed",
        },
        api_key_index=1,
    )

    assert env["BINANCE_API_KEY_1"] == "selected"
    assert env["GROQ_API_KEY"] == "needed"
    assert env["FENIX_SKIP_DOTENV"] == "1"
    assert "BINANCE_API_KEY_2" not in env
    assert "JWT_SECRET" not in env
    assert "FENIX_MASTER_PASSWORD" not in env
    assert "SMTP_PASSWORD" not in env


def test_private_output_open_does_not_follow_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep", encoding="utf-8")
    linked = tmp_path / "output.log"
    linked.symlink_to(target)

    with pytest.raises(ValueError, match="safely"):
        open_private_text(linked, "w")

    assert target.read_text(encoding="utf-8") == "keep"
