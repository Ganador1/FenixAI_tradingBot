# src/core/model_analysis_recommendation.py
"""
Análisis Comparativo y Recomendación de Modelos para Timeframe 1min.

Este script analiza las características teóricas de cada modelo disponible
y genera recomendaciones iniciales optimizadas para producción con timeframe 1min.

Factores considerados:
- Tamaño del modelo (parámetros) - afecta latencia
- Arquitectura (MoE vs dense) - afecta throughput
- Especialización (coder, vision, reasoning)
- Benchmarks conocidos (si disponibles)
- Requisitos del agente (técnico, sentimiento, decisión, etc.)

Modelos disponibles en Ollama Cloud:
1.  rnj-1:8b-cloud              - 8B, rápido, genérico
2.  ministral-3:14b-cloud      - 14B, Mistral, buen balance
3.  devstral-small-2:24b-cloud - 24B, razonamiento código
4.  nemotron-3-nano:30b-cloud  - 30B, NVIDIA, técnico
5.  kimi-k2.5:cloud            - Unknown, Moonshot AI
6.  kimi-k2-thinking:cloud     - Thinking model
7.  deepseek-v3.2:cloud        - DeepSeek, buen balance
8.  minimax-m2.1:cloud         - MiniMax, decisiones
9.  glm-4.7:cloud              - Zhipu, chino-inglés
10. deepseek-v3.1:671b-cloud   - 671B MoE, muy lento
11. gpt-oss:120b-cloud         - 120B, OpenAI-style
12. qwen3-coder:480b-cloud     - 480B, coder especializado
13. qwen3-next:80b-cloud       - 80B, gran capacidad
14. gemini-3-flash-preview     - Vision + Flash = rápido
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from datetime import datetime


@dataclass
class ModelProfile:
    """Perfil de un modelo LLM."""
    name: str
    params_b: float  # Parámetros en billones
    architecture: str  # dense, moe, unknown
    specialization: list[str]  # coding, vision, reasoning, chat, technical
    expected_latency_1k_tokens: float  # ms (estimado)
    throughput_tokens_per_sec: float  # estimado
    best_for: list[str]  # Agentes recomendados
    not_recommended_for: list[str]
    score_1min_tf: float  # 0-100, optimizado para 1min


# Perfiles de modelos basados en especificaciones conocidas
MODEL_PROFILES: dict[str, ModelProfile] = {
    "rnj-1:8b-cloud": ModelProfile(
        name="rnj-1:8b-cloud",
        params_b=8,
        architecture="dense",
        specialization=["chat", "fast"],
        expected_latency_1k_tokens=800,  # ~0.8s para 1k tokens
        throughput_tokens_per_sec=1200,
        best_for=["sentiment", "qabba"],  # Tareas simples, rápidas
        not_recommended_for=["decision", "risk_manager"],  # Requiere más reasoning
        score_1min_tf=85,  # Muy rápido, bueno para 1min
    ),

    "ministral-3:14b-cloud": ModelProfile(
        name="ministral-3:14b-cloud",
        params_b=14,
        architecture="dense",
        specialization=["chat", "instruct", "technical"],
        expected_latency_1k_tokens=1200,  # ~1.2s
        throughput_tokens_per_sec=800,
        best_for=["technical", "sentiment", "qabba"],
        not_recommended_for=[],
        score_1min_tf=92,  # Excelente balance velocidad/calidad
    ),

    "devstral-small-2:24b-cloud": ModelProfile(
        name="devstral-small-2:24b-cloud",
        params_b=24,
        architecture="dense",
        specialization=["coding", "reasoning", "technical"],
        expected_latency_1k_tokens=2000,  # ~2s
        throughput_tokens_per_sec=500,
        best_for=["decision", "risk_manager", "technical"],
        not_recommended_for=["sentiment"],  # Overkill para sentimiento
        score_1min_tf=88,  # Bueno para reasoning, pero más lento
    ),

    "nemotron-3-nano:30b-cloud": ModelProfile(
        name="nemotron-3-nano:30b-cloud",
        params_b=30,
        architecture="dense",
        specialization=["technical", "instruct", "chat"],
        expected_latency_1k_tokens=2500,  # ~2.5s
        throughput_tokens_per_sec=400,
        best_for=["technical", "qabba", "decision"],
        not_recommended_for=["sentiment"],
        score_1min_tf=85,
    ),

    "kimi-k2.5:cloud": ModelProfile(
        name="kimi-k2.5:cloud",
        params_b=32,  # Estimado
        architecture="dense",
        specialization=["chat", "long_context", "reasoning"],
        expected_latency_1k_tokens=2800,
        throughput_tokens_per_sec=350,
        best_for=["decision", "risk_manager"],
        not_recommended_for=["sentiment", "technical"],
        score_1min_tf=82,
    ),

    "kimi-k2-thinking:cloud": ModelProfile(
        name="kimi-k2-thinking:cloud",
        params_b=32,  # Estimado
        architecture="dense",
        specialization=["reasoning", "coding", "thinking"],
        expected_latency_1k_tokens=4000,  # Más lento por thinking
        throughput_tokens_per_sec=250,
        best_for=["decision"],  # Thinking es bueno para decisiones
        not_recommended_for=["technical", "sentiment", "qabba", "risk_manager"],
        score_1min_tf=70,  # Thinking es lento para 1min
    ),

    "deepseek-v3.2:cloud": ModelProfile(
        name="deepseek-v3.2:cloud",
        params_b=16,  # Estimado (version pequeña)
        architecture="dense",
        specialization=["coding", "technical", "chat"],
        expected_latency_1k_tokens=1500,
        throughput_tokens_per_sec=650,
        best_for=["technical", "qabba", "sentiment"],
        not_recommended_for=[],
        score_1min_tf=90,
    ),

    "minimax-m2.1:cloud": ModelProfile(
        name="minimax-m2.1:cloud",
        params_b=24,  # Estimado
        architecture="dense",
        specialization=["chat", "decision", "reasoning"],
        expected_latency_1k_tokens=2200,
        throughput_tokens_per_sec=450,
        best_for=["decision", "risk_manager"],
        not_recommended_for=["sentiment"],
        score_1min_tf=86,
    ),

    "glm-4.7:cloud": ModelProfile(
        name="glm-4.7:cloud",
        params_b=9,  # Estimado
        architecture="dense",
        specialization=["chat", "multilingual"],
        expected_latency_1k_tokens=1000,
        throughput_tokens_per_sec=900,
        best_for=["sentiment"],  # Bueno para análisis de texto
        not_recommended_for=["technical", "decision"],
        score_1min_tf=88,
    ),

    # Modelos GRANDES (lentos, no recomendados para 1min pero útiles para referencia)
    "deepseek-v3.1:671b-cloud": ModelProfile(
        name="deepseek-v3.1:671b-cloud",
        params_b=671,
        architecture="moe",
        specialization=["coding", "reasoning", "technical"],
        expected_latency_1k_tokens=8000,  # ~8s muy lento
        throughput_tokens_per_sec=120,
        best_for=[],  # No recomendado para 1min
        not_recommended_for=["technical", "sentiment", "qabba", "decision", "risk_manager", "visual"],
        score_1min_tf=30,  # Demasiado lento para 1min
    ),

    "gpt-oss:120b-cloud": ModelProfile(
        name="gpt-oss:120b-cloud",
        params_b=120,
        architecture="dense",
        specialization=["chat", "general"],
        expected_latency_1k_tokens=5000,
        throughput_tokens_per_sec=200,
        best_for=[],  # Lento
        not_recommended_for=["technical", "sentiment", "qabba", "decision", "risk_manager"],
        score_1min_tf=45,
    ),

    "qwen3-coder:480b-cloud": ModelProfile(
        name="qwen3-coder:480b-cloud",
        params_b=480,
        architecture="dense",
        specialization=["coding", "technical"],
        expected_latency_1k_tokens=6000,
        throughput_tokens_per_sec=160,
        best_for=[],  # Especializado en código, no trading
        not_recommended_for=["technical", "sentiment", "qabba", "decision", "risk_manager", "visual"],
        score_1min_tf=35,
    ),

    "qwen3-next:80b-cloud": ModelProfile(
        name="qwen3-next:80b-cloud",
        params_b=80,
        architecture="dense",
        specialization=["chat", "reasoning", "general"],
        expected_latency_1k_tokens=3500,
        throughput_tokens_per_sec=280,
        best_for=["decision"],  # Solo si el pipeline es rápido
        not_recommended_for=["technical", "sentiment", "qabba"],
        score_1min_tf=65,
    ),

    # Vision
    "gemini-3-flash-preview:cloud": ModelProfile(
        name="gemini-3-flash-preview:cloud",
        params_b=8,  # Estimado para versión flash
        architecture="dense",
        specialization=["vision", "fast", "multimodal"],
        expected_latency_1k_tokens=1500,  # Vision es más lento
        throughput_tokens_per_sec=600,
        best_for=["visual"],  # Especializado en visión
        not_recommended_for=["technical", "sentiment", "qabba", "decision", "risk_manager"],
        score_1min_tf=90,  # Flash = rápido incluso con vision
    ),
}


# Requisitos por tipo de agente
AGENT_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "technical": {
        "description": "Análisis técnico de indicadores",
        "needs": ["technical", "instruct"],
        "speed_priority": "high",  # Necesita ser rápido
        "reasoning_priority": "medium",
        "json_precision": "high",  # JSON debe ser preciso
        "max_latency_ms": 5000,  # < 5s
    },
    "sentiment": {
        "description": "Análisis de sentimiento de noticias/social",
        "needs": ["chat", "multilingual"],
        "speed_priority": "high",
        "reasoning_priority": "low",
        "json_precision": "medium",
        "max_latency_ms": 4000,  # < 4s
    },
    "qabba": {
        "description": "Análisis de microestructura (OBI, CVD)",
        "needs": ["technical", "coding"],  # Análisis numérico
        "speed_priority": "high",
        "reasoning_priority": "medium",
        "json_precision": "high",
        "max_latency_ms": 5000,
    },
    "visual": {
        "description": "Análisis visual de gráficos",
        "needs": ["vision"],
        "speed_priority": "medium",
        "reasoning_priority": "medium",
        "json_precision": "high",
        "max_latency_ms": 8000,  # Vision es más lento
    },
    "decision": {
        "description": "Síntesis final y decisión de trading",
        "needs": ["reasoning", "decision"],
        "speed_priority": "medium",  # Puede ser más lento
        "reasoning_priority": "high",  # Necesita buen razonamiento
        "json_precision": "high",
        "max_latency_ms": 10000,  # < 10s
    },
    "risk_manager": {
        "description": "Evaluación de riesgo y gestión",
        "needs": ["reasoning", "technical"],
        "speed_priority": "medium",
        "reasoning_priority": "high",
        "json_precision": "high",
        "max_latency_ms": 8000,
    },
}


def score_model_for_agent(model: ModelProfile, agent: str) -> tuple[float, str]:
    """
    Calcula un score de compatibilidad entre modelo y agente.

    Returns:
        (score, reasoning)
    """
    reqs = AGENT_REQUIREMENTS.get(agent, {})
    if not reqs:
        return 0, "Unknown agent"

    # Verificar si está en not_recommended_for
    if agent in model.not_recommended_for:
        return 20, f"Modelo no recomendado para {agent}"

    score = 0.0
    reasons = []

    # 1. Especialización (30%)
    specialization_match = 0
    for need in reqs["needs"]:
        if need in model.specialization:
            specialization_match += 1
    spec_score = (specialization_match / len(reqs["needs"])) * 30
    score += spec_score
    reasons.append(f"Especialización: {spec_score:.0f}/30")

    # 2. Velocidad (40% para 1min)
    max_acceptable = reqs["max_latency_ms"]
    if model.expected_latency_1k_tokens <= max_acceptable * 0.5:
        speed_score = 40  # Excelente
    elif model.expected_latency_1k_tokens <= max_acceptable:
        speed_score = 30  # Bueno
    elif model.expected_latency_1k_tokens <= max_acceptable * 1.5:
        speed_score = 20  # Aceptable
    else:
        speed_score = 10  # Lento
    score += speed_score
    reasons.append(f"Velocidad: {speed_score}/40 (latencia: {model.expected_latency_1k_tokens:.0f}ms)")

    # 3. Score general del modelo para 1min (20%)
    model_score = (model.score_1min_tf / 100) * 20
    score += model_score
    reasons.append(f"Score 1min: {model_score:.0f}/20")

    # 4. Throughput (10%)
    if model.throughput_tokens_per_sec >= 800:
        throughput_score = 10
    elif model.throughput_tokens_per_sec >= 500:
        throughput_score = 7
    elif model.throughput_tokens_per_sec >= 300:
        throughput_score = 5
    else:
        throughput_score = 3
    score += throughput_score
    reasons.append(f"Throughput: {throughput_score}/10")

    return score, " | ".join(reasons)


def generate_recommendations() -> dict[str, Any]:
    """Genera recomendaciones para cada agente."""
    recommendations = {}

    for agent, reqs in AGENT_REQUIREMENTS.items():
        agent_recs = []

        for model_name, profile in MODEL_PROFILES.items():
            score, reasoning = score_model_for_agent(profile, agent)
            agent_recs.append({
                "model": model_name,
                "score": score,
                "reasoning": reasoning,
                "params_b": profile.params_b,
                "expected_latency_ms": profile.expected_latency_1k_tokens,
            })

        # Ordenar por score
        agent_recs.sort(key=lambda x: x["score"], reverse=True)

        recommendations[agent] = {
            "description": reqs["description"],
            "requirements": reqs["needs"],
            "top_3_models": agent_recs[:3],
            "recommended": agent_recs[0] if agent_recs else None,
        }

    return recommendations


def generate_yaml_config(recommendations: dict) -> str:
    """Genera configuración YAML basada en recomendaciones."""
    lines = [
        "# config/llm_providers.yaml",
        "# Configuración optimizada para timeframe 1min basada en análisis de modelos",
        f"# Generado: {datetime.now().isoformat()}",
        "#",
        "# ANÁLISIS:",
        "# - Prioridad: Velocidad > Razonamiento para 1min",
        "# - Modelos grandes (>80B) excluidos por latencia",
        "# - Especialización por agente según requisitos",
        "",
        "active_profile: 'ollama_1min_optimized'",
        "",
        "ollama_1min_optimized:",
    ]

    agent_yaml_names = {
        "technical": "technical",
        "sentiment": "sentiment",
        "qabba": "qabba",
        "visual": "visual",
        "decision": "decision",
        "risk_manager": "risk_manager",
    }

    for agent, rec in recommendations.items():
        if not rec["recommended"]:
            continue

        model = rec["recommended"]["model"]
        score = rec["recommended"]["score"]

        # Determinar temperature según agente
        if agent == "technical":
            temp = 0.05
            max_tokens = 2500
            timeout = 8
        elif agent == "sentiment":
            temp = 0.15
            max_tokens = 1500
            timeout = 6
        elif agent == "qabba":
            temp = 0.05
            max_tokens = 1200
            timeout = 7
        elif agent == "visual":
            temp = 0.05
            max_tokens = 1500
            timeout = 10
        elif agent == "decision":
            temp = 0.1
            max_tokens = 2000
            timeout = 12
        elif agent == "risk_manager":
            temp = 0.15
            max_tokens = 1500
            timeout = 10
        else:
            temp = 0.1
            max_tokens = 2000
            timeout = 10

        yaml_name = agent_yaml_names.get(agent, agent)

        lines.extend([
            f"  {yaml_name}:",
            f"    provider_type: 'ollama_cloud'",
            f"    model_name: '{model}'  # Score: {score:.0f}/100",
            f"    temperature: {temp}",
            f"    max_tokens: {max_tokens}",
            f"    timeout: {timeout}",
            f"    api_base: 'http://localhost:11434'",
        ])

        if agent == "visual":
            lines.append(f"    supports_vision: true")

        lines.append("")

    return "\n".join(lines)


def print_analysis(recommendations: dict):
    """Imprime análisis detallado."""
    print("\n" + "="*90)
    print("📊 ANÁLISIS COMPARATIVO DE MODELOS - RECOMENDACIÓN PARA TIMEFRAME 1min")
    print("="*90)

    print("\n" + "-"*90)
    print("RESUMEN DE MODELOS DISPONIBLES:")
    print("-"*90)
    print(f"{'Modelo':<35} {'Params':<10} {'Latencia':<12} {'Score 1min':<12} {'Especialización'}")
    print("-"*90)

    for name, profile in sorted(MODEL_PROFILES.items(), key=lambda x: x[1].score_1min_tf, reverse=True):
        spec = ", ".join(profile.specialization[:2])
        print(f"{name:<35} {profile.params_b:<10.0f}B {profile.expected_latency_1k_tokens:<12.0f}ms "
              f"{profile.score_1min_tf:<12.0f} {spec}")

    print("\n" + "="*90)
    print("RECOMENDACIONES POR AGENTE:")
    print("="*90)

    for agent, rec in recommendations.items():
        print(f"\n📌 {agent.upper()}")
        print(f"   Descripción: {rec['description']}")
        print(f"   Requisitos: {', '.join(rec['requirements'])}")
        print()
        print(f"   {'Rank':<6} {'Modelo':<35} {'Score':<10} {'Latencia':<12}")
        print(f"   {'-'*65}")

        for i, model_rec in enumerate(rec["top_3_models"][:3], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            print(f"   {medal} {i:<4} {model_rec['model']:<35} {model_rec['score']:<10.0f} "
                  f"{model_rec['expected_latency_ms']:<12.0f}ms")

    print("\n" + "="*90)


def main():
    """Función principal."""
    print("\n" + "="*90)
    print("🔬 ANÁLISIS TEÓRICO DE MODELOS LLM PARA FENIX TRADING BOT")
    print("="*90)
    print("\nObjetivo: Optimizar asignación de modelos para timeframe 1min")
    print("Criterio: Velocidad (40%) + Especialización (30%) + Score 1min (20%) + Throughput (10%)")

    # Generar recomendaciones
    recommendations = generate_recommendations()

    # Imprimir análisis
    print_analysis(recommendations)

    # Generar YAML
    yaml_config = generate_yaml_config(recommendations)

    print("\n" + "="*90)
    print("⚙️ CONFIGURACIÓN YAML RECOMENDADA:")
    print("="*90)
    print(yaml_config)

    # Guardar a archivo
    output_path = "config/llm_providers_1min_optimized.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(yaml_config)

    print(f"\n💾 Configuración guardada en: {output_path}")

    # Guardar JSON detallado
    json_path = "logs/model_analysis_recommendation.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "timeframe": "1m",
            "recommendations": recommendations,
            "model_profiles": {k: {
                "name": v.name,
                "params_b": v.params_b,
                "architecture": v.architecture,
                "specialization": v.specialization,
                "expected_latency_1k_tokens": v.expected_latency_1k_tokens,
                "throughput_tokens_per_sec": v.throughput_tokens_per_sec,
                "score_1min_tf": v.score_1min_tf,
            } for k, v in MODEL_PROFILES.items()},
        }, f, indent=2, ensure_ascii=False)

    print(f"💾 Análisis detallado guardado en: {json_path}")
    print("\n" + "="*90)

    # Consejos finales
    print("\n📋 CONSEJOS PARA IMPLEMENTACIÓN:")
    print("-"*90)
    print("""
1. PRIORIDAD VELOCIDAD: En timeframe 1min, cada segundo cuenta. Los modelos 8B-14B
   son ideales para agentes paralelos (technical, sentiment, qabba).

2. PIPELINE CRÍTICO: El agente Decision puede usar un modelo más pesado (24B-30B)
   ya que se ejecuta después de los agentes paralelos.

3. VISION ESPECIAL: El agente Visual debe usar gemini-3-flash-preview:cloud que
   es el único con capacidad de visión en la lista.

4. REINTENTOS: Configurar max_retries=3 con backoff para manejar fallos de
   validación JSON, especialmente con modelos más rápidos.

5. VALIDACIÓN: El sistema de validación automática es crucial - descarta
   respuestas que no cumplan el formato JSON estricto.

6. MONITOREO: Usar el ProductionModelRotator para recolectar métricas reales
   y ajustar la asignación según comportamiento en producción.

7. FALLBACKS: Configurar siempre modelos fallback más ligeros por si el
   modelo principal falla o está sobrecargado.
""")
    print("="*90)


if __name__ == "__main__":
    main()
