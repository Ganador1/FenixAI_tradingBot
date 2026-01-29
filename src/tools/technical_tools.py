# tools/technical_tools.py
from __future__ import annotations
from collections import deque
from typing import Dict, Deque, List, Optional, Any, Tuple
import logging
import threading
import time
import numpy as np

logger = logging.getLogger(__name__)

try:
    import talib  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    talib = None
    logger.warning(
        "TA-Lib not available. Falling back to simplified numpy implementations"
    )

# --- ADVANCED LIBRARY IMPORTS ---
ta_volume_available = False
arch_available = False
pd = None
chaikin_money_flow = None
arch_model = None
try:
    from ta.volume import chaikin_money_flow as _cmf
    import pandas as _pd
    ta_volume_available = True
    chaikin_money_flow = _cmf
    pd = _pd
except Exception:
    ta_volume_available = False
    logger.warning("ta.volume or pandas not available. CMF will not be calculated.")
try:
    from arch import arch_model as _arch_model  # type: ignore
    arch_available = True
    arch_model = _arch_model
except Exception:
    arch_available = False
    logger.warning("arch not available. GARCH volatility will not be calculated.")

MAXLEN: int = 300 # Max length for primary data buffers (increased for MA 200)
MIN_CANDLES_FOR_CALC = 20 # Minimum candles needed before attempting TA-Lib calculations (some need more, e.g., MA50)
# TA-Lib functions often require a certain minimum number of data points.
# For example, SMA(timeperiod=N) needs at least N points.
# BBANDS(timeperiod=20) needs at least 20.
# RSI(timeperiod=14) needs at least 14+1 = 15.
# ADX(timeperiod=14) needs 2*14-1 = 27.
# ATR(timeperiod=14) needs 14.
# Set MIN_CANDLES_FOR_CALC to a value that covers most common periods, e.g., 30-50.
# Let's use 30 as a general minimum for this set of indicators.
# MA50 will only calculate if len >= 50.
MIN_CANDLES_FOR_RELIABLE_CALC = 30 # Stricter minimum for reliable indicator calculation output

_buffer_lock = threading.RLock()

# Primary data buffers
close_buf: Deque[float] = deque(maxlen=MAXLEN)
high_buf: Deque[float] = deque(maxlen=MAXLEN)
low_buf: Deque[float] = deque(maxlen=MAXLEN)
vol_buf: Deque[float] = deque(maxlen=MAXLEN)
open_buf: Deque[float] = deque(maxlen=MAXLEN)  # Added for chart generation
timestamp_buf: Deque[int] = deque(maxlen=MAXLEN)  # Unix timestamp in milliseconds

# Buffers for calculated indicator values (stores only the latest value)
# These are updated by _calculate_and_store_all_indicators
# For sequences, we'll slice from the primary buffers or re-calculate on demand.
_latest_indicators_cache: Dict[str, Any] = {}
# Rate-limited indicator warning timestamps (indicator_name -> last_log_time)
_indicator_warning_timestamps: Dict[str, float] = {}
_INDICATOR_WARNING_COOLDOWN = 300  # seconds


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Simple exponential moving average implementation."""
    if len(values) < period:
        return np.array([])
    weights = 2 / (period + 1)
    ema = np.zeros_like(values, dtype=float)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = (values[i] - ema[i - 1]) * weights + ema[i - 1]
    return ema


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    if len(values) < period + 1:
        return np.array([])
    diff = np.diff(values)
    gain = np.maximum(diff, 0)
    loss = np.abs(np.minimum(diff, 0))
    avg_gain = np.convolve(gain, np.ones(period), 'valid') / period
    avg_loss = np.convolve(loss, np.ones(period), 'valid') / period
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0)
    rsi = 100 - (100 / (1 + rs))
    # pad result to match input length
    return np.concatenate([np.full(period, np.nan), rsi])


def _macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray]:
    if len(values) < slow + signal:
        return np.array([]), np.array([])
    ema_fast = _ema(values, fast)
    ema_slow = _ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line[slow - 1 :], signal)
    macd_line = macd_line[(slow - 1) :]
    signal_line = signal_line[-len(macd_line) :]
    return macd_line, signal_line


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    if len(close) < period + 1:
        return np.array([])
    high_low = high[1:] - low[1:]
    high_close = np.abs(high[1:] - close[:-1])
    low_close = np.abs(low[1:] - close[:-1])
    tr = np.maximum.reduce([high_low, high_close, low_close])
    atr = np.zeros_like(high, dtype=float)
    atr[period] = np.mean(tr[:period])
    for i in range(period + 1, len(high)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i - 1]) / period
    return atr

# Buffers for indicator sequences (if you want to store sequences of indicators)
# This can consume more memory. Alternatively, calculate sequences on-demand.
# For now, let's keep them if LLM4FTS or other consumers need sequences.
rsi_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
macd_line_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
macd_signal_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
adx_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
atr_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
# obv_seq_buf: Deque[float] = deque(maxlen=MAXLEN) # OBV is cumulative, storing sequence might be less useful than latest
# ma50_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
# upper_band_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
# middle_band_seq_buf: Deque[float] = deque(maxlen=MAXLEN)
# lower_band_seq_buf: Deque[float] = deque(maxlen=MAXLEN)


def _validate_float(value: Any, name: str, positive: bool = False, non_negative: bool = False) -> Optional[float]:
    """Helper to validate and convert a value to float."""
    if not isinstance(value, (int, float)):
        logger.warning(f"Invalid type for {name}: {type(value)}. Expected float or int.")
        return None
    f_value = float(value)
    if np.isnan(f_value) or np.isinf(f_value):
        logger.warning(f"Invalid numeric value for {name}: {f_value}.")
        return None
    if positive and f_value <= 0:
        logger.warning(f"{name} must be positive: {f_value}.")
        return None
    if non_negative and f_value < 0:
        logger.warning(f"{name} must be non-negative: {f_value}.")
        return None
    return f_value

def validate_kline_data(close: float, high: float, low: float, volume: float) -> bool:
    """Validates k-line data components."""
    c = _validate_float(close, "close", positive=True)
    h = _validate_float(high, "high", positive=True)
    lo = _validate_float(low, "low", positive=True)
    v = _validate_float(volume, "volume", non_negative=True)

    if None in [c, h, lo, v]: # Type or basic value error
        return False
    
    # Ensure type hinting knows these are floats now
    # Type narrowing for static checkers
    assert c is not None and h is not None and lo is not None and v is not None
    c, h, lo = float(c), float(h), float(lo)

    if not (lo <= c <= h and lo <= h): # Logical consistency
        logger.warning(f"OHLC inconsistency: L({lo}) <= C({c}) <= H({h}) failed.")
        return False
    return True

def add_kline(close: float, high: float, low: float, volume: float, 
              open_price: Optional[float] = None, timestamp: Optional[int] = None) -> bool:
    """
    Adds a new k-line data point to the buffers and recalculates indicators.
    Thread-safe. Returns True if successful, False otherwise.
    
    Args:
        close: Close price
        high: High price
        low: Low price
        volume: Volume
        open_price: Open price (optional, will be synthesized from previous close if not provided)
        timestamp: Unix timestamp in milliseconds (optional, will use current time if not provided)
    """
    if not validate_kline_data(close, high, low, volume):
        logger.error(f"Invalid k-line data provided to add_kline: C={close}, H={high}, L={low}, V={volume}")
        return False
    
    # Ensure values are float after validation
    close_f, high_f, low_f, vol_f = float(close), float(high), float(low), float(volume)
    
    # Handle open price - synthesize from previous close if not provided
    if open_price is not None:
        open_f = float(open_price)
    elif len(close_buf) > 0:
        open_f = float(close_buf[-1])  # Use previous close as open
    else:
        open_f = close_f  # First candle: open = close
    
    # Handle timestamp - use current time if not provided
    if timestamp is not None:
        ts = int(timestamp)
    else:
        import time as time_module
        ts = int(time_module.time() * 1000)

    with _buffer_lock:
        close_buf.append(close_f)
        high_buf.append(high_f)
        low_buf.append(low_f)
        vol_buf.append(vol_f)
        open_buf.append(open_f)
        timestamp_buf.append(ts)

        # Recalculate all indicators and update cache if enough data
        if len(close_buf) >= MIN_CANDLES_FOR_CALC: # Use a less strict minimum for attempting calculation
            _calculate_and_store_all_indicators()
        else:
            # Not enough data yet, clear stale cache
            _latest_indicators_cache.clear()
            logger.debug(f"Not enough candles ({len(close_buf)}/{MIN_CANDLES_FOR_CALC}) to calculate indicators yet.")
        return True

def _calculate_and_store_all_indicators() -> None:
    """
    Internal function to calculate all TA indicators and store their latest values.
    Assumes _buffer_lock is already acquired.
    """
    global _latest_indicators_cache # Modifying global cache

    current_len = len(close_buf)
    if current_len < MIN_CANDLES_FOR_CALC: # Double check, though called after initial check
        return

    # Prepare numpy arrays from deques
    # Slicing deques: list(d)[-N:] is inefficient. Convert to numpy array once.
    close_arr = np.array(close_buf, dtype=np.float64)
    high_arr = np.array(high_buf, dtype=np.float64)
    low_arr = np.array(low_buf, dtype=np.float64)
    vol_arr = np.array(vol_buf, dtype=np.float64)

    temp_cache: Dict[str, Any] = {}

    # Helper to safely get last valid value from a TA-Lib output array
    def get_last_valid(arr: np.ndarray, name: str) -> Optional[float]:
        if arr is not None and len(arr) > 0:
            val = arr[-1]
            if val is not None and np.isfinite(val): # Checks for NaN and Inf
                return float(val)
            else:
                # Rate-limit repeated warnings to keep logs readable
                now = time.time()
                last_ts = _indicator_warning_timestamps.get(name, 0)
                if now - last_ts > _INDICATOR_WARNING_COOLDOWN:
                    logger.warning("Indicator '%s' last value is invalid (%s).", name, val)
                    _indicator_warning_timestamps[name] = now
                return None
        logger.warning(f"No valid value for {name}")
        return None

    # RSI
    if current_len >= 14 + 1:
        try:
            if talib:
                rsi_val = get_last_valid(talib.RSI(close_arr, timeperiod=14), "RSI")
            else:
                rsi_val = get_last_valid(_rsi(close_arr, 14), "RSI")
            
            if rsi_val is not None:
                temp_cache["rsi"] = rsi_val
        except Exception as e:
            logger.warning(f"Error calculating RSI: {e}")
    
    # MACD
    if current_len >= 26 + 9 -1:
        try:
            if talib:
                macd, macdsignal, _ = talib.MACD(close_arr, fastperiod=12, slowperiod=26, signalperiod=9)
            else:
                macd, macdsignal = _macd(close_arr, 12, 26, 9)
            
            macd_line_val = get_last_valid(macd, "MACD Line")
            if macd_line_val is not None:
                temp_cache["macd_line"] = macd_line_val
            
            signal_line_val = get_last_valid(macdsignal, "MACD Signal")
            if signal_line_val is not None:
                temp_cache["signal_line"] = signal_line_val
        except Exception as e:
            logger.warning(f"Error calculating MACD: {e}")

    # Bollinger Bands
    if current_len >= 20 and talib:
        try:
            # Llamada sin matype para máxima compatibilidad
            upper, middle, lower = talib.BBANDS(close_arr, timeperiod=20, nbdevup=2, nbdevdn=2)
            
            upper_val = get_last_valid(upper, "BB Upper")
            middle_val = get_last_valid(middle, "BB Middle")
            lower_val = get_last_valid(lower, "BB Lower")
            current_price = close_arr[-1]
            
            if upper_val is not None:
                temp_cache["upper_band"] = upper_val
            if middle_val is not None:
                temp_cache["middle_band"] = middle_val
            if lower_val is not None:
                temp_cache["lower_band"] = lower_val
                
            # Calcular percent_b y bandwidth si tenemos todas las bandas
            # Inicializar valores por defecto
            percent_b = 0.5
            bandwidth = None
            if upper_val is not None and middle_val is not None and lower_val is not None:
                # Percent B: posición del precio dentro de las bandas
                if upper_val > lower_val:
                    percent_b = (current_price - lower_val) / (upper_val - lower_val)
                # Bandwidth: ancho de las bandas relativo al precio medio
                if middle_val > 0:
                    bandwidth = (upper_val - lower_val) / middle_val
            temp_cache["percent_b"] = float(percent_b)
            if bandwidth is not None:
                temp_cache["bandwidth"] = float(bandwidth)
                temp_cache["bandwidth_pct"] = float(bandwidth * 100)
                
                # Squeeze detection: bandwidth menor al percentil 20 de los últimos 20 períodos
                if len(close_buf) >= 20:
                    # Calcular bandwidth histórico para detectar squeeze
                    historical_bandwidths = []
                    for i in range(max(0, len(close_buf) - 20), len(close_buf)):
                        if i >= 19:  # Necesitamos al menos 20 puntos para BB
                            hist_close = np.array(list(close_buf)[max(0, i-19):i+1])
                            if len(hist_close) >= 20:
                                try:
                                    hist_upper, hist_middle, hist_lower = talib.BBANDS(hist_close, timeperiod=20, nbdevup=2, nbdevdn=2)
                                    if (hist_upper[-1] is not None and hist_middle[-1] is not None and 
                                        hist_lower[-1] is not None and hist_middle[-1] > 0):
                                        hist_bw = (hist_upper[-1] - hist_lower[-1]) / hist_middle[-1]
                                        historical_bandwidths.append(hist_bw)
                                except Exception:
                                    continue
                    
                    if historical_bandwidths:
                        squeeze_threshold = np.percentile(historical_bandwidths, 20)
                        temp_cache["squeeze_status"] = bool(bandwidth is not None and bandwidth < squeeze_threshold)
                        temp_cache["bollinger_squeeze"] = bool(bandwidth is not None and bandwidth < squeeze_threshold)
                    else:
                        temp_cache["squeeze_status"] = False
                        temp_cache["bollinger_squeeze"] = False
                
                # Band position
                if percent_b <= 0:
                    temp_cache["band_position"] = "BELOW_LOWER"
                elif percent_b >= 1:
                    temp_cache["band_position"] = "ABOVE_UPPER"
                elif percent_b < 0.2:
                    temp_cache["band_position"] = "LOWER"
                elif percent_b > 0.8:
                    temp_cache["band_position"] = "UPPER"
                else:
                    temp_cache["band_position"] = "MIDDLE"
                    
                temp_cache["bollinger_position"] = temp_cache["band_position"]
                
        except Exception as e:
            logger.warning(f"Error calculating Bollinger Bands: {e}")

    # MA50
    if current_len >= 50 and talib:
        try:
            temp_cache["ma50"] = get_last_valid(talib.SMA(close_arr, timeperiod=50), "MA50")
        except Exception as e:
            logger.warning(f"Error calculating MA50: {e}")
    
    # ADX
    if talib and current_len >= 14 * 2 - 1 and np.all(np.isfinite(high_arr)) and np.all(np.isfinite(low_arr)) and np.all(np.isfinite(close_arr)):
        try:
            adx_val = get_last_valid(talib.ADX(high_arr, low_arr, close_arr, timeperiod=14), "ADX")
            if adx_val is not None:
                temp_cache["adx"] = adx_val
            # PLUS_DI y MINUS_DI (DMI)
            pdi_val = get_last_valid(talib.PLUS_DI(high_arr, low_arr, close_arr, timeperiod=14), "PLUS_DI")
            mdi_val = get_last_valid(talib.MINUS_DI(high_arr, low_arr, close_arr, timeperiod=14), "MINUS_DI")
            if pdi_val is not None:
                temp_cache["plus_di"] = pdi_val
            if mdi_val is not None:
                temp_cache["minus_di"] = mdi_val
        except Exception as e:
            logger.warning(f"Error calculating ADX: {e}")

    # OBV
    if talib:
        try:
            temp_cache["obv"] = get_last_valid(talib.OBV(close_arr, vol_arr), "OBV")
        except Exception as e:
            logger.warning(f"Error calculating OBV: {e}")
    
    # ATR
    if current_len >= 14 + 1:
        try:
            if talib:
                temp_cache["atr"] = get_last_valid(talib.ATR(high_arr, low_arr, close_arr, timeperiod=14), "ATR")
            else:
                temp_cache["atr"] = get_last_valid(_atr(high_arr, low_arr, close_arr, 14), "ATR")
        except Exception as e:
            logger.warning(f"Error calculating ATR: {e}")

    # --- ADVANCED INDICATORS ADDED FOR FULL AGENT SUPPORT ---
    # Aroon (Up/Down)
    if talib and current_len >= 25 and np.all(np.isfinite(high_arr)) and np.all(np.isfinite(low_arr)):
        try:
            aroon_down, aroon_up = talib.AROON(high_arr, low_arr, timeperiod=25)
            aroon_up_val = get_last_valid(aroon_up, "Aroon Up")
            if aroon_up_val is not None:
                temp_cache["aroon_up"] = aroon_up_val
            aroon_down_val = get_last_valid(aroon_down, "Aroon Down")
            if aroon_down_val is not None:
                temp_cache["aroon_down"] = aroon_down_val
        except Exception as e:
            logger.warning(f"Error calculating Aroon: {e}")
    # Parabolic SAR
    if talib and current_len >= 5:
        try:
            parabolic_sar = talib.SAR(high_arr, low_arr, acceleration=0.02, maximum=0.2)
            temp_cache["parabolic_sar"] = get_last_valid(parabolic_sar, "Parabolic SAR")
        except Exception as e:
            logger.warning(f"Error calculating Parabolic SAR: {e}")
    # Ichimoku (tenkan, kijun, senkouA, senkouB, chikou)
    if talib and current_len >= 52:
        try:
            # Tenkan-sen (Conversion Line)
            period9_high = np.max(high_arr[-9:]) if current_len >= 9 else np.nan
            period9_low = np.min(low_arr[-9:]) if current_len >= 9 else np.nan
            tenkan_sen = (period9_high + period9_low) / 2.0
            temp_cache["ichimoku_tenkan"] = float(tenkan_sen)
            # Kijun-sen (Base Line)
            period26_high = np.max(high_arr[-26:]) if current_len >= 26 else np.nan
            period26_low = np.min(low_arr[-26:]) if current_len >= 26 else np.nan
            kijun_sen = (period26_high + period26_low) / 2.0
            temp_cache["ichimoku_kijun"] = float(kijun_sen)
            # Senkou Span A
            senkou_a = (tenkan_sen + kijun_sen) / 2.0
            temp_cache["ichimoku_senkou_a"] = float(senkou_a)
            # Senkou Span B
            period52_high = np.max(high_arr[-52:]) if current_len >= 52 else np.nan
            period52_low = np.min(low_arr[-52:]) if current_len >= 52 else np.nan
            senkou_b = (period52_high + period52_low) / 2.0
            temp_cache["ichimoku_senkou_b"] = float(senkou_b)
            # Chikou Span (Lagging)
            chikou_span = close_arr[-26] if current_len >= 26 else np.nan
            temp_cache["ichimoku_chikou"] = float(chikou_span)
        except Exception as e:
            logger.warning(f"Error calculating Ichimoku: {e}")
    # Chaikin Money Flow (CMF)
    if ta_volume_available and pd is not None and chaikin_money_flow is not None and current_len >= 21:
        try:
            df = pd.DataFrame({"high": high_arr, "low": low_arr, "close": close_arr, "volume": vol_arr})
            cmf_val = chaikin_money_flow(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                volume=df["volume"],
                window=21,
                fillna=False
            )
            last_cmf = cmf_val.iloc[-1] if not np.isnan(cmf_val.iloc[-1]) else None
            if last_cmf is not None:
                temp_cache["cmf"] = float(last_cmf)
        except Exception as e:
            logger.warning(f"Error calculating CMF: {e}")
    # GARCH (volatility forecast)
    if arch_available and arch_model is not None and current_len >= 30:
        try:
            returns = np.diff(np.log(close_arr)) * 100  # percent returns
            # Rescaling automático si la escala es muy baja
            max_abs_return = np.max(np.abs(returns)) if len(returns) > 0 else 0
            if max_abs_return < 1:
                returns = returns * 10
            am = arch_model(returns, vol='GARCH', p=1, q=1, dist='normal', rescale=False)
            res = am.fit(disp='off')
            forecast = res.forecast(horizon=1)
            garch_vol = np.sqrt(forecast.variance.values[-1, :][0])
            temp_cache["garch_volatility_forecast"] = float(garch_vol)
        except Exception as e:
            logger.warning(f"Error calculating GARCH volatility: {e}")
    # VPVR (POC and Value Area, simple histogram approach)
    if current_len >= 30:
        try:
            price_bins = np.histogram(close_arr, bins=20, weights=vol_arr)
            bin_edges = price_bins[1]
            vol_hist = price_bins[0]
            poc_idx = np.argmax(vol_hist)
            poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx+1]) / 2.0
            temp_cache["vpvr_poc"] = float(poc_price)
            total_vol = np.sum(vol_hist)
            sorted_idx = np.argsort(vol_hist)[::-1]
            cum_vol = 0.0
            value_area_prices = []
            for idx in sorted_idx:
                cum_vol += vol_hist[idx]
                value_area_prices.append((bin_edges[idx] + bin_edges[idx+1]) / 2.0)
                if cum_vol / total_vol >= 0.7:
                    break
            if value_area_prices:
                temp_cache["vpvr_value_area_high"] = float(max(value_area_prices))
                temp_cache["vpvr_value_area_low"] = float(min(value_area_prices))
        except Exception as e:
            logger.warning(f"Error calculating VPVR: {e}")
    
    # Update indicator sequence buffers (optional, if needed elsewhere)
    if temp_cache.get("rsi") is not None and isinstance(temp_cache["rsi"], float):
        rsi_seq_buf.append(temp_cache["rsi"])
    if temp_cache.get("macd_line") is not None and isinstance(temp_cache["macd_line"], float):
        macd_line_seq_buf.append(temp_cache["macd_line"])
    if temp_cache.get("signal_line") is not None and isinstance(temp_cache["signal_line"], float):
        macd_signal_seq_buf.append(temp_cache["signal_line"])
    if temp_cache.get("adx") is not None and isinstance(temp_cache["adx"], float):
        adx_seq_buf.append(temp_cache["adx"])
    if temp_cache.get("atr") is not None and isinstance(temp_cache["atr"], float):
        atr_seq_buf.append(temp_cache["atr"])
    
    # NOTE: Cache will be updated at the end of the method after all indicators are calculated

    # EMA 9
    if current_len >= 9:
        try:
            if talib:
                temp_cache["ema_9"] = get_last_valid(talib.EMA(close_arr, timeperiod=9), "EMA 9")
            else:
                temp_cache["ema_9"] = get_last_valid(_ema(close_arr, 9), "EMA 9")
        except Exception as e:
            logger.warning(f"Error calculating EMA 9: {e}")
    
    # EMA 20 (faltante)
    if current_len >= 20:
        try:
            if talib:
                temp_cache["ema_20"] = get_last_valid(talib.EMA(close_arr, timeperiod=20), "EMA 20")
            else:
                temp_cache["ema_20"] = get_last_valid(_ema(close_arr, 20), "EMA 20")
        except Exception as e:
            logger.warning(f"Error calculating EMA 20: {e}")
    
    # EMA 21
    if current_len >= 21:
        try:
            if talib:
                temp_cache["ema_21"] = get_last_valid(talib.EMA(close_arr, timeperiod=21), "EMA 21")
            else:
                temp_cache["ema_21"] = get_last_valid(_ema(close_arr, 21), "EMA 21")
        except Exception as e:
            logger.warning(f"Error calculating EMA 21: {e}")
    
    # SMA 20 (faltante)
    if current_len >= 20:
        try:
            if talib:
                temp_cache["sma_20"] = get_last_valid(talib.SMA(close_arr, timeperiod=20), "SMA 20")
            else:
                temp_cache["sma_20"] = float(np.mean(close_arr[-20:]))
        except Exception as e:
            logger.warning(f"Error calculating SMA 20: {e}")
    
    # MA 50 (faltante)
    if current_len >= 50:
        try:
            if talib:
                temp_cache["ma_50"] = get_last_valid(talib.SMA(close_arr, timeperiod=50), "MA 50")
            else:
                temp_cache["ma_50"] = float(np.mean(close_arr[-50:]))
        except Exception as e:
            logger.warning(f"Error calculating MA 50: {e}")
    
    # MA 200 (faltante)
    if current_len >= 200:
        try:
            if talib:
                temp_cache["ma_200"] = get_last_valid(talib.SMA(close_arr, timeperiod=200), "MA 200")
            else:
                temp_cache["ma_200"] = float(np.mean(close_arr[-200:]))
        except Exception as e:
            logger.warning(f"Error calculating MA 200: {e}")
    
    # Williams %R (faltante - OSCILLATORS)
    if current_len >= 14:
        try:
            if talib:
                temp_cache["williams_r"] = get_last_valid(talib.WILLR(high_arr, low_arr, close_arr, timeperiod=14), "Williams %R")
            else:
                # Implementación manual de Williams %R
                if len(high_arr) >= 14 and len(low_arr) >= 14:
                    highest_high = np.max(high_arr[-14:])
                    lowest_low = np.min(low_arr[-14:])
                    current_close = close_arr[-1]
                    if highest_high != lowest_low:
                        williams_r = ((highest_high - current_close) / (highest_high - lowest_low)) * -100
                        temp_cache["williams_r"] = float(williams_r)
                else:
                    logger.debug("Not enough data for manual Williams %R")
        except Exception as e:
            logger.warning(f"Error calculating Williams %R: {e}")
    
    # CCI - Commodity Channel Index (faltante - OSCILLATORS)
    if current_len >= 20:
        try:
            if talib:
                temp_cache["cci"] = get_last_valid(talib.CCI(high_arr, low_arr, close_arr, timeperiod=20), "CCI")
            else:
                # Implementación manual de CCI
                typical_prices = (high_arr + low_arr + close_arr) / 3
                sma_tp = np.mean(typical_prices[-20:])
                mean_deviation = np.mean(np.abs(typical_prices[-20:] - sma_tp))
                if mean_deviation != 0:
                    cci = (typical_prices[-1] - sma_tp) / (0.015 * mean_deviation)
                    temp_cache["cci"] = float(cci)
        except Exception as e:
            logger.warning(f"Error calculating CCI: {e}")
    
    # ROC - Rate of Change (faltante - OSCILLATORS)
    if current_len >= 10:
        try:
            if talib:
                temp_cache["roc"] = get_last_valid(talib.ROC(close_arr, timeperiod=10), "ROC")
            else:
                # Implementación manual de ROC
                current_price = close_arr[-1]
                past_price = close_arr[-10]
                if past_price != 0:
                    roc = ((current_price - past_price) / past_price) * 100
                    temp_cache["roc"] = float(roc)
        except Exception as e:
            logger.warning(f"Error calculating ROC: {e}")
    
    # Stochastic Oscillator (faltante - MOMENTUM)
    if current_len >= 14:
        try:
            if talib:
                slowk, slowd = talib.STOCH(
                    high_arr, low_arr, close_arr,
                    fastk_period=14, slowk_period=3,
                    slowd_period=3
                )
                temp_cache["stoch_k"] = get_last_valid(slowk, "Stoch %K")
                temp_cache["stoch_d"] = get_last_valid(slowd, "Stoch %D")
            else:
                # Implementación manual de Stochastic
                if len(low_arr) >= 14 and len(high_arr) >= 14 and len(close_arr) > 0:
                    lowest_low = np.min(low_arr[-14:])
                    highest_high = np.max(high_arr[-14:])
                    current_close = close_arr[-1]
                    if highest_high != lowest_low:
                        k_percent = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
                        temp_cache["stoch_k"] = float(k_percent)
                        # %D es una media móvil de %K (simplificado)
                        if len(close_arr) >= 17:  # Necesitamos 3 valores de %K
                            k_values = []
                            for i in range(3):
                                idx = -(i+1)
                                # Ensure indices are valid
                                if abs(idx-13) <= len(low_arr) and abs(idx) <= len(high_arr):
                                    period_low = np.min(low_arr[idx-13:idx+1])
                                    period_high = np.max(high_arr[idx-13:idx+1])
                                    if period_high != period_low:
                                        k_val = ((close_arr[idx] - period_low) / (period_high - period_low)) * 100
                                        k_values.append(k_val)
                            if k_values:
                                temp_cache["stoch_d"] = float(np.mean(k_values))
                else:
                    logger.debug("Not enough data for manual Stochastic calculation")
        except Exception as e:
            logger.warning(f"Error calculating Stochastic: {e}")
    
    # MACD completo (faltante - MOMENTUM)
    if current_len >= 26:
        try:
            if talib:
                macd_line, macd_signal, macd_hist = talib.MACD(close_arr, fastperiod=12, slowperiod=26, signalperiod=9)
                temp_cache["macd"] = get_last_valid(macd_line, "MACD Line")
                temp_cache["macd_histogram"] = get_last_valid(macd_hist, "MACD Histogram")
                # macd_signal ya existe como signal_line, pero agregar alias
                if get_last_valid(macd_signal, "MACD Signal") is not None:
                    temp_cache["macd_signal"] = get_last_valid(macd_signal, "MACD Signal")
            else:
                # Usar implementación existente _macd
                macd_result = _macd(close_arr, 12, 26, 9)
                if macd_result and len(macd_result) >= 3:
                    temp_cache["macd"] = macd_result[0]
                    temp_cache["macd_signal"] = macd_result[1]
                    temp_cache["macd_histogram"] = macd_result[2]
        except Exception as e:
            logger.warning(f"Error calculating complete MACD: {e}")
    
    # Bollinger Bands completas (faltante - VOLATILITY)
    if current_len >= 20:
        try:
            if talib:
                bb_upper, bb_middle, bb_lower = talib.BBANDS(close_arr, timeperiod=20, nbdevup=2, nbdevdn=2)
                temp_cache["bollinger_upper"] = get_last_valid(bb_upper, "Bollinger Upper")
                temp_cache["bollinger_middle"] = get_last_valid(bb_middle, "Bollinger Middle")
                temp_cache["bollinger_lower"] = get_last_valid(bb_lower, "Bollinger Lower")
                
                # Calcular ancho de bandas
                upper_val = get_last_valid(bb_upper, "Bollinger Upper")
                lower_val = get_last_valid(bb_lower, "Bollinger Lower")
                middle_val = get_last_valid(bb_middle, "Bollinger Middle")
                
                if upper_val is not None and lower_val is not None and middle_val is not None:
                    temp_cache["bollinger_width"] = float((upper_val - lower_val) / middle_val * 100)
            else:
                # Implementación manual de Bollinger Bands
                sma_20 = np.mean(close_arr[-20:])
                std_20 = np.std(close_arr[-20:])
                
                temp_cache["bollinger_middle"] = float(sma_20)
                temp_cache["bollinger_upper"] = float(sma_20 + (2 * std_20))
                temp_cache["bollinger_lower"] = float(sma_20 - (2 * std_20))
                temp_cache["bollinger_width"] = float((2 * std_20 * 2) / sma_20 * 100)
        except Exception as e:
            logger.warning(f"Error calculating Bollinger Bands: {e}")

    # Keltner Channels y TTM Squeeze (bb_inside_kc)
    # Keltner: EMA20 +/- multiplier * ATR (prefer ATR 20, fallback ATR 14)
    try:
        kc_middle = None
        if current_len >= 20:
            if talib:
                kc_middle = get_last_valid(talib.EMA(close_arr, timeperiod=20), "EMA 20 (KC)")
            else:
                kc_middle = float(np.mean(close_arr[-20:]))
        kc_atr = None
        if current_len >= 20 and talib:
            kc_atr = get_last_valid(talib.ATR(high_arr, low_arr, close_arr, timeperiod=20), "ATR 20 (KC)")
        elif current_len >= 14:
            if talib:
                kc_atr = get_last_valid(talib.ATR(high_arr, low_arr, close_arr, timeperiod=14), "ATR 14 (KC)")
            else:
                # Fallback sencillo usando _atr(14)
                kc_atr = get_last_valid(_atr(high_arr, low_arr, close_arr, 14), "ATR 14 (KC)")

        if kc_middle is not None and kc_atr is not None:
            multiplier = 1.5
            kc_upper = kc_middle + multiplier * kc_atr
            kc_lower = kc_middle - multiplier * kc_atr
            temp_cache["keltner_middle"] = float(kc_middle)
            temp_cache["keltner_upper"] = float(kc_upper)
            temp_cache["keltner_lower"] = float(kc_lower)

            # Determinar si las BB están dentro de KC (TTM Squeeze clásico)
            bb_u = temp_cache.get("bollinger_upper")
            bb_l = temp_cache.get("bollinger_lower")
            if isinstance(bb_u, (int, float)) and isinstance(bb_l, (int, float)):
                temp_cache["bb_inside_kc"] = (bb_u < kc_upper) and (bb_l > kc_lower)
    except Exception as e:
        logger.warning(f"Error calculating Keltner/TTM Squeeze: {e}")
    # VWAP (simple implementation)
    if current_len >= 1:
        try:
            vwap = np.sum(vol_arr * close_arr) / np.sum(vol_arr) if np.sum(vol_arr) > 0 else close_arr[-1]
            temp_cache["vwap"] = float(vwap)
        except Exception as e:
            logger.warning(f"Error calculating VWAP: {e}")
    # Parabolic SAR (si no hay talib)
    if (not talib) and current_len >= 5:
        try:
            # Implementación simple: usar el mínimo de los últimos 5 lows como SAR
            temp_cache["sar"] = float(np.min(low_arr[-5:]))
        except Exception as e:
            logger.warning(f"Error calculating fallback SAR: {e}")
    # SAR con talib (ya existe como parabolic_sar)
    if talib and current_len >= 5:
        try:
            temp_cache["sar"] = get_last_valid(talib.SAR(high_arr, low_arr, acceleration=0.02, maximum=0.2), "SAR")
        except Exception as e:
            logger.warning(f"Error calculating SAR: {e}")

    # SuperTrend
    if current_len >= 12:  # Necesitamos al menos 12 períodos para un cálculo confiable
        try:
            supertrend_result = calculate_supertrend(
                high_prices=list(high_arr),
                low_prices=list(low_arr),
                close_prices=list(close_arr),
                period=10,
                multiplier=3.0
            )
            
            if supertrend_result and supertrend_result.get('supertrend') is not None:
                temp_cache["supertrend"] = supertrend_result['supertrend']
                temp_cache["supertrend_direction"] = supertrend_result['direction']
                temp_cache["supertrend_signal"] = supertrend_result['signal']
                temp_cache["supertrend_color"] = supertrend_result['trend_color']
                temp_cache["supertrend_price_position"] = supertrend_result['price_vs_supertrend']
                
                # Agregar información adicional para el análisis
                if supertrend_result.get('atr'):
                    temp_cache["supertrend_atr"] = supertrend_result['atr']
                if supertrend_result.get('upper_band'):
                    temp_cache["supertrend_upper_band"] = supertrend_result['upper_band']
                if supertrend_result.get('lower_band'):
                    temp_cache["supertrend_lower_band"] = supertrend_result['lower_band']
                    
        except Exception as e:
            logger.warning(f"Error calculating SuperTrend: {e}")

    # Update the main cache
    _latest_indicators_cache = temp_cache

def get_current_indicators() -> Dict[str, Any]:
    """
    Returns a dictionary of the latest calculated technical indicators.
    Always returns basic data (price, volume) if available, even with insufficient data for complex indicators.
    Thread-safe.
    """
    with _buffer_lock:
        # Always try to provide basic data first
        indicators = {}
        
        # Add essential non-TA-Lib data if we have at least one candle
        if len(close_buf) > 0 and len(vol_buf) > 0:
            try:
                indicators["last_price"] = float(close_buf[-1])
                indicators["curr_vol"] = float(vol_buf[-1])
                
                vol_period = min(20, len(vol_buf))
                if vol_period > 0:
                    vol_slice = list(vol_buf)[-vol_period:]
                    indicators["avg_vol_20"] = float(np.mean(vol_slice))
                else:
                    indicators["avg_vol_20"] = 0.0
                    
                logger.debug(f"Basic indicators available: last_price={indicators['last_price']}, curr_vol={indicators['curr_vol']}")
                    
            except (IndexError, ValueError) as e:
                logger.error(f"Error accessing basic data: {e}")
                return {}  # Critical basic data missing
        
        # Check if we have enough data for complex indicators
        if len(close_buf) < MIN_CANDLES_FOR_RELIABLE_CALC:
            logger.debug(f"Not enough data for complex indicators: {len(close_buf)}/{MIN_CANDLES_FOR_RELIABLE_CALC}, returning basic data only")
            return indicators
        
        # If cache is empty but we have enough data, try to calculate complex indicators
        if not _latest_indicators_cache and len(close_buf) >= MIN_CANDLES_FOR_CALC:
            _calculate_and_store_all_indicators()

        # Add complex indicators from cache
        if _latest_indicators_cache:
            indicators.update(_latest_indicators_cache.copy())

        # Correcciones para indicadores faltantes
        # Agregar macd_signal si signal_line está disponible
        if 'signal_line' in indicators:
            indicators['macd_signal'] = indicators['signal_line']

        # Agregar volume_sma como alias de avg_vol_20
        if 'avg_vol_20' in indicators:
            indicators['volume_sma'] = indicators['avg_vol_20']

        # Convertir tipos numpy a tipos nativos de Python
        def convert_numpy_types(obj):
            if isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, dict):
                return {k: convert_numpy_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy_types(item) for item in obj]
            return obj

        # Aplicar conversión a todos los indicadores
        indicators = convert_numpy_types(indicators)


        # Filter out any None values before returning, consumers should handle missing keys
        return {k: v for k, v in indicators.items() if v is not None}

def get_indicator_sequences(sequence_length: int = 10) -> Dict[str, List[float]]:
    """
    Returns recent sequences of specified indicators. Thread-safe.
    """
    with _buffer_lock:
        if len(close_buf) < max(MIN_CANDLES_FOR_RELIABLE_CALC, sequence_length):
            logger.debug(f"Not enough data for indicator sequences: {len(close_buf)} needed {max(MIN_CANDLES_FOR_RELIABLE_CALC, sequence_length)}")
            return {}

        # Ensure latest indicators (and thus sequences) are up-to-date
        if not _latest_indicators_cache and len(close_buf) >= MIN_CANDLES_FOR_CALC:
             _calculate_and_store_all_indicators()

        sequences: Dict[str, List[float]] = {}
        
        # Helper to safely get sequence
        def _get_seq(buf: Deque[float], name: str) -> List[float]:
            if len(buf) >= sequence_length:
                seq = list(buf)[-sequence_length:]
                if all(isinstance(x, (int, float)) and np.isfinite(x) for x in seq):
                    return seq
                else:
                    logger.warning(f"Sequence for '{name}' contains invalid values, returning empty.")
                    return []
            return []

        sequences["close_seq"] = _get_seq(close_buf, "close")
        sequences["high_seq"] = _get_seq(high_buf, "high")
        sequences["low_seq"] = _get_seq(low_buf, "low")
        sequences["volume_seq"] = _get_seq(vol_buf, "volume")
        sequences["rsi_seq"] = _get_seq(rsi_seq_buf, "rsi")
        sequences["macd_line_seq"] = _get_seq(macd_line_seq_buf, "macd_line")
        sequences["macd_signal_seq"] = _get_seq(macd_signal_seq_buf, "macd_signal")
        sequences["adx_seq"] = _get_seq(adx_seq_buf, "adx")
        sequences["atr_seq"] = _get_seq(atr_seq_buf, "atr")
        
        return {k: v for k, v in sequences.items() if v} # Return only non-empty sequences

def clear_all_buffers() -> None:
    """Clears all data and indicator buffers. Thread-safe."""
    with _buffer_lock:
        close_buf.clear()
        high_buf.clear()
        low_buf.clear()
        vol_buf.clear()
        
        rsi_seq_buf.clear()
        macd_line_seq_buf.clear()
        macd_signal_seq_buf.clear()
        adx_seq_buf.clear()
        atr_seq_buf.clear()
        
        _latest_indicators_cache.clear()
        logger.info("All technical tool buffers and cache cleared.")

def get_buffer_status() -> Dict[str, int]:
    """Returns the current length of primary data buffers."""
    with _buffer_lock:
        return {
            "close_buffer_len": len(close_buf),
            "high_buffer_len": len(high_buf),
            "low_buffer_len": len(low_buf),
            "volume_buffer_len": len(vol_buf),
            "rsi_sequence_len": len(rsi_seq_buf),
            "max_buffer_len": MAXLEN,
            "min_candles_for_calc": MIN_CANDLES_FOR_CALC,
            "min_candles_for_reliable_output": MIN_CANDLES_FOR_RELIABLE_CALC
        }

# Alias for backward compatibility if calc_indicators was used elsewhere
calc_indicators = get_current_indicators

def calculate_supertrend(high_prices, low_prices, close_prices, period=10, multiplier=3.0):
    """
    Calcula el indicador SuperTrend correctamente.
    
    Args:
        high_prices: Lista de precios máximos
        low_prices: Lista de precios mínimos  
        close_prices: Lista de precios de cierre
        period: Período para el ATR (default: 10)
        multiplier: Multiplicador para el ATR (default: 3.0)
    
    Returns:
        dict con 'supertrend', 'direction', 'signal', 'trend_color'
    """
    if len(high_prices) < period + 2:
        return {'supertrend': None, 'direction': 'neutral', 'signal': 'HOLD', 'trend_color': 'neutral'}
    
    try:
        # Convertir a numpy arrays para cálculos más eficientes
        highs = np.array(high_prices)
        lows = np.array(low_prices)
        closes = np.array(close_prices)
        
        # Calcular True Range
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        
        # Calcular ATR usando media móvil simple
        atr_values = []
        for i in range(len(tr)):
            start_idx = max(0, i - period + 1)
            atr_values.append(np.mean(tr[start_idx:i+1]))
        
        # Calcular líneas base (HL2)
        hl2 = (highs + lows) / 2
        
        # Calcular bandas superiores e inferiores
        upper_bands = []
        lower_bands = []
        
        for i in range(len(atr_values)):
            idx = i + 1  # Ajustar índice porque ATR empieza desde el segundo elemento
            if idx < len(hl2):
                upper_band = hl2[idx] + (multiplier * atr_values[i])
                lower_band = hl2[idx] - (multiplier * atr_values[i])
                upper_bands.append(upper_band)
                lower_bands.append(lower_band)
        
        if not upper_bands or not lower_bands:
            return {'supertrend': None, 'direction': 'neutral', 'signal': 'HOLD', 'trend_color': 'neutral'}
        
        # Calcular SuperTrend final
        supertrend_values = []
        trend_directions = []
        
        for i in range(len(upper_bands)):
            close_idx = i + 1  # Ajustar índice
            if close_idx >= len(closes):
                break
                
            current_close = closes[close_idx]
            
            if i == 0:
                # Primera iteración
                if current_close > upper_bands[i]:
                    supertrend_values.append(lower_bands[i])
                    trend_directions.append('bullish')
                else:
                    supertrend_values.append(upper_bands[i])
                    trend_directions.append('bearish')
            else:
                # Iteraciones siguientes
                prev_supertrend = supertrend_values[-1]
                prev_direction = trend_directions[-1]
                
                # Lógica del SuperTrend
                if prev_direction == 'bullish':
                    if current_close < lower_bands[i]:
                        supertrend_values.append(upper_bands[i])
                        trend_directions.append('bearish')
                    else:
                        # Mantener tendencia alcista, usar el mayor entre banda inferior actual y SuperTrend anterior
                        new_supertrend = max(lower_bands[i], prev_supertrend)
                        supertrend_values.append(new_supertrend)
                        trend_directions.append('bullish')
                else:  # bearish
                    if current_close > upper_bands[i]:
                        supertrend_values.append(lower_bands[i])
                        trend_directions.append('bullish')
                    else:
                        # Mantener tendencia bajista, usar el menor entre banda superior actual y SuperTrend anterior
                        new_supertrend = min(upper_bands[i], prev_supertrend)
                        supertrend_values.append(new_supertrend)
                        trend_directions.append('bearish')
        
        if not supertrend_values or not trend_directions:
            return {'supertrend': None, 'direction': 'neutral', 'signal': 'HOLD', 'trend_color': 'neutral'}
        
        # Obtener valores finales
        final_supertrend = supertrend_values[-1]
        final_direction = trend_directions[-1]
        current_price = closes[-1]
        
        # Determinar señal basada en cambio de tendencia
        signal = 'HOLD'
        if len(trend_directions) > 1:
            prev_direction = trend_directions[-2]
            if prev_direction != final_direction:
                signal = 'BUY' if final_direction == 'bullish' else 'SELL'
        
        # Determinar color de la línea
        trend_color = 'green' if final_direction == 'bullish' else 'red'
        
        return {
            'supertrend': round(final_supertrend, 4),
            'direction': final_direction,
            'signal': signal,
            'trend_color': trend_color,
            'atr': round(atr_values[-1], 4) if atr_values else 0,
            'upper_band': round(upper_bands[-1], 4) if upper_bands else 0,
            'lower_band': round(lower_bands[-1], 4) if lower_bands else 0,
            'price_vs_supertrend': 'above' if current_price > final_supertrend else 'below'
        }
        
    except Exception as e:
        logger.error(f"Error calculating SuperTrend: {e}")
        return {'supertrend': None, 'direction': 'neutral', 'signal': 'HOLD', 'trend_color': 'neutral'}
