#!/usr/bin/env python3
"""
Ejemplo de uso del Chart System para el Visual Agent.

Este script demuestra cómo integrar el sistema de charts
con el agente visual para análisis de trading.

Uso:
    python scripts/demo_chart_system.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.chart_provider import (
    start_scheduler,
    stop_scheduler,
    get_chart,
    get_fresh_chart,
    ensure_fresh_charts,
    get_scheduler_status,
)

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


async def demo_visual_agent_workflow():
    """
    Demo del workflow típico del Visual Agent.
    
    Muestra cómo:
    1. Iniciar el scheduler de charts
    2. Obtener charts frescos para análisis
    3. Usar múltiples timeframes
    4. Integrar con el prompt del agente
    """
    print("\n" + "="*60)
    print("📊 DEMO: Chart System para Visual Agent")
    print("="*60)
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    timeframes = ["15m", "1h", "4h"]
    
    # 1. Iniciar el scheduler
    print("\n1️⃣ Iniciando Chart Scheduler...")
    start_scheduler(symbols=symbols, timeframes=timeframes)
    
    # Esperar captura inicial
    print("   ⏳ Esperando captura inicial de todos los charts...")
    await asyncio.sleep(3)
    
    # 2. Verificar status
    print("\n2️⃣ Estado del scheduler:")
    status = get_scheduler_status()
    print(f"   Running: {status['running']}")
    print(f"   Cache entries: {status['cache']['valid_entries']}")
    print(f"   Uptime: {status['uptime_human']}")
    
    # 3. Obtener charts para análisis
    print("\n3️⃣ Obteniendo charts para análisis...")
    
    for symbol in symbols:
        print(f"\n   📈 {symbol}:")
        for tf in timeframes:
            chart = get_chart(symbol, tf)
            if chart and chart.image_b64:
                print(f"      {tf}: ✅ {len(chart.image_b64):,} bytes (age: {chart.age_seconds:.1f}s)")
            else:
                print(f"      {tf}: ❌ No disponible")
    
    # 4. Demo de multi-timeframe analysis
    print("\n4️⃣ Multi-Timeframe Analysis para BTCUSDT...")
    charts = await ensure_fresh_charts(["BTCUSDT"], ["1m", "5m", "15m", "1h", "4h"])
    
    analysis_prompt = generate_analysis_prompt("BTCUSDT", charts)
    print("\n   📝 Prompt generado para Visual Agent:")
    print("   " + "-"*50)
    print(analysis_prompt[:500] + "..." if len(analysis_prompt) > 500 else analysis_prompt)
    
    # 5. Cleanup
    print("\n5️⃣ Deteniendo scheduler...")
    stop_scheduler()
    print("   ✅ Done!")
    
    print("\n" + "="*60)
    print("✅ Demo completado")
    print("="*60)


def generate_analysis_prompt(symbol: str, charts: dict) -> str:
    """
    Genera el prompt de análisis para el Visual Agent.
    
    Este es el texto que acompañaría a las imágenes de los charts
    cuando se envían al modelo de visión.
    """
    prompt_parts = [
        f"# Análisis Visual Multi-Timeframe: {symbol}",
        "",
        "Se te proporcionan los siguientes charts para análisis:",
        "",
    ]
    
    indicators = ["EMA 9/21", "Bollinger Bands", "RSI", "VWAP", "Volume"]
    
    for key, chart in charts.items():
        if chart and chart.image_b64:
            tf = key.split("_")[1]
            prompt_parts.append(f"## Chart {tf}")
            prompt_parts.append(f"- Timeframe: {tf}")
            prompt_parts.append(f"- Indicadores: {', '.join(indicators)}")
            prompt_parts.append(f"- Edad: {chart.age_seconds:.0f} segundos")
            prompt_parts.append("")
    
    prompt_parts.extend([
        "## Instrucciones de Análisis",
        "",
        "1. **Tendencia General**: Identifica la tendencia en cada timeframe",
        "2. **Niveles Clave**: Identifica soportes y resistencias importantes",
        "3. **Indicadores**:",
        "   - RSI: ¿Sobrecompra/sobreventa?",
        "   - BBands: ¿Precio en banda superior/inferior/media?",
        "   - EMAs: ¿Cruce reciente? ¿Precio arriba o abajo?",
        "   - VWAP: ¿Precio por encima o por debajo?",
        "4. **Confluencia**: ¿Qué timeframes confirman la misma señal?",
        "5. **Acción Sugerida**: LONG / SHORT / HOLD con justificación",
        "",
        "Responde de forma estructurada y concisa.",
    ])
    
    return "\n".join(prompt_parts)


async def demo_quick_capture():
    """Demo de captura rápida sin scheduler."""
    print("\n" + "="*60)
    print("📸 DEMO: Captura Rápida (sin scheduler)")
    print("="*60)
    
    print("\n📊 Capturando BTCUSDT 4h...")
    chart = get_fresh_chart("BTCUSDT", "4h")
    
    print(f"\n✅ Chart capturado:")
    print(f"   Símbolo: {chart.symbol}")
    print(f"   Timeframe: {chart.timeframe}")
    print(f"   Tamaño: {len(chart.image_b64):,} bytes")
    print(f"   Tiempo: {chart.generation_time_ms:.0f}ms")
    print(f"   Indicadores: {chart.indicators}")
    
    if chart.filepath:
        print(f"   Archivo: {chart.filepath}")


if __name__ == "__main__":
    print("\n🚀 Chart System Demo\n")
    print("Selecciona una opción:")
    print("1. Demo completo con scheduler")
    print("2. Captura rápida sin scheduler")
    
    choice = input("\nOpción (1/2): ").strip() or "2"
    
    if choice == "1":
        asyncio.run(demo_visual_agent_workflow())
    else:
        asyncio.run(demo_quick_capture())
