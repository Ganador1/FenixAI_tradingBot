#!/usr/bin/env python3
"""
Run a single live-like Fenix testing slot using the real TradingEngine.

Unlike the legacy hybrid benchmark path, this script runs the full engine
decision->judge->risk->execution lifecycle.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _repo_python_candidates(project_root: Path) -> list[Path]:
    return [
        (project_root / ".venv" / "bin" / "python3").resolve(),
        (project_root / ".venv" / "bin" / "python").resolve(),
        (project_root / "fenix_env" / "bin" / "python").resolve(),
    ]


def _preferred_repo_python(project_root: Path) -> Path | None:
    for candidate in _repo_python_candidates(project_root):
        if candidate.exists():
            return candidate
    return None


# Prefer the repository virtualenv interpreter when available. This avoids
# missing runtime deps when the script is launched from the system Python.
if os.getenv("FENIX_LIVE_SLOT_REEXECED") != "1":
    try:
        current_python = Path(sys.executable).resolve()
        preferred_python = _preferred_repo_python(PROJECT_ROOT)
        managed_parents = {
            candidate.parent
            for candidate in _repo_python_candidates(PROJECT_ROOT)
            if candidate.exists()
        }
        if (
            preferred_python is not None
            and current_python.parent not in managed_parents
            and current_python.parent != preferred_python.parent
        ):
            os.environ["FENIX_LIVE_SLOT_REEXECED"] = "1"
            os.execv(
                str(preferred_python),
                [str(preferred_python), str(Path(__file__).resolve())] + sys.argv[1:],
            )
    except Exception:
        pass

from config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig
from src.system.connections.exchange_api import create_exchange_api
from src.trading.engine import TradingEngine

DEFAULT_OLLAMA_CLOUD_URL = os.getenv("OLLAMA_CLOUD_URL", "https://api.ollama.com")
DEFAULT_OLLAMA_LOCAL_URL = os.getenv("OLLAMA_LOCAL_URL", "http://localhost:11434")
SUPPORTED_PROVIDER_CHOICES = (
    "ollama_cloud",
    "ollama_local",
    "huggingface_mlx",
    "huggingface_inference",
    "groq",
    "openai",
    "anthropic",
)
TEAM_AGENT_KEYS = {"technical", "qabba", "decision", "sentiment", "visual", "risk_manager"}
TOKEN_ENV_KEYS = {
    "technical": "FENIX_TECHNICAL_MAX_TOKENS",
    "qabba": "FENIX_QABBA_MAX_TOKENS",
    "decision": "FENIX_DECISION_MAX_TOKENS",
    "sentiment": "FENIX_SENTIMENT_MAX_TOKENS",
    "visual": "FENIX_VISUAL_MAX_TOKENS",
    "risk_manager": "FENIX_RISK_MANAGER_MAX_TOKENS",
}
SAFE_DEFAULT_VISION_MODEL = os.getenv("FENIX_SAFE_VISION_MODEL", "gemma4:31b-cloud")
SHORT_TF_AGENT_TIMEOUT_DEFAULTS = {
    "FENIX_TECHNICAL_TIMEOUT_SHORT_SEC": "45.0",
    "FENIX_QABBA_TIMEOUT_SHORT_SEC": "30.0",
    "FENIX_DECISION_TIMEOUT_SHORT_SEC": "45.0",
    "FENIX_RISK_TIMEOUT_SEC": "45.0",
    "FENIX_SENTIMENT_AGENT_TIMEOUT_SHORT_SEC": "20.0",
    "FENIX_VISUAL_TIMEOUT_SHORT_SEC": "60.0",
}


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

    # Lightweight fallback when python-dotenv is not installed.
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _mask_secret(value: str | None) -> str:
    if not value:
        return "(missing)"
    if len(value) < 6:
        return "***"
    return f"***{value[-6:]}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)


def _active_binance_env_names(mode_cfg: EngineModeConfig) -> tuple[str, str]:
    if mode_cfg.use_testnet:
        return "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"
    return "BINANCE_API_KEY", "BINANCE_API_SECRET"


def _resolve_live_slot_lock_path(
    args: argparse.Namespace, mode_cfg: EngineModeConfig
) -> Path | None:
    allow_concurrent = os.getenv("FENIX_ALLOW_CONCURRENT_SYMBOL_SLOT", "0") == "1"
    if mode_cfg.paper_trading or allow_concurrent:
        return None

    active_key_name, _ = _active_binance_env_names(mode_cfg)
    active_key = os.getenv(active_key_name, "")
    if active_key:
        account_fingerprint = hashlib.sha256(active_key.encode("utf-8")).hexdigest()[:12]
    else:
        account_fingerprint = f"idx{args.api_key_index}"

    symbol_key = _slug(args.symbol.strip().lower())
    network = "testnet" if mode_cfg.use_testnet else "mainnet"
    lock_dir = PROJECT_ROOT / "logs" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"fenix_live_slot_{network}_{account_fingerprint}_{symbol_key}.lock"


@contextmanager
def live_slot_symbol_lock(args: argparse.Namespace, mode_cfg: EngineModeConfig):
    lock_path = _resolve_live_slot_lock_path(args, mode_cfg)
    if lock_path is None:
        yield None
        return

    handle = lock_path.open("a+", encoding="utf-8")
    locked = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Another live slot already holds the {args.symbol} lock for this account: {lock_path}"
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "symbol": args.symbol,
                    "timeframe": args.timeframe,
                    "slot_name": args.slot_name,
                    "slot_index": args.slot_index,
                    "run_tag": args.run_tag,
                    "mode": args.mode,
                    "locked_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        handle.flush()
        yield lock_path
    finally:
        try:
            if locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def apply_api_key_index(env: dict[str, str], api_key_index: int) -> dict[str, str]:
    """
    Apply indexed secrets (KEY_1/KEY_2) to canonical env var names.

    Returns a masked map with what was selected.
    """
    selected: dict[str, str] = {}

    provider_key_bases = (
        "OLLAMA_CLOUD_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "HUGGINGFACE_API_KEY",
    )
    for base in provider_key_bases:
        indexed = env.get(f"{base}_{api_key_index}")
        if indexed:
            env[base] = indexed
            selected[base] = _mask_secret(indexed)

    # Keep backwards compatibility aliases for Ollama clients.
    if env.get("OLLAMA_CLOUD_API_KEY"):
        env["OLLAMA_API_KEY"] = env["OLLAMA_CLOUD_API_KEY"]
        selected["OLLAMA_API_KEY"] = _mask_secret(env["OLLAMA_API_KEY"])

    binance_bases = (
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
    )
    for base in binance_bases:
        indexed = env.get(f"{base}_{api_key_index}")
        if indexed:
            env[base] = indexed
            selected[base] = _mask_secret(indexed)

    return selected


def _token_multiplier() -> float:
    raw = os.getenv("FENIX_MAX_TOKENS_MULTIPLIER", "1.0").strip()
    try:
        value = float(raw)
    except Exception:
        return 1.0
    if value <= 0:
        return 1.0
    return value


def _resolve_max_tokens(agent: str, base_tokens: int) -> int:
    env_key = TOKEN_ENV_KEYS.get(agent)
    if env_key:
        raw = os.getenv(env_key)
        if raw:
            try:
                parsed = int(raw)
                if parsed > 0:
                    return parsed
            except Exception:
                pass
    multiplier = _token_multiplier()
    return max(64, int(round(base_tokens * multiplier)))


def _default_api_base(provider_type: str, explicit_api_base: str | None) -> str:
    if explicit_api_base:
        return explicit_api_base
    if provider_type == "ollama_local":
        return DEFAULT_OLLAMA_LOCAL_URL
    if provider_type == "ollama_cloud":
        return DEFAULT_OLLAMA_CLOUD_URL
    return ""


def _model_supports_image_input(model_name: str | None) -> bool:
    normalized = (model_name or "").strip().lower()
    if not normalized:
        return False

    # Conservative allowlist based on current Ollama Cloud support verified in
    # the latest local runs and official model pages.
    vision_markers = (
        "gemma4",
        "qwen3.5",
        "qwen3-vl",
        "qwen2.5-vl",
        "gpt-4o",
        "gemini",
    )
    if any(marker in normalized for marker in vision_markers):
        return True

    # These models were recently assigned to the visual agent and failed with
    # HTTP 400 "this model does not support image input".
    non_vision_markers = (
        "minimax",
        "glm-5",
        "nemotron",
        "deepseek",
        "ministral",
        "cogito",
    )
    if any(marker in normalized for marker in non_vision_markers):
        return False

    return False


def _resolve_visual_model_name(
    requested_model: str | None,
    fallback_model: str | None = None,
) -> str:
    for candidate in (requested_model, fallback_model, SAFE_DEFAULT_VISION_MODEL):
        if _model_supports_image_input(candidate):
            return str(candidate)
    return SAFE_DEFAULT_VISION_MODEL


def _json_safe(value: Any) -> Any:
    """Convert value to JSON-serializable format, handling datetime and other types."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    # Handle any other object with isoformat (like pandas Timestamp)
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    # Handle timedelta
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return str(value)


def build_llm_config_for_model(
    model: str,
    *,
    vision_model: str | None = None,
    api_base: str | None = None,
    timeout: int = 120,
    disable_rotation: bool = True,
    provider_type: str = "ollama_cloud",
    risk_provider_type: str | None = None,
) -> LLMProvidersConfig:
    base = {
        "model_name": model,
        "temperature": 0.1,
        "max_tokens": 1200,
        "timeout": timeout,
        "extra_config": {"disable_rotation": disable_rotation},
    }

    def cfg(*, provider_override: str | None = None, **overrides: Any) -> AgentProviderConfig:
        active_provider = provider_override or provider_type
        payload = {
            **base,
            "provider_type": active_provider,
            "api_base": _default_api_base(
                active_provider,
                api_base if active_provider == provider_type else None,
            ),
        }
        payload.update(overrides)
        return AgentProviderConfig(**payload)

    visual_model = _resolve_visual_model_name(vision_model or model, SAFE_DEFAULT_VISION_MODEL)
    return LLMProvidersConfig(
        sentiment=cfg(temperature=0.15, max_tokens=_resolve_max_tokens("sentiment", 900)),
        technical=cfg(temperature=0.08, max_tokens=_resolve_max_tokens("technical", 1200)),
        visual=cfg(
            model_name=visual_model,
            supports_vision=True,
            max_tokens=_resolve_max_tokens("visual", 1000),
        ),
        qabba=cfg(temperature=0.06, max_tokens=_resolve_max_tokens("qabba", 800)),
        decision=cfg(temperature=0.12, max_tokens=_resolve_max_tokens("decision", 1000)),
        risk_manager=cfg(
            provider_override=risk_provider_type,
            temperature=0.1,
            max_tokens=_resolve_max_tokens("risk_manager", 700),
        ),
    )


def parse_team_models(raw: str | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not raw:
        return mapping

    for chunk in raw.replace(";", ",").split(","):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid team mapping chunk: {part}")
        agent, model = part.split("=", 1)
        agent = agent.strip().lower()
        model = model.strip()
        if agent not in TEAM_AGENT_KEYS:
            raise ValueError(f"Invalid team agent key: {agent}")
        if not model:
            raise ValueError(f"Empty model for agent: {agent}")
        mapping[agent] = model
    return mapping


def build_llm_config_for_team(
    team_models: dict[str, str],
    *,
    default_model: str | None = None,
    vision_model: str | None = None,
    api_base: str | None = None,
    timeout: int = 120,
    disable_rotation: bool = True,
    provider_type: str = "ollama_cloud",
    risk_provider_type: str | None = None,
) -> LLMProvidersConfig:
    fallback_model = default_model or (next(iter(team_models.values())) if team_models else None)
    if not fallback_model:
        raise ValueError("No default model available for team config")

    base = {
        "temperature": 0.1,
        "max_tokens": 1200,
        "timeout": timeout,
        "extra_config": {"disable_rotation": disable_rotation},
    }

    def model_for(agent: str, fallback: str | None = None) -> str:
        return team_models.get(agent) or fallback or fallback_model

    def cfg(
        agent: str,
        *,
        provider_override: str | None = None,
        **overrides: Any,
    ) -> AgentProviderConfig:
        active_provider = provider_override or provider_type
        payload = {
            **base,
            "provider_type": active_provider,
            "api_base": _default_api_base(
                active_provider,
                api_base if active_provider == provider_type else None,
            ),
            "model_name": model_for(agent),
        }
        payload.update(overrides)
        return AgentProviderConfig(**payload)

    resolved_visual_model = _resolve_visual_model_name(
        model_for("visual", vision_model or fallback_model),
        vision_model or SAFE_DEFAULT_VISION_MODEL,
    )

    return LLMProvidersConfig(
        sentiment=cfg(
            "sentiment",
            temperature=0.15,
            max_tokens=_resolve_max_tokens("sentiment", 900),
        ),
        technical=cfg(
            "technical",
            temperature=0.08,
            max_tokens=_resolve_max_tokens("technical", 1200),
        ),
        visual=cfg(
            "visual",
            model_name=resolved_visual_model,
            supports_vision=True,
            max_tokens=_resolve_max_tokens("visual", 1000),
        ),
        qabba=cfg("qabba", temperature=0.06, max_tokens=_resolve_max_tokens("qabba", 800)),
        decision=cfg(
            "decision",
            temperature=0.12,
            max_tokens=_resolve_max_tokens("decision", 1000),
        ),
        risk_manager=cfg(
            "risk_manager",
            provider_override=risk_provider_type,
            temperature=0.1,
            max_tokens=_resolve_max_tokens("risk_manager", 700),
        ),
    )


@dataclass(frozen=True)
class EngineModeConfig:
    paper_trading: bool
    use_testnet: bool
    allow_live_trading: bool


def resolve_engine_mode(mode: str, allow_live: bool, use_testnet_data: bool) -> EngineModeConfig:
    mode = mode.strip().lower()
    if mode == "paper":
        return EngineModeConfig(
            paper_trading=True,
            use_testnet=bool(use_testnet_data),
            allow_live_trading=False,
        )
    if mode == "testnet":
        return EngineModeConfig(
            paper_trading=False,
            use_testnet=True,
            allow_live_trading=True,
        )
    if mode == "live":
        if not allow_live:
            raise ValueError("Live mode requires --allow-live")
        return EngineModeConfig(
            paper_trading=False,
            use_testnet=False,
            allow_live_trading=True,
        )
    raise ValueError(f"Unsupported mode: {mode}")


class LiveEventCollector:
    def __init__(self, event_log_path: Path, metadata: dict[str, Any], *, append: bool = False):
        self.event_log_path = event_log_path
        self.metadata = dict(metadata)
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            # Start each slot with a clean event log unless append is explicitly requested.
            self.event_log_path.write_text("")

        self.total_events = 0
        self.event_counts: dict[str, int] = {}
        self.decision_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        self.risk_blocked = 0
        self.judge_blocked = 0
        self.filter_blocked = 0
        self.fast_decisions = 0
        self.trade_simulated = 0
        self.trade_executed = 0
        self.position_hydrated = 0
        self.position_opened = 0
        self.position_closed = 0

    async def on_event(self, event: str, payload: dict[str, Any]) -> None:
        self.total_events += 1
        self.event_counts[event] = self.event_counts.get(event, 0) + 1

        if event == "final_decision":
            decision = str(payload.get("decision", "HOLD")).upper()
            if decision in self.decision_counts:
                self.decision_counts[decision] += 1
            else:
                self.decision_counts["HOLD"] += 1
        elif event == "risk:blocked":
            self.risk_blocked += 1
        elif event == "judge:blocked":
            self.judge_blocked += 1
        elif event == "filter:blocked":
            self.filter_blocked += 1
        elif event == "fast_decision":
            self.fast_decisions += 1
        elif event == "trade:simulated":
            self.trade_simulated += 1
        elif event == "trade_executed":
            self.trade_executed += 1
        elif event == "position:hydrated":
            self.position_hydrated += 1
        elif event == "position:opened":
            self.position_opened += 1
        elif event == "position:closed":
            self.position_closed += 1

        payload_with_meta = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": _json_safe(payload),
            **_json_safe(self.metadata),
        }
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload_with_meta, ensure_ascii=False) + "\n")

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "event_counts": dict(self.event_counts),
            "decision_counts": dict(self.decision_counts),
            "risk_blocked": self.risk_blocked,
            "judge_blocked": self.judge_blocked,
            "filter_blocked": self.filter_blocked,
            "fast_decisions": self.fast_decisions,
            "trade_simulated": self.trade_simulated,
            "trade_executed": self.trade_executed,
            "position_hydrated": self.position_hydrated,
            "position_opened": self.position_opened,
            "position_closed": self.position_closed,
        }


def _event_count(events: dict[str, Any], key: str, *, event_name: str | None = None) -> int:
    try:
        direct_count = int(events.get(key) or 0)
    except Exception:
        direct_count = 0
    if direct_count:
        return direct_count

    if event_name:
        try:
            event_counts = events.get("event_counts") or {}
            return int(event_counts.get(event_name) or 0)
        except Exception:
            return 0
    return direct_count


def _build_event_accounting_report(
    events: dict[str, Any],
    mode_cfg: EngineModeConfig,
    *,
    active_trades: int,
) -> dict[str, Any]:
    trade_executed = _event_count(events, "trade_executed")
    trade_simulated = _event_count(events, "trade_simulated")
    hydrated = _event_count(events, "position_hydrated", event_name="position:hydrated")
    opened = _event_count(events, "position_opened")
    closed = _event_count(events, "position_closed")
    accounted_opens = opened + hydrated
    open_close_delta = accounted_opens - closed

    warnings: list[str] = []
    status = "ok"

    if mode_cfg.paper_trading:
        status = "paper_simulated" if trade_simulated else "ok"
        if trade_executed or opened or closed:
            status = "paper_event_gap"
            warnings.append("paper_slot_emitted_live_position_events")
    else:
        if active_trades > 0:
            status = "open_position"
            warnings.append("open_position_at_slot_end")
        elif open_close_delta != 0:
            status = "gap"
            warnings.append("open_close_mismatch")

        if closed > accounted_opens:
            status = "gap"
            warnings.append("more_closes_than_opens")
        if trade_executed != opened:
            status = "gap"
            warnings.append("trade_executed_position_opened_mismatch")

    return {
        "status": status,
        "warnings": warnings,
        "trade_executed": trade_executed,
        "trade_simulated": trade_simulated,
        "position_hydrated": hydrated,
        "position_opened": opened,
        "position_closed": closed,
        "accounted_position_opens": accounted_opens,
        "open_close_delta": open_close_delta,
        "active_trades": active_trades,
    }


def _configure_experiment_env(args: argparse.Namespace) -> dict[str, str]:
    applied: dict[str, str] = {}

    def _set_env(key: str, value: Any) -> None:
        os.environ[key] = str(value)
        applied[key] = str(value)

    if args.disable_reasoning_bank:
        _set_env("FENIX_DISABLE_REASONING_BANK", "1")
        _set_env("FENIX_ENABLE_REASONING_BANK", "0")
    if args.disable_risk_manager:
        _set_env("FENIX_DISABLE_RISK_MANAGER", "1")
    if args.disable_judge:
        _set_env("FENIX_ENABLE_JUDGE", "0")
    if args.monolithic_mode:
        _set_env("FENIX_MONOLITHIC_MODE", "1")
    if args.lite_pipeline:
        _set_env("FENIX_LITE_PIPELINE", "1")

    if args.balance_fallback_usdt is not None:
        _set_env("FENIX_BALANCE_FALLBACK_USDT", args.balance_fallback_usdt)
    if args.max_risk_per_trade is not None:
        _set_env("FENIX_MAX_RISK_PER_TRADE", args.max_risk_per_trade)
    if args.fast_loop_sec is not None:
        _set_env("FENIX_FAST_LOOP_SEC", args.fast_loop_sec)

    if args.min_klines_to_start is not None:
        _set_env("FENIX_MIN_KLINES_TO_START", args.min_klines_to_start)

    if args.analyze_on_start:
        _set_env("FENIX_ANALYZE_ON_START", "1")
        _set_env("FENIX_ANALYZE_ON_START_DELAY_SEC", args.analyze_on_start_delay_sec)
    else:
        _set_env("FENIX_ANALYZE_ON_START", "0")

    lite_consensus_mode = getattr(args, "lite_consensus_mode", None)
    if lite_consensus_mode:
        _set_env("FENIX_LITE_CONSENSUS_MODE", lite_consensus_mode)
    lite_node_timeout_sec = getattr(args, "lite_node_timeout_sec", None)
    if lite_node_timeout_sec is not None:
        _set_env("FENIX_LITE_NODE_TIMEOUT_SEC", lite_node_timeout_sec)
    strict_mtf_bias_timeframe = getattr(args, "strict_mtf_bias_timeframe", None)
    if strict_mtf_bias_timeframe:
        _set_env("FENIX_STRICT_MTF_BIAS_TIMEFRAME", strict_mtf_bias_timeframe)
    strict_mtf_opposing_veto_conf = getattr(args, "strict_mtf_opposing_veto_conf", None)
    if strict_mtf_opposing_veto_conf is not None:
        _set_env("FENIX_STRICT_MTF_OPPOSING_VETO_CONF", strict_mtf_opposing_veto_conf)
    strict_mtf_bias_cache_sec = getattr(args, "strict_mtf_bias_cache_sec", None)
    if strict_mtf_bias_cache_sec is not None:
        _set_env("FENIX_STRICT_MTF_BIAS_CACHE_SEC", strict_mtf_bias_cache_sec)
    lite_mtf_confirm_conf = getattr(args, "lite_mtf_confirm_conf", None)
    if lite_mtf_confirm_conf is not None:
        _set_env("FENIX_LITE_MTF_CONFIRM_CONF", lite_mtf_confirm_conf)
    lite_mtf_qabba_min_conf = getattr(args, "lite_mtf_qabba_min_conf", None)
    if lite_mtf_qabba_min_conf is not None:
        _set_env("FENIX_LITE_MTF_QABBA_MIN_CONF", lite_mtf_qabba_min_conf)
    if getattr(args, "lite_allow_mtf_qabba_when_tech_hold", False):
        _set_env("FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD", "1")

    return applied


def _configure_short_timeframe_defaults(args: argparse.Namespace) -> dict[str, str]:
    """Keep short timeframe live slots responsive instead of waiting on long LLM timeouts."""
    timeframe = str(args.timeframe or "").strip().lower()
    if timeframe not in {"1m", "3m", "5m", "15m"}:
        return {}

    applied: dict[str, str] = {}
    for key, value in SHORT_TF_AGENT_TIMEOUT_DEFAULTS.items():
        if not os.getenv(key):
            os.environ[key] = value
            applied[key] = value

    if not os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT"):
        os.environ["FENIX_AGENT_CACHE_ON_TIMEOUT"] = "1"
        applied["FENIX_AGENT_CACHE_ON_TIMEOUT"] = "1"

    if not os.getenv("FENIX_AGENT_CACHE_TTL_SHORT_SEC"):
        os.environ["FENIX_AGENT_CACHE_TTL_SHORT_SEC"] = "180"
        applied["FENIX_AGENT_CACHE_TTL_SHORT_SEC"] = "180"

    return applied


def _resolve_requested_exchange_leverage() -> int:
    raw = os.getenv("FENIX_LEVERAGE", "1").strip()
    try:
        leverage = int(float(raw))
    except Exception:
        return 1
    return max(1, leverage)


async def _apply_requested_exchange_leverage(
    args: argparse.Namespace,
    mode_cfg: EngineModeConfig,
) -> dict[str, Any] | None:
    """Align Binance Futures leverage with the runner's configured leverage."""
    if mode_cfg.paper_trading:
        return None

    leverage = _resolve_requested_exchange_leverage()
    if leverage <= 1:
        return None

    active_key_name, active_secret_name = _active_binance_env_names(mode_cfg)
    api_key = os.getenv(active_key_name, "")
    api_secret = os.getenv(active_secret_name, "")
    if not api_key or not api_secret:
        print(
            f"exchange_leverage: skipped for {args.symbol} "
            f"(missing {active_key_name} / {active_secret_name})"
        )
        return None

    def _call() -> dict[str, Any]:
        exchange = create_exchange_api(api_key, api_secret, mode_cfg.use_testnet)
        return exchange.futures_change_leverage(args.symbol, leverage)

    try:
        result = await asyncio.to_thread(_call)
        print(f"exchange_leverage: set {args.symbol} to {leverage}x -> {result}")
        return result
    except Exception as exc:
        print(f"exchange_leverage: failed to set {args.symbol} to {leverage}x -> {exc}")
        return None


def _build_llm_config(args: argparse.Namespace) -> LLMProvidersConfig | None:
    base_model = args.base_model.strip() if args.base_model else None
    base_vision_model = args.base_vision_model.strip() if args.base_vision_model else None
    team_models_raw = args.team_models.strip() if args.team_models else None
    team_provider = getattr(args, "team_provider", "ollama_cloud")
    risk_provider = getattr(args, "risk_provider", None)

    if team_models_raw:
        team_map = parse_team_models(team_models_raw)
        return build_llm_config_for_team(
            team_map,
            default_model=base_model,
            vision_model=base_vision_model,
            timeout=args.model_timeout_sec,
            provider_type=team_provider,
            risk_provider_type=risk_provider,
        )

    if base_model:
        return build_llm_config_for_model(
            base_model,
            vision_model=base_vision_model,
            timeout=args.model_timeout_sec,
            provider_type=team_provider,
            risk_provider_type=risk_provider,
        )

    return None


async def run_slot(args: argparse.Namespace) -> dict[str, Any]:
    slot_start = datetime.now(timezone.utc)
    mode_cfg = resolve_engine_mode(args.mode, args.allow_live, args.use_testnet_data)

    await _apply_requested_exchange_leverage(args, mode_cfg)
    llm_config = _build_llm_config(args)
    metadata = {
        "run_tag": args.run_tag,
        "slot_name": args.slot_name,
        "slot_index": args.slot_index,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "mode": args.mode,
        "experiment": args.experiment,
        "experiment_id": args.experiment_id,
    }
    collector = LiveEventCollector(
        args.event_log_path,
        metadata,
        append=args.append_event_log,
    )

    engine = TradingEngine(
        symbol=args.symbol,
        timeframe=args.timeframe,
        use_testnet=mode_cfg.use_testnet,
        paper_trading=mode_cfg.paper_trading,
        enable_visual_agent=not args.no_visual,
        enable_sentiment_agent=not args.no_sentiment,
        allow_live_trading=mode_cfg.allow_live_trading,
        llm_config=llm_config,
    )
    engine.on_agent_event = collector.on_event

    status = "completed"
    error: str | None = None
    engine_task = asyncio.create_task(engine.start())

    run_seconds = int(max(0, args.run_minutes) * 60)

    try:
        if run_seconds > 0:
            try:
                await asyncio.wait_for(asyncio.shield(engine_task), timeout=run_seconds)
                status = "engine_stopped_early"
            except asyncio.TimeoutError:
                status = "completed"
        else:
            await asyncio.shield(engine_task)
            status = "engine_stopped_early"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if not engine_task.done():
            try:
                await asyncio.wait_for(
                    engine.stop(), timeout=max(5.0, float(args.shutdown_timeout_sec))
                )
            except asyncio.TimeoutError:
                if status in {"completed", "engine_stopped_early"}:
                    status = "completed_with_shutdown_timeout"
                if error is None:
                    error = (
                        f"engine.stop timed out after {args.shutdown_timeout_sec:.1f}s; "
                        "forcing task cancellation"
                    )
            except Exception as exc:
                if status in {"completed", "engine_stopped_early"}:
                    status = "completed_with_shutdown_error"
                if error is None:
                    error = f"engine.stop failed: {exc}"
            engine_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(engine_task, return_exceptions=True),
                timeout=max(5.0, float(args.shutdown_timeout_sec)),
            )
        except asyncio.TimeoutError:
            if status in {"completed", "engine_stopped_early", "completed_with_shutdown_timeout"}:
                status = "completed_with_shutdown_timeout"
            if error is None:
                error = (
                    f"engine task join timed out after {args.shutdown_timeout_sec:.1f}s "
                    "after cancellation"
                )

    slot_end = datetime.now(timezone.utc)
    duration_sec = (slot_end - slot_start).total_seconds()

    status_payload = {}
    risk_payload = {}
    try:
        status_payload = engine.get_status()
    except Exception:
        status_payload = {}
    try:
        risk_payload = engine.get_risk_status() or {}
    except Exception:
        risk_payload = {}

    try:
        active_trades = int(risk_payload.get("active_trades") or 0)
    except Exception:
        active_trades = 0
    if not mode_cfg.paper_trading and active_trades > 0 and status.startswith("completed"):
        status = "completed_with_open_position"
        if error is None:
            error = (
                f"slot ended with {active_trades} active trade(s); "
                "cleanup_on_stop may be disabled or failed"
            )

    events_payload = collector.as_dict()
    accounting_report = _build_event_accounting_report(
        events_payload,
        mode_cfg,
        active_trades=active_trades,
    )
    if (
        not mode_cfg.paper_trading
        and accounting_report.get("status") == "gap"
        and status.startswith("completed")
        and status != "completed_with_open_position"
    ):
        status = "completed_with_accounting_gap"
        if error is None:
            error = "slot event accounting gap: " + ",".join(
                accounting_report.get("warnings") or ["unknown"]
            )

    summary = {
        "event": "slot_summary",
        "status": status,
        "error": error,
        "slot_name": args.slot_name,
        "slot_index": args.slot_index,
        "run_tag": args.run_tag,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "mode": args.mode,
        "run_minutes": args.run_minutes,
        "duration_sec": round(duration_sec, 2),
        "experiment": args.experiment,
        "experiment_id": args.experiment_id,
        "disable_reasoning_bank": args.disable_reasoning_bank,
        "disable_risk_manager": args.disable_risk_manager,
        "disable_judge": args.disable_judge,
        "monolithic_mode": args.monolithic_mode,
        "lite_pipeline": args.lite_pipeline,
        "events": events_payload,
        "accounting": accounting_report,
        "engine_status": status_payload,
        "risk_status": risk_payload,
        "event_log_path": str(args.event_log_path),
        "summary_path": str(args.summary_path),
        "started_at": slot_start.isoformat(),
        "ended_at": slot_end.isoformat(),
    }

    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a single full-engine Fenix test slot (paper/testnet/live)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--run-minutes", type=int, default=60)
    parser.add_argument("--mode", choices=["paper", "testnet", "live"], default="testnet")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Required safety switch when --mode live",
    )
    parser.add_argument(
        "--use-testnet-data",
        action="store_true",
        help="When in paper mode, use Binance testnet market data.",
    )
    parser.add_argument("--disable-trading", action="store_true", help="Do analysis-only slot.")
    parser.add_argument("--no-visual", action="store_true", help="Disable visual agent.")
    parser.add_argument("--no-sentiment", action="store_true", help="Disable sentiment agent.")

    parser.add_argument("--base-model", default=None)
    parser.add_argument("--base-vision-model", default=None)
    parser.add_argument(
        "--team-provider",
        choices=SUPPORTED_PROVIDER_CHOICES,
        default="ollama_cloud",
        help="Provider for non-risk agents.",
    )
    parser.add_argument(
        "--risk-provider",
        choices=SUPPORTED_PROVIDER_CHOICES,
        default=None,
        help="Optional provider override for risk_manager only.",
    )
    parser.add_argument(
        "--team-models",
        default=None,
        help=(
            "Per-agent team map "
            "(technical=...,qabba=...,decision=...,sentiment=...,visual=...,risk_manager=...)"
        ),
    )
    parser.add_argument("--model-timeout-sec", type=int, default=120)

    parser.add_argument("--disable-reasoning-bank", action="store_true")
    parser.add_argument("--disable-risk-manager", action="store_true")
    parser.add_argument("--disable-judge", action="store_true")
    parser.add_argument("--monolithic-mode", action="store_true")
    parser.add_argument("--lite-pipeline", action="store_true")

    parser.add_argument("--max-risk-per-trade", type=float, default=None)
    parser.add_argument("--balance-fallback-usdt", type=float, default=None)
    parser.add_argument("--min-klines-to-start", type=int, default=5)
    parser.add_argument("--fast-loop-sec", type=float, default=0.0)
    parser.add_argument("--analyze-on-start", action="store_true", default=True)
    parser.add_argument("--no-analyze-on-start", action="store_false", dest="analyze_on_start")
    parser.add_argument("--analyze-on-start-delay-sec", type=float, default=2.0)
    parser.add_argument("--shutdown-timeout-sec", type=float, default=25.0)
    parser.add_argument("--lite-consensus-mode", default=None)
    parser.add_argument("--lite-node-timeout-sec", type=float, default=None)
    parser.add_argument("--strict-mtf-bias-timeframe", default=None)
    parser.add_argument("--strict-mtf-opposing-veto-conf", type=float, default=None)
    parser.add_argument("--strict-mtf-bias-cache-sec", type=float, default=None)
    parser.add_argument("--lite-mtf-confirm-conf", type=float, default=None)
    parser.add_argument("--lite-mtf-qabba-min-conf", type=float, default=None)
    parser.add_argument("--lite-allow-mtf-qabba-when-tech-hold", action="store_true")

    parser.add_argument("--api-key-index", type=int, choices=[1, 2], default=1)
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--slot-name", default=None)
    parser.add_argument("--slot-index", type=int, default=None)
    parser.add_argument("--experiment", default=None)
    parser.add_argument("--experiment-id", type=int, default=None)
    parser.add_argument("--summary-path", default=None)
    parser.add_argument("--event-log-path", default=None)
    parser.add_argument(
        "--append-event-log",
        action="store_true",
        help="Append to an existing event log instead of truncating it at slot start.",
    )
    return parser.parse_args()


def _resolve_output_paths(args: argparse.Namespace) -> tuple[str, Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_tag = args.run_tag or timestamp
    slot_name = args.slot_name or "slot"
    slot_suffix = _slug(f"{slot_name}_{args.symbol}_{args.timeframe}_{run_tag}")

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    summary_path = (
        Path(args.summary_path)
        if args.summary_path
        else logs_dir / f"live_slot_summary_{slot_suffix}.json"
    )
    event_log_path = (
        Path(args.event_log_path)
        if args.event_log_path
        else logs_dir / f"live_slot_events_{slot_suffix}.jsonl"
    )
    return run_tag, summary_path, event_log_path


def configure_slot_runtime_env(
    args: argparse.Namespace,
    mode_cfg: EngineModeConfig,
) -> dict[str, str]:
    """Apply safe runtime defaults for finite live/testnet slots."""
    applied: dict[str, str] = {}
    finite_slot = float(args.run_minutes or 0) > 0

    if finite_slot and "FENIX_RISK_MANAGER_STORAGE_PATH" not in os.environ:
        storage_mode = None
        if mode_cfg.paper_trading:
            storage_mode = "paper"
        elif mode_cfg.use_testnet:
            storage_mode = "testnet"

        if storage_mode:
            slot_name = getattr(args, "slot_name", None) or f"{args.symbol}_{args.timeframe}"
            run_tag = getattr(args, "run_tag", None) or "slot"
            risk_storage_path = (
                PROJECT_ROOT
                / "logs"
                / f"risk_manager_{storage_mode}_{_slug(f'{slot_name}_{args.symbol}_{args.timeframe}_{run_tag}')}.jsonl"
            )
            os.environ["FENIX_RISK_MANAGER_STORAGE_PATH"] = str(risk_storage_path)
            applied["FENIX_RISK_MANAGER_STORAGE_PATH"] = str(risk_storage_path)

    cleanup_env = os.getenv("FENIX_CLEANUP_ON_STOP")
    if (
        cleanup_env is None
        and not mode_cfg.paper_trading
        and not args.disable_trading
        and finite_slot
    ):
        os.environ["FENIX_CLEANUP_ON_STOP"] = "1"
        applied["FENIX_CLEANUP_ON_STOP"] = "1"
    return applied


def main() -> None:
    _load_dotenv_file(PROJECT_ROOT)
    args = parse_args()

    run_tag, summary_path, event_log_path = _resolve_output_paths(args)
    args.run_tag = run_tag
    args.summary_path = summary_path
    args.event_log_path = event_log_path

    if args.slot_name is None:
        args.slot_name = f"{args.symbol}_{args.timeframe}"

    selected = apply_api_key_index(os.environ, args.api_key_index)
    mode_cfg = resolve_engine_mode(args.mode, args.allow_live, args.use_testnet_data)
    experiment_env_overrides = _configure_experiment_env(args)
    timeout_overrides = _configure_short_timeframe_defaults(args)
    runtime_env_overrides = configure_slot_runtime_env(args, mode_cfg)

    # Keep cloud URL explicit for reproducible runs.
    os.environ.setdefault("OLLAMA_CLOUD_URL", DEFAULT_OLLAMA_CLOUD_URL)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("LLM_PROFILE", "OLLAMA_CLOUD")

    print("=" * 72)
    print("Fenix Live Slot Runner")
    print("=" * 72)
    print(f"run_tag      : {args.run_tag}")
    print(f"slot         : {args.slot_name} (index={args.slot_index})")
    print(f"symbol/tf    : {args.symbol} @ {args.timeframe}")
    print(f"mode         : {args.mode}")
    print(f"run_minutes  : {args.run_minutes}")
    print(f"api_key_idx  : {args.api_key_index}")
    print(f"team_provider: {args.team_provider}")
    print(f"risk_provider: {args.risk_provider or args.team_provider}")
    active_key_name, active_secret_name = _active_binance_env_names(mode_cfg)

    print(f"binance_mode : {'testnet' if mode_cfg.use_testnet else 'mainnet'}")
    print(f"{active_key_name:<13}: {_mask_secret(os.getenv(active_key_name))}")
    print(f"{active_secret_name:<13}: {_mask_secret(os.getenv(active_secret_name))}")

    selected_non_binance = {
        key: value for key, value in selected.items() if not key.startswith("BINANCE_")
    }
    if selected_non_binance:
        print("selected_keys:")
        for key in sorted(selected_non_binance):
            print(f"  - {key}: {selected_non_binance[key]}")
    if experiment_env_overrides:
        print("experiment_overrides:")
        for key in sorted(experiment_env_overrides):
            print(f"  - {key}: {experiment_env_overrides[key]}")
    if runtime_env_overrides:
        print("runtime_overrides:")
        for key in sorted(runtime_env_overrides):
            print(f"  - {key}: {runtime_env_overrides[key]}")
    if timeout_overrides:
        print("timeout_overrides:")
        for key in sorted(timeout_overrides):
            print(f"  - {key}: {timeout_overrides[key]}")
    print(f"event_log    : {args.event_log_path}")
    print(f"summary_json : {args.summary_path}")
    print("=" * 72)

    try:
        with live_slot_symbol_lock(args, mode_cfg) as lock_path:
            if lock_path is not None:
                print(f"symbol_lock  : {lock_path}")
                print("=" * 72)
            summary = asyncio.run(run_slot(args))
    except KeyboardInterrupt:
        interrupted = {
            "event": "slot_summary",
            "status": "interrupted",
            "run_tag": args.run_tag,
            "slot_name": args.slot_name,
            "slot_index": args.slot_index,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
        }
        args.summary_path.write_text(json.dumps(interrupted, indent=2))
        print("Interrupted by user.")
        raise SystemExit(130)

    print(
        "Slot finished: status=%s decisions=%s risk_blocked=%s judge_blocked=%s"
        % (
            summary.get("status"),
            summary.get("events", {}).get("decision_counts"),
            summary.get("events", {}).get("risk_blocked"),
            summary.get("events", {}).get("judge_blocked"),
        )
    )

    if summary.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
