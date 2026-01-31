# 📊 Chart Capture System

Sistema autónomo de captura de charts multi-timeframe y multi-fuente para FenixAI.

## 🎯 Características Principales

### Sistema Dual de Charts
El sistema combina **dos fuentes** de información visual:

1. **Charts Generados (Plotly)** - Análisis técnico clásico:
   - EMAs (9, 21, 50, 200)
   - Bollinger Bands
   - RSI, MACD, VWAP
   - Volumen con colores
   - Generación rápida (~1.8s)

2. **Charts Externos (Playwright)** - Indicadores avanzados:
   - Liquidation Heatmap (Coinglass)
   - Open Interest por exchange
   - Funding Rates históricos
   - TradingView con indicadores adicionales

### Captura Inicial Completa
Al iniciar, el sistema captura **TODOS los timeframes** para cada símbolo,
asegurando que el caché esté lleno desde el inicio.

## Arquitectura

```
┌──────────────────────────────────────────────────────────┐
│                  Chart Capture Service                    │
│                (run_chart_service.py)                     │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │             ChartCaptureScheduler                    │ │
│  │         (chart_capture_scheduler.py)                 │ │
│  │                                                      │ │
│  │  ┌─────────────┐   ┌─────────────┐                  │ │
│  │  │ APScheduler │   │ ChartCache  │                  │ │
│  │  │ Background  │   │ Thread-safe │                  │ │
│  │  │   Jobs      │   │   LRU       │                  │ │
│  │  └──────┬──────┘   └──────┬──────┘                  │ │
│  │         │                 │                          │ │
│  │         ▼                 ▼                          │ │
│  │  ┌─────────────────────────────────────────────┐    │ │
│  │  │          ProfessionalChartGenerator         │    │ │
│  │  │           (Plotly + Kaleido)                │    │ │
│  │  └─────────────────────────────────────────────┘    │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                    chart_provider.py                      │
│              (API simple para consumidores)               │
├──────────────────────────────────────────────────────────┤
│  get_chart(symbol, timeframe)     → ChartSnapshot | None │
│  get_fresh_chart(symbol, tf)      → ChartSnapshot        │
│  ensure_fresh_charts(symbols, tfs) → Dict[str, Snapshot] │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│            Visual Agent / Trading Engine                  │
│           (Consume charts frescos del cache)              │
└──────────────────────────────────────────────────────────┘
```

## Componentes

### 1. ChartCaptureScheduler (`src/tools/chart_capture_scheduler.py`)

El scheduler principal que:
- Programa capturas por timeframe con intervalos óptimos
- Mantiene un cache thread-safe de charts
- Se auto-recupera en caso de errores
- Provee métricas de salud

**Configuración de Timeframes:**

| Timeframe | Intervalo Captura | TTL Cache | Prioridad |
|-----------|------------------|-----------|-----------|
| 1m        | 30s              | 60s       | 1 (alta)  |
| 5m        | 60s              | 180s      | 2         |
| 15m       | 180s             | 600s      | 3         |
| 1h        | 300s             | 1800s     | 4         |
| 4h        | 600s             | 3600s     | 5         |
| 1d        | 900s             | 7200s     | 6 (baja)  |

### 2. ChartProvider (`src/tools/chart_provider.py`)

Interface simple para obtener charts:

```python
from src.tools.chart_provider import get_chart, ensure_fresh_charts

# Obtener un chart del cache
chart = get_chart("BTCUSDT", "15m")
if chart:
    image_b64 = chart.image_b64

# Asegurar charts frescos antes de análisis
charts = await ensure_fresh_charts(
    ["BTCUSDT", "ETHUSDT"],
    ["15m", "1h", "4h"]
)
```

### 3. ChartService (`scripts/run_chart_service.py`)

Servicio daemon para ejecución autónoma:

```bash
# Status
python scripts/run_chart_service.py --status

# Iniciar (foreground)
python scripts/run_chart_service.py

# Iniciar como daemon
python scripts/run_chart_service.py --daemon

# Detener
python scripts/run_chart_service.py --stop

# Con opciones
python scripts/run_chart_service.py \
    --symbols BTCUSDT ETHUSDT SOLUSDT \
    --timeframes 1m 5m 15m 1h 4h \
    --verbose
```

## Uso

### Integración con Trading Engine

```python
from src.tools.chart_provider import start_scheduler, get_chart

# Al inicio de la aplicación
start_scheduler(
    symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    timeframes=["1m", "5m", "15m", "1h"]
)

# En cada loop de trading
async def trading_loop():
    # Los charts ya están pre-cacheados y frescos
    chart = get_chart("BTCUSDT", "15m")
    
    if chart and chart.is_valid():
        # Usar chart.image_b64 para análisis visual
        analysis = await visual_agent.analyze(chart.image_b64)
```

### Context Manager

```python
from src.tools.chart_provider import ChartSchedulerContext

async with ChartSchedulerContext(symbols=["BTCUSDT"]) as scheduler:
    # Charts disponibles
    chart = get_chart("BTCUSDT", "15m")
```

## Tests

```bash
# Ejecutar suite de tests
python scripts/test_chart_scheduler.py
```

**Resultados esperados:**
- ✅ Captura Individual (~2s primera vez)
- ✅ Sistema de Caché (<1ms hit)
- ✅ Multi-Timeframe (paralelo)
- ✅ Scheduler Background
- ✅ Robustez (fallback a mock data)

## Archivos

```
src/tools/
├── chart_capture_scheduler.py   # Scheduler principal
├── chart_provider.py            # API para consumidores
└── professional_chart_generator.py  # Generador Plotly

scripts/
├── run_chart_service.py         # Servicio daemon
└── test_chart_scheduler.py      # Tests

cache/charts/                    # Charts generados
logs/                            # Logs del servicio
```

## Rendimiento

| Operación | Tiempo Promedio |
|-----------|-----------------|
| Captura individual | ~1800ms |
| Cache hit | <0.1ms |
| Multi-timeframe (4 TFs) | ~2s (paralelo) |
| Startup scheduler | ~5s |

## Robustez

El sistema incluye:

1. **Fallback automático**: Si Binance falla, usa datos mock
2. **Coalesce de jobs**: Combina ejecuciones perdidas
3. **Max instances**: Evita ejecuciones concurrentes del mismo job
4. **Auto-recovery**: Se reinicia tras errores (max 5 en 5min)
5. **Cleanup automático**: Limpia cache expirado cada 5 min
6. **Thread-safe cache**: Lock RLock para acceso concurrente
7. **Graceful shutdown**: Maneja SIGTERM/SIGINT

## Dependencias

```
apscheduler>=3.10.0
plotly>=5.18.0
kaleido>=0.2.1
python-binance>=1.0.0
```
