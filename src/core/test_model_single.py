#!/usr/bin/env python3
"""
Script de Prueba Rápida de Modelo Individual.

Permite probar un modelo específico contra un agente para validar
su rendimiento antes de ponerlo en producción.

Uso:
    python -m src.core.test_model_single --model ministral-3:14b-cloud --agent technical --iterations 5

Salida:
    - Latencia promedio
    - Tasa de validación
    - Ejemplo de respuesta
    - Errores detectados
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import sys
from pathlib import Path

# Agregar proyecto al path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.langgraph_orchestrator import (
    FenixTradingGraph,
    validate_agent_response,
    AGENT_VALIDATION_RULES,
)
from config.llm_provider_config import LLMProvidersConfig


def create_single_model_config(model: str, agent: str) -> LLMProvidersConfig:
    """Crea configuración para un solo modelo/agente."""
    base = {
        "provider_type": "ollama_cloud",
        "model_name": model,
        "temperature": 0.1,
        "max_tokens": 2000,
        "timeout": 15,
        "api_base": "http://localhost:11434",
    }

    config = LLMProvidersConfig()

    # Ajustes por agente
    configs = {
        "technical": {**base, "temperature": 0.05, "max_tokens": 2500},
        "sentiment": {**base, "temperature": 0.15, "max_tokens": 1500},
        "qabba": {**base, "temperature": 0.05, "max_tokens": 1200},
        "decision": {**base, "temperature": 0.1, "max_tokens": 2000},
        "risk_manager": {**base, "temperature": 0.15, "max_tokens": 1500},
        "visual": {**base, "temperature": 0.05, "max_tokens": 1500, "supports_vision": True},
    }

    for agent_type, agent_config in configs.items():
        setattr(config, agent_type, agent_config)

    return config


async def test_model(
    model: str,
    agent: str,
    iterations: int = 5,
    verbose: bool = False,
) -> dict:
    """
    Prueba un modelo contra un agente.

    Returns:
        Dict con resultados de la prueba
    """
    print(f"\n{'='*70}")
    print(f"🔬 Prueba: {model} → {agent}")
    print(f"{'='*70}")

    # Crear configuración
    config = create_single_model_config(model, agent)

    # Crear grafo
    graph = FenixTradingGraph(
        llm_config=config,
        enable_visual=(agent == "visual"),
        enable_sentiment=(agent == "sentiment"),
        enable_risk=(agent == "risk_manager"),
    )

    # Datos de prueba
    test_scenarios = [
        {
            "symbol": "BTCUSDT",
            "indicators": {
                "rsi": 55.0,
                "macd_line": 150.0,
                "macd_signal": 100.0,
                "supertrend_signal": "BULLISH",
                "ema_9": 68500,
                "ema_21": 68000,
                "adx": 30.0,
            },
            "current_price": 69000.0,
            "current_volume": 1500000.0,
            "obi": 1.25,
            "cvd": 75000.0,
            "spread": 0.3,
        },
        {
            "symbol": "BTCUSDT",
            "indicators": {
                "rsi": 45.0,
                "macd_line": -150.0,
                "macd_signal": -100.0,
                "supertrend_signal": "BEARISH",
                "ema_9": 66500,
                "ema_21": 67000,
                "adx": 32.0,
            },
            "current_price": 66000.0,
            "current_volume": 1800000.0,
            "obi": 0.75,
            "cvd": -80000.0,
            "spread": 0.4,
        },
    ]

    results = {
        "model": model,
        "agent": agent,
        "iterations": iterations,
        "latencies_ms": [],
        "validations_passed": 0,
        "validations_failed": 0,
        "errors": [],
        "responses": [],
    }

    scenario_idx = 0
    for i in range(iterations):
        scenario = test_scenarios[scenario_idx % len(test_scenarios)]
        scenario_idx += 1

        print(f"\n🔄 Iteración {i+1}/{iterations}...")

        start = time.time()
        try:
            result = await graph.ainvoke(
                **scenario,
                timeframe="1m",
                thread_id=f"test_{model}_{agent}_{i}",
            )
            latency_ms = (time.time() - start) * 1000

            results["latencies_ms"].append(latency_ms)

            # Obtener reporte
            report_key = f"{agent}_report" if agent != "risk_manager" else "risk_assessment"
            if agent == "decision":
                report_key = "decision_report"
            elif agent == "technical":
                report_key = "technical_report"
            elif agent == "sentiment":
                report_key = "sentiment_report"
            elif agent == "visual":
                report_key = "visual_report"
            elif agent == "qabba":
                report_key = "qabba_report"

            report = result.get(report_key, {})

            # Validar
            errors = validate_agent_response(agent, report)
            if errors:
                results["validations_failed"] += 1
                results["errors"].append({
                    "iteration": i,
                    "errors": errors,
                })
                print(f"   ❌ Validación fallida: {errors}")
            else:
                results["validations_passed"] += 1
                print(f"   ✅ Válido en {latency_ms:.0f}ms")

            if verbose or i == 0:
                print(f"   📝 Respuesta: {json.dumps(report, indent=2)[:200]}...")

            results["responses"].append(report)

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            results["latencies_ms"].append(latency_ms)
            results["errors"].append({
                "iteration": i,
                "exception": str(e),
            })
            print(f"   ❌ Error: {e}")

    return results


def print_results(results: dict):
    """Imprime resultados formateados."""
    print(f"\n{'='*70}")
    print("📊 RESULTADOS")
    print(f"{'='*70}")

    latencies = results["latencies_ms"]
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        print(f"\n⚡ Latencia:")
        print(f"   Promedio: {avg_latency:.0f}ms ({avg_latency/1000:.1f}s)")
        print(f"   Mínima: {min_latency:.0f}ms")
        print(f"   Máxima: {max_latency:.0f}ms")

    total = results["validations_passed"] + results["validations_failed"]
    if total > 0:
        success_rate = results["validations_passed"] / total
        print(f"\n✅ Validación:")
        print(f"   Exitosas: {results['validations_passed']}/{total} ({success_rate:.1%})")
        print(f"   Fallidas: {results['validations_failed']}/{total}")

    if results["errors"]:
        print(f"\n⚠️ Errores ({len(results['errors'])}):")
        for err in results["errors"][:3]:  # Mostrar primeros 3
            if "errors" in err:
                print(f"   - Iter {err['iteration']}: {err['errors']}")
            else:
                print(f"   - Iter {err['iteration']}: {err.get('exception', 'Unknown')}")

    # Score final
    if latencies and total > 0:
        # Score: velocidad (50%) + validación (50%)
        speed_score = max(0, 100 - (avg_latency / 100))  # 100ms = 99, 10s = 0
        validation_score = (results["validations_passed"] / total) * 100
        final_score = speed_score * 0.5 + validation_score * 0.5

        print(f"\n🏆 SCORE FINAL: {final_score:.0f}/100")
        print(f"   (Velocidad: {speed_score:.0f}/100 | Validación: {validation_score:.0f}/100)")

        # Recomendación
        if final_score >= 80:
            print(f"\n✅ RECOMENDADO para producción 1min")
        elif final_score >= 60:
            print(f"\n⚠️ ACEPTABLE pero con reservas")
        else:
            print(f"\n❌ NO RECOMENDADO - Considerar alternativas")

    print(f"\n{'='*70}")


def main():
    parser = argparse.ArgumentParser(
        description="Prueba rápida de modelo individual para Fenix"
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Nombre del modelo a probar (ej: ministral-3:14b-cloud)"
    )
    parser.add_argument(
        "--agent",
        type=str,
        required=True,
        choices=["technical", "sentiment", "qabba", "decision", "risk_manager", "visual"],
        help="Agente a probar"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Número de iteraciones (default: 5)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostrar respuestas completas"
    )

    args = parser.parse_args()

    print("\n" + "="*70)
    print("🧪 TEST DE MODELO INDIVIDUAL - FENIX TRADING BOT")
    print("="*70)
    print(f"\nModelo: {args.model}")
    print(f"Agente: {args.agent}")
    print(f"Iteraciones: {args.iterations}")

    # Ejecutar prueba
    results = asyncio.run(test_model(
        model=args.model,
        agent=args.agent,
        iterations=args.iterations,
        verbose=args.verbose,
    ))

    # Imprimir resultados
    print_results(results)

    # Guardar resultados
    import os
    os.makedirs("logs/model_tests", exist_ok=True)
    filename = f"logs/model_tests/test_{args.model.replace(':', '_')}_{args.agent}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"💾 Resultados guardados en: {filename}")


if __name__ == "__main__":
    main()
