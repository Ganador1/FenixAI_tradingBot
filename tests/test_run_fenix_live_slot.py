import argparse
import asyncio
from datetime import datetime, timezone
import json
import os

os.environ.setdefault("FENIX_LIVE_SLOT_REEXECED", "1")


def _args(**overrides):
    base = {
        "run_minutes": 30,
        "disable_trading": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_configure_slot_runtime_env_enables_cleanup_for_timed_live_slot(monkeypatch):
    from scripts.run_fenix_live_slot import EngineModeConfig, configure_slot_runtime_env

    monkeypatch.delenv("FENIX_CLEANUP_ON_STOP", raising=False)
    monkeypatch.delenv("FENIX_RISK_MANAGER_STORAGE_PATH", raising=False)
    args = _args(run_minutes=30, disable_trading=False)
    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )

    applied = configure_slot_runtime_env(args, mode_cfg)

    assert applied == {"FENIX_CLEANUP_ON_STOP": "1"}
    assert os.getenv("FENIX_CLEANUP_ON_STOP") == "1"


def test_configure_slot_runtime_env_respects_existing_cleanup_override(monkeypatch):
    from scripts.run_fenix_live_slot import EngineModeConfig, configure_slot_runtime_env

    monkeypatch.setenv("FENIX_CLEANUP_ON_STOP", "0")
    monkeypatch.delenv("FENIX_RISK_MANAGER_STORAGE_PATH", raising=False)
    args = _args(run_minutes=30, disable_trading=False)
    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )

    applied = configure_slot_runtime_env(args, mode_cfg)

    assert applied == {}
    assert os.getenv("FENIX_CLEANUP_ON_STOP") == "0"


def test_configure_slot_runtime_env_isolates_paper_risk_storage(monkeypatch):
    from scripts.run_fenix_live_slot import EngineModeConfig, configure_slot_runtime_env

    monkeypatch.delenv("FENIX_CLEANUP_ON_STOP", raising=False)
    monkeypatch.delenv("FENIX_RISK_MANAGER_STORAGE_PATH", raising=False)
    args = _args(
        run_minutes=30,
        disable_trading=False,
        run_tag="paper-run",
        slot_name="paper-slot",
        symbol="SOLUSDT",
        timeframe="1m",
    )
    mode_cfg = EngineModeConfig(
        paper_trading=True,
        use_testnet=False,
        allow_live_trading=False,
    )

    applied = configure_slot_runtime_env(args, mode_cfg)

    assert set(applied) == {"FENIX_RISK_MANAGER_STORAGE_PATH"}
    assert "risk_manager_paper_paper-slot_SOLUSDT_1m_paper-run.jsonl" in applied[
        "FENIX_RISK_MANAGER_STORAGE_PATH"
    ]
    assert os.getenv("FENIX_RISK_MANAGER_STORAGE_PATH") == applied[
        "FENIX_RISK_MANAGER_STORAGE_PATH"
    ]


def test_configure_slot_runtime_env_isolates_testnet_risk_storage(monkeypatch):
    from scripts.run_fenix_live_slot import EngineModeConfig, configure_slot_runtime_env

    monkeypatch.delenv("FENIX_CLEANUP_ON_STOP", raising=False)
    monkeypatch.delenv("FENIX_RISK_MANAGER_STORAGE_PATH", raising=False)
    args = _args(
        run_minutes=30,
        disable_trading=False,
        run_tag="testnet-run",
        slot_name="testnet-slot",
        symbol="ETHUSDT",
        timeframe="5m",
    )
    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=True,
        allow_live_trading=False,
    )

    applied = configure_slot_runtime_env(args, mode_cfg)

    assert set(applied) == {"FENIX_CLEANUP_ON_STOP", "FENIX_RISK_MANAGER_STORAGE_PATH"}
    assert applied["FENIX_CLEANUP_ON_STOP"] == "1"
    assert "risk_manager_testnet_testnet-slot_ETHUSDT_5m_testnet-run.jsonl" in applied[
        "FENIX_RISK_MANAGER_STORAGE_PATH"
    ]


def test_configure_slot_runtime_env_respects_existing_risk_storage_override(monkeypatch):
    from scripts.run_fenix_live_slot import EngineModeConfig, configure_slot_runtime_env

    monkeypatch.delenv("FENIX_CLEANUP_ON_STOP", raising=False)
    monkeypatch.setenv("FENIX_RISK_MANAGER_STORAGE_PATH", "logs/custom-risk.jsonl")
    args = _args(
        run_minutes=30,
        disable_trading=False,
        run_tag="paper-run",
        slot_name="paper-slot",
        symbol="SOLUSDT",
        timeframe="1m",
    )
    mode_cfg = EngineModeConfig(
        paper_trading=True,
        use_testnet=False,
        allow_live_trading=False,
    )

    applied = configure_slot_runtime_env(args, mode_cfg)

    assert applied == {}
    assert os.getenv("FENIX_RISK_MANAGER_STORAGE_PATH") == "logs/custom-risk.jsonl"


def test_configure_experiment_env_applies_lite_mtf_guard_options(monkeypatch):
    from scripts.run_fenix_live_slot import _configure_experiment_env

    for key in (
        "FENIX_LITE_PIPELINE",
        "FENIX_LITE_CONSENSUS_MODE",
        "FENIX_LITE_NODE_TIMEOUT_SEC",
        "FENIX_STRICT_MTF_BIAS_TIMEFRAME",
        "FENIX_STRICT_MTF_OPPOSING_VETO_CONF",
        "FENIX_STRICT_MTF_BIAS_CACHE_SEC",
        "FENIX_LITE_MTF_CONFIRM_CONF",
        "FENIX_LITE_MTF_QABBA_MIN_CONF",
        "FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD",
    ):
        monkeypatch.delenv(key, raising=False)

    args = argparse.Namespace(
        disable_reasoning_bank=False,
        disable_risk_manager=False,
        disable_judge=False,
        monolithic_mode=False,
        lite_pipeline=True,
        balance_fallback_usdt=None,
        max_risk_per_trade=None,
        fast_loop_sec=None,
        min_klines_to_start=None,
        analyze_on_start=False,
        lite_consensus_mode="technical_mtf_qabba_guard",
        lite_node_timeout_sec=45,
        strict_mtf_bias_timeframe="30m",
        strict_mtf_opposing_veto_conf=0.75,
        strict_mtf_bias_cache_sec=120,
        lite_mtf_confirm_conf=0.55,
        lite_mtf_qabba_min_conf=0.70,
        lite_allow_mtf_qabba_when_tech_hold=True,
    )

    applied = _configure_experiment_env(args)

    assert applied["FENIX_LITE_PIPELINE"] == "1"
    assert applied["FENIX_LITE_CONSENSUS_MODE"] == "technical_mtf_qabba_guard"
    assert applied["FENIX_LITE_NODE_TIMEOUT_SEC"] == "45"
    assert applied["FENIX_STRICT_MTF_BIAS_TIMEFRAME"] == "30m"
    assert applied["FENIX_STRICT_MTF_OPPOSING_VETO_CONF"] == "0.75"
    assert applied["FENIX_STRICT_MTF_BIAS_CACHE_SEC"] == "120"
    assert applied["FENIX_LITE_MTF_CONFIRM_CONF"] == "0.55"
    assert applied["FENIX_LITE_MTF_QABBA_MIN_CONF"] == "0.7"
    assert applied["FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD"] == "1"


def test_apply_requested_exchange_leverage_skips_paper(monkeypatch):
    from scripts.run_fenix_live_slot import (
        EngineModeConfig,
        _apply_requested_exchange_leverage,
    )

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    mode_cfg = EngineModeConfig(
        paper_trading=True,
        use_testnet=False,
        allow_live_trading=False,
    )
    args = _args(symbol="SOLUSDT")

    result = asyncio.run(_apply_requested_exchange_leverage(args, mode_cfg))

    assert result is None


def test_apply_requested_exchange_leverage_calls_exchange_api(monkeypatch):
    from scripts import run_fenix_live_slot as mod

    calls = {}

    class _FakeExchange:
        def futures_change_leverage(self, symbol: str, leverage: int):
            calls["symbol"] = symbol
            calls["leverage"] = leverage
            return {"symbol": symbol, "leverage": leverage}

    def _fake_create_exchange_api(api_key: str, api_secret: str, is_paper: bool):
        calls["api_key"] = api_key
        calls["api_secret"] = api_secret
        calls["is_paper"] = is_paper
        return _FakeExchange()

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    monkeypatch.setattr(mod, "create_exchange_api", _fake_create_exchange_api)

    mode_cfg = mod.EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )
    args = _args(symbol="SOLUSDT")

    result = asyncio.run(mod._apply_requested_exchange_leverage(args, mode_cfg))

    assert result == {"symbol": "SOLUSDT", "leverage": 10}
    assert calls == {
        "api_key": "key",
        "api_secret": "secret",
        "is_paper": False,
        "symbol": "SOLUSDT",
        "leverage": 10,
    }


def test_live_event_collector_serializes_datetime_payload(tmp_path):
    from scripts.run_fenix_live_slot import LiveEventCollector

    log_path = tmp_path / "events.jsonl"
    collector = LiveEventCollector(log_path, {"slot_name": "test-slot"})

    asyncio.run(
        collector.on_event(
            "risk:blocked",
            {
                "status": {
                    "reason": "blocked",
                    "expires_at": datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc),
                }
            },
        )
    )

    payload = json.loads(log_path.read_text().strip())
    assert payload["payload"]["status"]["expires_at"] == "2026-03-08T12:00:00+00:00"


def test_live_event_collector_counts_hydrated_positions(tmp_path):
    from scripts.run_fenix_live_slot import LiveEventCollector

    log_path = tmp_path / "events.jsonl"
    collector = LiveEventCollector(log_path, {"slot_name": "test-slot"})

    asyncio.run(collector.on_event("position:hydrated", {"symbol": "SOLUSDT"}))

    summary = collector.as_dict()
    assert summary["event_counts"]["position:hydrated"] == 1
    assert summary["position_hydrated"] == 1


def test_build_event_accounting_report_flags_live_open_close_gap():
    from scripts.run_fenix_live_slot import (
        EngineModeConfig,
        _build_event_accounting_report,
    )

    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )
    events = {
        "trade_executed": 4,
        "position_opened": 4,
        "position_closed": 2,
    }

    report = _build_event_accounting_report(events, mode_cfg, active_trades=0)

    assert report["status"] == "gap"
    assert report["open_close_delta"] == 2
    assert "open_close_mismatch" in report["warnings"]


def test_build_event_accounting_report_tracks_live_open_position_separately():
    from scripts.run_fenix_live_slot import (
        EngineModeConfig,
        _build_event_accounting_report,
    )

    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )
    events = {
        "trade_executed": 1,
        "position_opened": 1,
        "position_closed": 0,
    }

    report = _build_event_accounting_report(events, mode_cfg, active_trades=1)

    assert report["status"] == "open_position"
    assert report["open_close_delta"] == 1
    assert "open_position_at_slot_end" in report["warnings"]
    assert "open_close_mismatch" not in report["warnings"]


def test_build_event_accounting_report_accepts_hydrated_position_close():
    from scripts.run_fenix_live_slot import (
        EngineModeConfig,
        _build_event_accounting_report,
    )

    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )
    events = {
        "position_hydrated": 1,
        "trade_executed": 0,
        "position_opened": 0,
        "position_closed": 1,
    }

    report = _build_event_accounting_report(events, mode_cfg, active_trades=0)

    assert report["status"] == "ok"
    assert report["position_hydrated"] == 1
    assert report["open_close_delta"] == 0
    assert report["warnings"] == []


def test_build_event_accounting_report_does_not_flag_paper_simulated_trades():
    from scripts.run_fenix_live_slot import (
        EngineModeConfig,
        _build_event_accounting_report,
    )

    mode_cfg = EngineModeConfig(
        paper_trading=True,
        use_testnet=False,
        allow_live_trading=False,
    )
    events = {
        "trade_simulated": 12,
        "trade_executed": 0,
        "position_opened": 0,
        "position_closed": 0,
    }

    report = _build_event_accounting_report(events, mode_cfg, active_trades=0)

    assert report["status"] == "paper_simulated"
    assert report["warnings"] == []


def test_resolve_live_slot_lock_path_groups_same_symbol_across_timeframes(monkeypatch):
    from scripts.run_fenix_live_slot import _resolve_live_slot_lock_path, EngineModeConfig

    monkeypatch.delenv("FENIX_ALLOW_CONCURRENT_SYMBOL_SLOT", raising=False)
    args_1 = argparse.Namespace(symbol="BTCUSDC", timeframe="15m", api_key_index=1)
    args_2 = argparse.Namespace(symbol="btcusdc", timeframe="3m", api_key_index=1)
    mode_cfg = EngineModeConfig(
        paper_trading=False,
        use_testnet=False,
        allow_live_trading=True,
    )

    lock_path_1 = _resolve_live_slot_lock_path(args_1, mode_cfg)
    lock_path_2 = _resolve_live_slot_lock_path(args_2, mode_cfg)

    assert lock_path_1 is not None
    assert lock_path_1 == lock_path_2


def test_preferred_repo_python_prefers_dotvenv(tmp_path):
    from scripts.run_fenix_live_slot import _preferred_repo_python

    dotvenv = tmp_path / ".venv" / "bin"
    fenix_env = tmp_path / "fenix_env" / "bin"
    dotvenv.mkdir(parents=True)
    fenix_env.mkdir(parents=True)
    (dotvenv / "python3").write_text("")
    (fenix_env / "python").write_text("")

    assert _preferred_repo_python(tmp_path) == (dotvenv / "python3").resolve()


def test_resolve_visual_model_name_falls_back_for_non_vision_assignment():
    from scripts.run_fenix_live_slot import _resolve_visual_model_name

    resolved = _resolve_visual_model_name("minimax-m2.7:cloud", "qwen3-vl:235b-cloud")

    assert resolved == "qwen3-vl:235b-cloud"


def test_resolve_visual_model_name_keeps_known_multimodal_model():
    from scripts.run_fenix_live_slot import _resolve_visual_model_name

    resolved = _resolve_visual_model_name("qwen3.5:cloud", None)

    assert resolved == "qwen3.5:cloud"


def test_resolve_visual_model_name_keeps_qwen3_vl():
    from scripts.run_fenix_live_slot import _resolve_visual_model_name

    resolved = _resolve_visual_model_name("qwen3-vl:235b-cloud", None)

    assert resolved == "qwen3-vl:235b-cloud"


def test_run_slot_passes_llm_config_to_engine(tmp_path, monkeypatch):
    from scripts import run_fenix_live_slot as mod

    sentinel = object()
    captured: dict[str, object] = {}

    class _FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.on_agent_event = None

        async def start(self):
            return None

        async def stop(self):
            return None

        def get_status(self):
            return {}

        def get_risk_status(self):
            return {}

    monkeypatch.setattr(mod, "_build_llm_config", lambda args: sentinel)
    monkeypatch.setattr(mod, "TradingEngine", _FakeEngine)
    monkeypatch.setattr(
        mod,
        "resolve_engine_mode",
        lambda mode, allow_live, use_testnet_data: mod.EngineModeConfig(
            paper_trading=True,
            use_testnet=False,
            allow_live_trading=False,
        ),
    )

    args = argparse.Namespace(
        run_tag="test",
        slot_name="slot",
        slot_index=1,
        symbol="BTCUSDT",
        timeframe="1m",
        mode="paper",
        experiment=None,
        experiment_id=None,
        append_event_log=False,
        event_log_path=tmp_path / "events.jsonl",
        summary_path=tmp_path / "summary.json",
        no_visual=False,
        no_sentiment=False,
        disable_reasoning_bank=True,
        disable_risk_manager=False,
        disable_judge=True,
        monolithic_mode=False,
        lite_pipeline=False,
        run_minutes=1,
        allow_live=False,
        use_testnet_data=False,
        shutdown_timeout_sec=5.0,
        disable_trading=True,
    )

    asyncio.run(mod.run_slot(args))

    assert captured["llm_config"] is sentinel
