#!/usr/bin/env python3
# run_fenix.py
"""
Main execution script for Fenix Trading Bot.

Usage:
    python run_fenix.py                    # Paper trading with Ollama
    python run_fenix.py --mode live        # Live trading
    python run_fenix.py --symbol ETHUSDT   # Different pair
    python run_fenix.py --help             # See options
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Fenix production hosts are POSIX.
    fcntl = None

# Load .env early so JWT_SECRET, API keys, etc. are visible to every module.
if os.getenv("FENIX_SKIP_DOTENV", "").strip().lower() not in {"1", "true", "yes", "on"}:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

# Create logs directory if it doesn't exist
Path("logs").mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/fenix_{datetime.now():%Y%m%d_%H%M%S}.log"),
    ],
)
logger = logging.getLogger("Fenix")


class InstanceLock:
    """Advisory process lock that prevents duplicate live engines per symbol."""

    def __init__(self, symbol: str):
        lock_dir = Path(os.getenv("FENIX_INSTANCE_LOCK_DIR", "logs/runtime_locks"))
        lock_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch for ch in symbol.lower() if ch.isalnum()) or "unknown"
        self.path = lock_dir / f"fenix_{slug}.lock"
        self._handle = None

    def acquire(self) -> None:
        if fcntl is None:
            raise RuntimeError("Fenix instance locking requires a POSIX host")
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown owner"
            handle.close()
            raise RuntimeError(
                f"Another Fenix process already owns {self.path} ({owner})"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started={datetime.now(timezone.utc).isoformat()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _api_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("API port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("API port must be between 1 and 65535")
    return port


def _start_log_retention_thread() -> None:
    """Run a recursive log-retention pass now and then once per day.

    FENIX_LOG_RETENTION_DAYS controls the age cutoff (default 30; <=0
    disables). Deletion races between concurrent Fenix processes are benign
    (unlink uses missing_ok) and the audit/lock directories are protected
    inside the cleanup utility itself.
    """
    import threading
    import time as time_module

    try:
        days = float(os.getenv("FENIX_LOG_RETENTION_DAYS", "30") or 30)
    except ValueError:
        days = 30.0
    if days <= 0:
        logger.info("Log retention disabled (FENIX_LOG_RETENTION_DAYS<=0)")
        return

    def _loop() -> None:
        from src.utils.log_cleanup import run_retention_pass

        while True:
            try:
                stats = run_retention_pass(days_old=days)
                logger.info(
                    "Log retention (%.0fd): %d deleted, %d kept, %.1f MB freed",
                    days,
                    stats.get("deleted", 0),
                    stats.get("kept", 0),
                    stats.get("bytes_freed", 0) / (1024 * 1024),
                )
            except Exception:
                logger.warning("Log retention pass failed", exc_info=True)
            time_module.sleep(24 * 3600)

    threading.Thread(target=_loop, name="fenix-log-retention", daemon=True).start()


def _find_nanofenix_companion_pids(symbol: str) -> list[int]:
    """Return running NanoFenix companion PIDs for a symbol."""
    normalized_symbol = symbol.upper()
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    pids: list[int] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if pid == os.getpid():
            continue
        if "run_nanofenixv3.py" not in command or "--companion" not in command:
            continue
        has_symbol = (
            f"--symbol {normalized_symbol}" in command
            or f"--symbol={normalized_symbol}" in command
        )
        if has_symbol:
            pids.append(pid)
    return pids


def _load_cli_defaults() -> dict:
    """Load CLI defaults from config/fenix.yaml (CLI flags always win).

    Falls back to built-in defaults when the YAML is missing or invalid so
    run_fenix.py keeps working in minimal environments.
    """
    defaults = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "interval": 60,
        "model": "qwen2.5:7b",
        "max_risk": 2.0,
    }
    try:
        from config.settings import get_config

        cfg = get_config()
        defaults.update(
            symbol=cfg.trading.symbol,
            timeframe=cfg.trading.timeframe,
            interval=cfg.trading.analysis_interval_seconds,
            model=cfg.llm.default_model,
            max_risk=cfg.trading.max_risk_per_trade * 100.0,
        )
    except Exception as e:  # pragma: no cover - defensive fallback
        logger.warning("Could not load config/fenix.yaml (%s); using built-in defaults", e)
    return defaults


def parse_args():
    """Parses command line arguments.

    Defaults come from config/fenix.yaml when available; explicit CLI flags
    always take precedence.
    """
    cfg_defaults = _load_cli_defaults()
    parser = argparse.ArgumentParser(
        description="Fenix AI Trading Bot - LangGraph Multi-Agent System (v2.5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_fenix.py                                   # Paper trading, BTCUSDT, 15m
  python run_fenix.py --mode live --allow-live          # Live trading
  python run_fenix.py --symbol ETHUSDT                  # Different pair
  python run_fenix.py --timeframe 5m                    # Different timeframe
  python run_fenix.py --mode paper --mainnet-data       # Public Mainnet data, simulated orders
  python run_fenix.py --no-visual                       # Without visual agent
  python run_fenix.py --with-nanofenix-companion        # Run NanoFenix v3.5 alongside
  python run_fenix.py --team-models technical=qwen2.5:7b,qabba=qwen2.5:7b
        """,
    )

    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Required for live mode execution to prevent accidental trades",
    )
    data_venue = parser.add_mutually_exclusive_group()
    data_venue.add_argument(
        "--testnet",
        action="store_true",
        help="Use Binance Futures Testnet market data (paper mode default)",
    )
    data_venue.add_argument(
        "--mainnet-data",
        action="store_true",
        help=(
            "Use public Binance Futures Mainnet market data while keeping "
            "execution simulated; valid only with --mode paper"
        ),
    )
    parser.add_argument(
        "--symbol",
        default=cfg_defaults["symbol"],
        help=f"Trading pair (default: {cfg_defaults['symbol']}, from config/fenix.yaml)",
    )
    parser.add_argument(
        "--timeframe",
        default=cfg_defaults["timeframe"],
        help=f"Analysis timeframe (default: {cfg_defaults['timeframe']}, from config/fenix.yaml)",
    )
    parser.add_argument(
        "--model",
        default=cfg_defaults["model"],
        help=(
            "Ollama model to use when --team-models is not provided "
            f"(default: {cfg_defaults['model']}, from config/fenix.yaml)"
        ),
    )
    parser.add_argument(
        "--team-models",
        default=None,
        help=(
            "Per-agent model assignment, e.g. "
            "'technical=ministral-3:14b-cloud,qabba=ministral-3:14b-cloud,"
            "decision=nemotron-3-nano:30b-cloud,risk_manager=devstral-small-2:24b-cloud'. "
            "Forwarded via FENIX_TEAM_MODELS to the engine. v2.5 model-role pattern."
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=cfg_defaults["interval"],
        help=f"Interval between analysis in seconds (default: {cfg_defaults['interval']}, from config/fenix.yaml)",
    )
    parser.add_argument(
        "--trade-flow-window-sec",
        type=float,
        default=None,
        help=(
            "Recent aggressive-trade window supplied to QABBA, in seconds "
            "(1-60; defaults to FENIX_TRADE_IMBALANCE_WINDOW_SEC or 5)"
        ),
    )
    parser.add_argument(
        "--no-visual",
        action="store_true",
        help="Disable visual agent",
    )
    parser.add_argument(
        "--no-sentiment",
        action="store_true",
        help="Disable sentiment agent",
    )
    parser.add_argument(
        "--max-risk",
        type=float,
        default=cfg_defaults["max_risk"],
        help=f"Max risk per trade in %% (default: {cfg_defaults['max_risk']:.1f}, from config/fenix.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate only, do not execute orders",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Start API server (FastAPI + Socket.IO) for frontend",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind API server (default: 127.0.0.1, not exposed publicly)",
    )
    parser.add_argument(
        "--port",
        type=_api_port,
        default=os.getenv("FENIX_API_PORT", "8000"),
        help="API port (default: FENIX_API_PORT or 8000)",
    )

    # ---- v2.5 NanoFenix companion ------------------------------------
    parser.add_argument(
        "--with-nanofenix-companion",
        action="store_true",
        help=(
            "Launch NanoFenix v3.5 as a companion subprocess and consume its "
            "signal in the engine. Sets FENIX_ENABLE_NANOFENIX_COMPANION=1 "
            "and points the engine at the written signal file."
        ),
    )
    parser.add_argument(
        "--nanofenix-observer-only",
        action="store_true",
        default=True,
        help=(
            "Run NanoFenix in observer-only mode (default). The companion "
            "publishes its signal but never opens its own paper trades. "
            "Use --nanofenix-active-paper to disable."
        ),
    )
    parser.add_argument(
        "--nanofenix-active-paper",
        action="store_true",
        help="Disable observer-only mode and let NanoFenix open its own paper trades.",
    )
    parser.add_argument(
        "--nanofenix-hard-veto-reasons",
        default="direction_mismatch,no_directional_signal,high_uncertainty,stale_signal,symbol_mismatch,run_id_mismatch,signal_file_missing,signal_file_empty,signal_parse_error,missing_or_invalid_timestamp",
        help=(
            "Comma-separated NanoFenix veto reasons that hard-block a Fenix entry. "
            "Soft reasons (e.g. low_actionable_edge, companion_not_ready) are observed "
            "but not enforced. Forwarded via FENIX_NANOFENIX_HARD_VETO_REASONS."
        ),
    )

    return parser.parse_args()


def _market_data_uses_testnet(args: argparse.Namespace) -> bool:
    """Resolve the data venue without weakening the paper/live boundary."""
    if args.mainnet_data and (args.mode != "paper" or args.allow_live):
        raise ValueError(
            "--mainnet-data requires --mode paper and forbids --allow-live"
        )
    return bool(args.testnet or (args.mode == "paper" and not args.mainnet_data))


def _start_nanofenix_companion(symbol: str, observer_only: bool) -> tuple[subprocess.Popen | None, Path | None]:
    """Spawn the NanoFenix v3.5 companion subprocess.

    Returns (process, signal_path). On any failure, returns (None, None) and
    logs a warning — the engine can still run without the companion.
    """
    repo_root = Path(__file__).resolve().parent
    nano_launcher = repo_root / "run_nanofenixv3.py"
    if not nano_launcher.exists():
        logger.warning("--with-nanofenix-companion set but %s not found; skipping", nano_launcher)
        return None, None

    Path("logs").mkdir(exist_ok=True)
    signal_path = Path("logs") / f"nanofenixv3_companion_{symbol.lower()}.json"
    if _env_bool("FENIX_NANOFENIX_COMPANION_SINGLETON", True):
        existing_pids = _find_nanofenix_companion_pids(symbol)
        if existing_pids:
            logger.error(
                "Refusing to launch NanoFenix companion for %s; existing companion PID(s): %s",
                symbol,
                existing_pids,
            )
            return None, None

    env = os.environ.copy()
    if observer_only:
        env["NANOFENIXV3_COMPANION_OBSERVER_ONLY"] = "1"
    env["NANOFENIX_SIGNAL_STATE_PATH"] = str(signal_path)
    run_id = f"{symbol.lower()}-{os.getpid()}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    env["NANOFENIXV3_RUN_ID"] = run_id

    cmd = [
        sys.executable,
        str(nano_launcher),
        "--symbol", symbol,
        "--companion",
        "--adaptive-fusion",
        "--output-path", str(signal_path),
    ]

    # Warm start: explicit model via env, else pretrained for this symbol,
    # else the closest market (same base asset, e.g. ETHUSDT for ETHUSDC) so
    # online learning starts from a useful prior instead of from scratch.
    nano_dir = repo_root / "nanofenixv3"
    model_path = os.getenv("NANOFENIX_PRETRAINED_MODEL", "").strip() or None
    if not model_path:
        exact = nano_dir / f"pretrained_{symbol.lower()}.pkl"
        if exact.exists():
            model_path = str(exact)
        else:
            base = symbol.upper().replace("USDC", "USDT")
            sibling = nano_dir / f"pretrained_{base.lower()}.pkl"
            if sibling.exists():
                model_path = str(sibling)
                logger.info("NanoFenix warm-start from sibling market model: %s", sibling.name)
    if model_path:
        cmd.extend(["--model", model_path])

    # Persist live training so each session continues learning from the last.
    runtime_path = nano_dir / f"runtime_{symbol.lower()}_live.pkl"
    runtime_model_path = nano_dir / f"runtime_{symbol.lower()}_live_model.pkl"
    env["NANOFENIXV3_RUNTIME_STATE_PATH"] = str(runtime_path)
    env["NANOFENIXV3_RUNTIME_MODEL_PATH"] = str(runtime_model_path)
    cmd.extend(["--runtime-state-path", str(runtime_path)])

    logger.info("Launching NanoFenix companion: %s", " ".join(cmd))
    companion_log_path = Path("logs") / f"nanofenixv3_companion_{symbol.lower()}.log"
    companion_log = None
    try:
        os.environ["FENIX_NANOFENIX_EXPECTED_RUN_ID"] = run_id
        companion_log = companion_log_path.open("a", buffering=1, encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=companion_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except (OSError, ValueError) as e:
        logger.warning("Could not launch NanoFenix companion: %s", e)
        os.environ.pop("FENIX_NANOFENIX_EXPECTED_RUN_ID", None)
        return None, None
    finally:
        if companion_log is not None:
            companion_log.close()
    pid_path = Path("logs") / f"nanofenixv3_companion_{symbol.lower()}.pid"
    try:
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    except OSError as e:
        logger.debug("Could not write NanoFenix companion pidfile %s: %s", pid_path, e)
    logger.info("NanoFenix companion PID=%s, signal=%s", proc.pid, signal_path)
    return proc, signal_path


def _stop_nanofenix_companion(proc: subprocess.Popen | None) -> None:
    """Stop the NanoFenix companion subprocess gracefully."""
    if proc is None or proc.poll() is not None:
        return
    logger.info("Stopping NanoFenix companion PID=%s...", proc.pid)
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("NanoFenix companion did not stop in 10s, killing")
            proc.kill()
            proc.wait(timeout=5)
    except (OSError, ValueError) as e:
        logger.warning("Error stopping NanoFenix companion: %s", e)


def _apply_per_instance_isolation(symbol: str) -> None:
    """Derive per-instance isolation env vars from ``symbol`` when unset.

    Sets, only if not already present in the environment:
    - FENIX_BALANCE_ASSETS: the quote stablecoin (USDC/USDT) so each bot sizes
      from its own capital bucket instead of the combined account balance.
    - FENIX_RISK_MANAGER_STORAGE_PATH: per-symbol risk state file so loss-streak
      / peak / drawdown tracking is not corrupted by the other instance.
    - FENIX_REASONING_BANK_DIR / FENIX_LLM_RESPONSE_LOG_DIR: per-symbol dirs so
      reasoning banks and raw LLM dumps do not interleave between instances.

    Explicit user configuration always wins — we never overwrite an existing var.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return
    slug = sym.lower()

    quote = None
    for asset in ("USDC", "USDT", "FDUSD", "BUSD"):
        if sym.endswith(asset):
            quote = asset
            break

    defaults = {
        "FENIX_RISK_MANAGER_STORAGE_PATH": f"logs/risk_manager_{slug}.jsonl",
        "FENIX_REASONING_BANK_DIR": f"logs/reasoning_bank_{slug}",
        "FENIX_LLM_RESPONSE_LOG_DIR": f"logs/llm_responses_{slug}",
    }
    if quote:
        defaults["FENIX_BALANCE_ASSETS"] = quote

    for key, value in defaults.items():
        if not os.getenv(key):
            os.environ[key] = value
            logger.info("  Per-instance isolation: %s=%s", key, value)


async def main():
    """Main function."""
    args = parse_args()

    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║   🦅  FENIX AI TRADING BOT                                   ║
    ║   LangGraph Multi-Agent Architecture                         ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)

    logger.info("Starting Fenix Trading Bot (v2.5)")
    logger.info(f"  Mode: {args.mode.upper()}")
    logger.info(f"  Symbol: {args.symbol}")
    logger.info(f"  Timeframe: {args.timeframe}")
    if args.team_models:
        logger.info(f"  Team models: {args.team_models}")
    else:
        logger.info(f"  Model: {args.model}")
    logger.info(f"  Interval: {args.interval}s")
    logger.info(f"  Visual: {'Yes' if not args.no_visual else 'No'}")
    logger.info(f"  Sentiment: {'Yes' if not args.no_sentiment else 'No'}")
    logger.info(f"  NanoFenix companion: {'Yes' if args.with_nanofenix_companion else 'No'}")

    if args.mode == "live" and not args.allow_live:
        logger.error("Live mode requested but --allow-live not provided. Aborting for safety.")
        return 1
    try:
        use_testnet = _market_data_uses_testnet(args)
    except ValueError as exc:
        logger.error("%s. It selects public Mainnet data only; execution stays simulated.", exc)
        return 1
    if args.trade_flow_window_sec is not None and not 1 <= args.trade_flow_window_sec <= 60:
        logger.error("--trade-flow-window-sec must be between 1 and 60")
        return 1

    if not 0 < args.max_risk <= 100:
        logger.error("--max-risk must be greater than 0 and no more than 100 percent")
        return 1
    os.environ["FENIX_MAX_RISK_PER_TRADE"] = str(args.max_risk / 100.0)

    # Per-instance isolation (dual-bot safety). When two Fenix instances run on
    # the same account (e.g. ETHUSDC + SOLUSDT), they must NOT share balance
    # accounting or risk/log state, otherwise both size against the same capital
    # and corrupt each other's peak/drawdown tracking (root cause of the
    # 2026-07-05 double-exposure). We derive sane per-symbol defaults from the
    # quote asset without overriding anything the user set explicitly.
    _apply_per_instance_isolation(args.symbol)

    instance_lock = None
    if not args.api:
        instance_lock = InstanceLock(args.symbol)
        try:
            instance_lock.acquire()
        except RuntimeError as exc:
            logger.error("Refusing to start duplicate engine: %s", exc)
            return 1
        atexit.register(instance_lock.release)

    # Retention was configured but never scheduled anywhere, so logs/ grew to
    # ~1GB / 47k files by 2026-07-10 (mostly llm_responses* agent dumps that
    # the old top-level-only cleanup pattern never matched).
    _start_log_retention_thread()

    # v2.5: forward model-role assignment to the engine. The LLMFactory
    # honours FENIX_ROTATE_MODELS_<AGENT> with a single-model "rotation"
    # which is equivalent to an override. We parse the comma-separated
    # assignment list and export one env var per agent so the existing
    # factory picks them up without any code change.
    if args.team_models:
        os.environ["FENIX_TEAM_MODELS"] = args.team_models  # informational
        valid_agents = {"technical", "qabba", "visual", "sentiment", "decision", "risk_manager"}
        for pair in args.team_models.split(","):
            if "=" not in pair:
                continue
            agent, model = pair.split("=", 1)
            agent = agent.strip().lower()
            model = model.strip()
            if not agent or not model or agent not in valid_agents:
                logger.warning(f"  Ignoring unknown --team-models entry: {pair!r}")
                continue
            env_var = f"FENIX_ROTATE_MODELS_{agent.upper()}"
            os.environ[env_var] = model
            logger.info(f"  Override {agent} -> {model} ({env_var})")

    # v2.5: forward NanoFenix companion configuration to the engine.
    observer_only = args.nanofenix_observer_only and not args.nanofenix_active_paper
    nanofenix_proc = None
    if args.with_nanofenix_companion:
        nanofenix_proc, signal_path = _start_nanofenix_companion(
            symbol=args.symbol, observer_only=observer_only,
        )
        if signal_path is not None:
            os.environ["FENIX_ENABLE_NANOFENIX_COMPANION"] = "1"
            os.environ["FENIX_NANOFENIX_SIGNAL_PATH"] = str(signal_path)
            os.environ["FENIX_NANOFENIX_HARD_VETO_REASONS"] = args.nanofenix_hard_veto_reasons
            logger.info(f"  Companion signal path: {signal_path}")
            logger.info(f"  Hard-veto reasons: {args.nanofenix_hard_veto_reasons}")

    # Verify Ollama
    logger.info("Verifying Ollama connection...")
    try:
        import httpx
        response = httpx.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code != 200:
            logger.error("Ollama is not available. Run: ollama serve")
            _stop_nanofenix_companion(nanofenix_proc)
            if instance_lock:
                instance_lock.release()
            return 1

        models = [m["name"] for m in response.json().get("models", [])]
        if args.model not in models and not any(args.model.split(":")[0] in m for m in models):
            logger.warning(f"Model {args.model} not found. Available: {models[:5]}")
            args.model = models[0] if models else "gemma3:1b"
            logger.info(f"Using alternative model: {args.model}")

        logger.info(f"✅ Ollama OK - Model: {args.model}")

    except Exception as e:
        logger.error(f"Error connecting to Ollama: {e}")
        _stop_nanofenix_companion(nanofenix_proc)
        if instance_lock:
            instance_lock.release()
        return 1

    # Execution mode and market-data venue are separate. Paper mode defaults
    # to Testnet data; Mainnet public data requires the explicit safe flag.
    data_venue_name = "TESTNET" if use_testnet else "MAINNET PUBLIC DATA"
    execution_name = "SIMULATED/PAPER" if args.mode == "paper" or args.dry_run else "LIVE"
    logger.info("Execution=%s | Market data=%s", execution_name, data_venue_name)
    logger.info(f"Verifying Binance connection ({data_venue_name})...")
    try:
        from src.trading.binance_client import BinanceClient

        client = BinanceClient(testnet=use_testnet)
        connected = await client.connect()

        if connected:
            price = await client.get_price(args.symbol)
            if price:
                mode_str = "TESTNET" if use_testnet else "MAINNET DATA"
                logger.info(f"✅ Binance {mode_str} OK - {args.symbol}: ${price:,.2f}")
            else:
                logger.warning(f"Could not get price for {args.symbol}")
        else:
            logger.warning("Could not connect to Binance, continuing in simulated mode")

        await client.close()

    except ImportError:
        logger.warning("Binance client not available, continuing in simulated mode")
    except Exception as e:
        logger.warning(f"Error connecting to Binance: {e}")

    # Start API server if requested
    if args.api:
        logger.info("🚀 Starting API server (Frontend Backend)...")
        import uvicorn
        # Importar app_socketio desde el nuevo módulo server
        # Nota: uvicorn necesita el import string "src.api.server:app_socketio"
        uvicorn.run("src.api.server:app_socketio", host=args.host, port=args.port, reload=False)
        return 0

    # Start standard trading engine (CLI mode)
    logger.info("Starting trading engine (CLI Mode)...")

    try:
        # CLI experiments use isolated databases and need their schema before
        # the first evaluator or simulated execution writes to them.
        from src.config.database import init_db

        await init_db()

        from src.trading.engine import TradingEngine

        engine = TradingEngine(
            symbol=args.symbol,
            timeframe=args.timeframe,
            use_testnet=use_testnet,
            paper_trading=args.mode == "paper" or args.dry_run,
            enable_visual_agent=not args.no_visual,
            enable_sentiment_agent=not args.no_sentiment,
            allow_live_trading=args.allow_live,
            trade_flow_window_sec=args.trade_flow_window_sec,
        )

        # ── Redis Bridge: emit engine events to the API server frontend ──
        # When REDIS_URL is set and the API server is running with the same
        # Redis channel, the live process forwards all agent events to the
        # connected web clients without needing a separate engine in the API.
        redis_bridge = None
        try:
            from src.api.redis_bridge import get_redis_bridge

            redis_bridge = get_redis_bridge()
            if redis_bridge:
                # Shared handler: transforms raw engine events into the exact
                # Socket.IO payloads the frontend expects AND persists agent
                # outputs to the shared SQLite DB so the dashboard's REST
                # endpoints (Agents, Reasoning Bank, history) show this live
                # session even after a page reload.
                from src.api.engine_events import create_engine_event_handler

                engine.on_agent_event = create_engine_event_handler(
                    redis_bridge.emit, persist=True
                )
                logger.info("✅ Redis bridge active — engine events forwarded to API server")
            else:
                logger.info("Redis bridge not configured (set REDIS_URL to enable)")
        except Exception as e:
            logger.warning(f"Redis bridge setup failed: {e}")

        # Signal handling
        stop_event = asyncio.Event()

        def signal_handler(sig, frame):
            logger.info("Interrupt signal received, stopping...")
            stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start
        logger.info("Trading engine ready")

        # AutoEvaluator: label ReasoningBank entries against actual market
        # moves. Without it, CLI sessions write memory that is never evaluated
        # (success stays None) so scorecards and distilled strategies starve.
        evaluator_task = None
        auto_evaluator = None
        try:
            from src.analysis.auto_evaluator import AutoEvaluator

            auto_evaluator = AutoEvaluator(symbol=args.symbol, timeframe=args.timeframe)
            evaluator_task = asyncio.create_task(auto_evaluator.start())
            logger.info(
                "AutoEvaluator started (horizon=%sm, cost=%.2f%%)",
                auto_evaluator.horizon,
                auto_evaluator.cost_pct,
            )
        except Exception as e:
            logger.warning(f"AutoEvaluator unavailable: {e}")

        # Execute. The signal event races the long-running engine task so
        # SIGINT/SIGTERM always reaches TradingEngine.stop() before teardown.
        try:
            engine_task = asyncio.create_task(engine.start())
            stop_task = asyncio.create_task(stop_event.wait())
            done, _ = await asyncio.wait(
                {engine_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done and stop_event.is_set():
                await engine.stop()
                await engine_task
            else:
                await engine_task
            if not stop_task.done():
                stop_task.cancel()
        finally:
            if auto_evaluator is not None:
                await auto_evaluator.stop()
            if evaluator_task is not None:
                evaluator_task.cancel()
            _stop_nanofenix_companion(nanofenix_proc)
            if instance_lock:
                instance_lock.release()

        return 0

    except ImportError as e:
        logger.error(f"Error importing trading engine: {e}")
        logger.info("Running in simplified test mode...")
        try:
            return await run_simple_test(args)
        finally:
            _stop_nanofenix_companion(nanofenix_proc)
            if instance_lock:
                instance_lock.release()


async def run_simple_test(args):
    """Executes a simplified test without the full engine."""
    logger.info("=== Simplified Test Mode ===")

    from src.prompts.agent_prompts import format_prompt
    from langchain_ollama import ChatOllama
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.trading.binance_client import BinanceClient

    # Connect to Binance
    client = BinanceClient(testnet=True)
    await client.connect()

    # Get real data
    price = await client.get_price(args.symbol)
    klines = await client.get_klines(args.symbol, args.timeframe, limit=50)

    logger.info(f"Data received: {args.symbol} @ ${price:,.2f}")
    logger.info(f"Klines: {len(klines)} candles")

    # Calculate simple indicators
    if klines:
        closes = [k["close"] for k in klines]

        # Simple RSI
        gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
        losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
        avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0
        avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 0.0001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        # Simple EMA
        ema_9 = sum(closes[-9:]) / 9 if len(closes) >= 9 else closes[-1]
        ema_21 = sum(closes[-21:]) / 21 if len(closes) >= 21 else closes[-1]

        indicators = {
            "rsi": round(rsi, 2),
            "ema_9": round(ema_9, 2),
            "ema_21": round(ema_21, 2),
            "price": price,
        }

        logger.info(f"Indicators: RSI={rsi:.1f}, EMA9={ema_9:.0f}, EMA21={ema_21:.0f}")
    else:
        indicators = {"rsi": 50, "price": price}

    # Run analysis with LLM
    logger.info("Running analysis with LLM...")

    messages = format_prompt(
        "technical_analyst",
        symbol=args.symbol,
        timeframe=args.timeframe,
        indicators_json=str(indicators),
        current_price=str(price),
    )

    llm = ChatOllama(
        model=args.model,
        temperature=0.1,
        num_predict=500,
    )

    response = llm.invoke([
        SystemMessage(content=messages[0]["content"]),
        HumanMessage(content=messages[1]["content"]),
    ])

    logger.info("=== Technical Agent Response ===")
    print(response.content[:1000])

    await client.close()
    return 0


if __name__ == "__main__":
    # Parse args first to handle --api mode which uses uvicorn (blocking, owns loop)
    args = parse_args()

    if args.api:
        print("🚀 Starting API server (Frontend Backend)...")
        import uvicorn
        host = args.host or "127.0.0.1"
        if host == "0.0.0.0":
            allow_expose = os.getenv("ALLOW_EXPOSE_API", "false").lower() == "true"
            if not allow_expose:
                logger.warning("API host set to 0.0.0.0; to expose the API explicitly set ALLOW_EXPOSE_API=true")
                logger.info("Binding to 127.0.0.1 instead for safety")
                host = "127.0.0.1"
        uvicorn.run("src.api.server:app_socketio", host=host, port=args.port, reload=False)
        sys.exit(0)

    try:
        # Pass args to main (we need to modify main signature or use global/re-parse)
        # Easier: Re-parse inside main or refactor main to accept args. 
        # Since main calls parse_args again, it's fine (argparse is idempotent usually if args not passed explicitly)
        # But clearer to pass args.
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
