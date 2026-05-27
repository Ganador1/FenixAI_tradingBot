"""Shim for BayesianStrategyOptimizer to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.bayesian_strategy_optimizer`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.bayesian_strategy_optimizer import *  # noqa: F401,F403
else:

    class BayesianStrategyOptimizer:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "BayesianStrategyOptimizer is disabled; enable legacy modules to use it."
            )

    __all__ = ["BayesianStrategyOptimizer"]
