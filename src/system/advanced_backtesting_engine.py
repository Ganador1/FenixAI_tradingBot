"""Shim for AdvancedBacktestingEngine to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.advanced_backtesting_engine`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.advanced_backtesting_engine import *  # noqa: F401,F403
else:

    class AdvancedBacktestingEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdvancedBacktestingEngine is disabled; enable legacy modules to use it."
            )

    __all__ = ["AdvancedBacktestingEngine"]
