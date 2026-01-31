"""
Biblioteca de Indicadores - Gestión de indicadores traducidos de Pine Script

Esta biblioteca mantiene:
1. Indicadores traducidos de Pine Script a Python
2. Registro de indicadores scrapeados pendientes de traducción
3. Sistema de testing y validación
"""

import json
import importlib
import inspect
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Callable, Any, Type
from enum import Enum
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IndicatorCategory(Enum):
    """Categorías de indicadores"""
    TREND = "trend"                    # RSI, MACD, Moving Averages
    MOMENTUM = "momentum"              # Stochastic, Williams %R
    VOLATILITY = "volatility"          # Bollinger Bands, ATR
    VOLUME = "volume"                  # OBV, Volume Profile
    PATTERN = "pattern"                # SFP, Harmonics
    LIQUIDITY = "liquidity"            # Liquidations, Order Flow
    STRUCTURE = "structure"            # Market Structure, BOS
    CUSTOM = "custom"                  # Indicadores propios


@dataclass
class IndicatorMetadata:
    """Metadata de un indicador"""
    name: str
    description: str
    category: IndicatorCategory
    author: str
    version: str
    source: str  # "original", "tradingview", "custom"
    source_url: Optional[str] = None
    pine_version: Optional[int] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    output_columns: List[str] = field(default_factory=list)


@dataclass
class IndicatorResult:
    """Resultado de aplicar un indicador"""
    indicator_name: str
    success: bool
    data: Optional[pd.DataFrame] = None
    signals: List[Dict] = field(default_factory=list)
    error: Optional[str] = None


class IndicatorRegistry:
    """
    Registro central de indicadores disponibles
    
    Uso:
        registry = IndicatorRegistry()
        registry.register(SwingFailurePattern, metadata)
        
        result = registry.apply("swing_failure_pattern", df)
    """
    
    REGISTRY_FILE = Path("cache/indicators/registry.json")
    
    def __init__(self):
        self._indicators: Dict[str, Type] = {}
        self._metadata: Dict[str, IndicatorMetadata] = {}
        self._load_builtin()
    
    def _load_builtin(self):
        """Cargar indicadores built-in"""
        try:
            from src.indicators.swing_failure_pattern import SwingFailurePattern
            
            self.register(
                "swing_failure_pattern",
                SwingFailurePattern,
                IndicatorMetadata(
                    name="Swing Failure Pattern",
                    description="Detecta liquidity grabs en swing highs/lows con confirmación CISD",
                    category=IndicatorCategory.PATTERN,
                    author="AlgoAlpha (traducido)",
                    version="1.0.0",
                    source="tradingview",
                    source_url="https://www.tradingview.com/script/AlgoAlpha-SFP/",
                    pine_version=6,
                    parameters={
                        "pivot_len": {"type": "int", "default": 12, "description": "Longitud para detección de pivots"},
                        "max_pivot_age": {"type": "int", "default": 50, "description": "Máxima edad de pivots"},
                        "patience": {"type": "int", "default": 7, "description": "Barras máximas para CISD"},
                        "tolerance": {"type": "float", "default": 0.7, "description": "Filtro de ruido"}
                    },
                    output_columns=["sfp_signal", "sfp_swept_level", "sfp_cisd_level", "sfp_trend"]
                )
            )
            logger.info("✅ Indicadores built-in cargados")
        except ImportError as e:
            logger.warning(f"⚠️ Error cargando indicadores built-in: {e}")
    
    def register(
        self,
        name: str,
        indicator_class: Type,
        metadata: IndicatorMetadata
    ):
        """Registrar un indicador"""
        self._indicators[name] = indicator_class
        self._metadata[name] = metadata
        logger.debug(f"📝 Registrado: {name}")
    
    def list_indicators(self, category: Optional[IndicatorCategory] = None) -> List[str]:
        """Listar indicadores disponibles"""
        if category:
            return [
                name for name, meta in self._metadata.items()
                if meta.category == category
            ]
        return list(self._indicators.keys())
    
    def get_metadata(self, name: str) -> Optional[IndicatorMetadata]:
        """Obtener metadata de un indicador"""
        return self._metadata.get(name)
    
    def apply(
        self,
        name: str,
        df: pd.DataFrame,
        **params
    ) -> IndicatorResult:
        """
        Aplicar un indicador a datos OHLCV
        
        Args:
            name: Nombre del indicador
            df: DataFrame con columnas open, high, low, close
            **params: Parámetros del indicador
            
        Returns:
            IndicatorResult con los datos procesados
        """
        if name not in self._indicators:
            return IndicatorResult(
                indicator_name=name,
                success=False,
                error=f"Indicador '{name}' no encontrado"
            )
        
        try:
            indicator_class = self._indicators[name]
            
            # Obtener parámetros válidos del constructor
            sig = inspect.signature(indicator_class.__init__)
            valid_params = {
                k: v for k, v in params.items()
                if k in sig.parameters
            }
            
            # Crear instancia y calcular
            indicator = indicator_class(**valid_params)
            result_df = indicator.calculate(df)
            
            # Extraer señales si el indicador tiene método get_signals
            signals = []
            if hasattr(indicator, 'get_signals'):
                raw_signals = indicator.get_signals(result_df)
                signals = [asdict(s) if hasattr(s, '__dataclass_fields__') else s for s in raw_signals]
            
            return IndicatorResult(
                indicator_name=name,
                success=True,
                data=result_df,
                signals=signals
            )
            
        except Exception as e:
            logger.error(f"❌ Error aplicando {name}: {e}")
            return IndicatorResult(
                indicator_name=name,
                success=False,
                error=str(e)
            )
    
    def apply_multiple(
        self,
        indicators: List[str],
        df: pd.DataFrame,
        params: Optional[Dict[str, Dict]] = None
    ) -> Dict[str, IndicatorResult]:
        """
        Aplicar múltiples indicadores
        
        Args:
            indicators: Lista de nombres de indicadores
            df: DataFrame OHLCV
            params: Diccionario de parámetros por indicador
            
        Returns:
            Diccionario de resultados por indicador
        """
        params = params or {}
        results = {}
        
        for name in indicators:
            indicator_params = params.get(name, {})
            results[name] = self.apply(name, df, **indicator_params)
        
        return results
    
    def to_dict(self) -> Dict:
        """Exportar registro a diccionario"""
        return {
            name: {
                "metadata": asdict(meta),
                "available": True
            }
            for name, meta in self._metadata.items()
        }


# ==================== Indicadores Pendientes de Traducción ====================

PENDING_TRANSLATIONS = [
    {
        "name": "Smart Money Concepts",
        "description": "Detecta BOS (Break of Structure), CHoCH, Order Blocks",
        "source_url": "https://www.tradingview.com/script/...",
        "priority": "high",
        "complexity": "high"
    },
    {
        "name": "Liquidity Zones",
        "description": "Identifica zonas de liquidez donde hay stops acumulados",
        "source_url": "https://www.tradingview.com/script/...",
        "priority": "high",
        "complexity": "medium"
    },
    {
        "name": "Fair Value Gap",
        "description": "Detecta FVG (imbalances) y monitorea su llenado",
        "source_url": "https://www.tradingview.com/script/...",
        "priority": "medium",
        "complexity": "low"
    },
    {
        "name": "Order Block Finder",
        "description": "Identifica Order Blocks institucionales",
        "source_url": "https://www.tradingview.com/script/...",
        "priority": "medium",
        "complexity": "medium"
    },
    {
        "name": "VWAP Bands",
        "description": "VWAP con bandas de desviación estándar",
        "source_url": "https://www.tradingview.com/script/...",
        "priority": "low",
        "complexity": "low"
    }
]


# ==================== Singleton Registry ====================

_registry: Optional[IndicatorRegistry] = None

def get_registry() -> IndicatorRegistry:
    """Obtener instancia singleton del registro"""
    global _registry
    if _registry is None:
        _registry = IndicatorRegistry()
    return _registry


# ==================== Demo ====================

if __name__ == "__main__":
    import numpy as np
    
    print("📊 Biblioteca de Indicadores - FenixAI")
    print("="*50)
    
    registry = get_registry()
    
    # Listar indicadores
    print("\n📌 Indicadores disponibles:")
    for name in registry.list_indicators():
        meta = registry.get_metadata(name)
        print(f"  • {meta.name} [{meta.category.value}]")
        print(f"    {meta.description[:60]}...")
    
    # Crear datos de prueba
    print("\n🔧 Probando indicador SFP...")
    np.random.seed(42)
    n = 100
    
    price = 100.0
    data = []
    for _ in range(n):
        open_p = price
        change = np.random.randn() * 2
        high_p = open_p + abs(change) + np.random.rand()
        low_p = open_p - abs(change) - np.random.rand()
        close_p = open_p + change
        
        data.append({
            'open': open_p,
            'high': max(high_p, open_p, close_p),
            'low': min(low_p, open_p, close_p),
            'close': close_p
        })
        price = close_p
    
    df = pd.DataFrame(data)
    
    # Aplicar indicador
    result = registry.apply("swing_failure_pattern", df, pivot_len=5)
    
    if result.success:
        print(f"  ✅ Éxito - {len(result.signals)} señales detectadas")
        for sig in result.signals[-3:]:
            print(f"     {sig}")
    else:
        print(f"  ❌ Error: {result.error}")
    
    # Mostrar pendientes
    print("\n⏳ Indicadores pendientes de traducción:")
    for ind in PENDING_TRANSLATIONS[:3]:
        print(f"  • {ind['name']} (prioridad: {ind['priority']})")
