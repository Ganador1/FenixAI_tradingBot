"""Shim for OnDemandModelManager to allow it to be moved to legacy.
The original heavy implementation may live under `src.system.legacy.on_demand_model_manager`.
"""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.on_demand_model_manager import *  # noqa: F401,F403
else:

    class OnDemandModelManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OnDemandModelManager is disabled; enable legacy modules to use it.")

    __all__ = ["OnDemandModelManager"]
