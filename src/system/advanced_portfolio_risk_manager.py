"""Shim for AdvancedPortfolioRiskManager to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.advanced_portfolio_risk_manager`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.advanced_portfolio_risk_manager import *  # noqa: F401,F403
else:

    class AdvancedPortfolioRiskManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AdvancedPortfolioRiskManager is disabled; enable legacy modules to use it."
            )

    __all__ = ["AdvancedPortfolioRiskManager"]
