"""
FenixAI Indicators - Biblioteca de indicadores técnicos

Indicadores traducidos de Pine Script y personalizados para trading de crypto.
"""

from .indicator_library import (
    PENDING_TRANSLATIONS,
    IndicatorCategory,
    IndicatorMetadata,
    IndicatorRegistry,
    IndicatorResult,
    get_registry,
)
from .swing_failure_pattern import SFPSignal, SignalType, SwingFailurePattern, detect_sfp

__all__ = [
    # SFP
    "SwingFailurePattern",
    "detect_sfp",
    "SFPSignal",
    "SignalType",
    # Registry
    "IndicatorRegistry",
    "IndicatorMetadata",
    "IndicatorResult",
    "IndicatorCategory",
    "get_registry",
    "PENDING_TRANSLATIONS",
]
