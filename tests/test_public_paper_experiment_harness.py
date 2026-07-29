from __future__ import annotations

import os
import shutil
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import run_fenix
from scripts.inspect_paper_experiment import inspect_experiment


def test_mainnet_data_is_an_explicit_paper_only_venue(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_fenix.py",
            "--mode",
            "paper",
            "--mainnet-data",
            "--trade-flow-window-sec",
            "15",
        ],
    )
    args = run_fenix.parse_args()

    assert args.mainnet_data is True
    assert args.testnet is False
    assert args.trade_flow_window_sec == 15
    assert run_fenix._market_data_uses_testnet(args) is False


def test_paper_defaults_to_testnet_data(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_fenix.py", "--mode", "paper"])
    assert run_fenix._market_data_uses_testnet(run_fenix.parse_args()) is True


def test_mainnet_data_rejects_live_authorization(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_fenix.py", "--mode", "paper", "--mainnet-data", "--allow-live"],
    )
    with pytest.raises(ValueError, match="forbids --allow-live"):
        run_fenix._market_data_uses_testnet(run_fenix.parse_args())


def test_data_venue_flags_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_fenix.py", "--mode", "paper", "--mainnet-data", "--testnet"],
    )
    with pytest.raises(SystemExit):
        run_fenix.parse_args()


def test_market_data_factory_tracks_venue_and_flow_window(monkeypatch):
    import src.trading.market_data as market_data

    monkeypatch.setattr(market_data, "_market_data_instance", None)
    mainnet = market_data.get_market_data_manager(
        symbol="BTCUSDT",
        timeframe="5m",
        use_testnet=False,
        trade_flow_window_sec=15,
    )
    assert mainnet._trade_imbalance_window_sec == 15
    assert "fstream.binance.com" in mainnet.trade_ws_url

    testnet = market_data.get_market_data_manager(
        symbol="BTCUSDT",
        timeframe="5m",
        use_testnet=True,
        trade_flow_window_sec=5,
    )
    assert testnet is not mainnet
    assert testnet._trade_imbalance_window_sec == 5
    assert "stream.binancefuture.com" in testnet.trade_ws_url


def test_harness_strips_exchange_credentials_and_disables_dotenv_reload():
    source = Path("scripts/paper_experiment_harness.sh").read_text(encoding="utf-8")

    assert "nohup env -i" in source
    assert 'source ".env"' not in source
    assert "FENIX_SKIP_DOTENV=1" in source
    assert "--mode paper" in source
    assert "--mainnet-data" in source


def test_inspector_reports_aggregates_without_raw_log_content(tmp_path):
    database = tmp_path / "fenix_btcusdt_5m.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE orders (id TEXT)")
        connection.execute("INSERT INTO orders VALUES ('one')")
        connection.execute("CREATE TABLE positions (id TEXT)")
        connection.execute("CREATE TABLE trades (id TEXT)")
        connection.execute("CREATE TABLE agent_outputs (id TEXT)")

    secret_text = "raw model response that must not be returned"
    (tmp_path / "fenix_btcusdt_5m.log").write_text(
        f"INFO PAPER TRADE\nWARNING example\nERROR example\n{secret_text}\n",
        encoding="utf-8",
    )
    (tmp_path / "pids.txt").write_text(f"{os.getpid()} 5m\n", encoding="utf-8")

    result = inspect_experiment(tmp_path)

    assert result["processes"][0]["running"] is True
    assert result["databases"][0]["counts"]["orders"] == 1
    assert result["logs"][0]["simulated_trade_events"] == 1
    assert secret_text not in str(result)


def test_harness_launches_mainnet_data_child_without_exchange_credentials(tmp_path):
    fake_python = tmp_path / "fake-python"
    capture = tmp_path / "child"
    runtime = tmp_path / "runtime"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"if [[ \"${{1:-}}\" == \"-\" || \"${{1:-}}\" == \"-c\" ]]; then "
        f"exec {shlex.quote(sys.executable)} \"$@\"; fi\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(capture))}.args\n"
        f"env | sort > {shlex.quote(str(capture))}.env\n"
        "while true; do read -r -t 1 _ || true; done\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    environment = {
        **os.environ,
        "FENIX_EXPERIMENT_PYTHON": str(fake_python),
        "FENIX_EXPERIMENT_VENUE": "mainnet-data",
        "FENIX_EXPERIMENT_TIMEFRAMES": "5m",
        "FENIX_EXPERIMENT_ROOT": str(runtime),
        "FENIX_EXPERIMENT_BASE_DIR": str(tmp_path),
        "FENIX_EXPERIMENT_LOAD_DOTENV": "0",
        "FENIX_EXPERIMENT_WITH_NANO": "0",
        "BINANCE_API_KEY": "must-not-reach-child",
        "BINANCE_API_SECRET": "must-not-reach-child",
        "BINANCE_TESTNET_API_KEY": "must-not-reach-child",
        "BINANCE_TESTNET_API_SECRET": "must-not-reach-child",
        "BINANCE_SECRET_KEY": "must-not-reach-child",
        "BINANCE_TESTNET_API_KEY_1": "must-not-reach-child",
        "BINANCE_TESTNET_API_SECRET_1": "must-not-reach-child",
        "JWT_SECRET": "must-not-reach-child",
        "FENIX_MASTER_PASSWORD": "must-not-reach-child",
        "FENIX_CLEANUP_ON_STOP": "1",
        "OPENROUTER_API_KEY": "provider-key-may-reach-candidate",
    }
    command = ["bash", "scripts/paper_experiment_harness.sh"]

    try:
        started = subprocess.run(
            [*command, "start"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )
        assert started.returncode == 0, started.stderr

        arguments = Path(f"{capture}.args").read_text(encoding="utf-8")
        child_environment = Path(f"{capture}.env").read_text(encoding="utf-8")
        assert "--mode\npaper\n" in arguments
        assert "--mainnet-data\n" in arguments
        assert "--allow-live" not in arguments
        assert "FENIX_SKIP_DOTENV=1" in child_environment
        assert "must-not-reach-child" not in child_environment
        assert "FENIX_CLEANUP_ON_STOP" not in child_environment
        assert "provider-key-may-reach-candidate" in child_environment

        status = subprocess.run(
            [*command, "status"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )
        assert status.returncode == 0
        assert "RUNNING_SAFE role=5m" in status.stdout
    finally:
        subprocess.run(
            [*command, "stop"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )


def test_harness_never_executes_dotenv_as_shell_code(tmp_path):
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    security_dir = project / "src" / "security"
    venv_bin = project / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    security_dir.mkdir(parents=True)
    venv_bin.mkdir(parents=True)
    shutil.copy2("scripts/paper_experiment_harness.sh", scripts_dir)
    shutil.copy2("scripts/secure_dotenv_exec.py", scripts_dir)
    shutil.copy2("src/security/dotenv_security.py", security_dir)
    (project / "src" / "__init__.py").write_text("", encoding="utf-8")
    (security_dir / "__init__.py").write_text("", encoding="utf-8")
    (venv_bin / "python").symlink_to(sys.executable)

    marker = tmp_path / "dotenv-command-executed"
    (project / ".env").write_text(
        f"OPENAI_API_KEY=$(touch {marker})\n",
        encoding="utf-8",
    )
    (project / ".env").chmod(0o600)

    result = subprocess.run(
        ["bash", str(scripts_dir / "paper_experiment_harness.sh"), "status"],
        cwd=project,
        env={
            **os.environ,
            "FENIX_EXPERIMENT_LOAD_DOTENV": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert not marker.exists()


def test_harness_rejects_external_root_by_default(tmp_path):
    runtime = tmp_path / "outside" / "experiment"
    result = subprocess.run(
        ["bash", "scripts/paper_experiment_harness.sh", "status"],
        env={
            **os.environ,
            "FENIX_EXPERIMENT_PYTHON": sys.executable,
            "FENIX_EXPERIMENT_ROOT": str(runtime),
            "FENIX_EXPERIMENT_BASE_DIR": str(tmp_path / "allowed"),
            "FENIX_EXPERIMENT_LOAD_DOTENV": "0",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "must stay beneath" in result.stderr
    assert not runtime.exists()


def test_harness_refuses_symlinked_pidfile_without_signalling_process(tmp_path):
    runtime = tmp_path / "allowed" / "experiment"
    runtime.mkdir(parents=True)
    target = tmp_path / "attacker-controlled-pids"
    target.write_text(f"{os.getpid()} 5m {'0' * 64}\n", encoding="utf-8")
    (runtime / "pids.txt").symlink_to(target)

    result = subprocess.run(
        ["bash", "scripts/paper_experiment_harness.sh", "stop"],
        env={
            **os.environ,
            "FENIX_EXPERIMENT_PYTHON": sys.executable,
            "FENIX_EXPERIMENT_ROOT": str(runtime),
            "FENIX_EXPERIMENT_BASE_DIR": str(tmp_path / "allowed"),
            "FENIX_EXPERIMENT_LOAD_DOTENV": "0",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "unsafe experiment pidfile" in result.stderr
    assert target.exists()
