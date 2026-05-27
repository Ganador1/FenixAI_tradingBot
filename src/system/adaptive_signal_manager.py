"""Shim for AdaptiveSignalManager to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.adaptive_signal_manager`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.adaptive_signal_manager import *  # noqa: F401,F403
else:

    class AdaptiveSignalManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdaptiveSignalManager is disabled; enable legacy modules to use it."
            )

    __all__ = ["AdaptiveSignalManager"]
