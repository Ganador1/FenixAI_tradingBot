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
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Load .env early so JWT_SECRET, API keys, etc. are visible to every module.
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


def parse_args():
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(
        description="Fenix AI Trading Bot - LangGraph Multi-Agent System (v2.5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_fenix.py                                   # Paper trading, BTCUSDT, 15m
  python run_fenix.py --mode live --allow-live          # Live trading
  python run_fenix.py --symbol ETHUSDT                  # Different pair
  python run_fenix.py --timeframe 5m                    # Different timeframe
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
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use Binance Futures Testnet (for risk-free testing)",
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading pair (default: BTCUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        default="15m",
        help="Analysis timeframe (default: 15m)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model to use when --team-models is not provided (default: qwen2.5:7b)",
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
        default=60,
        help="Interval between analysis in seconds (default: 60)",
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
        default=2.0,
        help="Max risk per trade in %% (default: 2.0)",
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
        default="direction_mismatch,high_uncertainty,stale_signal,symbol_mismatch,signal_file_missing,signal_file_empty,signal_parse_error,missing_or_invalid_timestamp",
        help=(
            "Comma-separated NanoFenix veto reasons that hard-block a Fenix entry. "
            "Soft reasons (e.g. low_actionable_edge, companion_not_ready) are observed "
            "but not enforced. Forwarded via FENIX_NANOFENIX_HARD_VETO_REASONS."
        ),
    )

    return parser.parse_args()


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

    env = os.environ.copy()
    if observer_only:
        env["NANOFENIXV3_COMPANION_OBSERVER_ONLY"] = "1"
    env["NANOFENIX_SIGNAL_STATE_PATH"] = str(signal_path)

    cmd = [
        sys.executable,
        str(nano_launcher),
        "--symbol", symbol,
        "--companion",
        "--adaptive-fusion",
        "--output-path", str(signal_path),
    ]
    logger.info("Launching NanoFenix companion: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, env=env)
    except (OSError, ValueError) as e:
        logger.warning("Could not launch NanoFenix companion: %s", e)
        return None, None
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
            return 1
        
        models = [m["name"] for m in response.json().get("models", [])]
        if args.model not in models and not any(args.model.split(":")[0] in m for m in models):
            logger.warning(f"Model {args.model} not found. Available: {models[:5]}")
            args.model = models[0] if models else "gemma3:1b"
            logger.info(f"Using alternative model: {args.model}")
        
        logger.info(f"✅ Ollama OK - Model: {args.model}")
        
    except Exception as e:
        logger.error(f"Error connecting to Ollama: {e}")
        return 1
    
    # Verify Binance
    use_testnet = args.mode == "paper" or args.testnet
    logger.info(f"Verifying Binance connection {'(TESTNET)' if use_testnet else '(PRODUCTION)'}...")
    try:
        from src.trading.binance_client import BinanceClient
        
        client = BinanceClient(testnet=use_testnet)
        connected = await client.connect()
        
        if connected:
            price = await client.get_price(args.symbol)
            if price:
                mode_str = "TESTNET" if use_testnet else "LIVE"
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
        uvicorn.run("src.api.server:app_socketio", host=args.host, port=8000, reload=False)
        return 0

    # Start standard trading engine (CLI mode)
    logger.info("Starting trading engine (CLI Mode)...")

    try:
        from src.trading.engine import TradingEngine

        engine = TradingEngine(
            symbol=args.symbol,
            timeframe=args.timeframe,
            use_testnet=args.mode == "paper" or args.testnet,
            paper_trading=args.mode == "paper" or args.dry_run,
            enable_visual_agent=not args.no_visual,
            enable_sentiment_agent=not args.no_sentiment,
            allow_live_trading=args.allow_live,
        )

        # Signal handling
        stop_event = asyncio.Event()

        def signal_handler(sig, frame):
            logger.info("Interrupt signal received, stopping...")
            stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start
        logger.info("Trading engine ready")

        # Execute
        try:
            await engine.start()
        finally:
            _stop_nanofenix_companion(nanofenix_proc)

        return 0

    except ImportError as e:
        logger.error(f"Error importing trading engine: {e}")
        logger.info("Running in simplified test mode...")
        try:
            return await run_simple_test(args)
        finally:
            _stop_nanofenix_companion(nanofenix_proc)


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
        uvicorn.run("src.api.server:app_socketio", host=host, port=8000, reload=False)
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
