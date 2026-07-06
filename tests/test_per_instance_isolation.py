"""Tests for per-instance (dual-bot) isolation derived from the trading symbol.

Regression coverage for the 2026-07-05 double-exposure: two Fenix instances on
the same account shared balance accounting and risk/log state, so both sized
against the same capital and corrupted each other's peak/drawdown tracking.
"""

import importlib

import pytest

run_fenix = importlib.import_module("run_fenix")


ISOLATION_KEYS = [
    "FENIX_BALANCE_ASSETS",
    "FENIX_RISK_MANAGER_STORAGE_PATH",
    "FENIX_REASONING_BANK_DIR",
    "FENIX_LLM_RESPONSE_LOG_DIR",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_usdc_symbol_sets_usdc_bucket(monkeypatch):
    run_fenix._apply_per_instance_isolation("ETHUSDC")
    import os

    assert os.environ["FENIX_BALANCE_ASSETS"] == "USDC"
    assert os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] == "logs/risk_manager_ethusdc.jsonl"
    assert os.environ["FENIX_REASONING_BANK_DIR"] == "logs/reasoning_bank_ethusdc"
    assert os.environ["FENIX_LLM_RESPONSE_LOG_DIR"] == "logs/llm_responses_ethusdc"


def test_usdt_symbol_sets_usdt_bucket(monkeypatch):
    run_fenix._apply_per_instance_isolation("SOLUSDT")
    import os

    assert os.environ["FENIX_BALANCE_ASSETS"] == "USDT"
    assert os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] == "logs/risk_manager_solusdt.jsonl"


def test_two_symbols_get_distinct_paths(monkeypatch):
    import os

    run_fenix._apply_per_instance_isolation("ETHUSDC")
    eth_risk = os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"]
    eth_assets = os.environ["FENIX_BALANCE_ASSETS"]
    for key in ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    run_fenix._apply_per_instance_isolation("SOLUSDT")
    assert os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] != eth_risk
    assert os.environ["FENIX_BALANCE_ASSETS"] != eth_assets


def test_explicit_env_is_not_overwritten(monkeypatch):
    import os

    monkeypatch.setenv("FENIX_BALANCE_ASSETS", "USDT,USDC")
    monkeypatch.setenv("FENIX_RISK_MANAGER_STORAGE_PATH", "custom/path.jsonl")
    run_fenix._apply_per_instance_isolation("ETHUSDC")
    assert os.environ["FENIX_BALANCE_ASSETS"] == "USDT,USDC"
    assert os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] == "custom/path.jsonl"


def test_unknown_quote_asset_skips_balance_bucket(monkeypatch):
    import os

    run_fenix._apply_per_instance_isolation("ETHBTC")
    # No stablecoin quote -> do not restrict balance (would read near-zero)
    assert "FENIX_BALANCE_ASSETS" not in os.environ
    # But log/risk isolation still applies
    assert os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] == "logs/risk_manager_ethbtc.jsonl"


def test_empty_symbol_is_noop(monkeypatch):
    import os

    run_fenix._apply_per_instance_isolation("")
    for key in ISOLATION_KEYS:
        assert key not in os.environ


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
