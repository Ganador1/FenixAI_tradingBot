#!/usr/bin/env python3
"""
Dual Chart System - Sistema de charts dual para análisis visual completo.

Combina dos fuentes de charts para dar al Visual Agent información más rica:

1. CHARTS GENERADOS (Plotly) - Indicadores técnicos clásicos:
   - EMAs (9, 21, 50, 200)
   - Bollinger Bands
   - RSI, MACD
   - VWAP
   - Volume con colores

2. CHARTS EXTERNOS (Playwright) - Indicadores avanzados de derivados:
   - Liquidation Heatmap (Coinglass)
   - Open Interest por exchange
   - Funding Rates históricos
   - TradingView con indicadores adicionales

Uso:
    from src.tools.dual_chart_system import get_dual_analysis_charts
    
    charts = await get_dual_analysis_charts("BTCUSDT", "4h")
    
    # Para el Visual Agent:
    generated_chart = charts["generated"]  # EMAs, BB, RSI, MACD
    liquidation_chart = charts["liquidation"]  # Heatmap de liquidaciones
    tradingview_chart = charts["tradingview"]  # Indicadores adicionales
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.tools.chart_capture_scheduler import ChartCaptureScheduler, ChartSnapshot
from src.tools.external_chart_capturer import (
    ExternalChartCapturer,
    ExternalChartSnapshot,
    ChartSource,
)

logger = logging.getLogger(__name__)


@dataclass
class DualChartAnalysis:
    """
    Contenedor para análisis dual de charts.
    
    Combina el chart generado (técnico) con capturas externas (derivados).
    """
    symbol: str
    timeframe: str
    timestamp: datetime
    
    # Chart generado con Plotly (indicadores técnicos)
    generated: ChartSnapshot | None = None
    generated_indicators: list[str] | None = None
    
    # Chart de TradingView (indicadores adicionales)
    tradingview: ExternalChartSnapshot | None = None
    tradingview_indicators: list[str] | None = None
    
    # Chart de Coinglass (liquidaciones/derivados)
    liquidation: ExternalChartSnapshot | None = None
    open_interest: ExternalChartSnapshot | None = None
    funding_rate: ExternalChartSnapshot | None = None
    
    @property
    def has_generated(self) -> bool:
        return self.generated is not None and self.generated.image_b64
    
    @property
    def has_external(self) -> bool:
        return any([
            self.tradingview and self.tradingview.image_b64,
            self.liquidation and self.liquidation.image_b64,
            self.open_interest and self.open_interest.image_b64,
        ])
    
    @property
    def chart_count(self) -> int:
        count = 0
        if self.has_generated:
            count += 1
        if self.tradingview and self.tradingview.image_b64:
            count += 1
        if self.liquidation and self.liquidation.image_b64:
            count += 1
        if self.open_interest and self.open_interest.image_b64:
            count += 1
        if self.funding_rate and self.funding_rate.image_b64:
            count += 1
        return count
    
    def get_all_images_b64(self) -> dict[str, str]:
        """Retorna todos los charts como dict source -> base64."""
        images = {}
        if self.generated and self.generated.image_b64:
            images["generated_technical"] = self.generated.image_b64
        if self.tradingview and self.tradingview.image_b64:
            images["tradingview_advanced"] = self.tradingview.image_b64
        if self.liquidation and self.liquidation.image_b64:
            images["coinglass_liquidation"] = self.liquidation.image_b64
        if self.open_interest and self.open_interest.image_b64:
            images["coinglass_oi"] = self.open_interest.image_b64
        if self.funding_rate and self.funding_rate.image_b64:
            images["coinglass_funding"] = self.funding_rate.image_b64
        return images
    
    def get_analysis_prompt_context(self) -> str:
        """
        Genera contexto para el prompt del Visual Agent.
        
        Describe qué charts están disponibles y qué indicadores muestran.
        """
        context_parts = []
        
        if self.has_generated:
            indicators = ", ".join(self.generated_indicators or ["EMA", "BB", "RSI", "MACD"])
            context_parts.append(
                f"📊 **Chart Técnico Generado** ({self.symbol} {self.timeframe}):\n"
                f"   Indicadores: {indicators}\n"
                f"   Muestra: Velas, volumen, y análisis técnico clásico."
            )
        
        if self.tradingview and self.tradingview.image_b64:
            tv_indicators = ", ".join(self.tradingview_indicators or ["RSI", "MACD", "BB"])
            context_parts.append(
                f"📈 **Chart TradingView** ({self.symbol} {self.timeframe}):\n"
                f"   Indicadores: {tv_indicators}\n"
                f"   Muestra: Vista alternativa con indicadores de TradingView."
            )
        
        if self.liquidation and self.liquidation.image_b64:
            context_parts.append(
                f"🔥 **Liquidation Heatmap** ({self.symbol}):\n"
                f"   Fuente: Coinglass\n"
                f"   Muestra: Zonas de liquidación, clusters de stops, "
                f"niveles de alta actividad."
            )
        
        if self.open_interest and self.open_interest.image_b64:
            context_parts.append(
                f"📉 **Open Interest Chart** ({self.symbol}):\n"
                f"   Fuente: Coinglass\n"
                f"   Muestra: Posiciones abiertas por exchange, "
                f"cambios en OI que indican acumulación/distribución."
            )
        
        if self.funding_rate and self.funding_rate.image_b64:
            context_parts.append(
                f"💰 **Funding Rate History** ({self.symbol}):\n"
                f"   Fuente: Coinglass\n"
                f"   Muestra: Historial de funding rates, "
                f"sentimiento del mercado de futuros."
            )
        
        return "\n\n".join(context_parts)


class DualChartSystem:
    """
    Sistema dual de captura de charts.
    
    Combina:
    - ProfessionalChartGenerator (Plotly) para análisis técnico
    - ExternalChartCapturer (Playwright) para datos de derivados
    """
    
    def __init__(
        self,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        enable_external: bool = True,
        external_headless: bool = True,
    ):
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.timeframes = timeframes or ["15m", "1h", "4h"]
        self.enable_external = enable_external
        
        # Sistema interno de charts generados
        self._scheduler = ChartCaptureScheduler(
            symbols=self.symbols,
            timeframes=self.timeframes,
        )
        
        # Sistema externo (lazy loading)
        self._external_capturer: ExternalChartCapturer | None = None
        self._external_headless = external_headless
    
    @property
    def external_capturer(self) -> ExternalChartCapturer:
        """Lazy loading del capturador externo."""
        if self._external_capturer is None and self.enable_external:
            self._external_capturer = ExternalChartCapturer(
                headless=self._external_headless,
                cache_dir="cache/external_charts",
            )
        return self._external_capturer
    
    def start(self) -> None:
        """Inicia el scheduler de charts generados."""
        self._scheduler.start()
    
    async def stop(self) -> None:
        """Detiene todos los sistemas."""
        self._scheduler.stop()
        if self._external_capturer:
            await self._external_capturer.close()
    
    async def get_dual_analysis(
        self,
        symbol: str,
        timeframe: str = "4h",
        include_generated: bool = True,
        include_tradingview: bool = True,
        include_liquidation: bool = True,
        include_oi: bool = False,
        include_funding: bool = False,
        tradingview_indicators: list[str] | None = None,
    ) -> DualChartAnalysis:
        """
        Obtiene análisis dual de charts.
        
        Args:
            symbol: Símbolo (BTCUSDT, ETHUSDT, etc.)
            timeframe: Timeframe para análisis
            include_generated: Incluir chart generado con Plotly
            include_tradingview: Incluir captura de TradingView
            include_liquidation: Incluir heatmap de liquidaciones
            include_oi: Incluir chart de Open Interest
            include_funding: Incluir chart de Funding Rates
            tradingview_indicators: Indicadores para TradingView
        
        Returns:
            DualChartAnalysis con todos los charts solicitados
        """
        analysis = DualChartAnalysis(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
        )
        
        tasks = []
        task_names = []
        
        # 1. Chart generado (sync, rápido)
        if include_generated:
            generated = self._scheduler.get_fresh_chart(symbol, timeframe)
            analysis.generated = generated
            analysis.generated_indicators = ["ema_9", "ema_21", "bb_bands", "rsi", "vwap"]
        
        # 2. Charts externos (async, paralelos)
        if self.enable_external:
            tradingview_indicators = tradingview_indicators or ["rsi", "macd", "bb", "volume", "ichimoku"]
            
            if include_tradingview:
                tasks.append(
                    self.external_capturer.capture_tradingview_widget(
                        symbol, timeframe, tradingview_indicators
                    )
                )
                task_names.append("tradingview")
                analysis.tradingview_indicators = tradingview_indicators
            
            if include_liquidation:
                tasks.append(
                    self.external_capturer.capture_coinglass(
                        symbol, ChartSource.COINGLASS_LIQUIDATION, timeframe
                    )
                )
                task_names.append("liquidation")
            
            if include_oi:
                tasks.append(
                    self.external_capturer.capture_coinglass(
                        symbol, ChartSource.COINGLASS_OI, timeframe
                    )
                )
                task_names.append("oi")
            
            if include_funding:
                tasks.append(
                    self.external_capturer.capture_coinglass(
                        symbol, ChartSource.COINGLASS_FUNDING, timeframe
                    )
                )
                task_names.append("funding")
        
        # Ejecutar capturas externas en paralelo
        if tasks:
            logger.info("📸 Capturando %d charts externos...", len(tasks))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                name = task_names[i]
                if isinstance(result, Exception):
                    logger.error("Error en %s: %s", name, result)
                elif isinstance(result, ExternalChartSnapshot):
                    if name == "tradingview":
                        analysis.tradingview = result
                    elif name == "liquidation":
                        analysis.liquidation = result
                    elif name == "oi":
                        analysis.open_interest = result
                    elif name == "funding":
                        analysis.funding_rate = result
        
        logger.info(
            "✅ Análisis dual completado: %d charts para %s %s",
            analysis.chart_count, symbol, timeframe
        )
        
        return analysis
    
    async def get_quick_dual(
        self,
        symbol: str,
        timeframe: str = "4h",
    ) -> DualChartAnalysis:
        """
        Análisis dual rápido: Generado + Liquidaciones.
        
        El combo más útil para decisiones de trading:
        - Chart técnico con EMAs, BB, RSI, MACD
        - Heatmap de liquidaciones para ver zonas de stop hunting
        """
        return await self.get_dual_analysis(
            symbol=symbol,
            timeframe=timeframe,
            include_generated=True,
            include_tradingview=False,  # Skip para rapidez
            include_liquidation=True,
            include_oi=False,
            include_funding=False,
        )
    
    async def get_full_analysis(
        self,
        symbol: str,
        timeframe: str = "4h",
    ) -> DualChartAnalysis:
        """
        Análisis completo con todos los charts disponibles.
        
        Incluye:
        - Chart técnico generado
        - TradingView con Ichimoku, RSI, MACD
        - Liquidation heatmap
        - Open Interest
        - Funding Rates
        """
        return await self.get_dual_analysis(
            symbol=symbol,
            timeframe=timeframe,
            include_generated=True,
            include_tradingview=True,
            include_liquidation=True,
            include_oi=True,
            include_funding=True,
            tradingview_indicators=["ichimoku", "rsi", "macd", "bb", "volume"],
        )


# =============================================================================
# Funciones de conveniencia
# =============================================================================

# Singleton del sistema
_dual_system: DualChartSystem | None = None


def get_dual_system() -> DualChartSystem:
    """Obtiene el singleton del sistema dual."""
    global _dual_system
    if _dual_system is None:
        _dual_system = DualChartSystem()
    return _dual_system


async def get_dual_analysis_charts(
    symbol: str,
    timeframe: str = "4h",
) -> dict[str, str]:
    """
    Obtiene charts duales como diccionario de imágenes base64.
    
    Uso simple para el Visual Agent:
    
        charts = await get_dual_analysis_charts("BTCUSDT", "4h")
        # charts = {
        #     "generated_technical": "base64...",
        #     "coinglass_liquidation": "base64...",
        # }
    """
    system = get_dual_system()
    analysis = await system.get_quick_dual(symbol, timeframe)
    return analysis.get_all_images_b64()


async def get_full_visual_context(
    symbol: str,
    timeframe: str = "4h",
) -> tuple[dict[str, str], str]:
    """
    Obtiene charts + contexto para prompt del Visual Agent.
    
    Returns:
        (dict de imágenes, string de contexto para prompt)
    """
    system = get_dual_system()
    analysis = await system.get_full_analysis(symbol, timeframe)
    return analysis.get_all_images_b64(), analysis.get_analysis_prompt_context()


# =============================================================================
# Test
# =============================================================================

async def _test():
    """Test del sistema dual."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    
    print("\n🧪 Testing Dual Chart System\n")
    
    system = DualChartSystem(
        symbols=["BTCUSDT"],
        timeframes=["4h"],
        enable_external=True,
    )
    
    try:
        # No iniciamos el scheduler para test rápido
        
        # Test 1: Quick dual
        print("1️⃣ Quick Dual Analysis (Generated + Liquidation)...")
        analysis = await system.get_quick_dual("BTCUSDT", "4h")
        
        print(f"   📊 Charts obtenidos: {analysis.chart_count}")
        print(f"   Generated: {'✅' if analysis.has_generated else '❌'}")
        print(f"   External: {'✅' if analysis.has_external else '❌'}")
        
        if analysis.liquidation:
            status = "✅" if analysis.liquidation.image_b64 else "❌"
            print(f"   Liquidation: {status}")
        
        # Test 2: Get images
        print("\n2️⃣ Getting images dict...")
        images = analysis.get_all_images_b64()
        for name, img in images.items():
            print(f"   📸 {name}: {len(img)} bytes")
        
        # Test 3: Prompt context
        print("\n3️⃣ Analysis prompt context:")
        context = analysis.get_analysis_prompt_context()
        print(context[:500] + "..." if len(context) > 500 else context)
        
    finally:
        await system.stop()
    
    print("\n✅ Test completado!")


if __name__ == "__main__":
    asyncio.run(_test())
