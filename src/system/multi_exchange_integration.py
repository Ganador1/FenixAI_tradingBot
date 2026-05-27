"""Shim for MultiExchangeIntegration to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.multi_exchange_integration`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.multi_exchange_integration import *  # noqa: F401,F403
else:

    class MultiExchangeIntegration:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "MultiExchangeIntegration is disabled; enable legacy modules to use it."
            )

    __all__ = ["MultiExchangeIntegration"]
