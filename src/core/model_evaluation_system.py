# src/core/model_evaluation_system.py
"""
Sistema de Evaluación y Rotación de Modelos LLM para Fenix Trading Bot.

Este sistema evalúa todos los modelos disponibles en Ollama Cloud para
determinar cuál es el mejor para cada agente en producción con timeframe 1min.

Métricas de evaluación:
- Latencia (ms): Tiempo de respuesta
- Tasa de validación: % de respuestas que pasan validación estructural
- Coherencia técnica: Alineación de señales con datos de mercado
- Estabilidad: Varianza en calidad de respuestas
- Throughput: Peticiones/segundo sostenibles

Modelos disponibles (14 total):
Rápidos (8B-14B): rnj-1:8b, ministral-3:14b
Medianos (24B-30B): devstral-small-2:24b, nemotron-3-nano:30b
Grandes (80B+): qwen3-next:80b, deepseek-v3.1:671b, gpt-oss:120b, qwen3-coder:480b
Vision: gemini-3-flash-preview:cloud
Other: kimi-k2.5, kimi-k2-thinking, deepseek-v3.2, minimax-m2.1, glm-4.7

Para timeframe 1min:
- Latencia total del pipeline < 30s ideal, < 60s máximo
- Por agente: < 5s para agentes paralelos, < 10s para agentes secuenciales
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
from pathlib import Path
import statistics

# Importar el orchestrator
from src.core.langgraph_orchestrator import (
    FenixTradingGraph,
    AGENT_VALIDATION_RULES,
    validate_agent_response,
    get_retry_stats,
    log_retry_stats,
)
from config.llm_provider_config import LLMProvidersConfig

logger = logging.getLogger(__name__)


@dataclass
class ModelScore:
    """Puntuación de un modelo para un agente específico."""
    model_name: str
    agent_type: str

    # Métricas de rendimiento
    total_invocations: int = 0
    successful_validations: int = 0
    total_latency_ms: float = 0.0
    latencies: list[float] = field(default_factory=list)

    # Métricas de coherencia
    coherent_signals: int = 0  # Señales alineadas con tendencia de mercado
    signal_accuracy: float = 0.0  # Precisión de señales (comparado con movimiento real)

    # Errores
    validation_errors: dict[str, int] = field(default_factory=dict)
    json_parse_errors: int = 0
    timeout_errors: int = 0

    # Timestamp
    first_tested: datetime = field(default_factory=datetime.now)
    last_tested: datetime = field(default_factory=datetime.now)

    @property
    def avg_latency_ms(self) -> float:
        """Latencia promedio en ms."""
        if not self.latencies:
            return 0.0
        return statistics.mean(self.latencies)

    @property
    def p95_latency_ms(self) -> float:
        """Percentil 95 de latencia."""
        if len(self.latencies) < 20:
            return self.avg_latency_ms
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[idx]

    @property
    def validation_rate(self) -> float:
        """Tasa de validación exitosa (0.0 - 1.0)."""
        if self.total_invocations == 0:
            return 0.0
        return self.successful_validations / self.total_invocations

    @property
    def coherence_rate(self) -> float:
        """Tasa de coherencia técnica (0.0 - 1.0)."""
        if self.total_invocations == 0:
            return 0.0
        return self.coherent_signals / self.total_invocations

    @property
    def error_rate(self) -> float:
        """Tasa de errores (0.0 - 1.0)."""
        if self.total_invocations == 0:
            return 0.0
        total_errors = sum(self.validation_errors.values()) + self.json_parse_errors + self.timeout_errors
        return total_errors / self.total_invocations

    @property
    def composite_score(self) -> float:
        """
        Puntuación compuesta ponderada para timeframe 1min.
        Pesos:
        - Velocidad (40%): Latencia promedio < 3s = 100pts, > 10s = 0pts
        - Validación (30%): Tasa de validación exitosa
        - Coherencia (20%): Señales coherentes con mercado
        - Estabilidad (10%): 1 - tasa de errores
        """
        # Score de velocidad (40%): inversamente proporcional a latencia
        # < 2s = 100, 2-5s = 80-100, 5-10s = 40-80, > 10s = 0-40
        latency_s = self.avg_latency_ms / 1000
        if latency_s <= 2:
            speed_score = 100
        elif latency_s <= 5:
            speed_score = 80 + (5 - latency_s) * (20 / 3)
        elif latency_s <= 10:
            speed_score = 40 + (10 - latency_s) * (40 / 5)
        else:
            speed_score = max(0, 40 - (latency_s - 10) * 2)

        # Score de validación (30%)
        validation_score = self.validation_rate * 100

        # Score de coherencia (20%)
        coherence_score = self.coherence_rate * 100

        # Score de estabilidad (10%)
        stability_score = (1 - self.error_rate) * 100

        # Puntuación ponderada
        return (speed_score * 0.40 + validation_score * 0.30 +
                coherence_score * 0.20 + stability_score * 0.10)

    def to_dict(self) -> dict[str, Any]:
        """Convierte a diccionario para serialización."""
        return {
            "model_name": self.model_name,
            "agent_type": self.agent_type,
            "total_invocations": self.total_invocations,
            "validation_rate": self.validation_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "coherence_rate": self.coherence_rate,
            "error_rate": self.error_rate,
            "composite_score": self.composite_score,
            "first_tested": self.first_tested.isoformat(),
            "last_tested": self.last_tested.isoformat(),
        }


class ModelEvaluationSystem:
    """
    Sistema de evaluación y rotación de modelos LLM.

    Evalúa todos los modelos disponibles contra todos los agentes
    y determina la mejor asignación para producción con timeframe 1min.
    """

    # Modelos disponibles en Ollama Cloud
    AVAILABLE_MODELS = [
        # Rápidos (8B-14B) - Ideal para 1min
        "rnj-1:8b-cloud",
        "ministral-3:14b-cloud",

        # Medianos (24B-30B) - Balance
        "devstral-small-2:24b-cloud",
        "nemotron-3-nano:30b-cloud",

        # Grandes (varios tamaños) - Mayor capacidad, más lento
        "kimi-k2.5:cloud",
        "deepseek-v3.2:cloud",
        "minimax-m2.1:cloud",
        "glm-4.7:cloud",

        # Muy grandes (80B+) - Solo para agentes críticos
        "qwen3-next:80b-cloud",
        "gpt-oss:120b-cloud",

        # Especializados
        "kimi-k2-thinking:cloud",  # Thinking model
        "gemini-3-flash-preview:cloud",  # Vision + rápido
        "deepseek-v3.1:671b-cloud",  # Enorme, muy lento
        "qwen3-coder:480b-cloud",  # Coder especializado
    ]

    # Agentes a evaluar
    AGENT_TYPES = [
        "technical",
        "sentiment",
        "qabba",
        "decision",
        "risk_manager",
        "visual",
    ]

    def __init__(self, output_dir: str = "logs/model_evaluation"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Scores acumulados: {(model, agent): ModelScore}
        self.scores: dict[tuple[str, str], ModelScore] = {}

        # Mejor asignación actual: {agent: model}
        self.best_assignment: dict[str, str] = {}

        # Historial de evaluaciones
        self.evaluation_history: list[dict] = []

    def _get_or_create_score(self, model: str, agent: str) -> ModelScore:
        """Obtiene o crea un ModelScore."""
        key = (model, agent)
        if key not in self.scores:
            self.scores[key] = ModelScore(model_name=model, agent_type=agent)
        return self.scores[key]

    def create_test_config(self, model: str, agent_type: str) -> LLMProvidersConfig:
        """Crea configuración de prueba para un modelo específico."""
        # Configuración base para todos los agentes usando el mismo modelo
        base_config = {
            "provider_type": "ollama_cloud",
            "model_name": model,
            "temperature": 0.1,  # Baja temperatura para consistencia
            "max_tokens": 2000,
            "timeout": 30,  # 30s timeout para 1min timeframe
            "api_base": "http://localhost:11434",
        }

        # Ajustes por tipo de agente
        agent_configs = {
            "technical": {**base_config, "temperature": 0.05, "max_tokens": 2500},
            "sentiment": {**base_config, "temperature": 0.15, "max_tokens": 1500},
            "qabba": {**base_config, "temperature": 0.05, "max_tokens": 1200},
            "decision": {**base_config, "temperature": 0.1, "max_tokens": 2000},
            "risk_manager": {**base_config, "temperature": 0.15, "max_tokens": 1500},
            "visual": {**base_config, "temperature": 0.05, "max_tokens": 1500, "supports_vision": True},
        }

        # Crear config
        config = LLMProvidersConfig()
        for agent, agent_config in agent_configs.items():
            setattr(config, agent, agent_config)

        return config

    async def evaluate_model_for_agent(
        self,
        model: str,
        agent_type: str,
        test_data: dict[str, Any],
        iterations: int = 5,
    ) -> ModelScore:
        """
        Evalúa un modelo específico para un agente.

        Args:
            model: Nombre del modelo
            agent_type: Tipo de agente
            test_data: Datos de prueba (indicadores, precio, etc.)
            iterations: Número de iteraciones de prueba

        Returns:
            ModelScore con métricas acumuladas
        """
        score = self._get_or_create_score(model, agent_type)

        logger.info(f"🔬 Evaluando {model} para {agent_type} ({iterations} iteraciones)...")

        # Crear configuración
        config = self.create_test_config(model, agent_type)

        # Crear grafo con solo el agente a evaluar
        graph = FenixTradingGraph(
            llm_config=config,
            enable_visual=(agent_type == "visual"),
            enable_sentiment=(agent_type == "sentiment"),
            enable_risk=(agent_type == "risk_manager"),
        )

        for i in range(iterations):
            start_time = time.time()

            try:
                # Ejecutar
                result = await graph.ainvoke(
                    symbol=test_data["symbol"],
                    timeframe="1m",  # Timeframe 1min para evaluación
                    indicators=test_data["indicators"],
                    current_price=test_data["current_price"],
                    current_volume=test_data["current_volume"],
                    obi=test_data.get("obi", 1.0),
                    cvd=test_data.get("cvd", 0.0),
                    spread=test_data.get("spread", 0.01),
                    thread_id=f"eval_{model}_{agent_type}_{i}",
                )

                latency_ms = (time.time() - start_time) * 1000

                # Actualizar métricas de latencia
                score.total_invocations += 1
                score.latencies.append(latency_ms)
                score.total_latency_ms += latency_ms
                score.last_tested = datetime.now()

                # Obtener reporte del agente
                report_key = f"{agent_type}_report" if agent_type != "risk_manager" else "risk_assessment"
                if agent_type == "decision":
                    report_key = "decision_report"
                elif agent_type == "technical":
                    report_key = "technical_report"
                elif agent_type == "sentiment":
                    report_key = "sentiment_report"
                elif agent_type == "visual":
                    report_key = "visual_report"
                elif agent_type == "qabba":
                    report_key = "qabba_report"

                report = result.get(report_key, {})

                # Validar estructura
                validation_errors = validate_agent_response(agent_type, report)
                if not validation_errors:
                    score.successful_validations += 1
                else:
                    for error in validation_errors:
                        error_type = error.split(":")[0]
                        score.validation_errors[error_type] = score.validation_errors.get(error_type, 0) + 1

                # Verificar coherencia técnica (si hay datos de mercado)
                if "market_context" in test_data:
                    coherence = self._check_coherence(report, agent_type, test_data["market_context"])
                    if coherence:
                        score.coherent_signals += 1

                # Verificar errores de parseo
                if report.get("parse_error") or report.get("_validation_failed"):
                    score.json_parse_errors += 1

                logger.info(f"  Iteración {i+1}/{iterations}: {latency_ms:.0f}ms | "
                           f"Valid: {not validation_errors} | "
                           f"Coherente: {score.coherent_signals}")

            except asyncio.TimeoutError:
                score.timeout_errors += 1
                score.total_invocations += 1
                logger.warning(f"  Iteración {i+1}/{iterations}: TIMEOUT")
            except Exception as e:
                score.json_parse_errors += 1
                score.total_invocations += 1
                logger.error(f"  Iteración {i+1}/{iterations}: ERROR - {e}")

        logger.info(f"✅ Evaluación completada: Score compuesto = {score.composite_score:.1f}")
        return score

    def _check_coherence(
        self,
        report: dict[str, Any],
        agent_type: str,
        market_context: dict[str, Any],
    ) -> bool:
        """
        Verifica si la señal del agente es coherente con el contexto de mercado.

        Args:
            report: Reporte del agente
            agent_type: Tipo de agente
            market_context: Contexto de mercado (tendencia, rsi, etc.)

        Returns:
            True si la señal es coherente
        """
        trend = market_context.get("trend", "neutral")
        rsi = market_context.get("rsi", 50)
        macd = market_context.get("macd_signal", "neutral")

        # Obtener señal según tipo de agente
        signal = None
        if agent_type == "technical":
            signal = report.get("signal", "HOLD")
        elif agent_type == "sentiment":
            sentiment = report.get("overall_sentiment", "NEUTRAL")
            # Convertir sentimiento a señal aproximada
            signal = "BUY" if sentiment == "POSITIVE" else "SELL" if sentiment == "NEGATIVE" else "HOLD"
        elif agent_type == "visual":
            signal = report.get("action", "HOLD")
        elif agent_type == "qabba":
            signal = report.get("signal", "HOLD_QABBA").replace("_QABBA", "")
        elif agent_type == "decision":
            signal = report.get("final_decision", "HOLD")

        if not signal or signal == "HOLD":
            # HOLD es siempre coherente (neutralidad)
            return True

        # Verificar coherencia con tendencia
        if trend == "bullish" and signal == "BUY":
            return True
        if trend == "bearish" and signal == "SELL":
            return True
        if trend == "neutral":
            return True  # Cualquier señal es válida en neutral

        # Verificar con RSI
        if rsi < 30 and signal == "BUY":  # Sobrevendido + BUY = coherente
            return True
        if rsi > 70 and signal == "SELL":  # Sobrecomprado + SELL = coherente
            return True

        # Verificar con MACD
        if macd == "bullish" and signal == "BUY":
            return True
        if macd == "bearish" and signal == "SELL":
            return True

        # Señal contradictoria
        return False

    async def run_full_evaluation(
        self,
        test_scenarios: list[dict[str, Any]] | None = None,
        models_to_test: list[str] | None = None,
        agents_to_test: list[str] | None = None,
        iterations_per_test: int = 5,
    ) -> dict[str, dict[str, ModelScore]]:
        """
        Ejecuta evaluación completa de todos los modelos contra todos los agentes.

        Args:
            test_scenarios: Lista de escenarios de mercado para probar
            models_to_test: Modelos a evaluar (default: todos)
            agents_to_test: Agentes a evaluar (default: todos)
            iterations_per_test: Iteraciones por combinación

        Returns:
            Dict anidado: {agent_type: {model_name: ModelScore}}
        """
        models = models_to_test or self.AVAILABLE_MODELS
        agents = agents_to_test or self.AGENT_TYPES

        # Escenarios de prueba por defecto (simulando diferentes condiciones de mercado)
        if test_scenarios is None:
            test_scenarios = [
                # Escenario 1: Tendencia alcista clara
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
                    "market_context": {
                        "trend": "bullish",
                        "rsi": 55,
                        "macd_signal": "bullish",
                    },
                },
                # Escenario 2: Tendencia bajista clara
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
                    "market_context": {
                        "trend": "bearish",
                        "rsi": 45,
                        "macd_signal": "bearish",
                    },
                },
                # Escenario 3: Mercado neutral/ranging
                {
                    "symbol": "BTCUSDT",
                    "indicators": {
                        "rsi": 50.0,
                        "macd_line": 5.0,
                        "macd_signal": 0.0,
                        "supertrend_signal": "BULLISH",
                        "ema_9": 67500,
                        "ema_21": 67520,
                        "adx": 18.0,
                    },
                    "current_price": 67510.0,
                    "current_volume": 800000.0,
                    "obi": 1.0,
                    "cvd": 5000.0,
                    "spread": 0.2,
                    "market_context": {
                        "trend": "neutral",
                        "rsi": 50,
                        "macd_signal": "neutral",
                    },
                },
            ]

        logger.info(f"🚀 Iniciando evaluación completa: {len(models)} modelos x {len(agents)} agentes x {len(test_scenarios)} escenarios")

        total_tests = len(models) * len(agents) * len(test_scenarios)
        completed = 0

        for agent in agents:
            logger.info(f"\n{'='*60}")
            logger.info(f"📊 Evaluando agente: {agent.upper()}")
            logger.info(f"{'='*60}")

            for model in models:
                logger.info(f"\n🔧 Modelo: {model}")

                # Evaluar contra todos los escenarios
                for scenario_idx, scenario in enumerate(test_scenarios):
                    logger.info(f"  Escenario {scenario_idx + 1}/{len(test_scenarios)}...")

                    try:
                        await self.evaluate_model_for_agent(
                            model=model,
                            agent_type=agent,
                            test_data=scenario,
                            iterations=iterations_per_test,
                        )
                    except Exception as e:
                        logger.error(f"  Error evaluando {model} para {agent}: {e}")

                    completed += 1

                # Log progreso
                progress = (completed / total_tests) * 100
                logger.info(f"📈 Progreso: {completed}/{total_tests} ({progress:.1f}%)")

        # Organizar resultados
        results: dict[str, dict[str, ModelScore]] = {}
        for agent in agents:
            results[agent] = {}
            for model in models:
                key = (model, agent)
                if key in self.scores:
                    results[agent][model] = self.scores[key]

        return results

    def determine_best_assignment(self, results: dict[str, dict[str, ModelScore]]) -> dict[str, str]:
        """
        Determina la mejor asignación de modelos a agentes basada en scores.

        Args:
            results: Resultados de evaluación

        Returns:
            Dict: {agent_type: model_name}
        """
        assignment = {}

        for agent, model_scores in results.items():
            if not model_scores:
                continue

            # Encontrar mejor modelo para este agente
            best_model = None
            best_score = -1

            for model, score in model_scores.items():
                if score.composite_score > best_score:
                    best_score = score.composite_score
                    best_model = model

            if best_model:
                assignment[agent] = best_model
                logger.info(f"🏆 Mejor modelo para {agent}: {best_model} (score: {best_score:.1f})")

        self.best_assignment = assignment
        return assignment

    def generate_recommendations(self, results: dict[str, dict[str, ModelScore]]) -> list[str]:
        """
        Genera recomendaciones basadas en los resultados.

        Returns:
            Lista de recomendaciones en formato string
        """
        recommendations = []

        for agent, model_scores in results.items():
            if not model_scores:
                continue

            # Ordenar por score
            sorted_models = sorted(model_scores.items(), key=lambda x: x[1].composite_score, reverse=True)

            best = sorted_models[0]
            best_model, best_score = best

            # Recomendación principal
            rec = f"\n{'='*60}\n"
            rec += f"📌 AGENTE: {agent.upper()}\n"
            rec += f"{'='*60}\n"
            rec += f"🥇 MEJOR MODELO: {best_model}\n"
            rec += f"   Score compuesto: {best_score.composite_score:.1f}/100\n"
            rec += f"   Latencia promedio: {best_score.avg_latency_ms:.0f}ms ({best_score.avg_latency_ms/1000:.1f}s)\n"
            rec += f"   Tasa de validación: {best_score.validation_rate:.1%}\n"
            rec += f"   Tasa de coherencia: {best_score.coherence_rate:.1%}\n"
            rec += f"   Invocaciones: {best_score.total_invocations}\n"

            # Modelos alternativos
            if len(sorted_models) > 1:
                rec += f"\n🥈 ALTERNATIVAS:\n"
                for model, score in sorted_models[1:4]:  # Top 3 alternativas
                    rec += f"   - {model}: {score.composite_score:.1f}/100 "
                    rec += f"({score.avg_latency_ms:.0f}ms, {score.validation_rate:.0%} valid)\n"

            # Advertencias para timeframe 1min
            if best_score.avg_latency_ms > 5000:  # > 5s
                rec += f"\n⚠️ ADVERTENCIA: Latencia alta para 1min timeframe\n"
                rec += f"   Considerar modelos más rápidos si el pipeline es lento.\n"

            if best_score.validation_rate < 0.8:  # < 80%
                rec += f"\n⚠️ ADVERTENCIA: Tasa de validación baja\n"
                rec += f"   El modelo tiene dificultades con el formato JSON requerido.\n"

            recommendations.append(rec)

        return recommendations

    def export_results(
        self,
        results: dict[str, dict[str, ModelScore]],
        assignment: dict[str, str],
        recommendations: list[str],
    ) -> str:
        """
        Exporta resultados a archivo JSON.

        Returns:
            Ruta del archivo exportado
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.output_dir / f"model_evaluation_{timestamp}.json"

        export_data = {
            "timestamp": datetime.now().isoformat(),
            "timeframe_tested": "1m",
            "iterations_per_test": 5,
            "total_scores": len(self.scores),
            "best_assignment": assignment,
            "recommendations": recommendations,
            "detailed_scores": {},
        }

        # Exportar scores detallados
        for agent, model_scores in results.items():
            export_data["detailed_scores"][agent] = {}
            for model, score in model_scores.items():
                export_data["detailed_scores"][agent][model] = score.to_dict()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Resultados exportados a: {filepath}")
        return str(filepath)

    def print_leaderboard(self, results: dict[str, dict[str, ModelScore]]):
        """Imprime tabla de clasificación por agente."""
        print("\n" + "="*100)
        print("🏆 LEADERBOARD - MEJORES MODELOS POR AGENTE (Timeframe 1min)")
        print("="*100)

        for agent in self.AGENT_TYPES:
            if agent not in results or not results[agent]:
                continue

            print(f"\n📊 {agent.upper()}")
            print("-"*100)
            print(f"{'Rank':<6} {'Modelo':<35} {'Score':<8} {'Latencia':<12} {'Valid%':<8} {'Coh%':<8} {'Invoc':<8}")
            print("-"*100)

            sorted_models = sorted(results[agent].items(), key=lambda x: x[1].composite_score, reverse=True)

            for rank, (model, score) in enumerate(sorted_models[:10], 1):  # Top 10
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
                print(f"{medal} {rank:<4} {model:<35} {score.composite_score:<8.1f} "
                      f"{score.avg_latency_ms:<12.0f} {score.validation_rate:<8.1%} "
                      f"{score.coherence_rate:<8.1%} {score.total_invocations:<8}")

        print("\n" + "="*100)


async def main():
    """Función principal de evaluación."""
    print("\n" + "="*80)
    print("🔬 SISTEMA DE EVALUACIÓN DE MODELOS LLM - FENIX TRADING BOT")
    print("="*80)
    print("\nConfiguración:")
    print("  - Timeframe: 1min (producción)")
    print("  - Modelos disponibles: 14")
    print("  - Agentes a evaluar: 6")
    print("  - Iteraciones por test: 5")
    print("  - Escenarios de mercado: 3 (bullish, bearish, neutral)")
    print("\n" + "="*80)

    # Crear sistema de evaluación
    evaluator = ModelEvaluationSystem()

    # Para pruebas rápidas, podemos limitar modelos
    # En producción, evaluar todos
    quick_test_models = [
        "rnj-1:8b-cloud",           # Rápido, ligero
        "ministral-3:14b-cloud",    # Balance
        "devstral-small-2:24b-cloud",  # Razonamiento
        "nemotron-3-nano:30b-cloud",   # Técnico
        "kimi-k2.5:cloud",          # Potente
        "deepseek-v3.2:cloud",      # Análisis
    ]

    print("\n🚀 Iniciando evaluación con modelos:")
    for model in quick_test_models:
        print(f"  - {model}")

    # Ejecutar evaluación
    try:
        results = await evaluator.run_full_evaluation(
            models_to_test=quick_test_models,
            iterations_per_test=5,
        )

        # Determinar mejor asignación
        assignment = evaluator.determine_best_assignment(results)

        # Generar recomendaciones
        recommendations = evaluator.generate_recommendations(results)

        # Imprimir leaderboard
        evaluator.print_leaderboard(results)

        # Imprimir recomendaciones
        print("\n" + "="*80)
        print("📋 RECOMENDACIONES PARA CONFIGURACIÓN EN PRODUCCIÓN (1min)")
        print("="*80)
        for rec in recommendations:
            print(rec)

        # Exportar resultados
        filepath = evaluator.export_results(results, assignment, recommendations)
        print(f"\n💾 Resultados completos guardados en: {filepath}")

        # Mostrar configuración recomendada
        print("\n" + "="*80)
        print("⚙️  CONFIGURACIÓN YAML RECOMENDADA (ollama_cloud_optimized.yaml)")
        print("="*80)
        print("\nollama_cloud_optimized:")
        for agent, model in assignment.items():
            config = evaluator.create_test_config(model, agent)
            agent_config = getattr(config, agent, {})
            print(f"  {agent}:")
            print(f"    provider_type: \"ollama_cloud\"")
            print(f"    model_name: \"{model}\"")
            print(f"    temperature: {agent_config.get('temperature', 0.1)}")
            print(f"    max_tokens: {agent_config.get('max_tokens', 2000)}")
            print(f"    timeout: {agent_config.get('timeout', 30)}")
            print(f"    api_base: \"http://localhost:11434\"")

    except Exception as e:
        logger.error(f"Error en evaluación: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Ejecutar
    asyncio.run(main())
