# 🦅 FENIX v3 - PAPER TRADING TEST - GUÍA DE EJECUCIÓN

## 📋 RESUMEN DE PREPARACIÓN

Hemos configurado todo para una prueba de paper trading con Ollama Cloud (0 costo). Aquí está el checklist:

### ✅ ARCHIVOS CREADOS

1. **`fenix_v3_agent_organization.py`** - Organización óptima de LLMs por agente
2. **`run_paper_test.sh`** - Script de ejecución automatizado
3. **`crypto_sentiment_system.py`** - Sistema de sentiment gratis (ya existía)

### 🧠 ORGANIZACIÓN DE AGENTES → LLMs (OLLAMA CLOUD)

| Agente           | Modelo Asignado          | Por qué                                                                 |
| ---------------- | ------------------------ | ----------------------------------------------------------------------- |
| **Sentiment**    | `qwen3:next`             | 128K context window perfecto para procesar 50+ noticias + on-chain data |
| **Technical**    | `deepseek-v3.2`          | Excelente en razonamiento numérico sobre indicadores (RSI, MACD)        |
| **Visual**       | `gemini-3-flash-preview` | Multimodal ultra-rápido para análisis de charts base64                  |
| **QABBA**        | `kimi-k2-thinking`       | Modo thinking para análisis profundo de microestructura                 |
| **Decision**     | `deepseek-v3.1:671b`     | Modelo más potente (671B params) para decisiones críticas               |
| **Risk Manager** | `nemotron-3-nano`        | Rápido y eficiente (30B params) para evaluaciones de riesgo             |

### 🔄 CADENA DE FALLBACK

Si un modelo falla, automáticamente usa:

1. Modelo Ollama Cloud alternativo
2. Groq free tier (backup)
3. Modelo local si está disponible

### 💰 API KEYS CONFIGURADAS (Todas Gratis)

```bash
✅ OLLAMA_CLOUD_API_KEY=<configured_in_env>
✅ GROQ_API_KEY=<configured_in_env>
✅ HUGGINGFACE_API_KEY=<configured_in_env>
```

### 🚀 CÓMO EJECUTAR

#### Opción 1: Script Automatizado (Recomendado)

```bash
# En tu terminal local
./run_paper_test.sh
```

#### Opción 2: Manual

```bash
# 1. Activar entorno
source .venv/bin/activate

# 2. Configurar variables
export LLM_PROFILE=ollama_cloud
export TRADING_MODE=testnet
export ENABLE_PAPER_TRADING=true

# 3. Ejecutar
python run_fenix.py \
    --mode paper \
    --symbol BTCUSDT \
    --timeframe 5m \
    --interval 60 \
    --dry-run
```

### 📊 QUÉ ESPERAR EN LA PRUEBA

1. **Inicio**: Verificación de conexión a Ollama Cloud y modelos
2. **Ciclo de Trading**: Cada 60 segundos:
   - Obtiene datos de mercado de Binance Testnet
   - Ejecuta los 6 agentes en paralelo (LangGraph)
   - Cada agente usa su LLM asignado
   - El agente de decisión sintetiza todo
   - Risk Manager evalúa y aprueba/vetea
   - Muestra decisión: BUY/SELL/HOLD + confianza
3. **Logging**: Todos los prompts y respuestas se guardan en `logs/`

### 🎯 VERIFICACIÓN DE FUNCIONAMIENTO

Durante la ejecución deberías ver:

```
🦅 FENIX AI TRADING BOT
========================
Modo: PAPER
Symbol: BTCUSDT @ $67,420.50
Timeframe: 5m

✅ Ollama Cloud OK - Sentiment: qwen3:next
✅ Ollama Cloud OK - Technical: deepseek-v3.2
✅ Ollama Cloud OK - Visual: gemini-3-flash-preview
✅ Binance Testnet OK

🔄 Starting analysis cycle
📊 Kline closed: 67450.30 (H:67600 L:67200)
📈 Technical: BUY (confidence: HIGH)
📊 QABBA: BUY_QABBA (confidence: 0.78)
💭 Sentiment: POSITIVE (sentiment_score: 65/100)
👁️ Visual: BUY (pattern: Bull Flag)

📋 FINAL DECISION: BUY (HIGH)
📝 Reasoning: Convergencia de señales alcistas...
⏱️ Analysis cycle completed in 4.2s
```

### 🛠️ TROUBLESHOOTING

#### Problema: "Modelo no encontrado"

**Solución**: El sistema usará automáticamente el fallback. Verifica conectividad:

```bash
curl https://api.ollama.ai/v1/models \
  -H "Authorization: Bearer $OLLAMA_CLOUD_API_KEY"
```

#### Problema: "Rate limit exceeded"

**Solución**: El sistema alternará automáticamente entre Ollama Cloud y Groq free tier.

#### Problema: "Binance connection failed"

**Solución**: Continuará en modo simulado (precios de backup). Verifica:

```bash
curl https://testnet.binancefuture.com/fapi/v1/ping
```

### 📁 ESTRUCTURA DE RESULTADOS

```
logs/
├── llm_responses/
│   ├── sentiment_agent/
│   │   ├── 20250130_143052_prompt.txt
│   │   ├── 20250130_143052_raw_response.txt
│   │   └── 20250130_143052_output.json
│   ├── technical_agent/
│   ├── visual_agent/
│   ├── qabba_agent/
│   └── decision_agent/
├── fenix_20250130_143052.log
└── signals.jsonl
```

### 🎓 PRÓXIMOS PASOS DESPUÉS DE LA PRUEBA

1. **Analizar logs**: Revisa `logs/llm_responses/` para ver qué decidió cada agente
2. **Ajustar prompts**: Edita `src/prompts/agent_prompts.py` si los agentes no son precisos
3. **Tuning de pesos**: Ajusta ponderaciones de agentes en `config/fenix.yaml`
4. **Activar Visual**: Si tienes GPU, prueba el agente visual con charts reales
5. **Live Trading**: Cuando estés seguro, agrega `--allow-live` (¡solo si sabes lo que haces!)

### 📞 COMANDOS ÚTILES

```bash
# Ver estado de modelos Ollama
python fenix_v3_agent_organization.py

# Ejecutar solo el test de organización
python -c "from fenix_v3_agent_organization import print_agent_organization; print_agent_organization()"

# Ver logs en tiempo real
tail -f logs/fenix_*.log

# Limpiar cache
rm -rf cache/* logs/*
```

---

## 🚀 LISTO PARA DESPEGAR

Todo está configurado para una prueba 100% gratuita usando:

- Ollama Cloud (modelos potentes en la nube)
- Binance Testnet (fondos virtuales)
- Paper Trading (sin riesgo de dinero real)

**Ejecuta ahora:** `./run_paper_test.sh`

¡Vamos pepe! A probar esta bestia! 🔥🦅
