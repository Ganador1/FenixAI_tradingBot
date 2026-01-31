#!/usr/bin/env python3
"""
Unified Visual Agent Chart System para Fenix Trading Bot.

Sistema unificado que combina múltiples métodos de generación de gráficos
con fallback automático para máxima confiabilidad.

Prioridad de generación:
1. TradingView via Playwright (más profesional, pero puede fallar)
2. Plotly Professional Chart (local, estilo TradingView)
3. mplfinance Enhanced (fallback rápido)

Características:
- Fallback automático entre métodos
- Caché inteligente
- Métricas de éxito por método
- Validación de imagen antes de retornar
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json

logger = logging.getLogger(__name__)


class ChartMethod(Enum):
    """Métodos disponibles para generación de charts."""
    TRADINGVIEW_PLAYWRIGHT = "tradingview_playwright"
    PLOTLY_PROFESSIONAL = "plotly_professional"
    MPLFINANCE = "mplfinance"
    PLACEHOLDER = "placeholder"


@dataclass
class ChartResult:
    """Resultado de generación de chart."""
    success: bool
    image_b64: Optional[str] = None
    filepath: Optional[str] = None
    method: ChartMethod = ChartMethod.PLACEHOLDER
    description: str = ""
    indicators_summary: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    generation_time_ms: float = 0
    image_size_bytes: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class UnifiedChartSystem:
    """
    Sistema unificado de generación de gráficos para el Visual Agent.
    
    Características:
    - Múltiples backends con fallback automático
    - Caché por símbolo/timeframe
    - Métricas de rendimiento
    - Validación de calidad de imagen
    """
    
    def __init__(
        self,
        preferred_method: ChartMethod = ChartMethod.PLOTLY_PROFESSIONAL,
        enable_playwright: bool = True,
        cache_ttl_seconds: int = 180,
        save_path: str = "cache/charts",
        min_image_size_bytes: int = 5000,  # Mínimo 5KB para imagen válida
    ):
        self.preferred_method = preferred_method
        self.enable_playwright = enable_playwright
        self.cache_ttl_seconds = cache_ttl_seconds
        self.save_path = Path(save_path)
        self.save_path.mkdir(parents=True, exist_ok=True)
        self.min_image_size_bytes = min_image_size_bytes
        
        # Generadores (inicializados bajo demanda)
        self._plotly_generator = None
        self._playwright_capture = None
        
        # Métricas
        self.metrics = {
            method.value: {
                'attempts': 0,
                'successes': 0,
                'failures': 0,
                'avg_time_ms': 0,
                'total_time_ms': 0,
            }
            for method in ChartMethod
        }
        
        # Caché simple en memoria
        self._cache: Dict[str, Tuple[str, datetime]] = {}
        
        logger.info(
            "UnifiedChartSystem inicializado (preferred=%s, playwright=%s)",
            preferred_method.value, enable_playwright
        )
    
    @property
    def plotly_generator(self):
        """Lazy load del generador Plotly."""
        if self._plotly_generator is None:
            try:
                from src.tools.professional_chart_generator import ProfessionalChartGenerator
                self._plotly_generator = ProfessionalChartGenerator(save_path=str(self.save_path))
                logger.info("✅ ProfessionalChartGenerator cargado")
            except ImportError as e:
                logger.warning("⚠️ ProfessionalChartGenerator no disponible: %s", e)
        return self._plotly_generator
    
    async def generate_chart(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        kline_data: Optional[Dict[str, List]] = None,
        show_indicators: Optional[List[str]] = None,
        force_method: Optional[ChartMethod] = None,
        skip_cache: bool = False,
    ) -> ChartResult:
        """
        Genera un gráfico usando el mejor método disponible.
        
        Args:
            symbol: Par de trading (ej: BTCUSDT)
            timeframe: Timeframe (ej: 15m, 1h)
            kline_data: Datos OHLCV opcionales (requerido para métodos locales)
            show_indicators: Lista de indicadores a mostrar
            force_method: Forzar un método específico
            skip_cache: Ignorar caché
            
        Returns:
            ChartResult con imagen o error
        """
        if show_indicators is None:
            show_indicators = ['ema_9', 'ema_21', 'bb_bands', 'vwap']
        
        cache_key = f"{symbol}_{timeframe}"
        
        # Verificar caché
        if not skip_cache:
            cached = self._get_from_cache(cache_key)
            if cached:
                logger.info("📦 Chart desde caché: %s", cache_key)
                return ChartResult(
                    success=True,
                    image_b64=cached,
                    method=ChartMethod.PLOTLY_PROFESSIONAL,
                    description=f"Chart de {symbol} {timeframe} (cached)",
                )
        
        # Determinar orden de métodos a intentar
        if force_method:
            methods_to_try = [force_method]
        else:
            methods_to_try = self._get_method_order(kline_data)
        
        # Intentar cada método
        for method in methods_to_try:
            start_time = datetime.now()
            
            try:
                result = await self._try_method(method, symbol, timeframe, kline_data, show_indicators)
                
                generation_time = (datetime.now() - start_time).total_seconds() * 1000
                
                if result.success and result.image_b64:
                    # Validar imagen
                    image_size = len(base64.b64decode(result.image_b64))
                    
                    if image_size >= self.min_image_size_bytes:
                        result.generation_time_ms = generation_time
                        result.image_size_bytes = image_size
                        
                        # Actualizar métricas
                        self._update_metrics(method, success=True, time_ms=generation_time)
                        
                        # Guardar en caché
                        self._set_cache(cache_key, result.image_b64)
                        
                        logger.info(
                            "✅ Chart generado con %s: %s %s (%d bytes, %.0fms)",
                            method.value, symbol, timeframe, image_size, generation_time
                        )
                        return result
                    else:
                        logger.warning(
                            "⚠️ Imagen muy pequeña con %s: %d bytes (min: %d)",
                            method.value, image_size, self.min_image_size_bytes
                        )
                
                self._update_metrics(method, success=False, time_ms=generation_time)
                
            except Exception as e:
                logger.error("❌ Error con método %s: %s", method.value, e)
                self._update_metrics(method, success=False, time_ms=0)
        
        # Todos los métodos fallaron - generar placeholder
        return self._generate_placeholder(symbol, timeframe)
    
    def _get_method_order(self, kline_data: Optional[Dict]) -> List[ChartMethod]:
        """Determina el orden de métodos según disponibilidad y preferencia."""
        methods = []
        
        # Si tenemos datos OHLCV, priorizar métodos locales (más confiables)
        if kline_data and len(kline_data.get('close', [])) >= 10:
            if self.preferred_method == ChartMethod.PLOTLY_PROFESSIONAL:
                methods.append(ChartMethod.PLOTLY_PROFESSIONAL)
            methods.append(ChartMethod.MPLFINANCE)
        
        # Playwright como alternativa (requiere internet, puede fallar)
        if self.enable_playwright:
            methods.append(ChartMethod.TRADINGVIEW_PLAYWRIGHT)
        
        # Asegurar que siempre hay al menos un método
        if not methods:
            methods.append(ChartMethod.PLACEHOLDER)
        
        return methods
    
    async def _try_method(
        self,
        method: ChartMethod,
        symbol: str,
        timeframe: str,
        kline_data: Optional[Dict],
        show_indicators: List[str],
    ) -> ChartResult:
        """Intenta generar chart con un método específico."""
        
        if method == ChartMethod.PLOTLY_PROFESSIONAL:
            return await self._generate_plotly(symbol, timeframe, kline_data, show_indicators)
        
        elif method == ChartMethod.TRADINGVIEW_PLAYWRIGHT:
            return await self._generate_playwright(symbol, timeframe, show_indicators)
        
        elif method == ChartMethod.MPLFINANCE:
            return await self._generate_mplfinance(symbol, timeframe, kline_data, show_indicators)
        
        else:
            return self._generate_placeholder(symbol, timeframe)
    
    async def _generate_plotly(
        self,
        symbol: str,
        timeframe: str,
        kline_data: Optional[Dict],
        show_indicators: List[str],
    ) -> ChartResult:
        """Genera chart con Plotly profesional."""
        if not kline_data:
            return ChartResult(success=False, error="No hay datos OHLCV para Plotly")
        
        if not self.plotly_generator:
            return ChartResult(success=False, error="ProfessionalChartGenerator no disponible")
        
        try:
            result = self.plotly_generator.generate_chart(
                kline_data=kline_data,
                symbol=symbol,
                timeframe=timeframe,
                show_indicators=show_indicators,
                show_volume=True,
                show_rsi=True,
                show_macd=True,
            )
            
            if result.get('image_b64'):
                return ChartResult(
                    success=True,
                    image_b64=result['image_b64'],
                    filepath=result.get('filepath'),
                    method=ChartMethod.PLOTLY_PROFESSIONAL,
                    description=result.get('description', ''),
                    indicators_summary=result.get('indicators_summary', {}),
                )
            else:
                return ChartResult(success=False, error=result.get('error', 'Unknown error'))
                
        except Exception as e:
            return ChartResult(success=False, error=str(e))
    
    async def _generate_playwright(
        self,
        symbol: str,
        timeframe: str,
        show_indicators: List[str],
    ) -> ChartResult:
        """Genera chart capturando TradingView con Playwright."""
        try:
            from src.tools.enhanced_playwright_capture import capture_chart_async
            
            # Convertir timeframe a formato TradingView
            tv_timeframe = self._convert_timeframe_to_tradingview(timeframe)
            
            image_b64 = await capture_chart_async(
                symbol=symbol,
                timeframe=tv_timeframe,
                required_indicators=show_indicators,
            )
            
            if image_b64 and len(image_b64) > 100:
                # Guardar copia local
                filepath = self._save_image(image_b64, symbol, timeframe, "playwright")
                
                return ChartResult(
                    success=True,
                    image_b64=image_b64,
                    filepath=filepath,
                    method=ChartMethod.TRADINGVIEW_PLAYWRIGHT,
                    description=f"TradingView chart de {symbol} {timeframe}",
                )
            else:
                return ChartResult(success=False, error="Playwright captura vacía")
                
        except ImportError:
            return ChartResult(success=False, error="Playwright no disponible")
        except Exception as e:
            return ChartResult(success=False, error=str(e))
    
    async def _generate_mplfinance(
        self,
        symbol: str,
        timeframe: str,
        kline_data: Optional[Dict],
        show_indicators: List[str],
    ) -> ChartResult:
        """Genera chart con mplfinance como fallback."""
        if not kline_data:
            return ChartResult(success=False, error="No hay datos OHLCV para mplfinance")
        
        try:
            from src.tools.chart_generator import FenixChartGenerator
            
            generator = FenixChartGenerator(save_path=str(self.save_path))
            result = generator.generate_chart(
                kline_data=kline_data,
                symbol=symbol,
                timeframe=timeframe,
                show_indicators=show_indicators,
            )
            
            if result.get('image_b64'):
                return ChartResult(
                    success=True,
                    image_b64=result['image_b64'],
                    filepath=result.get('filepath'),
                    method=ChartMethod.MPLFINANCE,
                    description=result.get('description', ''),
                    indicators_summary=result.get('indicators_summary', {}),
                )
            else:
                return ChartResult(success=False, error=result.get('error', 'Unknown error'))
                
        except ImportError:
            return ChartResult(success=False, error="mplfinance no disponible")
        except Exception as e:
            return ChartResult(success=False, error=str(e))
    
    def _generate_placeholder(self, symbol: str, timeframe: str) -> ChartResult:
        """Genera imagen placeholder cuando todos los métodos fallan."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Crear imagen oscura con mensaje
            img = Image.new('RGB', (800, 600), color='#131722')
            draw = ImageDraw.Draw(img)
            
            # Texto centrado
            text = f"⚠️ Chart unavailable\n{symbol} · {timeframe}\n\nAll generation methods failed"
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
            except Exception:
                font = ImageFont.load_default()
            
            # Calcular posición centrada
            bbox = draw.textbbox((0, 0), text, font=font)
            x = (800 - bbox[2]) // 2
            y = (600 - bbox[3]) // 2
            
            draw.text((x, y), text, fill='#d1d4dc', font=font, align='center')
            
            # Convertir a base64
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            image_b64 = base64.b64encode(buf.read()).decode('utf-8')
            
            return ChartResult(
                success=True,  # Técnicamente éxito, pero es placeholder
                image_b64=image_b64,
                method=ChartMethod.PLACEHOLDER,
                description=f"Placeholder para {symbol} {timeframe}",
                error="Todos los métodos de generación fallaron",
            )
            
        except Exception as e:
            # Si hasta PIL falla, retornar pixel transparente mínimo
            fallback_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            return ChartResult(
                success=False,
                image_b64=fallback_b64,
                method=ChartMethod.PLACEHOLDER,
                error=f"Placeholder generation failed: {e}",
            )
    
    def _convert_timeframe_to_tradingview(self, timeframe: str) -> str:
        """Convierte timeframe a formato TradingView."""
        mapping = {
            '1m': '1',
            '5m': '5',
            '15m': '15',
            '30m': '30',
            '1h': '60',
            '4h': '240',
            '1d': 'D',
            '1w': 'W',
        }
        return mapping.get(timeframe.lower(), timeframe)
    
    def _save_image(self, image_b64: str, symbol: str, timeframe: str, method: str) -> str:
        """Guarda imagen a disco y retorna filepath."""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timeframe}_{method}_{timestamp}.png"
            filepath = self.save_path / filename
            
            image_bytes = base64.b64decode(image_b64)
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            return str(filepath)
        except Exception as e:
            logger.warning("Error guardando imagen: %s", e)
            return ""
    
    def _get_from_cache(self, key: str) -> Optional[str]:
        """Obtiene imagen de caché si es válida."""
        if key in self._cache:
            image_b64, cached_at = self._cache[key]
            age = (datetime.now() - cached_at).total_seconds()
            
            if age < self.cache_ttl_seconds:
                return image_b64
            else:
                del self._cache[key]
        
        return None
    
    def _set_cache(self, key: str, image_b64: str):
        """Guarda imagen en caché."""
        self._cache[key] = (image_b64, datetime.now())
        
        # Limpiar caché viejo (máximo 20 entradas)
        if len(self._cache) > 20:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
    
    def _update_metrics(self, method: ChartMethod, success: bool, time_ms: float):
        """Actualiza métricas de rendimiento."""
        m = self.metrics[method.value]
        m['attempts'] += 1
        
        if success:
            m['successes'] += 1
        else:
            m['failures'] += 1
        
        m['total_time_ms'] += time_ms
        if m['successes'] > 0:
            m['avg_time_ms'] = m['total_time_ms'] / m['successes']
    
    def get_metrics_report(self) -> Dict[str, Any]:
        """Retorna reporte de métricas."""
        report = {}
        
        for method, m in self.metrics.items():
            if m['attempts'] > 0:
                success_rate = (m['successes'] / m['attempts']) * 100
                report[method] = {
                    'attempts': m['attempts'],
                    'success_rate': f"{success_rate:.1f}%",
                    'avg_time_ms': f"{m['avg_time_ms']:.0f}",
                }
        
        return report


# ============================================================================
# Singleton y funciones helper
# ============================================================================

_unified_system: Optional[UnifiedChartSystem] = None


def get_unified_chart_system() -> UnifiedChartSystem:
    """Obtiene instancia singleton del sistema unificado."""
    global _unified_system
    if _unified_system is None:
        _unified_system = UnifiedChartSystem()
    return _unified_system


async def generate_chart_for_visual_agent(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    kline_data: Optional[Dict[str, List]] = None,
    show_indicators: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Función principal para el Visual Agent.
    
    Returns:
        Tuple[str, str]: (image_b64, filepath)
    """
    system = get_unified_chart_system()
    result = await system.generate_chart(
        symbol=symbol,
        timeframe=timeframe,
        kline_data=kline_data,
        show_indicators=show_indicators,
    )
    
    return (result.image_b64 or "", result.filepath or "")


# Versión sync para compatibilidad
def generate_chart_for_visual_agent_sync(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    kline_data: Optional[Dict[str, List]] = None,
    show_indicators: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Versión síncrona para compatibilidad."""
    return asyncio.run(generate_chart_for_visual_agent(
        symbol, timeframe, kline_data, show_indicators
    ))


# ============================================================================
# Test
# ============================================================================

if __name__ == "__main__":
    import random
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    async def test():
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
        
        system = get_unified_chart_system()
        
        # Test con datos locales
        print("\n🧪 Test 1: Generación con datos locales")
        result = await system.generate_chart(
            symbol="BTCUSDT",
            timeframe="15m",
            kline_data=test_data,
        )
        
        if result.success:
            print(f"✅ Éxito con {result.method.value}")
            print(f"   Tamaño: {result.image_size_bytes} bytes")
            print(f"   Tiempo: {result.generation_time_ms:.0f}ms")
            if result.filepath:
                print(f"   Archivo: {result.filepath}")
        else:
            print(f"❌ Error: {result.error}")
        
        # Mostrar métricas
        print("\n📊 Métricas:")
        print(json.dumps(system.get_metrics_report(), indent=2))
    
    asyncio.run(test())
