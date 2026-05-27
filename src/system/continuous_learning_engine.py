"""Shim for ContinuousLearningEngine to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.continuous_learning_engine`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.continuous_learning_engine import *  # noqa: F401,F403
else:

    class ContinuousLearningEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "ContinuousLearningEngine is disabled; enable legacy modules to use it."
            )

    __all__ = ["ContinuousLearningEngine"]
