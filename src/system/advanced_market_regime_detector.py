"""Shim for AdvancedMarketRegimeDetector.

The actual heavy implementation is now under `src.system.legacy.advanced_market_regime_detector`.
This shim exports placeholders when legacy mode is disabled.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.advanced_market_regime_detector import *  # noqa: F401,F403
else:

    class MarketRegime:
        pass

    class RegimeFeatures:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdvancedMarketRegimeDetector is disabled. Set FENIX_LOAD_LEGACY_SYSTEM or config.system.enable_legacy_systems=True to enable legacy modules."
            )

    class RegimePrediction:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("AdvancedMarketRegimeDetector is disabled.")

    class AdvancedMarketRegimeDetector:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdvancedMarketRegimeDetector is disabled. Enable legacy modules to use it."
            )

    __all__ = ["MarketRegime", "RegimeFeatures", "RegimePrediction", "AdvancedMarketRegimeDetector"]
