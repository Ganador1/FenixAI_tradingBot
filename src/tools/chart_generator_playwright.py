#!/usr/bin/env python3
"""
Chart Generator usando Enhanced Playwright Capture para TradingView
Genera gráficos reales de TradingView para el agente visual enhanced
"""

import logging
from typing import Dict, Any, Tuple, Optional
from pathlib import Path
import base64
from datetime import datetime

logger = logging.getLogger(__name__)

# Import del enhanced playwright capture
try:
    from src.tools.enhanced_playwright_capture import capture_chart_sync, capture_chart_async
    PLAYWRIGHT_AVAILABLE = True
    logger.info("✅ Enhanced Playwright Capture disponible para chart generation")
except ImportError as e:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning(f"⚠️ Enhanced Playwright Capture no disponible: {e}")
    logger.warning("   Usando fallback simple sin captura real")


async def generate_chart_for_visual_agent_playwright_async(
    symbol: str,
    timeframe: str,
    save_chart: bool = True,
    output_dir: str = "screenshots",
    required_indicators: list[str] | None = None
) -> tuple[str, str]:
    """
    Versión async para generar chart usando Enhanced Playwright Capture.
    Usar esta versión cuando ya estés en un contexto async.

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT')
        timeframe: Timeframe string (e.g., '15m', '1h')
        save_chart: Si True, guarda el chart a disco
        output_dir: Directorio donde guardar el chart

    Returns:
        Tuple[str, str]: (base64_image, filepath)
    """
    try:
        # Usar directamente la versión async
        chart_b64 = await capture_chart_async(
            symbol=symbol,
            timeframe=timeframe,
            required_indicators=required_indicators or [
                "Supertrend", "SAR", "Volume", "EMA", "VWAP"
            ]
        )

        if not chart_b64 or len(chart_b64) < 100:
            logger.warning(f"⚠️ Captura resultó en imagen vacía o muy pequeña: {len(chart_b64) if chart_b64 else 0} chars")
            fallback_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            return fallback_b64, ""

        # Guardar si se solicita
        filepath = ""
        if save_chart:
            try:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)

                # Crear filename con timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{timestamp}_{symbol}_{timeframe}.png"
                filepath = str(output_path / filename)

                # Decodificar y guardar
                image_bytes = base64.b64decode(chart_b64)
                with open(filepath, 'wb') as f:
                    f.write(image_bytes)

                logger.info(f"� Gráfico guardado en: {filepath} ({len(image_bytes)} bytes)")
            except Exception as e:
                logger.error(f"❌ Error guardando gráfico: {e}")
                filepath = ""

        logger.info(f"✅ Gráfico generado exitosamente: {len(chart_b64)} chars base64")
        return chart_b64, filepath

    except Exception as e:
        logger.error(f"❌ Error generando gráfico con Playwright (async): {e}", exc_info=True)
        fallback_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        return fallback_b64, ""


def generate_chart_for_visual_agent_playwright(
    symbol: str,
    timeframe: str,
    save_chart: bool = True,
    output_dir: str = "screenshots",
    required_indicators: list[str] | None = None
) -> tuple[str, str]:
    """
    Genera un chart usando Enhanced Playwright Capture (versión sync).

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT')
        timeframe: Timeframe string (e.g., '15m', '1h')
        save_chart: Si True, guarda el chart a disco
        output_dir: Directorio donde guardar el chart

    Returns:
        Tuple[str, str]: (base64_image, filepath)
    """
    try:
        chart_b64 = capture_chart_sync(
            symbol=symbol,
            timeframe=timeframe,
            required_indicators=required_indicators or [
                "Supertrend", "SAR", "Volume", "EMA", "VWAP"
            ]
        )

        if not chart_b64 or len(chart_b64) < 500:
            logger.warning(f"⚠️ Captura resultó en imagen vacía o muy pequeña: {len(chart_b64) if chart_b64 else 0} chars")
            fallback_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            return fallback_b64, ""

        # Si se requiere guardar en disco
        filepath = ""
        if save_chart and chart_b64:
            try:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)

                filename = f"{symbol}_{timeframe}m_chart.png"
                filepath = str(output_path / filename)

                # Decodificar base64 y guardar
                image_bytes = base64.b64decode(chart_b64)
                with open(filepath, 'wb') as f:
                    f.write(image_bytes)

                logger.info(f"💾 Gráfico guardado en: {filepath} ({len(image_bytes)} bytes)")

            except Exception as e:
                logger.error(f"❌ Error guardando gráfico: {e}")
                filepath = ""

        logger.info(f"✅ Gráfico generado exitosamente: {len(chart_b64)} chars base64")
        return chart_b64, filepath

    except Exception as e:
        logger.error(f"❌ Error generando gráfico con Playwright: {e}", exc_info=True)
        fallback_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        return fallback_b64, ""


# Alias para compatibilidad con código legacy
def generate_chart_for_visual_agent(
    symbol: str,
    timeframe: str,
    close_buf=None,
    high_buf=None,
    low_buf=None,
    vol_buf=None,
    tech_metrics: dict[str, Any] | None = None,
    lookback_periods: int = 100,
    save_chart: bool = True,
    required_indicators: list[str] | None = None
) -> tuple[str, str]:
    """
    Wrapper para compatibilidad con código legacy que pasa buffers de datos.

    NOTA: Esta versión ignora los buffers porque usa captura real de TradingView,
    donde los datos ya están calculados y visibles en el gráfico.

    Args:
        symbol: Símbolo a capturar
        timeframe: Timeframe en minutos
        close_buf, high_buf, low_buf, vol_buf: Ignorados (legacy compatibility)
        tech_metrics: Ignorado (legacy compatibility)
        lookback_periods: Ignorado (legacy compatibility)
        save_chart: Si guardar en disco

    Returns:
        Tuple[str, str]: (base64_image, filepath)
    """
    logger.debug(f"📞 generate_chart_for_visual_agent llamado para {symbol} {timeframe}m")
    logger.debug("   (buffers de datos ignorados - usando captura real de TradingView)")

    return generate_chart_for_visual_agent_playwright(
        symbol=symbol,
        timeframe=timeframe,
        save_chart=save_chart,
        required_indicators=required_indicators
    )


if __name__ == "__main__":
    # Test rápido
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("🧪 Test de Chart Generator con Playwright")
    print("=" * 50)

    chart_b64, chart_path = generate_chart_for_visual_agent_playwright(
        symbol="SOLUSDT",
        timeframe="1",
        save_chart=True
    )

    if chart_b64 and len(chart_b64) > 500:
        print(f"✅ Chart generado: {len(chart_b64)} chars")
        if chart_path:
            print(f"💾 Guardado en: {chart_path}")
    else:
        print("❌ Chart generation failed")
