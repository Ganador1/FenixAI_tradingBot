#!/usr/bin/env python3
"""
External Chart Capturer - Captura de charts de fuentes externas.

Este módulo captura screenshots de charts desde diferentes fuentes externas
que ofrecen indicadores avanzados no disponibles en nuestro generador Plotly:

Fuentes soportadas:
1. Coinglass - Liquidation Heatmap, Open Interest, Funding Rates
2. TradingView Widget - Chart embebido con indicadores
3. Bybit/Binance - Charts con datos de futuros

Características:
- Cambio automático de timeframe
- Activación de indicadores específicos
- Captura sin necesidad de login
- Cache inteligente por fuente/símbolo/timeframe
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ChartSource(Enum):
    """Fuentes de charts externos disponibles."""
    COINGLASS_LIQUIDATION = "coinglass_liquidation"
    COINGLASS_OI = "coinglass_oi"
    COINGLASS_FUNDING = "coinglass_funding"
    COINGLASS_HEATMAP = "coinglass_heatmap"
    TRADINGVIEW_WIDGET = "tradingview_widget"
    BYBIT_CHART = "bybit_chart"


# Configuración de URLs y selectores por fuente
SOURCE_CONFIG = {
    ChartSource.COINGLASS_LIQUIDATION: {
        "url_template": "https://www.coinglass.com/LiquidationData/{symbol}",
        "chart_selector": ".liquidation-chart, .echarts-container, canvas",
        "wait_selector": "canvas",
        "wait_time": 3000,
        "viewport": {"width": 1400, "height": 900},
    },
    ChartSource.COINGLASS_OI: {
        "url_template": "https://www.coinglass.com/pro/futures/OpenInterest?symbol={symbol}",
        "chart_selector": ".chart-container, canvas",
        "wait_selector": "canvas",
        "wait_time": 3000,
        "viewport": {"width": 1400, "height": 800},
    },
    ChartSource.COINGLASS_FUNDING: {
        "url_template": "https://www.coinglass.com/FundingRate?symbol={symbol}",
        "chart_selector": ".funding-chart, canvas",
        "wait_selector": "canvas", 
        "wait_time": 2500,
        "viewport": {"width": 1400, "height": 700},
    },
    ChartSource.COINGLASS_HEATMAP: {
        "url_template": "https://www.coinglass.com/pro/futures/LiquidationHeatMap?symbol={symbol}",
        "chart_selector": ".heatmap-container, canvas",
        "wait_selector": "canvas",
        "wait_time": 4000,
        "viewport": {"width": 1600, "height": 900},
    },
    ChartSource.TRADINGVIEW_WIDGET: {
        # Widget embebido que no requiere login
        "url_template": None,  # Generamos HTML local
        "chart_selector": "#tradingview-widget",
        "wait_selector": "iframe",
        "wait_time": 5000,
        "viewport": {"width": 1200, "height": 800},
    },
    ChartSource.BYBIT_CHART: {
        "url_template": "https://www.bybit.com/trade/usdt/{symbol}",
        "chart_selector": ".chart-container, .tv-chart",
        "wait_selector": "canvas",
        "wait_time": 5000,
        "viewport": {"width": 1400, "height": 900},
    },
}

# Mapeo de timeframes a selectores de UI (para Coinglass)
TIMEFRAME_SELECTORS = {
    "15m": "[data-interval='15m'], button:has-text('15m')",
    "1h": "[data-interval='1h'], button:has-text('1h'), button:has-text('1H')",
    "4h": "[data-interval='4h'], button:has-text('4h'), button:has-text('4H')",
    "1d": "[data-interval='1d'], button:has-text('1d'), button:has-text('1D'), button:has-text('Daily')",
    "1w": "[data-interval='1w'], button:has-text('1w'), button:has-text('1W'), button:has-text('Weekly')",
}


@dataclass
class ExternalChartSnapshot:
    """Snapshot de un chart externo."""
    source: ChartSource
    symbol: str
    timeframe: str
    timestamp: datetime
    image_b64: str
    url: str
    capture_time_ms: float
    error: str | None = None
    indicators: list[str] = field(default_factory=list)
    
    @property
    def cache_key(self) -> str:
        return f"{self.source.value}_{self.symbol}_{self.timeframe}"
    
    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.timestamp.replace(tzinfo=timezone.utc)).total_seconds()
    
    def is_valid(self, max_age_seconds: float = 300) -> bool:
        return self.age_seconds < max_age_seconds and self.image_b64 is not None


def _generate_tradingview_widget_html(symbol: str, timeframe: str, indicators: list[str]) -> str:
    """
    Genera HTML con widget de TradingView embebido.
    
    El widget es gratuito y no requiere login.
    """
    # Mapear timeframe a formato TradingView
    tv_intervals = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "4h": "240", "1d": "D", "1w": "W",
    }
    interval = tv_intervals.get(timeframe, "60")
    
    # Mapear indicadores a formato TradingView
    tv_studies = []
    indicator_map = {
        "rsi": "RSI@tv-basicstudies",
        "macd": "MACD@tv-basicstudies",
        "bb": "BollingerBands@tv-basicstudies",
        "ema": "EMA@tv-basicstudies",
        "volume": "Volume@tv-basicstudies",
        "vwap": "VWAP@tv-basicstudies",
        "ichimoku": "IchimokuCloud@tv-basicstudies",
        "stoch": "Stochastic@tv-basicstudies",
        "atr": "ATR@tv-basicstudies",
    }
    for ind in indicators:
        if ind.lower() in indicator_map:
            tv_studies.append(f'"{indicator_map[ind.lower()]}"')
    
    studies_json = ", ".join(tv_studies) if tv_studies else '"Volume@tv-basicstudies"'
    
    # Símbolo en formato TradingView (BINANCE:BTCUSDT)
    tv_symbol = f"BINANCE:{symbol}"
    
    html = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ margin: 0; padding: 0; background: #131722; }}
        #tradingview-widget {{ width: 100%; height: 100vh; }}
    </style>
</head>
<body>
    <div id="tradingview-widget"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
        new TradingView.widget({{
            "autosize": true,
            "symbol": "{tv_symbol}",
            "interval": "{interval}",
            "timezone": "Etc/UTC",
            "theme": "dark",
            "style": "1",
            "locale": "en",
            "toolbar_bg": "#131722",
            "enable_publishing": false,
            "hide_top_toolbar": false,
            "hide_legend": false,
            "save_image": false,
            "container_id": "tradingview-widget",
            "studies": [{studies_json}],
            "show_popup_button": false,
            "popup_width": "1000",
            "popup_height": "650"
        }});
    </script>
</body>
</html>
'''
    return html


class ExternalChartCapturer:
    """
    Capturador de charts desde fuentes externas usando Playwright.
    
    Proporciona acceso a indicadores avanzados como:
    - Liquidation heatmaps (Coinglass)
    - Open Interest charts
    - Funding rates
    - TradingView con indicadores personalizados
    """
    
    def __init__(self, headless: bool = True, cache_dir: str = "cache/external_charts"):
        self.headless = headless
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._browser = None
        self._context = None
        self._playwright = None
        
    async def _ensure_browser(self):
        """Inicializa el browser si no está activo."""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                    ]
                )
                self._context = await self._browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                )
                logger.info("✅ Playwright browser inicializado")
            except ImportError:
                logger.warning("⚠️ playwright no instalado. Ejecutar: pip install playwright && playwright install chromium")
                raise RuntimeError("Playwright no disponible - las capturas externas están deshabilitadas")
            except Exception as e:
                logger.warning("⚠️ Error inicializando Playwright: %s", e)
                logger.warning("   Las capturas externas de TradingView/Coinglass no estarán disponibles")
                logger.warning("   Usa solo los charts generados con Plotly")
                raise RuntimeError(f"Playwright error: {e}")
    
    async def close(self):
        """Cierra el browser."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
    
    async def capture_coinglass(
        self,
        symbol: str,
        chart_type: ChartSource = ChartSource.COINGLASS_LIQUIDATION,
        timeframe: str = "4h",
    ) -> ExternalChartSnapshot:
        """
        Captura chart de Coinglass (liquidaciones, OI, funding).
        
        Coinglass ofrece datos únicos:
        - Liquidation heatmap
        - Open Interest por exchange
        - Funding rates históricos
        """
        await self._ensure_browser()
        
        config = SOURCE_CONFIG[chart_type]
        # Limpiar símbolo (quitar USDT si está)
        clean_symbol = symbol.replace("USDT", "").replace("USD", "")
        url = config["url_template"].format(symbol=clean_symbol)
        
        start_time = time.time()
        error = None
        image_b64 = ""
        
        try:
            page = await self._context.new_page()
            await page.set_viewport_size(config["viewport"])
            
            # Navegar
            logger.info("📡 Navegando a %s...", url)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Esperar que cargue el chart
            await page.wait_for_selector(config["wait_selector"], timeout=15000)
            await asyncio.sleep(config["wait_time"] / 1000)
            
            # Intentar cambiar timeframe si hay selector
            if timeframe in TIMEFRAME_SELECTORS:
                try:
                    tf_selector = TIMEFRAME_SELECTORS[timeframe]
                    tf_button = page.locator(tf_selector).first
                    if await tf_button.is_visible(timeout=2000):
                        await tf_button.click()
                        await asyncio.sleep(1.5)
                        logger.info("⏱️ Timeframe cambiado a %s", timeframe)
                except Exception:
                    pass  # No todos los charts soportan cambio de TF
            
            # Cerrar popups/banners si existen
            try:
                close_buttons = page.locator("button:has-text('Close'), .close-button, [aria-label='Close']")
                if await close_buttons.count() > 0:
                    await close_buttons.first.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            
            # Capturar screenshot
            screenshot_bytes = await page.screenshot(
                type="png",
                full_page=False,
            )
            image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            # Guardar a disco
            filename = f"{chart_type.value}_{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = self.cache_dir / filename
            filepath.write_bytes(screenshot_bytes)
            logger.info("📸 Capturado: %s (%d bytes)", filename, len(screenshot_bytes))
            
            await page.close()
            
        except Exception as e:
            error = str(e)
            logger.error("❌ Error capturando %s: %s", chart_type.value, e)
        
        capture_time = (time.time() - start_time) * 1000
        
        return ExternalChartSnapshot(
            source=chart_type,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
            image_b64=image_b64,
            url=url,
            capture_time_ms=capture_time,
            error=error,
            indicators=["liquidations", "open_interest", "funding"] if "coinglass" in chart_type.value else [],
        )
    
    async def capture_tradingview_widget(
        self,
        symbol: str,
        timeframe: str = "1h",
        indicators: list[str] | None = None,
    ) -> ExternalChartSnapshot:
        """
        Captura chart de TradingView usando widget embebido.
        
        No requiere login y permite indicadores personalizados:
        - RSI, MACD, Bollinger Bands
        - Ichimoku, Stochastic, ATR
        - Volume, VWAP, EMAs
        """
        await self._ensure_browser()
        
        indicators = indicators or ["rsi", "macd", "bb", "volume"]
        config = SOURCE_CONFIG[ChartSource.TRADINGVIEW_WIDGET]
        
        start_time = time.time()
        error = None
        image_b64 = ""
        
        try:
            # Generar HTML del widget
            html_content = _generate_tradingview_widget_html(symbol, timeframe, indicators)
            
            # Guardar HTML temporal
            html_file = self.cache_dir / f"tv_widget_{symbol}.html"
            html_file.write_text(html_content)
            
            page = await self._context.new_page()
            await page.set_viewport_size(config["viewport"])
            
            # Cargar el widget local
            await page.goto(f"file://{html_file.absolute()}", wait_until="networkidle", timeout=30000)
            
            # Esperar que cargue el iframe de TradingView
            await page.wait_for_selector("iframe", timeout=20000)
            await asyncio.sleep(config["wait_time"] / 1000)
            
            # Capturar screenshot
            screenshot_bytes = await page.screenshot(type="png")
            image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            # Guardar
            filename = f"tradingview_{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = self.cache_dir / filename
            filepath.write_bytes(screenshot_bytes)
            logger.info("📸 TradingView Widget: %s (%d bytes)", filename, len(screenshot_bytes))
            
            await page.close()
            # Limpiar HTML temporal
            html_file.unlink(missing_ok=True)
            
        except Exception as e:
            error = str(e)
            logger.error("❌ Error capturando TradingView widget: %s", e)
        
        capture_time = (time.time() - start_time) * 1000
        
        return ExternalChartSnapshot(
            source=ChartSource.TRADINGVIEW_WIDGET,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
            image_b64=image_b64,
            url=f"tradingview_widget_{symbol}",
            capture_time_ms=capture_time,
            error=error,
            indicators=indicators,
        )
    
    async def capture_multi_source(
        self,
        symbol: str,
        timeframe: str = "1h",
        sources: list[ChartSource] | None = None,
    ) -> dict[str, ExternalChartSnapshot]:
        """
        Captura charts de múltiples fuentes en paralelo.
        
        Ideal para tener visión completa:
        - TradingView: Análisis técnico clásico
        - Coinglass: Datos de derivados (liquidaciones, OI)
        """
        sources = sources or [
            ChartSource.TRADINGVIEW_WIDGET,
            ChartSource.COINGLASS_LIQUIDATION,
        ]
        
        results = {}
        tasks = []
        
        for source in sources:
            if source == ChartSource.TRADINGVIEW_WIDGET:
                tasks.append(self.capture_tradingview_widget(symbol, timeframe))
            elif "coinglass" in source.value:
                tasks.append(self.capture_coinglass(symbol, source, timeframe))
        
        captures = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, capture in enumerate(captures):
            if isinstance(capture, Exception):
                logger.error("Error en captura %s: %s", sources[i].value, capture)
            else:
                results[sources[i].value] = capture
        
        return results


# =============================================================================
# Funciones de conveniencia
# =============================================================================

async def capture_coinglass_liquidation(symbol: str, timeframe: str = "4h") -> ExternalChartSnapshot:
    """Captura rápida del heatmap de liquidaciones."""
    capturer = ExternalChartCapturer()
    try:
        return await capturer.capture_coinglass(symbol, ChartSource.COINGLASS_LIQUIDATION, timeframe)
    finally:
        await capturer.close()


async def capture_tradingview(
    symbol: str,
    timeframe: str = "1h",
    indicators: list[str] | None = None,
) -> ExternalChartSnapshot:
    """Captura rápida de TradingView con indicadores."""
    capturer = ExternalChartCapturer()
    try:
        return await capturer.capture_tradingview_widget(symbol, timeframe, indicators)
    finally:
        await capturer.close()


async def capture_dual_charts(
    symbol: str,
    timeframe: str = "1h",
) -> tuple[ExternalChartSnapshot, ExternalChartSnapshot]:
    """
    Captura dual: TradingView (técnico) + Coinglass (derivados).
    
    Retorna ambos charts para análisis completo.
    """
    capturer = ExternalChartCapturer()
    try:
        results = await capturer.capture_multi_source(
            symbol,
            timeframe,
            [ChartSource.TRADINGVIEW_WIDGET, ChartSource.COINGLASS_LIQUIDATION],
        )
        tv_chart = results.get("tradingview_widget")
        cg_chart = results.get("coinglass_liquidation")
        return tv_chart, cg_chart
    finally:
        await capturer.close()


# =============================================================================
# Test
# =============================================================================

async def _test():
    """Test del capturador externo."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    
    print("\n🧪 Testing External Chart Capturer\n")
    
    capturer = ExternalChartCapturer(headless=True)
    
    try:
        # Test 1: TradingView Widget
        print("1️⃣ Capturando TradingView Widget...")
        tv_chart = await capturer.capture_tradingview_widget(
            "BTCUSDT",
            "1h",
            ["rsi", "macd", "bb", "volume"],
        )
        print(f"   ✅ TradingView: {len(tv_chart.image_b64)} bytes, {tv_chart.capture_time_ms:.0f}ms")
        
        # Test 2: Coinglass Liquidation
        print("\n2️⃣ Capturando Coinglass Liquidation...")
        cg_chart = await capturer.capture_coinglass(
            "BTCUSDT",
            ChartSource.COINGLASS_LIQUIDATION,
            "4h",
        )
        if cg_chart.error:
            print(f"   ⚠️ Coinglass error: {cg_chart.error}")
        else:
            print(f"   ✅ Coinglass: {len(cg_chart.image_b64)} bytes, {cg_chart.capture_time_ms:.0f}ms")
        
        # Test 3: Multi-source
        print("\n3️⃣ Captura multi-source...")
        multi = await capturer.capture_multi_source("ETHUSDT", "4h")
        for source, chart in multi.items():
            status = "✅" if chart.image_b64 else "❌"
            print(f"   {status} {source}: {len(chart.image_b64) if chart.image_b64 else 0} bytes")
        
    finally:
        await capturer.close()
    
    print("\n✅ Tests completados!")


if __name__ == "__main__":
    asyncio.run(_test())
