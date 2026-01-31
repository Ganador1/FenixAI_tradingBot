"""
Swing Failure Pattern (SFP) Indicator - Traducido de Pine Script a Python

Original: "Swing Failure Signals [AlgoAlpha]" de TradingView
Traducción: FenixAI

El SFP detecta "liquidity grabs" - cuando el precio barre stops de swing highs/lows
y luego revierte. Muy efectivo en crypto para detectar manipulación institucional.

Conceptos clave:
- Pivot High/Low: Puntos de swing donde el precio cambió dirección
- Sweep: Cuando el precio rompe un pivot para cazar stops
- CISD (Change in State of Delivery): Confirmación de reversión
- SFP Signal: Sweep + CISD dentro del período de paciencia
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum


class SignalType(Enum):
    """Tipo de señal SFP"""
    BULLISH = "bullish"  # Sweep de lows + reversión alcista
    BEARISH = "bearish"  # Sweep de highs + reversión bajista
    NONE = "none"


@dataclass
class SFPSignal:
    """Señal de Swing Failure Pattern"""
    signal_type: SignalType
    bar_index: int
    price: float
    swept_level: float
    cisd_level: float
    confidence: float  # 0-1 basado en qué tan claro fue el patrón


@dataclass
class PivotPoint:
    """Punto pivot (swing high o low)"""
    bar_index: int
    price: float
    is_high: bool


class SwingFailurePattern:
    """
    Detector de Swing Failure Pattern (SFP)
    
    Parámetros (traducidos del Pine Script original):
    - pivot_len: Longitud para detección de pivots (default: 12)
    - max_pivot_age: Máxima edad de pivots para considerar (default: 50)
    - patience: Barras máximas para esperar CISD después del sweep (default: 7)
    - tolerance: Filtro de ruido de tendencia (default: 0.7)
    """
    
    def __init__(
        self,
        pivot_len: int = 12,
        max_pivot_age: int = 50,
        patience: int = 7,
        tolerance: float = 0.7
    ):
        self.pivot_len = pivot_len
        self.max_pivot_age = max_pivot_age
        self.patience = patience
        self.tolerance = tolerance
        
        # Estado interno
        self.pivot_highs: List[PivotPoint] = []
        self.pivot_lows: List[PivotPoint] = []
        self.bar_sweep_bull: int = 0
        self.bar_sweep_bear: int = 0
        self.trend: int = 0
        
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calcula señales SFP para un DataFrame OHLCV
        
        Args:
            df: DataFrame con columnas: open, high, low, close, volume (opcional)
            
        Returns:
            DataFrame con columnas adicionales:
            - sfp_signal: 1 (bullish), -1 (bearish), 0 (none)
            - sfp_swept_level: Nivel que fue barrido
            - sfp_cisd_level: Nivel de CISD
            - sfp_trend: Tendencia actual del indicador
        """
        df = df.copy()
        n = len(df)
        
        # Inicializar columnas de salida
        df['sfp_signal'] = 0
        df['sfp_swept_level'] = np.nan
        df['sfp_cisd_level'] = np.nan
        df['sfp_trend'] = 0
        df['pivot_high'] = np.nan
        df['pivot_low'] = np.nan
        
        # Detectar pivots usando rolling window
        df = self._detect_pivots(df)
        
        # Procesar barra por barra
        pivot_highs: List[Tuple[int, float]] = []  # (bar_index, price)
        pivot_lows: List[Tuple[int, float]] = []
        
        bar_sweep_bull = 0
        bar_sweep_bear = 0
        trend = 0
        
        bear_potential: List[Tuple[int, float]] = []  # (bar_index, open_price)
        bull_potential: List[Tuple[int, float]] = []
        
        temp_bull: List[Tuple[int, float, int]] = []  # Para drawing lines info
        temp_bear: List[Tuple[int, float, int]] = []
        
        for i in range(self.pivot_len, n):
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            close = df['close'].iloc[i]
            open_price = df['open'].iloc[i]
            
            # Agregar nuevos pivots detectados
            if not np.isnan(df['pivot_high'].iloc[i]):
                pivot_highs.append((i - self.pivot_len, df['pivot_high'].iloc[i]))
            if not np.isnan(df['pivot_low'].iloc[i]):
                pivot_lows.append((i - self.pivot_len, df['pivot_low'].iloc[i]))
            
            # Limitar tamaño de arrays
            pivot_highs = pivot_highs[-50:]
            pivot_lows = pivot_lows[-50:]
            
            # --- Sweep Detection (Bearish) ---
            new_pivot_highs = []
            swept_high_level = 0.0
            
            for idx, price in pivot_highs:
                if high > price:  # Sweep!
                    if i - idx < self.max_pivot_age:
                        if price > swept_high_level:
                            swept_high_level = price
                        temp_bear = [(i, price, idx)]
                else:
                    new_pivot_highs.append((idx, price))
            
            pivot_highs = new_pivot_highs
            
            if swept_high_level != 0.0:
                bar_sweep_bear = i
            
            # --- Sweep Detection (Bullish) ---
            new_pivot_lows = []
            swept_low_level = float('inf')
            
            for idx, price in pivot_lows:
                if low < price:  # Sweep!
                    if i - idx < self.max_pivot_age:
                        if price < swept_low_level:
                            swept_low_level = price
                        temp_bull = [(i, price, idx)]
                else:
                    new_pivot_lows.append((idx, price))
            
            pivot_lows = new_pivot_lows
            
            if swept_low_level != float('inf'):
                bar_sweep_bull = i
            
            # --- CISD Detection ---
            prev_close = df['close'].iloc[i-1] if i > 0 else close
            prev_open = df['open'].iloc[i-1] if i > 0 else open_price
            
            # Bear potential (bullish reversal candle after bearish)
            if prev_close < prev_open and close > open_price:
                bear_potential.append((i, open_price))
            
            # Bull potential (bearish reversal candle after bullish)
            if prev_close > prev_open and close < open_price:
                bull_potential.append((i, open_price))
            
            # Limitar tamaño
            bear_potential = bear_potential[-20:]
            bull_potential = bull_potential[-20:]
            
            cisd = 0
            cisd_level = np.nan
            
            # Check bear potential (bearish CISD - close below reversal level)
            new_bear_potential = []
            for p_idx, p_val in bear_potential:
                if close < p_val:
                    # Verificar tolerancia
                    len_check = i - p_idx
                    if len_check >= 0 and len_check < 100:
                        highest = df['close'].iloc[p_idx:i+1].max()
                        
                        # Buscar el top del movimiento previo
                        top = p_val
                        for j in range(p_idx, max(0, p_idx - 20), -1):
                            if df['close'].iloc[j] < df['open'].iloc[j]:
                                top = df['open'].iloc[j]
                                break
                        
                        denom = top - p_val
                        if denom != 0 and (highest - p_val) / denom > self.tolerance:
                            cisd_level = p_val
                            cisd = 1  # Bearish CISD
                            new_bear_potential = []
                            break
                        else:
                            continue
                else:
                    new_bear_potential.append((p_idx, p_val))
            
            if cisd == 0:
                bear_potential = new_bear_potential
            
            # Check bull potential (bullish CISD - close above reversal level)
            if cisd == 0:
                new_bull_potential = []
                for p_idx, p_val in bull_potential:
                    if close > p_val:
                        len_check = i - p_idx
                        if len_check >= 0 and len_check < 100:
                            lowest = df['close'].iloc[p_idx:i+1].min()
                            
                            # Buscar el bottom del movimiento previo
                            bottom = p_val
                            for j in range(p_idx, max(0, p_idx - 20), -1):
                                if df['close'].iloc[j] > df['open'].iloc[j]:
                                    bottom = df['open'].iloc[j]
                                    break
                            
                            denom = p_val - bottom
                            if denom != 0 and (p_val - lowest) / denom > self.tolerance:
                                cisd_level = p_val
                                cisd = 2  # Bullish CISD
                                new_bull_potential = []
                                break
                            else:
                                continue
                    else:
                        new_bull_potential.append((p_idx, p_val))
                
                if cisd == 0:
                    bull_potential = new_bull_potential
            
            # --- Update Trend ---
            if cisd == 1:
                trend = -1
            elif cisd == 2:
                trend = 1
            
            df.loc[df.index[i], 'sfp_trend'] = trend
            
            # --- Signal Generation ---
            bullsfp = False
            bearsfp = False
            
            # Bullish SFP: trend crosses above 0 AND recent sweep of lows
            prev_trend = df['sfp_trend'].iloc[i-1] if i > 0 else 0
            if prev_trend <= 0 and trend > 0:
                if i - bar_sweep_bull < self.patience:
                    bullsfp = True
            
            # Bearish SFP: trend crosses below 0 AND recent sweep of highs
            if prev_trend >= 0 and trend < 0:
                if i - bar_sweep_bear < self.patience:
                    bearsfp = True
            
            # Registrar señales
            if bullsfp:
                df.loc[df.index[i], 'sfp_signal'] = 1
                if temp_bull:
                    df.loc[df.index[i], 'sfp_swept_level'] = temp_bull[0][1]
                if not np.isnan(cisd_level):
                    df.loc[df.index[i], 'sfp_cisd_level'] = cisd_level
            
            if bearsfp:
                df.loc[df.index[i], 'sfp_signal'] = -1
                if temp_bear:
                    df.loc[df.index[i], 'sfp_swept_level'] = temp_bear[0][1]
                if not np.isnan(cisd_level):
                    df.loc[df.index[i], 'sfp_cisd_level'] = cisd_level
        
        return df
    
    def _detect_pivots(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detecta pivot highs y lows usando rolling window"""
        n = len(df)
        length = self.pivot_len
        
        for i in range(length, n - length):
            # Pivot High: high[i] es el máximo en la ventana
            window_high = df['high'].iloc[i-length:i+length+1]
            if df['high'].iloc[i] == window_high.max():
                # Verificar que sea único máximo
                if (window_high == df['high'].iloc[i]).sum() == 1:
                    df.loc[df.index[i+length], 'pivot_high'] = df['high'].iloc[i]
            
            # Pivot Low: low[i] es el mínimo en la ventana
            window_low = df['low'].iloc[i-length:i+length+1]
            if df['low'].iloc[i] == window_low.min():
                # Verificar que sea único mínimo
                if (window_low == df['low'].iloc[i]).sum() == 1:
                    df.loc[df.index[i+length], 'pivot_low'] = df['low'].iloc[i]
        
        return df
    
    def get_signals(self, df: pd.DataFrame) -> List[SFPSignal]:
        """
        Retorna lista de señales SFP detectadas
        
        Args:
            df: DataFrame ya procesado con calculate()
            
        Returns:
            Lista de SFPSignal
        """
        signals = []
        
        for i in range(len(df)):
            sig = df['sfp_signal'].iloc[i]
            if sig != 0:
                signals.append(SFPSignal(
                    signal_type=SignalType.BULLISH if sig == 1 else SignalType.BEARISH,
                    bar_index=i,
                    price=df['close'].iloc[i],
                    swept_level=df['sfp_swept_level'].iloc[i] if not np.isnan(df['sfp_swept_level'].iloc[i]) else 0,
                    cisd_level=df['sfp_cisd_level'].iloc[i] if not np.isnan(df['sfp_cisd_level'].iloc[i]) else 0,
                    confidence=0.8  # TODO: calcular basado en factores adicionales
                ))
        
        return signals
    
    def get_latest_signal(self, df: pd.DataFrame, lookback: int = 10) -> Optional[SFPSignal]:
        """
        Retorna la señal SFP más reciente en las últimas N barras
        
        Args:
            df: DataFrame procesado
            lookback: Cuántas barras hacia atrás buscar
            
        Returns:
            SFPSignal o None
        """
        recent = df.tail(lookback)
        
        for i in range(len(recent) - 1, -1, -1):
            sig = recent['sfp_signal'].iloc[i]
            if sig != 0:
                idx = len(df) - len(recent) + i
                return SFPSignal(
                    signal_type=SignalType.BULLISH if sig == 1 else SignalType.BEARISH,
                    bar_index=idx,
                    price=recent['close'].iloc[i],
                    swept_level=recent['sfp_swept_level'].iloc[i] if not np.isnan(recent['sfp_swept_level'].iloc[i]) else 0,
                    cisd_level=recent['sfp_cisd_level'].iloc[i] if not np.isnan(recent['sfp_cisd_level'].iloc[i]) else 0,
                    confidence=0.8
                )
        
        return None


# ==================== Función de conveniencia ====================

def detect_sfp(
    df: pd.DataFrame,
    pivot_len: int = 12,
    max_pivot_age: int = 50,
    patience: int = 7,
    tolerance: float = 0.7
) -> pd.DataFrame:
    """
    Función de conveniencia para detectar Swing Failure Patterns
    
    Args:
        df: DataFrame OHLCV
        pivot_len: Longitud para detección de pivots
        max_pivot_age: Máxima edad de pivots
        patience: Barras máximas para esperar CISD
        tolerance: Filtro de ruido
        
    Returns:
        DataFrame con columnas SFP añadidas
    """
    sfp = SwingFailurePattern(
        pivot_len=pivot_len,
        max_pivot_age=max_pivot_age,
        patience=patience,
        tolerance=tolerance
    )
    return sfp.calculate(df)


if __name__ == "__main__":
    # Demo con datos sintéticos
    import random
    
    # Crear datos de prueba
    np.random.seed(42)
    n = 200
    
    # Simular precio con tendencia y ruido
    price = 100.0
    data = []
    
    for i in range(n):
        volatility = random.uniform(0.5, 2.0)
        open_p = price
        high_p = open_p + random.uniform(0, volatility)
        low_p = open_p - random.uniform(0, volatility)
        close_p = random.uniform(low_p, high_p)
        
        data.append({
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': random.randint(1000, 10000)
        })
        
        price = close_p + random.uniform(-1, 1)
    
    df = pd.DataFrame(data)
    
    # Detectar SFP
    sfp = SwingFailurePattern(pivot_len=5, patience=5)  # Parámetros más sensibles para demo
    result = sfp.calculate(df)
    
    # Mostrar señales
    signals = sfp.get_signals(result)
    
    print(f"📊 Swing Failure Pattern Detector")
    print(f"{'='*50}")
    print(f"Total barras analizadas: {len(df)}")
    print(f"Señales detectadas: {len(signals)}")
    print()
    
    if signals:
        print("Últimas señales:")
        for sig in signals[-5:]:
            emoji = "🟢" if sig.signal_type == SignalType.BULLISH else "🔴"
            print(f"  {emoji} Barra {sig.bar_index}: {sig.signal_type.value.upper()} @ ${sig.price:.2f}")
            print(f"     Nivel barrido: ${sig.swept_level:.2f}")
    else:
        print("No se detectaron señales en los datos de prueba")
