# src/tools/chart_generator.py
"""
Generador de Gráficos Avanzado para Fenix Trading Bot.
Inspirado en QuantAgent pero con indicadores técnicos avanzados de Fenix.

Combina:
- mplfinance para renderizado de velas
- TA-Lib/numpy para indicadores
- Líneas de tendencia automáticas (de QuantAgent)
- Indicadores avanzados: SuperTrend, Ichimoku, VWAP, etc.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Dict, Any, Optional, List, Tuple, Annotated
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")  # Backend sin GUI para servidores

try:
    import mplfinance as mpf
    MPLFINANCE_AVAILABLE = True
except ImportError:
    MPLFINANCE_AVAILABLE = False

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

try:
    from langchain_core.tools import tool
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:
    LANGCHAIN_TOOLS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================================
# ESTILOS DE GRÁFICO (Similar a color_style.py de QuantAgent)
# ============================================================================

FENIX_CHART_STYLE = {
    "base_mpl_style": "dark_background",
    "marketcolors": {
        "candle": {"up": "#26a69a", "down": "#ef5350"},
        "edge": {"up": "#26a69a", "down": "#ef5350"},
        "wick": {"up": "#26a69a", "down": "#ef5350"},
        "ohlc": {"up": "#26a69a", "down": "#ef5350"},
        "volume": {"up": "#26a69a80", "down": "#ef535080"},
        "vcedge": {"up": "#26a69a", "down": "#ef5350"},
        "vcdopcod": False,
        "alpha": 1.0,
    },
    "mavcolors": ["#2196f3", "#ff9800", "#9c27b0"],  # MA colors
    "facecolor": "#1e1e1e",
    "edgecolor": "#1e1e1e",
    "figcolor": "#1e1e1e",
    "gridcolor": "#333333",
    "gridstyle": "--",
    "y_on_right": True,
    "rc": {
        "axes.labelcolor": "#cccccc",
        "axes.edgecolor": "#333333",
        "xtick.color": "#cccccc",
        "ytick.color": "#cccccc",
        "font.size": 10,
    },
}


def get_fenix_style():
    """Retorna el estilo de gráfico de Fenix para mplfinance."""
    if not MPLFINANCE_AVAILABLE:
        return None
    
    mc = mpf.make_marketcolors(
        up=FENIX_CHART_STYLE["marketcolors"]["candle"]["up"],
        down=FENIX_CHART_STYLE["marketcolors"]["candle"]["down"],
        edge={"up": FENIX_CHART_STYLE["marketcolors"]["edge"]["up"], 
              "down": FENIX_CHART_STYLE["marketcolors"]["edge"]["down"]},
        wick={"up": FENIX_CHART_STYLE["marketcolors"]["wick"]["up"], 
              "down": FENIX_CHART_STYLE["marketcolors"]["wick"]["down"]},
        volume={"up": FENIX_CHART_STYLE["marketcolors"]["volume"]["up"], 
                "down": FENIX_CHART_STYLE["marketcolors"]["volume"]["down"]},
    )
    
    style = mpf.make_mpf_style(
        base_mpl_style=FENIX_CHART_STYLE["base_mpl_style"],
        marketcolors=mc,
        facecolor=FENIX_CHART_STYLE["facecolor"],
        edgecolor=FENIX_CHART_STYLE["edgecolor"],
        figcolor=FENIX_CHART_STYLE["figcolor"],
        gridcolor=FENIX_CHART_STYLE["gridcolor"],
        gridstyle=FENIX_CHART_STYLE["gridstyle"],
        y_on_right=FENIX_CHART_STYLE["y_on_right"],
        rc=FENIX_CHART_STYLE["rc"],
        mavcolors=FENIX_CHART_STYLE["mavcolors"],
    )
    
    return style


# ============================================================================
# FUNCIONES DE TRENDLINES (Adaptadas de QuantAgent)
# ============================================================================

def check_trend_line(support: bool, pivot: int, slope: float, y: np.ndarray) -> float:
    """
    Valida una línea de tendencia calculando el error cuadrático.
    Retorna -1 si la línea es inválida.
    """
    intercept = -slope * pivot + y[pivot]
    line_vals = slope * np.arange(len(y)) + intercept
    diffs = line_vals - y
    
    if support and diffs.max() > 1e-5:
        return -1.0
    elif not support and diffs.min() < -1e-5:
        return -1.0
    
    err = (diffs ** 2.0).sum()
    return err


def optimize_slope(support: bool, pivot: int, init_slope: float, y: np.ndarray) -> Tuple[float, float]:
    """
    Optimiza la pendiente de una línea de tendencia usando descenso de gradiente numérico.
    """
    slope_unit = (y.max() - y.min()) / len(y)
    opt_step = 1.0
    min_step = 0.0001
    curr_step = opt_step
    
    best_slope = init_slope
    best_err = check_trend_line(support, pivot, init_slope, y)
    
    if best_err < 0:
        return (init_slope, -init_slope * pivot + y[pivot])
    
    get_derivative = True
    derivative = 0.0
    
    while curr_step > min_step:
        if get_derivative:
            slope_change = best_slope + slope_unit * min_step
            test_err = check_trend_line(support, pivot, slope_change, y)
            derivative = test_err - best_err
            
            if test_err < 0.0:
                slope_change = best_slope - slope_unit * min_step
                test_err = check_trend_line(support, pivot, slope_change, y)
                derivative = best_err - test_err
            
            if test_err < 0.0:
                break
            
            get_derivative = False
        
        if derivative > 0.0:
            test_slope = best_slope - slope_unit * curr_step
        else:
            test_slope = best_slope + slope_unit * curr_step
        
        test_err = check_trend_line(support, pivot, test_slope, y)
        if test_err < 0 or test_err >= best_err:
            curr_step *= 0.5
        else:
            best_err = test_err
            best_slope = test_slope
            get_derivative = True
    
    return (best_slope, -best_slope * pivot + y[pivot])


def fit_trendlines(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    Calcula líneas de tendencia de soporte y resistencia basadas en High/Low/Close.
    """
    x = np.arange(len(close))
    coefs = np.polyfit(x, close, 1)
    line_points = coefs[0] * x + coefs[1]
    
    upper_pivot = int((high - line_points).argmax())
    lower_pivot = int((low - line_points).argmin())
    
    support_coefs = optimize_slope(True, lower_pivot, coefs[0], low)
    resist_coefs = optimize_slope(False, upper_pivot, coefs[0], high)
    
    return (support_coefs, resist_coefs)


# ============================================================================
# CLASE PRINCIPAL: FenixChartGenerator
# ============================================================================

class FenixChartGenerator:
    """
    Generador de gráficos técnicos avanzados para Fenix.
    
    Características:
    - Gráficos de velas con volumen
    - Líneas de tendencia automáticas
    - Indicadores: EMA, Bollinger Bands, SuperTrend, VWAP
    - Exportación a base64 para LLM Vision
    - Compatible con LangChain tools
    """
    
    def __init__(
        self,
        style: Optional[Dict] = None,
        save_path: Optional[str] = None,
        dpi: int = 150,
        figsize: Tuple[int, int] = (16, 10)
    ):
        self.style = style or get_fenix_style()
        self.save_path = Path(save_path) if save_path else Path("cache/charts")
        self.save_path.mkdir(parents=True, exist_ok=True)
        self.dpi = dpi
        self.figsize = figsize
    
    def prepare_dataframe(self, kline_data: Dict[str, List]) -> Optional[pd.DataFrame]:
        """
        Prepara un DataFrame con los datos OHLCV para mplfinance.
        """
        try:
            df = pd.DataFrame(kline_data)
            
            # Asegurar nombres de columnas correctos
            column_mapping = {
                'open': 'Open', 'high': 'High', 'low': 'Low', 
                'close': 'Close', 'volume': 'Volume',
                'datetime': 'Datetime', 'timestamp': 'Datetime'
            }
            df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns}, inplace=True)
            
            # Configurar índice de datetime
            if 'Datetime' in df.columns:
                # Si viene en milisegundos numéricos (timestamp), convertir
                if pd.api.types.is_integer_dtype(df['Datetime']) or pd.api.types.is_float_dtype(df['Datetime']):
                    df['Datetime'] = pd.to_datetime(df['Datetime'], unit='ms', origin='unix')
                else:
                    df['Datetime'] = pd.to_datetime(df['Datetime'])
                df.set_index('Datetime', inplace=True)
            
            # Asegurar tipos numéricos
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # Si no tenemos columna 'Open', sintetizarla a partir del 'Close' previo
            if 'Open' not in df.columns and 'Close' in df.columns:
                df['Open'] = df['Close'].shift(1).fillna(df['Close'])
                df['Open'] = pd.to_numeric(df['Open'], errors='coerce')
                logger.debug("ChartGenerator: 'Open' column missing, synthesized from 'Close' shift")
            
            return df
            
        except Exception as e:
            logger.error(f"Error preparando DataFrame: {e}")
            return None
    
    def calculate_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calcula indicadores técnicos para overlay en el gráfico.
        """
        # We'll return pandas Series aligned to df.index for all indicators
        indicators: Dict[str, Any] = {}
        close = df['Close'].to_numpy(dtype=float)
        high = df['High'].to_numpy(dtype=float)
        low = df['Low'].to_numpy(dtype=float)
        volume = df['Volume'].to_numpy(dtype=float) if 'Volume' in df.columns else None
        
        n = len(close)
        
        # EMAs
        if n >= 9:
            if TALIB_AVAILABLE:
                arr = talib.EMA(close, timeperiod=9)
                indicators['ema_9'] = pd.Series(arr, index=df.index)
            else:
                indicators['ema_9'] = pd.Series(close, index=df.index).ewm(span=9, adjust=False).mean()
        
        if n >= 21:
            if TALIB_AVAILABLE:
                arr = talib.EMA(close, timeperiod=21)
                indicators['ema_21'] = pd.Series(arr, index=df.index)
            else:
                indicators['ema_21'] = pd.Series(close, index=df.index).ewm(span=21, adjust=False).mean()
        
        if n >= 50:
            if TALIB_AVAILABLE:
                arr = talib.SMA(close, timeperiod=50)
                indicators['sma_50'] = pd.Series(arr, index=df.index)
            else:
                indicators['sma_50'] = pd.Series(close, index=df.index).rolling(window=50).mean()
        
        # Bollinger Bands
        if n >= 20:
            if TALIB_AVAILABLE:
                upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
                indicators['bb_upper'] = pd.Series(upper, index=df.index)
                indicators['bb_middle'] = pd.Series(middle, index=df.index)
                indicators['bb_lower'] = pd.Series(lower, index=df.index)
            else:
                sma = pd.Series(close, index=df.index).rolling(window=20).mean()
                std = pd.Series(close, index=df.index).rolling(window=20).std()
                indicators['bb_upper'] = sma + 2 * std
                indicators['bb_middle'] = sma
                indicators['bb_lower'] = sma - 2 * std
        
        # RSI
        if n >= 14:
            if TALIB_AVAILABLE:
                indicators['rsi'] = pd.Series(talib.RSI(close, timeperiod=14), index=df.index)
            else:
                delta = pd.Series(close).diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                indicators['rsi'] = pd.Series((100 - (100 / (1 + rs))), index=df.index)
        
        # MACD
        if n >= 26:
            if TALIB_AVAILABLE:
                macd, signal, hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
                indicators['macd'] = pd.Series(macd, index=df.index)
                indicators['macd_signal'] = pd.Series(signal, index=df.index)
                indicators['macd_hist'] = pd.Series(hist, index=df.index)
            else:
                ema_12 = pd.Series(close, index=df.index).ewm(span=12, adjust=False).mean()
                ema_26 = pd.Series(close, index=df.index).ewm(span=26, adjust=False).mean()
                macd = ema_12 - ema_26
                signal = macd.ewm(span=9, adjust=False).mean()
                indicators['macd'] = macd
                indicators['macd_signal'] = signal
                indicators['macd_hist'] = macd - signal
        
        # SuperTrend
        if n >= 12:
            supertrend_result = self._calculate_supertrend(high, low, close, period=10, multiplier=3.0)
            if supertrend_result:
                indicators['supertrend'] = pd.Series(supertrend_result['line'], index=df.index)
                indicators['supertrend_direction'] = pd.Series(supertrend_result['direction'], index=df.index)
        
        # VWAP (si hay volumen)
        if volume is not None and n >= 1:
            typical_price = (high + low + close) / 3
            cumulative_tp_vol = np.cumsum(typical_price * volume)
            cumulative_vol = np.cumsum(volume)
            vwap = np.divide(cumulative_tp_vol, cumulative_vol, 
                           out=np.zeros_like(cumulative_tp_vol), where=cumulative_vol != 0)
            indicators['vwap'] = pd.Series(vwap, index=df.index)
        
        # Líneas de tendencia
        if n >= 10:
            try:
                support_coefs, resist_coefs = fit_trendlines(high, low, close)
                x = np.arange(n)
                indicators['support_line'] = pd.Series(support_coefs[0] * x + support_coefs[1], index=df.index)
                indicators['resist_line'] = pd.Series(resist_coefs[0] * x + resist_coefs[1], index=df.index)
            except Exception as e:
                logger.debug(f"No se pudieron calcular líneas de tendencia: {e}")
        
        # Ensure all numeric arrays are converted to pandas Series aligned to df.index
        for key, val in list(indicators.items()):
            if isinstance(val, np.ndarray):
                indicators[key] = pd.Series(val, index=df.index)
            # If it's a plain list/tuple, convert as well
            if isinstance(val, (list, tuple)):
                indicators[key] = pd.Series(list(val), index=df.index)
        return indicators
    
    def _calculate_supertrend(
        self, 
        high: np.ndarray, 
        low: np.ndarray, 
        close: np.ndarray, 
        period: int = 10, 
        multiplier: float = 3.0
    ) -> Optional[Dict[str, Any]]:
        """Calcula el indicador SuperTrend."""
        n = len(close)
        if n < period + 2:
            return None
        
        try:
            # ATR
            if TALIB_AVAILABLE:
                atr = talib.ATR(high, low, close, timeperiod=period)
            else:
                tr1 = high[1:] - low[1:]
                tr2 = np.abs(high[1:] - close[:-1])
                tr3 = np.abs(low[1:] - close[:-1])
                tr = np.maximum(tr1, np.maximum(tr2, tr3))
                atr = np.concatenate([[np.nan], pd.Series(tr).rolling(window=period).mean().values])
            
            # HL2
            hl2 = (high + low) / 2
            
            # Bandas
            upper_band = hl2 + (multiplier * atr)
            lower_band = hl2 - (multiplier * atr)
            
            # SuperTrend
            supertrend = np.zeros(n)
            direction = np.zeros(n)
            
            for i in range(period, n):
                if close[i] > upper_band[i-1]:
                    direction[i] = 1  # Bullish
                elif close[i] < lower_band[i-1]:
                    direction[i] = -1  # Bearish
                else:
                    direction[i] = direction[i-1]
                
                if direction[i] == 1:
                    supertrend[i] = max(lower_band[i], supertrend[i-1] if direction[i-1] == 1 else lower_band[i])
                else:
                    supertrend[i] = min(upper_band[i], supertrend[i-1] if direction[i-1] == -1 else upper_band[i])
            
            # Llenar valores iniciales con NaN
            supertrend[:period] = np.nan
            
            return {
                'line': supertrend,
                'direction': direction,
            }
            
        except Exception as e:
            logger.error(f"Error calculando SuperTrend: {e}")
            return None
    
    def generate_chart(
        self,
        kline_data: Dict[str, List],
        symbol: str = "SYMBOL",
        timeframe: str = "1h",
        show_indicators: List[str] = None,
        show_trendlines: bool = True,
        last_n_candles: int = 50,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Genera un gráfico de velas con indicadores técnicos.
        
        Args:
            kline_data: Diccionario con datos OHLCV
            symbol: Símbolo del par (ej: "BTCUSDT")
            timeframe: Temporalidad (ej: "15m", "1h", "4h")
            show_indicators: Lista de indicadores a mostrar
            show_trendlines: Si mostrar líneas de tendencia
            last_n_candles: Número de velas a mostrar
            title: Título personalizado
        
        Returns:
            Dict con 'image_b64', 'description', 'indicators_summary'
        """
        if not MPLFINANCE_AVAILABLE:
            return {
                'image_b64': None,
                'error': 'mplfinance no disponible',
                'description': 'No se pudo generar el gráfico: mplfinance no instalado'
            }
        
        # Preparar DataFrame
        df = self.prepare_dataframe(kline_data)
        if df is None or len(df) < 5:
            return {
                'image_b64': None,
                'error': 'Datos insuficientes',
                'description': 'No hay suficientes datos para generar el gráfico'
            }
        
        # Tomar últimas N velas
        df = df.tail(last_n_candles).copy()
        
        # Calcular indicadores
        indicators = self.calculate_indicators(df)
        
        # Configurar indicadores por defecto
        if show_indicators is None:
            show_indicators = ['ema_9', 'ema_21', 'bb_bands', 'volume', 'rsi', 'macd']
        
        # Construir addplots
        addplots = []
        
        # EMAs - made thicker for better visibility
        if 'ema_9' in show_indicators and 'ema_9' in indicators:
            s = indicators['ema_9'].reindex(df.index)
            addplots.append(mpf.make_addplot(
                s, 
                color='#2196f3', width=2.0, label='EMA 9'
            ))
        
        if 'ema_21' in show_indicators and 'ema_21' in indicators:
            s = indicators['ema_21'].reindex(df.index)
            addplots.append(mpf.make_addplot(
                s, 
                color='#ff9800', width=2.0, label='EMA 21'
            ))
        
        if 'sma_50' in show_indicators and 'sma_50' in indicators:
            s = indicators['sma_50'].reindex(df.index)
            addplots.append(mpf.make_addplot(
                s, 
                color='#9c27b0', width=1.8, label='SMA 50'
            ))
        
        # Bollinger Bands
        if 'bb_bands' in show_indicators and 'bb_upper' in indicators:
            addplots.append(mpf.make_addplot(
                indicators['bb_upper'].reindex(df.index), 
                color='#90caf9', width=0.8, linestyle='--'
            ))
            addplots.append(mpf.make_addplot(
                indicators['bb_lower'].reindex(df.index), 
                color='#90caf9', width=0.8, linestyle='--'
            ))
        
        # SuperTrend
        if 'supertrend' in show_indicators and 'supertrend' in indicators:
            st_line = indicators['supertrend'].reindex(df.index)
            st_dir = indicators['supertrend_direction'].reindex(df.index)
            
            # Colorear según dirección
            colors = ['#26a69a' if d == 1 else '#ef5350' for d in st_dir]
            addplots.append(mpf.make_addplot(
                st_line, 
                color='#26a69a', width=2, label='SuperTrend'
            ))
        
        # VWAP
        if 'vwap' in show_indicators and 'vwap' in indicators:
            addplots.append(mpf.make_addplot(
                indicators['vwap'].reindex(df.index), 
                color='#e91e63', width=1.5, linestyle=':', label='VWAP'
            ))

        # --- OSCILLATORS (Separate Panels) ---
        # Panel 0 is Price, Panel 1 is Volume (if enabled)
        # We start adding panels from 2
        current_panel = 2 if 'volume' in show_indicators else 1

        if 'rsi' in show_indicators and 'rsi' in indicators:
            rsi_series = indicators['rsi'].reindex(df.index)
            addplots.append(mpf.make_addplot(
                rsi_series,
                panel=current_panel,
                color='#b388ff',
                ylabel='RSI',
                secondary_y=False
            ))
            # Add RSI levels
            addplots.append(mpf.make_addplot(
                [70] * len(df),
                panel=current_panel,
                color='#ffffff',
                linestyle='--',
                alpha=0.3,
                secondary_y=False
            ))
            addplots.append(mpf.make_addplot(
                [30] * len(df),
                panel=current_panel,
                color='#ffffff',
                linestyle='--',
                alpha=0.3,
                secondary_y=False
            ))
            current_panel += 1

        if 'macd' in show_indicators and 'macd' in indicators:
            macd_series = indicators['macd'].reindex(df.index)
            macd_signal_series = indicators['macd_signal'].reindex(df.index)
            macd_hist_series = indicators['macd_hist'].reindex(df.index)
            addplots.append(mpf.make_addplot(
                macd_series,
                panel=current_panel,
                color='#2962ff',
                ylabel='MACD',
                secondary_y=False
            ))
            addplots.append(mpf.make_addplot(
                macd_signal_series,
                panel=current_panel,
                color='#ff6d00',
                secondary_y=False
            ))
            addplots.append(mpf.make_addplot(
                macd_hist_series,
                type='bar',
                panel=current_panel,
                color=['#26a69a' if v >= 0 else '#ef5350' for v in macd_hist_series.fillna(0).tolist()],
                secondary_y=False
            ))
            current_panel += 1
        
        # Líneas de tendencia
        alines = []
        if show_trendlines:
            # Helper para convertir a tipos nativos
            def to_native(val):
                if hasattr(val, 'item'):
                    return val.item()
                return val

            if 'support_line' in indicators:
                support = indicators['support_line'][-len(df):]
                p1 = (to_native(df.index[0]), float(support.iloc[0]))
                p2 = (to_native(df.index[-1]), float(support.iloc[-1]))
                alines.append([p1, p2])
            
            if 'resist_line' in indicators:
                resist = indicators['resist_line'][-len(df):]
                p1 = (to_native(df.index[0]), float(resist.iloc[0]))
                p2 = (to_native(df.index[-1]), float(resist.iloc[-1]))
                alines.append([p1, p2])
        
        # Generar gráfico
        try:
            chart_title = title or f"{symbol} - {timeframe}"
            
            # Configure candle width for better visibility
            # Thicker candles are more readable, especially for AI vision analysis
            width_config = dict(
                candle_linewidth=1.5,   # Wick/shadow line width
                candle_width=0.7,       # Body width (0.0 to 1.0, where 1.0 is full width)
                volume_width=0.6,       # Volume bar width
            )
            
            fig, axlist = mpf.plot(
                df,
                type='candle',
                style=self.style,
                addplot=addplots if addplots else None,
                alines=alines if alines else None,
                volume='volume' in show_indicators,
                title=chart_title,
                figsize=self.figsize,
                returnfig=True,
                warn_too_much_data=500,
                update_width_config=width_config,
                datetime_format='%H:%M',  # Format x-axis timestamps as HH:MM
                xrotation=45,             # Rotate labels for better readability
            )
            
            # Guardar a buffer
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight', 
                       facecolor=FENIX_CHART_STYLE['figcolor'])
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            # También guardar archivo local
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timeframe}_{timestamp}.png"
            filepath = self.save_path / filename
            
            buf.seek(0)
            with open(filepath, 'wb') as f:
                f.write(buf.read())
            logger.info(f"💾 Chart saved to: {filepath}")
            
            # Resumen de indicadores
            indicators_summary = self._generate_indicators_summary(df, indicators)
            
            return {
                'image_b64': img_b64,
                'filepath': str(filepath),
                'description': f"Gráfico de velas para {symbol} ({timeframe}) con {len(df)} velas",
                'indicators_summary': indicators_summary,
                'symbol': symbol,
                'timeframe': timeframe,
                'candles_count': len(df),
                'timestamp': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error generando gráfico: {e}")
            return {
                'image_b64': None,
                'error': str(e),
                'description': f'Error al generar gráfico: {e}'
            }

    def generate_placeholder(self, message: str = "No data available", symbol: str = "SYMBOL", timeframe: str = "1h") -> Dict[str, Any]:
        """
        Genera una imagen PNG simple con un mensaje para indicar que no hay datos suficientes.
        Útil como placeholder cuando no hay suficientes velas para un gráfico real.
        """
        try:
            fig, ax = plt.subplots(figsize=(6, 3))
            ax.set_facecolor(FENIX_CHART_STYLE['figcolor'])
            ax.text(0.5, 0.5, message, horizontalalignment='center', verticalalignment='center', color='#ffffff', fontsize=12)
            ax.set_axis_off()

            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight', facecolor=FENIX_CHART_STYLE['figcolor'])
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)

            return {
                'image_b64': img_b64,
                'description': f'Placeholder chart: {message}',
                'symbol': symbol,
                'timeframe': timeframe,
                'error': None,
            }
        except Exception as e:
            logger.error(f"Failed to create placeholder chart: {e}")
            return {'image_b64': None, 'error': str(e), 'description': 'Failed to generate placeholder'}
    
    def _generate_indicators_summary(self, df: pd.DataFrame, indicators: Dict) -> Dict[str, Any]:
        """Genera un resumen de los indicadores calculados."""
        summary = {}
        
        close = df['Close'].iloc[-1]
        summary['last_price'] = float(close)
        summary['price_change_pct'] = float((df['Close'].iloc[-1] / df['Close'].iloc[0] - 1) * 100)
        
        if 'ema_9' in indicators and not np.isnan(indicators['ema_9'].iloc[-1]):
            summary['ema_9'] = float(indicators['ema_9'].iloc[-1])
            summary['price_vs_ema9'] = 'above' if close > indicators['ema_9'].iloc[-1] else 'below'
        
        if 'ema_21' in indicators and not np.isnan(indicators['ema_21'].iloc[-1]):
            summary['ema_21'] = float(indicators['ema_21'].iloc[-1])
            summary['price_vs_ema21'] = 'above' if close > indicators['ema_21'].iloc[-1] else 'below'
        
        if 'bb_upper' in indicators:
            summary['bb_upper'] = float(indicators['bb_upper'].iloc[-1])
            summary['bb_lower'] = float(indicators['bb_lower'].iloc[-1])
            bb_width = (indicators['bb_upper'].iloc[-1] - indicators['bb_lower'].iloc[-1]) / indicators['bb_middle'].iloc[-1]
            summary['bb_width_pct'] = float(bb_width * 100)
        
        if 'rsi' in indicators and not np.isnan(indicators['rsi'].iloc[-1]):
            rsi_val = indicators['rsi'].iloc[-1]
            summary['rsi'] = float(rsi_val)
            if rsi_val >= 70:
                summary['rsi_signal'] = 'OVERBOUGHT'
            elif rsi_val <= 30:
                summary['rsi_signal'] = 'OVERSOLD'
            else:
                summary['rsi_signal'] = 'NEUTRAL'
        
        if 'supertrend_direction' in indicators:
            direction = indicators['supertrend_direction'].iloc[-1]
            summary['supertrend_signal'] = 'BULLISH' if direction == 1 else 'BEARISH'
        
        if 'support_line' in indicators:
            summary['support_level'] = float(indicators['support_line'].iloc[-1])
            summary['resistance_level'] = float(indicators['resist_line'].iloc[-1])
        
        return summary


# ============================================================================
# LANGCHAIN TOOLS (Compatibles con QuantAgent)
# ============================================================================

# Instancia global del generador
_chart_generator: Optional[FenixChartGenerator] = None


def get_chart_generator() -> FenixChartGenerator:
    """Obtiene o crea la instancia global del generador de gráficos."""
    global _chart_generator
    if _chart_generator is None:
        _chart_generator = FenixChartGenerator()
    return _chart_generator


if LANGCHAIN_TOOLS_AVAILABLE:
    @tool
    def generate_candlestick_chart(
        kline_data: Annotated[
            dict,
            "Diccionario con datos OHLCV: 'Open', 'High', 'Low', 'Close', 'Volume', 'Datetime'"
        ],
        symbol: Annotated[str, "Símbolo del par (ej: 'BTCUSDT')"] = "BTCUSDT",
        timeframe: Annotated[str, "Temporalidad (ej: '15m', '1h', '4h')"] = "1h",
    ) -> dict:
        """
        Genera un gráfico de velas profesional con indicadores técnicos.
        
        Incluye:
        - EMA 9/21
        - Bollinger Bands
        - Líneas de soporte/resistencia automáticas
        - Volumen
        
        Returns:
            dict con image_b64 (imagen codificada), descripción e indicadores
        """
        generator = get_chart_generator()
        return generator.generate_chart(
            kline_data=kline_data,
            symbol=symbol,
            timeframe=timeframe,
            show_indicators=['ema_9', 'ema_21', 'bb_bands', 'volume'],
            show_trendlines=True,
        )
    
    @tool
    def generate_trend_chart(
        kline_data: Annotated[
            dict,
            "Diccionario con datos OHLCV: 'Open', 'High', 'Low', 'Close', 'Volume', 'Datetime'"
        ],
        symbol: Annotated[str, "Símbolo del par"] = "BTCUSDT",
        timeframe: Annotated[str, "Temporalidad"] = "1h",
    ) -> dict:
        """
        Genera un gráfico enfocado en análisis de tendencia con SuperTrend, EMAs y VWAP.
        
        Ideal para identificar:
        - Dirección de tendencia
        - Niveles de soporte/resistencia dinámicos
        - Puntos de entrada/salida
        
        Returns:
            dict con image_b64, descripción e indicadores de tendencia
        """
        generator = get_chart_generator()
        return generator.generate_chart(
            kline_data=kline_data,
            symbol=symbol,
            timeframe=timeframe,
            show_indicators=['ema_9', 'ema_21', 'sma_50', 'supertrend', 'vwap', 'volume'],
            show_trendlines=True,
        )


# ============================================================================
# FUNCIONES DE UTILIDAD
# ============================================================================

def convert_binance_klines_to_dict(klines: List[List]) -> Dict[str, List]:
    """
    Convierte klines de Binance API al formato esperado por el generador.
    
    Args:
        klines: Lista de listas con formato Binance 
                [timestamp, open, high, low, close, volume, ...]
    
    Returns:
        Diccionario con columnas OHLCV
    """
    return {
        'Datetime': [pd.to_datetime(k[0], unit='ms') for k in klines],
        'Open': [float(k[1]) for k in klines],
        'High': [float(k[2]) for k in klines],
        'Low': [float(k[3]) for k in klines],
        'Close': [float(k[4]) for k in klines],
        'Volume': [float(k[5]) for k in klines],
    }


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Test básico
    import random
    
    # Generar datos de ejemplo
    n = 100
    base_price = 50000
    dates = pd.date_range(end=datetime.now(), periods=n, freq='1H')
    
    test_data = {
        'Datetime': dates.tolist(),
        'Open': [base_price + random.uniform(-500, 500) for _ in range(n)],
        'High': [],
        'Low': [],
        'Close': [base_price + random.uniform(-500, 500) for _ in range(n)],
        'Volume': [random.uniform(100, 1000) for _ in range(n)],
    }
    
    # Calcular High/Low basados en Open/Close
    for i in range(n):
        o, c = test_data['Open'][i], test_data['Close'][i]
        test_data['High'].append(max(o, c) + random.uniform(0, 200))
        test_data['Low'].append(min(o, c) - random.uniform(0, 200))
    
    # Generar gráfico
    generator = FenixChartGenerator()
    result = generator.generate_chart(
        kline_data=test_data,
        symbol='BTCUSDT',
        timeframe='1h',
        show_indicators=['ema_9', 'ema_21', 'bb_bands', 'supertrend', 'volume'],
    )
    
    print(f"Gráfico generado: {result.get('filepath')}")
    print(f"Indicadores: {result.get('indicators_summary')}")
