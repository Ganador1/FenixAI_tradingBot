"""
FenixAI Indicators - Biblioteca de indicadores técnicos

Indicadores traducidos de Pine Script y personalizados para trading de crypto.
"""

from .swing_failure_pattern import SwingFailurePattern, detect_sfp, SFPSignal, SignalType
from .indicator_library import (
    IndicatorRegistry,
    IndicatorMetadata,
    IndicatorResult,
    IndicatorCategory,
    get_registry,
    PENDING_TRANSLATIONS
)

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
    "PENDING_TRANSLATIONS"
]
