"""
NanoFenix v3 — "The Precision Companion"

Best ideas from all predecessors:
- V2's 1-sec bar aggregation + aggTrade data (noise reduction)
- V0's Deep LOB features (simplified, no PyTorch)
- Multi-scale features (1s/5s/30s/60s lookbacks)
- Dual-horizon LightGBM ensemble (30s + 120s)
- Adaptive confidence calibration (matches Fenix thresholds)
- Rich companion signal for Fenix engine integration

This version is designed to be USEFUL as a companion to Fenix,
not just a standalone paper trader.
"""

from .core import NanoFenixV3, main

__all__ = ["NanoFenixV3", "main"]
