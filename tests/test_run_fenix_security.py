import argparse
import os
from pathlib import Path
import stat

import pytest

import run_fenix


def test_cli_security_validators_normalize_and_reject_control_data():
    assert run_fenix._symbol("ethusdt") == "ETHUSDT"
    assert run_fenix._timeframe("15m") == "15m"
    assert (
        run_fenix._team_models("Technical=qwen2.5:7b,RISK_MANAGER=model/path")
        == "technical=qwen2.5:7b,risk_manager=model/path"
    )

    with pytest.raises(argparse.ArgumentTypeError):
        run_fenix._symbol("../../etc/passwd")
    with pytest.raises(argparse.ArgumentTypeError):
        run_fenix._timeframe("0m")
    with pytest.raises(argparse.ArgumentTypeError):
        run_fenix._team_models("technical=model\nJWT_SECRET=stolen")
    with pytest.raises(argparse.ArgumentTypeError):
        run_fenix._team_models("technical=model,technical=other")


def test_nanofenix_child_environment_does_not_inherit_unrelated_secrets(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("BINANCE_API_SECRET", "exchange-secret")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret")
    monkeypatch.setenv("FENIX_MASTER_PASSWORD", "vault-secret")
    monkeypatch.setenv("SMTP_PASSWORD", "mail-secret")
    monkeypatch.setenv("NANOFENIXV3_BAR_INTERVAL", "2")
    monkeypatch.setenv("FENIX_MODEL_SIGNING_KEY", "model-key")

    env = run_fenix._nanofenix_child_environment(tmp_path)

    assert env["NANOFENIXV3_BAR_INTERVAL"] == "2"
    assert env["FENIX_MODEL_SIGNING_KEY"] == "model-key"
    assert env["FENIX_SKIP_DOTENV"] == "1"
    assert env["PYTHONPATH"] == str(tmp_path)
    assert "BINANCE_API_SECRET" not in env
    assert "JWT_SECRET" not in env
    assert "FENIX_MASTER_PASSWORD" not in env
    assert "SMTP_PASSWORD" not in env


@pytest.mark.skipif(run_fenix.fcntl is None, reason="POSIX file locking is required")
def test_instance_lock_rejects_symlink_and_keeps_target_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("FENIX_INSTANCE_LOCK_DIR", str(tmp_path))
    target = tmp_path / "target"
    target.write_text("do-not-touch", encoding="utf-8")
    (tmp_path / "fenix_btcusdt.lock").symlink_to(target)

    lock = run_fenix.InstanceLock("BTCUSDT")
    with pytest.raises(RuntimeError, match="safely"):
        lock.acquire()

    assert target.read_text(encoding="utf-8") == "do-not-touch"


@pytest.mark.skipif(run_fenix.fcntl is None, reason="POSIX file locking is required")
def test_instance_lock_is_private(monkeypatch, tmp_path):
    monkeypatch.setenv("FENIX_INSTANCE_LOCK_DIR", str(tmp_path))
    lock = run_fenix.InstanceLock("ETHUSDT")

    lock.acquire()
    try:
        assert stat.S_IMODE(lock.path.stat().st_mode) == 0o600
        assert f"pid={os.getpid()}" in lock.path.read_text(encoding="utf-8")
    finally:
        lock.release()


def test_private_file_helpers_reject_symlinks(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "log"
    link.symlink_to(target)

    with pytest.raises(OSError):
        run_fenix._open_private_append(link)
    with pytest.raises(RuntimeError, match="symbolic link"):
        run_fenix._write_private_text(link, "unsafe")

    assert target.read_text(encoding="utf-8") == "safe"
