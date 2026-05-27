"""Shim for SignalEvolutionEngine to load legacy implementation conditionally."""

from . import should_load_legacy

if should_load_legacy():
    from src.system.legacy.signal_evolution_engine import *  # noqa: F401,F403
else:

    class TradingStrategy:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "SignalEvolutionEngine is disabled; enable legacy modules to use it."
            )

    class EvolutionExperiment:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "SignalEvolutionEngine is disabled; enable legacy modules to use it."
            )

    class SignalEvolutionEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "SignalEvolutionEngine is disabled; enable legacy modules to use it."
            )

    __all__ = ["TradingStrategy", "EvolutionExperiment", "SignalEvolutionEngine"]
