#!/usr/bin/env python3
"""
Professional Chart Generator para Fenix Trading Bot.

Genera gráficos de trading profesionales estilo TradingView usando:
- lightweight-charts-python (TradingView's Lightweight Charts)
- Plotly para alternativa interactiva
- mplfinance mejorado como fallback

Características:
- Estética profesional similar a TradingView
- Subcharts sincronizados para RSI/MACD
- Indicadores overlay: EMA, Bollinger Bands, SuperTrend, VWAP
- Líneas de soporte/resistencia
- Exportación a imagen para análisis de visión IA
"""
from __future__ import annotations

import base64
import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================================
# Imports opcionales
# ============================================================================

try:
    from lightweight_charts import Chart
    LIGHTWEIGHT_CHARTS_AVAILABLE = True
    logger.info("✅ lightweight-charts-python disponible")
except ImportError:
    LIGHTWEIGHT_CHARTS_AVAILABLE = False
    logger.info("⚠️ lightweight-charts-python no disponible, usando fallback")

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

try:
    import mplfinance as mpf
    import matplotlib
    import matplotlib.pyplot as plt
    matplotlib.use("Agg")
    MPLFINANCE_AVAILABLE = True
except ImportError:
    MPLFINANCE_AVAILABLE = False


# ============================================================================
# Estilos profesionales - Paleta TradingView Dark
# ============================================================================

TRADINGVIEW_DARK_THEME = {
    "background": "#131722",      # Fondo principal
    "grid": "#1e222d",            # Líneas de grid
    "text": "#d1d4dc",            # Texto principal
    "text_secondary": "#787b86",  # Texto secundario
    "up_color": "#26a69a",        # Velas alcistas (verde)
    "down_color": "#ef5350",      # Velas bajistas (rojo)
    "up_color_light": "rgba(38, 166, 154, 0.5)",   # Volumen alcista (verde semi-transparente)
    "down_color_light": "rgba(239, 83, 80, 0.5)", # Volumen bajista (rojo semi-transparente)
    "ema_9": "#2196f3",           # EMA 9 - Azul
    "ema_21": "#ff9800",          # EMA 21 - Naranja
    "ema_50": "#9c27b0",          # EMA 50 - Morado
    "bb_color": "#42a5f5",        # Bollinger Bands - Azul claro
    "vwap": "#e91e63",            # VWAP - Rosa
    "supertrend_up": "#00c853",   # SuperTrend alcista
    "supertrend_down": "#ff1744", # SuperTrend bajista
    "support": "#00bcd4",         # Línea soporte
    "resistance": "#ff5722",      # Línea resistencia
    "rsi_line": "#b388ff",        # RSI
    "macd_line": "#2962ff",       # MACD
    "signal_line": "#ff6d00",     # Signal
    "histogram_positive": "#26a69a",
    "histogram_negative": "#ef5350",
}


# ============================================================================
# Cálculo de indicadores
# ============================================================================

def calculate_ema(close: np.ndarray, period: int) -> np.ndarray:
    """Calcula EMA."""
    alpha = 2 / (period + 1)
    ema = np.zeros_like(close, dtype=float)
    ema[0] = close[0]
    for i in range(1, len(close)):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
    return ema


def calculate_sma(close: np.ndarray, period: int) -> np.ndarray:
    """Calcula SMA."""
    sma = np.convolve(close, np.ones(period) / period, mode='valid')
    return np.concatenate([np.full(period - 1, np.nan), sma])


def calculate_bollinger_bands(close: np.ndarray, period: int = 20, std_dev: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calcula Bollinger Bands."""
    sma = calculate_sma(close, period)
    rolling_std = pd.Series(close).rolling(window=period).std().values
    upper = sma + (rolling_std * std_dev)
    lower = sma - (rolling_std * std_dev)
    return upper, sma, lower


def calculate_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Calcula RSI."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    
    avg_gain = pd.Series(gain).rolling(window=period).mean().values
    avg_loss = pd.Series(loss).rolling(window=period).mean().values
    
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calcula MACD."""
    ema_fast = calculate_ema(close, fast)
    ema_slow = calculate_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray, 
                         period: int = 10, multiplier: float = 3.0) -> Tuple[np.ndarray, np.ndarray]:
    """Calcula SuperTrend."""
    n = len(close)
    atr = np.zeros(n)
    tr = np.zeros(n)
    
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i-1])
        lc = abs(low[i] - close[i-1])
        tr[i] = max(hl, hc, lc)
    
    atr = pd.Series(tr).rolling(window=period).mean().values
    
    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    
    supertrend = np.zeros(n)
    direction = np.ones(n)  # 1 = up, -1 = down
    
    for i in range(period, n):
        if close[i] > upper_band[i-1]:
            direction[i] = 1
        elif close[i] < lower_band[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
        
        if direction[i] == 1:
            supertrend[i] = lower_band[i]
        else:
            supertrend[i] = upper_band[i]
    
    return supertrend, direction


def calculate_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Calcula VWAP."""
    typical_price = (high + low + close) / 3
    cumulative_tp_vol = np.cumsum(typical_price * volume)
    cumulative_vol = np.cumsum(volume)
    vwap = np.divide(cumulative_tp_vol, cumulative_vol, 
                     out=np.zeros_like(cumulative_tp_vol), where=cumulative_vol != 0)
    return vwap


# ============================================================================
# Generador de Charts Profesional con Plotly
# ============================================================================

class ProfessionalChartGenerator:
    """
    Generador de gráficos profesionales para trading.
    
    Produce charts de alta calidad estilo TradingView que son ideales
    para análisis visual por modelos de IA con capacidad de visión.
    """
    
    def __init__(
        self,
        save_path: str = "cache/charts",
        width: int = 1600,
        height: int = 900,
        dpi: int = 150
    ):
        self.save_path = Path(save_path)
        self.save_path.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.height = height
        self.dpi = dpi
        self.theme = TRADINGVIEW_DARK_THEME
    
    def generate_chart(
        self,
        kline_data: Dict[str, List],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        show_indicators: Optional[List[str]] = None,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = True,
    ) -> Dict[str, Any]:
        """
        Genera un gráfico profesional con indicadores.
        
        Args:
            kline_data: Dict con keys 'open', 'high', 'low', 'close', 'volume', 'datetime'
            symbol: Símbolo del par
            timeframe: Timeframe del gráfico
            show_indicators: Lista de indicadores a mostrar ['ema_9', 'ema_21', 'bb_bands', etc.]
            show_volume: Mostrar panel de volumen
            show_rsi: Mostrar panel RSI
            show_macd: Mostrar panel MACD
            
        Returns:
            Dict con 'image_b64', 'filepath', 'description', etc.
        """
        if show_indicators is None:
            show_indicators = ['ema_9', 'ema_21', 'bb_bands', 'vwap']
        
        # Preparar DataFrame
        df = self._prepare_dataframe(kline_data)
        if df is None or len(df) < 5:
            return self._generate_error_response("Datos insuficientes para generar gráfico")
        
        # Intentar con Plotly primero (mejor calidad)
        if PLOTLY_AVAILABLE:
            return self._generate_with_plotly(df, symbol, timeframe, show_indicators, 
                                               show_volume, show_rsi, show_macd)
        
        # Fallback a mplfinance
        if MPLFINANCE_AVAILABLE:
            return self._generate_with_mplfinance(df, symbol, timeframe, show_indicators)
        
        return self._generate_error_response("No hay librerías de gráficos disponibles")
    
    def _prepare_dataframe(self, kline_data: Dict[str, List]) -> Optional[pd.DataFrame]:
        """Prepara y valida el DataFrame."""
        try:
            df = pd.DataFrame(kline_data)
            
            # Mapear columnas
            col_map = {
                'open': 'Open', 'high': 'High', 'low': 'Low',
                'close': 'Close', 'volume': 'Volume', 
                'datetime': 'Date', 'timestamp': 'Date'
            }
            df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)
            
            # Convertir Date
            if 'Date' in df.columns:
                if pd.api.types.is_numeric_dtype(df['Date']):
                    df['Date'] = pd.to_datetime(df['Date'], unit='ms')
                else:
                    df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
            
            # Asegurar tipos numéricos
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)
            return df
            
        except Exception as e:
            logger.error("Error preparando DataFrame: %s", e)
            return None
    
    def _generate_with_plotly(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        show_indicators: List[str],
        show_volume: bool,
        show_rsi: bool,
        show_macd: bool,
    ) -> Dict[str, Any]:
        """Genera gráfico profesional con Plotly."""
        try:
            close = df['Close'].values
            high = df['High'].values
            low = df['Low'].values
            volume = df['Volume'].values if 'Volume' in df.columns else None
            
            # Calcular número de filas para subplots
            rows = 1  # Candlestick siempre
            row_heights = [0.6]  # 60% para price
            
            if show_volume and volume is not None:
                rows += 1
                row_heights.append(0.15)
            if show_rsi:
                rows += 1
                row_heights.append(0.12)
            if show_macd:
                rows += 1
                row_heights.append(0.13)
            
            # Normalizar heights
            total = sum(row_heights)
            row_heights = [h/total for h in row_heights]
            
            # Crear subplot
            fig = make_subplots(
                rows=rows, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.02,
                row_heights=row_heights,
            )
            
            current_row = 1
            
            # ============ CALCULAR ANCHO DE VELA DINÁMICO ============
            # Mapear timeframe a milisegundos para calcular el ancho apropiado
            timeframe_ms = {
                '1m': 60000, '3m': 180000, '5m': 300000, '15m': 900000,
                '30m': 1800000, '1h': 3600000, '4h': 14400000, '1d': 86400000
            }
            base_ms = timeframe_ms.get(timeframe, 900000)  # Default 15m
            num_candles = len(df)
            
            # Para pocas velas, usamos índice numérico en lugar de datetime
            # Esto evita que Plotly expanda las velas para llenar el espacio
            use_numeric_x = num_candles < 30
            x_values = list(range(num_candles)) if use_numeric_x else df.index
            
            # Preparar labels para el eje X si usamos índice numérico
            if use_numeric_x:
                x_tickvals = list(range(0, num_candles, max(1, num_candles // 10)))
                x_ticktext = [df.index[i].strftime('%H:%M') if hasattr(df.index[i], 'strftime') 
                              else str(df.index[i]) for i in x_tickvals]
            
            # ============ CANDLESTICK ============
            fig.add_trace(
                go.Candlestick(
                    x=x_values,
                    open=df['Open'],
                    high=df['High'],
                    low=df['Low'],
                    close=df['Close'],
                    increasing=dict(line=dict(color=self.theme['up_color'], width=1), fillcolor=self.theme['up_color']),
                    decreasing=dict(line=dict(color=self.theme['down_color'], width=1), fillcolor=self.theme['down_color']),
                    name='Price',
                    showlegend=False,
                ),
                row=current_row, col=1
            )
            
            # ============ INDICADORES OVERLAY ============
            # EMAs
            if 'ema_9' in show_indicators:
                ema_9 = calculate_ema(close, 9)
                fig.add_trace(go.Scatter(
                    x=x_values, y=ema_9,
                    line=dict(color=self.theme['ema_9'], width=1.5),
                    name='EMA 9', showlegend=True,
                ), row=current_row, col=1)
            
            if 'ema_21' in show_indicators:
                ema_21 = calculate_ema(close, 21)
                fig.add_trace(go.Scatter(
                    x=x_values, y=ema_21,
                    line=dict(color=self.theme['ema_21'], width=1.5),
                    name='EMA 21', showlegend=True,
                ), row=current_row, col=1)
            
            if 'ema_50' in show_indicators or 'sma_50' in show_indicators:
                sma_50 = calculate_sma(close, 50)
                fig.add_trace(go.Scatter(
                    x=x_values, y=sma_50,
                    line=dict(color=self.theme['ema_50'], width=1.5),
                    name='SMA 50', showlegend=True,
                ), row=current_row, col=1)
            
            # Bollinger Bands
            if 'bb_bands' in show_indicators:
                bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(close)
                fig.add_trace(go.Scatter(
                    x=x_values, y=bb_upper,
                    line=dict(color=self.theme['bb_color'], width=1, dash='dot'),
                    name='BB Upper', showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=x_values, y=bb_lower,
                    line=dict(color=self.theme['bb_color'], width=1, dash='dot'),
                    fill='tonexty', fillcolor='rgba(66, 165, 245, 0.1)',
                    name='BB Lower', showlegend=True,
                ), row=current_row, col=1)
            
            # VWAP
            if 'vwap' in show_indicators and volume is not None:
                vwap = calculate_vwap(high, low, close, volume)
                fig.add_trace(go.Scatter(
                    x=x_values, y=vwap,
                    line=dict(color=self.theme['vwap'], width=1.5, dash='dash'),
                    name='VWAP', showlegend=True,
                ), row=current_row, col=1)
            
            # SuperTrend
            if 'supertrend' in show_indicators:
                st_line, st_dir = calculate_supertrend(high, low, close)
                colors = [self.theme['supertrend_up'] if d == 1 else self.theme['supertrend_down'] 
                         for d in st_dir]
                fig.add_trace(go.Scatter(
                    x=x_values, y=st_line,
                    mode='lines+markers',
                    marker=dict(color=colors, size=3),
                    line=dict(width=2),
                    name='SuperTrend', showlegend=True,
                ), row=current_row, col=1)
            
            price_row = current_row
            current_row += 1
            
            # ============ VOLUME ============
            if show_volume and volume is not None:
                colors = [self.theme['up_color_light'] if df['Close'].iloc[i] >= df['Open'].iloc[i] 
                         else self.theme['down_color_light'] for i in range(len(df))]
                fig.add_trace(go.Bar(
                    x=x_values, y=volume,
                    marker_color=colors,
                    name='Volume', showlegend=False,
                ), row=current_row, col=1)
                fig.update_yaxes(title_text="Vol", row=current_row, col=1)
                current_row += 1
            
            # ============ RSI ============
            if show_rsi:
                rsi = calculate_rsi(close)
                fig.add_trace(go.Scatter(
                    x=x_values, y=rsi,
                    line=dict(color=self.theme['rsi_line'], width=1.5),
                    name='RSI', showlegend=False,
                ), row=current_row, col=1)
                # Niveles 30/70
                fig.add_hline(y=70, line=dict(color='rgba(255,255,255,0.3)', dash='dash'), 
                             row=current_row, col=1)
                fig.add_hline(y=30, line=dict(color='rgba(255,255,255,0.3)', dash='dash'), 
                             row=current_row, col=1)
                fig.add_hline(y=50, line=dict(color='rgba(255,255,255,0.2)', dash='dot'), 
                             row=current_row, col=1)
                fig.update_yaxes(title_text="RSI", range=[0, 100], row=current_row, col=1)
                current_row += 1
            
            # ============ MACD ============
            if show_macd:
                macd_line, signal_line, histogram = calculate_macd(close)
                
                # Histograma
                hist_colors = [self.theme['histogram_positive'] if v >= 0 else self.theme['histogram_negative'] 
                              for v in histogram]
                fig.add_trace(go.Bar(
                    x=x_values, y=histogram,
                    marker_color=hist_colors,
                    name='MACD Hist', showlegend=False,
                ), row=current_row, col=1)
                
                # Líneas MACD y Signal
                fig.add_trace(go.Scatter(
                    x=x_values, y=macd_line,
                    line=dict(color=self.theme['macd_line'], width=1.5),
                    name='MACD', showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=x_values, y=signal_line,
                    line=dict(color=self.theme['signal_line'], width=1.5),
                    name='Signal', showlegend=False,
                ), row=current_row, col=1)
                fig.update_yaxes(title_text="MACD", row=current_row, col=1)
            
            # ============ LAYOUT PROFESIONAL ============
            last_price = close[-1]
            price_change = ((close[-1] - close[0]) / close[0]) * 100
            change_symbol = "▲" if price_change >= 0 else "▼"
            change_color = self.theme['up_color'] if price_change >= 0 else self.theme['down_color']
            
            title_text = (
                f"<b>{symbol}</b> · {timeframe} · "
                f"<span style='color:{change_color}'>{change_symbol} {abs(price_change):.2f}%</span> · "
                f"Last: {last_price:,.2f}"
            )
            
            fig.update_layout(
                title=dict(
                    text=title_text,
                    font=dict(size=16, color=self.theme['text']),
                    x=0.01, xanchor='left'
                ),
                plot_bgcolor=self.theme['background'],
                paper_bgcolor=self.theme['background'],
                font=dict(color=self.theme['text'], size=11),
                xaxis_rangeslider_visible=False,
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.01,
                    xanchor="left",
                    x=0,
                    bgcolor='rgba(0,0,0,0)',
                    font=dict(size=10),
                ),
                margin=dict(l=60, r=30, t=60, b=30),
                width=self.width,
                height=self.height,
            )
            
            # Estilo de ejes
            for i in range(1, rows + 1):
                xaxis_config = dict(
                    gridcolor=self.theme['grid'],
                    showgrid=True,
                    zeroline=False,
                    row=i, col=1
                )
                
                # Cuando usamos índice numérico, configurar etiquetas de tiempo
                if use_numeric_x:
                    xaxis_config.update(dict(
                        tickvals=x_tickvals,
                        ticktext=x_ticktext,
                        tickangle=-45,
                    ))
                else:
                    xaxis_config.update(dict(
                        type='date',
                        nticks=min(num_candles, 20),
                    ))
                
                fig.update_xaxes(**xaxis_config)
                fig.update_yaxes(
                    gridcolor=self.theme['grid'],
                    showgrid=True,
                    zeroline=False,
                    side='right',
                    row=i, col=1
                )
            
            # Último precio en eje Y del gráfico de precios
            fig.update_yaxes(
                title_text="Price",
                row=price_row, col=1
            )
            
            # Ocultar slider del eje X
            fig.update_xaxes(rangeslider_visible=False)
            
            # ============ EXPORTAR A IMAGEN ============
            img_bytes = fig.to_image(format="png", scale=2)
            img_b64 = base64.b64encode(img_bytes).decode('utf-8')
            
            # Guardar archivo
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timeframe}_{timestamp}_pro.png"
            filepath = self.save_path / filename
            
            with open(filepath, 'wb') as f:
                f.write(img_bytes)
            
            logger.info("✅ Gráfico profesional generado: %s (%d bytes)", filepath, len(img_bytes))
            
            # Generar resumen para el agente visual
            indicators_summary = self._generate_summary(df, close, high, low, volume, show_indicators)
            
            return {
                'image_b64': img_b64,
                'filepath': str(filepath),
                'description': f"Gráfico profesional de {symbol} ({timeframe}) con {len(df)} velas",
                'indicators_summary': indicators_summary,
                'symbol': symbol,
                'timeframe': timeframe,
                'candles_count': len(df),
                'last_price': float(last_price),
                'price_change_pct': float(price_change),
                'timestamp': datetime.now().isoformat(),
                'generator': 'plotly_professional',
            }
            
        except Exception as e:
            logger.error("Error generando gráfico Plotly: %s", e, exc_info=True)
            return self._generate_error_response(f"Error Plotly: {e}")
    
    def _generate_with_mplfinance(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        show_indicators: List[str],
    ) -> Dict[str, Any]:
        """Fallback con mplfinance mejorado."""
        try:
            # Crear estilo oscuro profesional
            mc = mpf.make_marketcolors(
                up=self.theme['up_color'],
                down=self.theme['down_color'],
                edge={'up': self.theme['up_color'], 'down': self.theme['down_color']},
                wick={'up': self.theme['up_color'], 'down': self.theme['down_color']},
                volume={'up': self.theme['up_color_light'], 'down': self.theme['down_color_light']},
            )
            
            style = mpf.make_mpf_style(
                base_mpl_style='dark_background',
                marketcolors=mc,
                facecolor=self.theme['background'],
                edgecolor=self.theme['grid'],
                figcolor=self.theme['background'],
                gridcolor=self.theme['grid'],
                gridstyle='--',
                y_on_right=True,
                rc={
                    'axes.labelcolor': self.theme['text'],
                    'axes.edgecolor': self.theme['grid'],
                    'xtick.color': self.theme['text'],
                    'ytick.color': self.theme['text'],
                    'font.size': 10,
                }
            )
            
            # Calcular indicadores
            close = df['Close'].values
            addplots = []
            
            if 'ema_9' in show_indicators:
                ema_9 = pd.Series(calculate_ema(close, 9), index=df.index)
                addplots.append(mpf.make_addplot(ema_9, color=self.theme['ema_9'], width=1.5))
            
            if 'ema_21' in show_indicators:
                ema_21 = pd.Series(calculate_ema(close, 21), index=df.index)
                addplots.append(mpf.make_addplot(ema_21, color=self.theme['ema_21'], width=1.5))
            
            # Generar gráfico
            fig, axlist = mpf.plot(
                df,
                type='candle',
                style=style,
                volume=True,
                addplot=addplots if addplots else None,
                figsize=(16, 10),
                returnfig=True,
                title=f"{symbol} - {timeframe}",
                warn_too_much_data=500,
            )
            
            # Exportar
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight',
                       facecolor=self.theme['background'])
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            # Guardar archivo
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timeframe}_{timestamp}_mpf.png"
            filepath = self.save_path / filename
            
            buf.seek(0)
            with open(filepath, 'wb') as f:
                f.write(buf.read())
            
            return {
                'image_b64': img_b64,
                'filepath': str(filepath),
                'description': f"Gráfico mplfinance de {symbol} ({timeframe})",
                'symbol': symbol,
                'timeframe': timeframe,
                'candles_count': len(df),
                'timestamp': datetime.now().isoformat(),
                'generator': 'mplfinance',
            }
            
        except Exception as e:
            logger.error("Error generando gráfico mplfinance: %s", e)
            return self._generate_error_response(f"Error mplfinance: {e}")
    
    def _generate_summary(
        self,
        df: pd.DataFrame,
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        volume: Optional[np.ndarray],
        show_indicators: List[str],
    ) -> Dict[str, Any]:
        """Genera resumen de indicadores para el agente."""
        summary = {
            'price': {
                'current': float(close[-1]),
                'open': float(close[0]),
                'high': float(high.max()),
                'low': float(low.min()),
                'change_pct': float(((close[-1] - close[0]) / close[0]) * 100),
            }
        }
        
        if 'ema_9' in show_indicators:
            ema_9 = calculate_ema(close, 9)
            summary['ema_9'] = {
                'value': float(ema_9[-1]),
                'position': 'above' if close[-1] > ema_9[-1] else 'below',
            }
        
        if 'ema_21' in show_indicators:
            ema_21 = calculate_ema(close, 21)
            summary['ema_21'] = {
                'value': float(ema_21[-1]),
                'position': 'above' if close[-1] > ema_21[-1] else 'below',
            }
        
        if 'bb_bands' in show_indicators:
            bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(close)
            summary['bollinger'] = {
                'upper': float(bb_upper[-1]) if not np.isnan(bb_upper[-1]) else None,
                'middle': float(bb_middle[-1]) if not np.isnan(bb_middle[-1]) else None,
                'lower': float(bb_lower[-1]) if not np.isnan(bb_lower[-1]) else None,
                'position': 'overbought' if close[-1] > bb_upper[-1] else ('oversold' if close[-1] < bb_lower[-1] else 'neutral'),
            }
        
        # RSI
        rsi = calculate_rsi(close)
        summary['rsi'] = {
            'value': float(rsi[-1]) if not np.isnan(rsi[-1]) else None,
            'condition': 'overbought' if rsi[-1] > 70 else ('oversold' if rsi[-1] < 30 else 'neutral'),
        }
        
        # MACD
        macd_line, signal_line, histogram = calculate_macd(close)
        summary['macd'] = {
            'macd': float(macd_line[-1]) if not np.isnan(macd_line[-1]) else None,
            'signal': float(signal_line[-1]) if not np.isnan(signal_line[-1]) else None,
            'histogram': float(histogram[-1]) if not np.isnan(histogram[-1]) else None,
            'trend': 'bullish' if histogram[-1] > 0 else 'bearish',
        }
        
        return summary
    
    def _generate_error_response(self, message: str) -> Dict[str, Any]:
        """Genera respuesta de error."""
        logger.warning("Chart generation error: %s", message)
        return {
            'image_b64': None,
            'filepath': None,
            'error': message,
            'description': f"Error: {message}",
            'timestamp': datetime.now().isoformat(),
        }


# ============================================================================
# Singleton y función helper
# ============================================================================

_chart_generator: Optional[ProfessionalChartGenerator] = None


def get_professional_chart_generator() -> ProfessionalChartGenerator:
    """Obtiene instancia singleton del generador."""
    global _chart_generator
    if _chart_generator is None:
        _chart_generator = ProfessionalChartGenerator()
    return _chart_generator


def generate_professional_chart(
    kline_data: Dict[str, List],
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    **kwargs
) -> Dict[str, Any]:
    """Función helper para generar un gráfico profesional."""
    generator = get_professional_chart_generator()
    return generator.generate_chart(kline_data, symbol, timeframe, **kwargs)


# ============================================================================
# Test
# ============================================================================

if __name__ == "__main__":
    import random
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    # Generar datos de prueba
    n = 100
    base_price = 45000
    prices = [base_price]
    for _ in range(n - 1):
        change = random.uniform(-500, 500)
        prices.append(prices[-1] + change)
    
    test_data = {
        'open': prices,
        'high': [p + random.uniform(50, 200) for p in prices],
        'low': [p - random.uniform(50, 200) for p in prices],
        'close': [p + random.uniform(-100, 100) for p in prices],
        'volume': [random.uniform(1000, 10000) for _ in range(n)],
        'datetime': [int((datetime.now().timestamp() - (n-i)*900) * 1000) for i in range(n)],
    }
    
    result = generate_professional_chart(
        kline_data=test_data,
        symbol="BTCUSDT",
        timeframe="15m",
        show_indicators=['ema_9', 'ema_21', 'bb_bands', 'vwap'],
    )
    
    if result.get('image_b64'):
        print(f"✅ Gráfico generado: {result['filepath']}")
        print(f"   Tamaño: {len(result['image_b64'])} chars base64")
        print(f"   Indicadores: {result.get('indicators_summary', {}).keys()}")
    else:
        print(f"❌ Error: {result.get('error')}")
