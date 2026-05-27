import asyncio
import logging
import sys
import signal
from minifenix.brain import SlowBrain, OLLAMA_MODEL
from minifenix.trigger import FastTrigger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
# Silence verbose logs
logging.getLogger("lightgbm").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

brain: SlowBrain | None = None
trigger: FastTrigger | None = None


def _shutdown_handler(sig, frame):
    """Print paper-trading summary when exiting via Ctrl+C."""
    print("\n\n[STOP] Stopping MiniFenix...")
    if trigger is not None and brain is not None:
        trigger.paper.print_summary(trigger._price_history[-1] if trigger._price_history else 0)
    sys.exit(0)


async def main():
    global brain, trigger

    print("=" * 70)
    print("[START] MiniFenix SOTA v4  -  LightGBM + Ollama + Binance Testnet")
    print("=" * 70)
    print(f"  [BRAIN] Brain LLM:      {OLLAMA_MODEL}")
    print(f"  [FAST] Fast Loop:      LightGBM + Online Learning + 26 LOB features")
    print(f"  [LIVE] Testnet Trader: $10,000 USDT initial | 5% pos size | SL-0.3% / TP+0.6%")
    print(f"  [DATA] Data:          Binance WebSocket BTC/USDT @bookTicker")
    print(f"  [NET] Orders:        Binance Futures Testnet (real fictitious money)")
    print("=" * 70)
    print("Phases:")
    print("  1. Ticks 0-200:   Accumulating data (Brain and ML warming up)")
    print("  2. Ticks 200-700: Features ready, LightGBM training")
    print("  3. Tick 700+:     Full system active")
    print("  4. Every 30s:     Brain (Ollama) re-analyses the live market")
    print("  5. Ctrl+C:        Show the full paper-trading summary")
    print("=" * 70)

    brain = SlowBrain()
    trigger = FastTrigger(brain, symbol="btcusdt")

    try:
        await asyncio.gather(
            brain.run_loop(interval_seconds=30),
            trigger.run_loop(),
        )
    finally:
        await brain.close()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)
    asyncio.run(main())
