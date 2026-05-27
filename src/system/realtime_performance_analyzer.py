"""Shim for RealtimePerformanceAnalyzer to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.realtime_performance_analyzer`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.realtime_performance_analyzer import *  # noqa: F401,F403
else:

    class RealtimePerformanceAnalyzer:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "RealtimePerformanceAnalyzer is disabled; enable legacy modules to use it."
            )

    __all__ = ["RealtimePerformanceAnalyzer"]
