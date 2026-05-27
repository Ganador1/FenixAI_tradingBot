"""Shim for AdvancedMetricsSystem to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.advanced_metrics_system`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.advanced_metrics_system import *  # noqa: F401,F403
else:

    class AdvancedMetricsSystem:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdvancedMetricsSystem is disabled; enable legacy modules to use it."
            )

    __all__ = ["AdvancedMetricsSystem"]
