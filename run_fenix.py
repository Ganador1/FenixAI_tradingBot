#!/usr/bin/env python3
# run_fenix.py
"""
Main execution script for Fenix Trading Bot.

Usage:
    python run_fenix.py                    # Paper trading with Ollama
    python run_fenix.py --mode live        # Live trading
    python run_fenix.py --symbol ETHUSDT   # Other pair
    python run_fenix.py --help             # See options
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import os
from datetime import datetime
from pathlib import Path

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
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fenix AI Trading Bot - LangGraph Multi-Agent System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_fenix.py                       # Paper trading, BTCUSDT, 15m
  python run_fenix.py --mode live           # Live trading
  python run_fenix.py --symbol ETHUSDT      # Other pair
  python run_fenix.py --timeframe 5m        # Other timeframe
  python run_fenix.py --no-visual           # Without visual agent
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
        help="Required to run in live mode and prevent accidental trades",
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Pair to trade (default: BTCUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        default="15m",
        help="Analysis timeframe (default: 15m)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model to use (default: qwen2.5:7b)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Interval between analyses in seconds (default: 60)",
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
        help="Maximum risk per trade in %% (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only simulate, do not execute orders",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Start API server (FastAPI + Socket.IO) for the frontend",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the API server to (default: 127.0.0.1, not publicly exposed)",
    )
    
    return parser.parse_args()


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
    
    logger.info("Starting Fenix Trading Bot")
    logger.info(f"  Mode: {args.mode.upper()}")
    logger.info(f"  Symbol: {args.symbol}")
    logger.info(f"  Timeframe: {args.timeframe}")
    logger.info(f"  Model: {args.model}")
    logger.info(f"  Interval: {args.interval}s")
    logger.info(f"  Visual: {'Yes' if not args.no_visual else 'No'}")
    logger.info(f"  Sentiment: {'Yes' if not args.no_sentiment else 'No'}")

    if args.mode == "live" and not args.allow_live:
        logger.error("Live mode requested but --allow-live was not provided. Aborting for safety.")
        return 1
    
    # Check Ollama
    logger.info("Checking connection to Ollama...")
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
    
    # Check Binance
    logger.info("Checking connection to Binance...")
    try:
        from src.trading.binance_client import BinanceClient
        
        testnet = args.mode == "paper"
        client = BinanceClient(testnet=testnet)
        connected = await client.connect()
        
        if connected:
            price = await client.get_price(args.symbol)
            if price:
                logger.info(f"✅ Binance OK - {args.symbol}: ${price:,.2f}")
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
        # Import app_socketio from the new server module
        # Note: uvicorn needs the import string "src.api.server:app_socketio"
        uvicorn.run("src.api.server:app_socketio", host=args.host, port=8000, reload=False)
        return 0

    # Start standard trading engine (CLI mode)
    logger.info("Starting trading engine (CLI Mode)...")
    
    try:
        from src.trading.engine import TradingEngine
        
        engine = TradingEngine(
            symbol=args.symbol,
            timeframe=args.timeframe,
            use_testnet=args.mode == "paper",
            paper_trading=args.mode == "paper" or args.dry_run,
            enable_visual_agent=not args.no_visual,
            enable_sentiment_agent=not args.no_sentiment,
            allow_live_trading=args.allow_live,
        )
        
        # System signal handling
        stop_event = asyncio.Event()
        
        def signal_handler(sig, frame):
            logger.info("Interrupt signal received, stopping...")
            stop_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start
        logger.info("✅ Trading engine ready")
        
        # Execute
        await engine.start()
        
        return 0
        
    except ImportError as e:
        logger.error(f"Error importing trading engine: {e}")
        logger.info("Running in simplified test mode...")
        
        # Simplified test mode
        return await run_simple_test(args)


async def run_simple_test(args):
    """Runs a simplified test without the full engine."""
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
    
    # Execute analysis with LLM
    logger.info("Executing analysis with LLM...")
    
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
