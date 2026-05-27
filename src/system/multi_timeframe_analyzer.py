"""Shim for MultiTimeframeAnalyzer to allow it to be moved to legacy.
The original heavy implementation lives under `src.system.legacy.multi_timeframe_analyzer`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.multi_timeframe_analyzer import *  # noqa: F401,F403
else:

    class TimeFrame:
        pass

    class TimeFrameSignal:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "MultiTimeframeAnalyzer is disabled; enable legacy modules to use it."
            )

    class MultiTimeframeConsensus:
        pass

    class MultiTimeframeAnalyzer:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "MultiTimeframeAnalyzer is disabled; enable legacy plugins to use it."
            )

    __all__ = ["TimeFrame", "TimeFrameSignal", "MultiTimeframeConsensus", "MultiTimeframeAnalyzer"]
