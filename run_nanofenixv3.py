#!/usr/bin/env python3
"""
Run NanoFenix v3 — The Precision Companion.

Usage:
  python run_nanofenixv3.py                              # ETHUSDT default
  python run_nanofenixv3.py --symbol BTCUSDT             # Different symbol
  python run_nanofenixv3.py --companion                  # Enable Fenix companion signal
  python run_nanofenixv3.py --symbol ETHUSDT --companion # Both

Environment variables:
  NANOFENIX_SIGNAL_STATE_PATH   Path for companion JSON signal file
  NANOFENIXV3_SYMBOL            Symbol override
  NANOFENIXV3_BASE_MIN_BPS      Base prediction threshold (default: 1.5)
  NANOFENIXV3_HORIZON_SHORT     Short horizon in bars (default: 30)
  NANOFENIXV3_HORIZON_LONG      Long horizon in bars (default: 120)
  NANOFENIXV3_MIN_SAMPLES       Min samples before first train (default: 600)
  NANOFENIXV3_RETRAIN_EVERY     Retrain cadence in bars (default: 800)
  NANOFENIXV3_ENABLE_ADAPTIVE_FUSION  Enable adaptive dual-horizon fusion (default: 0)
"""

import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nanofenixv3.core import main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NanoFenix v3 — The Precision Companion")
    parser.add_argument("--symbol", default="ETHUSDT", help="Trading pair (default: ETHUSDT)")
    parser.add_argument("--balance", type=float, default=10_000.0, help="Paper trading balance")
    parser.add_argument("--model", default=None, help="Path to pre-trained model file")
    parser.add_argument(
        "--companion",
        action="store_true",
        help="Enable companion signal output for Fenix engine",
    )
    parser.add_argument(
        "--adaptive-fusion",
        action="store_true",
        help="Enable adaptive fusion when short/long horizons disagree",
    )
    parser.add_argument(
        "--strict-consensus",
        action="store_true",
        help="Force strict consensus mode (disable adaptive fusion)",
    )
    parser.add_argument(
        "--output-path",
        default=None,
        help="Optional companion signal JSON path (also exported to NANOFENIX_SIGNAL_STATE_PATH)",
    )
    parser.add_argument(
        "--runtime-state-path",
        default=None,
        help="Optional runtime warm-start pickle path (also exported to NANOFENIXV3_RUNTIME_STATE_PATH)",
    )
    args = parser.parse_args()

    # Auto-set companion signal path if --companion flag is used
    if args.output_path:
        os.environ["NANOFENIX_SIGNAL_STATE_PATH"] = args.output_path
        print(f"📡 Companion signal path: {args.output_path}")
    elif args.companion and not os.getenv("NANOFENIX_SIGNAL_STATE_PATH"):
        signal_path = f"logs/nanofenixv3_companion_{args.symbol.lower()}.json"
        os.environ["NANOFENIX_SIGNAL_STATE_PATH"] = signal_path
        print(f"📡 Companion signal path: {signal_path}")

    if args.strict_consensus:
        os.environ["NANOFENIXV3_ENABLE_ADAPTIVE_FUSION"] = "0"
        print("🧭 Fusion mode: strict consensus")
    elif args.adaptive_fusion:
        os.environ["NANOFENIXV3_ENABLE_ADAPTIVE_FUSION"] = "1"
        print("🧭 Fusion mode: adaptive")

    if args.runtime_state_path:
        os.environ["NANOFENIXV3_RUNTIME_STATE_PATH"] = args.runtime_state_path
        print(f"♻️ Runtime state path: {args.runtime_state_path}")

    main(
        symbol=args.symbol,
        balance=args.balance,
        model_path=args.model,
    )
