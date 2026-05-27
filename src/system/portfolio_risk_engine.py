"""Shim for PortfolioRiskEngine (clean) - only a small shim.
Loads full implementation from `src.system.legacy.portfolio_risk_engine` when `should_load_legacy()` is true.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.portfolio_risk_engine import *  # noqa: F401,F403
else:

    class PortfolioRiskEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PortfolioRiskEngine is disabled; enable legacy modules to use it.")

    __all__ = ["PortfolioRiskEngine"]
