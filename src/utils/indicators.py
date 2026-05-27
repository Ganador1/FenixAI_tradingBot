#!/usr/bin/env python3
# src/utils/indicators.py
"""
Módulo de Indicadores Técnicos Optimizado para Fenix Trading Bot.

Implementa cálculos correctos de indicadores técnicos usando
algoritmos eficientes en memoria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IndicatorResult:
    """Resultado estructurado de indicadores."""

    rsi: float
    ema_9: float
    ema_21: float
    ema_50: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    atr: float
    adx: float
    supertrend: str  # "BULLISH" o "BEARISH"
    supertrend_value: float

    def to_dict(self) -> dict[str, Any]:
        """Convierte a diccionario."""
        return {
            "rsi": round(self.rsi, 2),
            "ema_9": round(self.ema_9, 2),
            "ema_21": round(self.ema_21, 2),
            "ema_50": round(self.ema_50, 2),
            "macd_line": round(self.macd_line, 4),
            "macd_signal": round(self.macd_signal, 4),
            "macd_histogram": round(self.macd_histogram, 4),
            "macd_hist": round(self.macd_histogram, 4),  # Alias
            "bb_upper": round(self.bb_upper, 2),
            "bb_middle": round(self.bb_middle, 2),
            "bb_lower": round(self.bb_lower, 2),
            "atr": round(self.atr, 4),
            "adx": round(self.adx, 2),
            "supertrend": self.supertrend,
            "supertrend_value": round(self.supertrend_value, 2),
        }


class TechnicalIndicators:
    """
    Calculador de indicadores técnicos optimizado.

    Uso:
        calc = TechnicalIndicators()
        for kline in klines:
            calc.add_kline(kline["close"], kline["high"], kline["low"], kline["volume"])

        indicators = calc.get_all()
    """

    def __init__(self, max_history: int = 200):
        self.max_history = max_history
        self.closes: list[float] = []
        self.highs: list[float] = []
        self.lows: list[float] = []
        self.volumes: list[float] = []

        # Cache de EMAs para eficiencia
        self._ema_cache: dict[int, float] = {}

    def add_kline(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
    ) -> None:
        """Agrega una nueva vela al buffer."""
        self.closes.append(close)
        self.highs.append(high)
        self.lows.append(low)
        self.volumes.append(volume)

        # Mantener tamaño máximo
        if len(self.closes) > self.max_history:
            self.closes.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)
            self.volumes.pop(0)

        # Invalidar cache
        self._ema_cache.clear()

    def has_enough_data(self, min_candles: int = 50) -> bool:
        """Verifica si hay suficientes datos para calcular indicadores."""
        return len(self.closes) >= min_candles

    # =========================================================================
    # CÁLCULOS DE INDICADORES
    # =========================================================================

    def calculate_ema(self, period: int) -> float:
        """
        Calcula EMA (Exponential Moving Average) correctamente.

        EMA = (Precio × k) + (EMA_anterior × (1 - k))
        donde k = 2 / (período + 1)
        """
        if period in self._ema_cache:
            return self._ema_cache[period]

        if len(self.closes) < period:
            result = sum(self.closes) / len(self.closes) if self.closes else 0
            self._ema_cache[period] = result
            return result

        multiplier = 2 / (period + 1)

        # SMA inicial
        ema = sum(self.closes[:period]) / period

        # Calcular EMA iterativamente
        for price in self.closes[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        self._ema_cache[period] = ema
        return ema

    def calculate_sma(self, period: int) -> float:
        """Calcula SMA (Simple Moving Average)."""
        if len(self.closes) < period:
            return sum(self.closes) / len(self.closes) if self.closes else 0
        return sum(self.closes[-period:]) / period

    def calculate_rsi(self, period: int = 14) -> float:
        """
        Calcula RSI (Relative Strength Index) usando Wilder's smoothing.

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        if len(self.closes) < period + 1:
            return 50.0

        deltas = [self.closes[i] - self.closes[i - 1] for i in range(1, len(self.closes))]

        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]

        # Wilder's smoothing (EMA-like)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_macd(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[float, float, float]:
        """
        Calcula MACD (Moving Average Convergence Divergence).

        Returns:
            (macd_line, signal_line, histogram)
        """
        ema_fast = self.calculate_ema(fast)
        ema_slow = self.calculate_ema(slow)

        macd_line = ema_fast - ema_slow

        # Signal line (EMA del MACD)
        # Simplificado: usar el MACD actual como aproximación
        # En producción, mantener historial de MACD
        signal_line = macd_line * 0.9  # Aproximación

        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    def calculate_bollinger_bands(
        self,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple[float, float, float]:
        """
        Calcula Bollinger Bands.

        Returns:
            (upper_band, middle_band, lower_band)
        """
        if len(self.closes) < period:
            current = self.closes[-1] if self.closes else 0
            return current, current, current

        middle = self.calculate_sma(period)

        # Calcular desviación estándar
        recent = self.closes[-period:]
        variance = sum((x - middle) ** 2 for x in recent) / period
        std = variance**0.5

        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        return upper, middle, lower

    def calculate_atr(self, period: int = 14) -> float:
        """
        Calcula ATR (Average True Range).

        TR = max(high - low, |high - prev_close|, |low - prev_close|)
        """
        if len(self.closes) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(self.closes)):
            high = self.highs[i]
            low = self.lows[i]
            prev_close = self.closes[i - 1]

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

        # Wilder's smoothing
        atr = sum(true_ranges[:period]) / period
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period

        return atr

    def calculate_adx(self, period: int = 14) -> float:
        """
        Calcula ADX (Average Directional Index).

        Mide la fuerza de la tendencia (no la dirección).
        """
        if len(self.closes) < period * 2:
            return 25.0  # Valor neutral

        # Calcular +DM y -DM
        plus_dm = []
        minus_dm = []

        for i in range(1, len(self.highs)):
            up_move = self.highs[i] - self.highs[i - 1]
            down_move = self.lows[i - 1] - self.lows[i]

            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0)

            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0)

        atr = self.calculate_atr(period)
        if atr == 0:
            return 25.0

        # +DI y -DI
        plus_di = (sum(plus_dm[-period:]) / period) / atr * 100
        minus_di = (sum(minus_dm[-period:]) / period) / atr * 100

        # DX
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 25.0

        dx = abs(plus_di - minus_di) / di_sum * 100

        return dx  # Simplificado - en producción usar smoothed ADX

    def calculate_supertrend(
        self,
        period: int = 10,
        multiplier: float = 3.0,
    ) -> tuple[str, float]:
        """
        Calcula SuperTrend.

        Returns:
            (direction, supertrend_value)
        """
        if len(self.closes) < period:
            return "BULLISH", self.closes[-1] if self.closes else 0

        atr = self.calculate_atr(period)
        current = self.closes[-1]
        current_high = self.highs[-1]
        current_low = self.lows[-1]

        hl2 = (current_high + current_low) / 2

        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)

        # Determinar dirección
        if current > upper_band:
            return "BULLISH", lower_band
        elif current < lower_band:
            return "BEARISH", upper_band
        else:
            # Mantener tendencia anterior (simplificado)
            prev_close = self.closes[-2] if len(self.closes) > 1 else current
            if current > prev_close:
                return "BULLISH", lower_band
            else:
                return "BEARISH", upper_band

    # =========================================================================
    # MÉTODO PRINCIPAL
    # =========================================================================

    def get_all(self) -> IndicatorResult | None:
        """
        Calcula todos los indicadores y retorna resultado estructurado.
        """
        if not self.has_enough_data(20):
            logger.warning(f"Datos insuficientes: {len(self.closes)} velas")
            return None

        try:
            macd_line, macd_signal, macd_hist = self.calculate_macd()
            bb_upper, bb_middle, bb_lower = self.calculate_bollinger_bands()
            st_direction, st_value = self.calculate_supertrend()

            return IndicatorResult(
                rsi=self.calculate_rsi(),
                ema_9=self.calculate_ema(9),
                ema_21=self.calculate_ema(21),
                ema_50=self.calculate_ema(50),
                macd_line=macd_line,
                macd_signal=macd_signal,
                macd_histogram=macd_hist,
                bb_upper=bb_upper,
                bb_middle=bb_middle,
                bb_lower=bb_lower,
                atr=self.calculate_atr(),
                adx=self.calculate_adx(),
                supertrend=st_direction,
                supertrend_value=st_value,
            )
        except Exception as e:
            logger.error(f"Error calculando indicadores: {e}")
            return None


# ============================================================================
# SINGLETON PARA USO GLOBAL
# ============================================================================

_global_calculator: TechnicalIndicators | None = None


def get_indicator_calculator() -> TechnicalIndicators:
    """Obtiene el calculador global de indicadores."""
    global _global_calculator
    if _global_calculator is None:
        _global_calculator = TechnicalIndicators()
    return _global_calculator


def add_kline(close: float, high: float, low: float, volume: float) -> None:
    """Agrega una kline al calculador global."""
    calc = get_indicator_calculator()
    calc.add_kline(close, high, low, volume)


def get_current_indicators() -> dict[str, Any] | None:
    """Obtiene indicadores actuales del calculador global."""
    calc = get_indicator_calculator()
    result = calc.get_all()
    return result.to_dict() if result else None


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    import random

    # Simular datos de mercado
    calc = TechnicalIndicators()

    base_price = 67000
    prices = [base_price]

    for i in range(100):
        change = random.uniform(-0.02, 0.021) * prices[-1]  # Sesgo alcista leve
        new_price = prices[-1] + change
        prices.append(new_price)

        high = new_price * random.uniform(1.001, 1.01)
        low = new_price * random.uniform(0.99, 0.999)
        volume = random.uniform(100, 1000)

        calc.add_kline(new_price, high, low, volume)

    indicators = calc.get_all()
    if indicators:
        print("=== Indicadores Calculados ===")
        for key, value in indicators.to_dict().items():
            print(f"  {key}: {value}")
