#!/usr/bin/env python3
"""
FENIX v3 - LLM Agent Organization & Paper Trading Test
Configuración óptima de LLMs por agente usando Ollama Cloud
"""

import os
from dataclasses import dataclass
from typing import Optional, Dict, List

# =============================================================================
# CONFIGURACIÓN DE AGENTES A LLMs - FENIX v3
# =============================================================================

@dataclass
class LLMConfig:
    agent_name: str
    model: str
    provider: str
    temperature: float
    max_tokens: int
    timeout: int
    reasoning: str
    use_case: str

# ORGANIZACIÓN ÓPTIMA DE AGENTES A LLMs
# Basado en: capacidades del modelo + requerimientos del agente + latencia

AGENT_LLM_ASSIGNMENTS = {
    # -----------------------------------------------------------------------------
    # 1. SENTIMENT AGENT → qwen3:next (Mejor para análisis de texto largo)
    # -----------------------------------------------------------------------------
    "sentiment": LLMConfig(
        agent_name="Sentiment Analyst",
        model="qwen3:next",
        provider="ollama_cloud",
        temperature=0.15,
        max_tokens=4096,  # Ventana grande para muchas noticias
        timeout=60,
        reasoning="Ventana de contexto grande (128K) perfecta para procesar múltiples noticias + social data + on-chain metrics en un solo prompt. Qwen3 es excelente en análisis de sentimiento multilingüe.",
        use_case="Procesar 50+ noticias + métricas on-chain + Fear&Greed en un solo análisis"
    ),
    
    # -----------------------------------------------------------------------------
    # 2. TECHNICAL AGENT → deepseek-v3.2 (Óptimo para razonamiento numérico)
    # -----------------------------------------------------------------------------
    "technical": LLMConfig(
        agent_name="Technical Analyst", 
        model="deepseek-v3.2",
        provider="ollama_cloud",
        temperature=0.1,  # Muy determinístico para números
        max_tokens=2048,
        timeout=90,
        reasoning="DeepSeek v3.2 tiene excelente rendimiento en tareas numéricas y razonamiento lógico sobre indicadores técnicos (RSI, MACD, Bollinger). Muy preciso en cálculos.",
        use_case="Análisis de 10+ indicadores técnicos con cálculos precisos"
    ),
    
    # -----------------------------------------------------------------------------
    # 3. VISUAL AGENT → gemini-3-flash-preview (Multimodal rápido)
    # -----------------------------------------------------------------------------
    "visual": LLMConfig(
        agent_name="Visual Analyst",
        model="gemini-3-flash-preview",
        provider="ollama_cloud", 
        temperature=0.1,
        max_tokens=2048,
        timeout=120,
        reasoning="Gemini 3 Flash es multimodal ultra-rápido optimizado para visión. Puede analizar charts (base64) en <2 segundos. Ideal para reconocimiento de patrones chartistas.",
        use_case="Análisis de charts técnicos (velas, soportes, resistencias, patrones)"
    ),
    
    # -----------------------------------------------------------------------------
    # 4. QABBA AGENT → kimi-k2.5:cloud (1T+ parámetros - reasoning ultra profundo)
    # -----------------------------------------------------------------------------
    "qabba": LLMConfig(
        agent_name="QABBA Analyst",
        model="kimi-k2.5:cloud",
        provider="ollama_cloud",
        temperature=0.05,  # Muy bajo para análisis cuantitativo preciso
        max_tokens=4000,
        timeout=180,  # Más tiempo por el modelo más grande
        supports_vision=False,
        reasoning="kimi-k2.5 con 1T+ parámetros y capacidad de reasoning profundo. Es el modelo más potente disponible para análisis de microestructura y flujo de órdenes. Detecta patrones sutiles de OBI, CVD, y absorción que otros modelos no ven.",
        use_case="Análisis cuantitativo avanzado de microestructura: Order Book Imbalance, CVD, liquidez, clusters de órdenes, detección de ballenas"
    ),
    
    # -----------------------------------------------------------------------------
    # 5. DECISION AGENT → kimi-k2.5:cloud (1T+ parámetros - máxima inteligencia)
    # -----------------------------------------------------------------------------
    "decision": LLMConfig(
        agent_name="Decision Agent",
        model="kimi-k2.5:cloud",  # El modelo más potente disponible
        provider="ollama_cloud",
        temperature=0.10,
        max_tokens=4000,
        timeout=200,  # Tiempo extra para reasoning profundo
        supports_vision=False,
        reasoning="kimi-k2.5:cloud con 1T+ parámetros. El modelo más inteligente disponible para sintetizar 4+ análisis de agentes y tomar decisiones de trading con máxima precisión. Capaz de detectar correlaciones y contradicciones sutiles entre fuentes.",
        use_case="Síntesis final de Technical + QABBA + Visual + Sentiment → Decisión crítica BUY/SELL/HOLD con razonamiento profundo"
    ),
    
    # -----------------------------------------------------------------------------
    # 6. RISK MANAGER → nemotron-3-nano (Rápido y conservador)
    # -----------------------------------------------------------------------------
    "risk_manager": LLMConfig(
        agent_name="Risk Manager",
        model="nemotron-3-nano",
        provider="ollama_cloud",
        temperature=0.1,
        max_tokens=1500,
        timeout=45,
        reasoning="Nemotron 3 Nano (30B) es rápido (<<1s) y eficiente para evaluaciones de riesgo. No necesita ser el más grande, necesita ser rápido para no bloquear el pipeline.",
        use_case="Evaluación de riesgo: position sizing, circuit breakers, drawdown limits"
    ),
}

# =============================================================================
# FALLBACK HIERARCHY (Por si un modelo falla)
# =============================================================================

FALLBACK_CHAIN = {
    "sentiment": ["qwen3:next", "qwen2.5:14b", "groq-llama-3.3-70b"],
    "technical": ["deepseek-v3.2", "qwen2.5:14b", "groq-mixtral-8x7b"],
    "visual": ["gemini-3-flash-preview", "qwen2.5-vl:72b", "groq-llama-4-scout"],
    "qabba": ["kimi-k2-thinking", "deepseek-r1:32b", "groq-llama-3.3-70b"],
    "decision": ["deepseek-v3.1:671b", "qwen3:next", "groq-llama-3.3-70b"],
    "risk_manager": ["nemotron-3-nano", "qwen2.5:7b", "groq-llama-3.3-70b"],
}

# =============================================================================
# PAPER TRADING TEST CONFIGURATION
# =============================================================================

PAPER_TRADING_CONFIG = {
    "mode": "paper",
    "symbol": "BTCUSDT",
    "timeframe": "5m",  # Más rápido para pruebas
    "testnet": True,
    "initial_balance": 10000,  # USDT virtual
    "max_risk_per_trade": 0.02,  # 2%
    "agents_enabled": {
        "sentiment": True,
        "technical": True,
        "visual": True,
        "qabba": True,
        "decision": True,
        "risk_manager": True,
    },
    "llm_profile": "ollama_cloud",
    "update_interval_seconds": 60,
}

# =============================================================================
# FUNCIÓN DE RESUMEN
# =============================================================================

def print_agent_organization():
    """Imprime la organización de agentes de forma legible"""
    print("\n" + "="*80)
    print("🦅 FENIX v3 - ORGANIZACIÓN DE AGENTES LLM")
    print("="*80)
    
    for agent_key, config in AGENT_LLM_ASSIGNMENTS.items():
        print(f"\n📊 {config.agent_name}")
        print(f"   Modelo: {config.model}")
        print(f"   Provider: {config.provider}")
        print(f"   Temp: {config.temperature} | Tokens: {config.max_tokens} | Timeout: {config.timeout}s")
        print(f"   💡 {config.reasoning}")
        print(f"   🎯 {config.use_case}")
        print("-" * 80)
    
    print("\n🔄 CADENA DE FALLBACK:")
    for agent, chain in FALLBACK_CHAIN.items():
        print(f"   {agent}: {' → '.join(chain)}")
    
    print("\n💰 PAPER TRADING CONFIG:")
    for key, value in PAPER_TRADING_CONFIG.items():
        print(f"   {key}: {value}")
    
    print("="*80)

if __name__ == "__main__":
    print_agent_organization()
    
    # Validar que tenemos API keys
    ollama_key = os.getenv("OLLAMA_CLOUD_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    hf_key = os.getenv("HUGGINGFACE_API_KEY")
    
    print("\n🔑 API Keys detectadas:")
    print(f"   Ollama Cloud: {'✅ CONFIGURADA' if ollama_key else '❌ NO CONFIGURADA'}")
    print(f"   Groq: {'✅ CONFIGURADA' if groq_key else '❌ NO CONFIGURADA'}")
    print(f"   HuggingFace: {'✅ CONFIGURADA' if hf_key else '❌ NO CONFIGURADA'}")
    
    if ollama_key:
        print(f"   Ollama Key (primeros 20 chars): {ollama_key[:20]}...")
    
    print("\n✅ Listo para iniciar Paper Trading con Ollama Cloud!")
    print("   Ejecuta: python run_fenix.py --mode paper --symbol BTCUSDT")
