# src/core/model_rotation_production.py
"""
Sistema de Rotación de Modelos en Producción - Timeframe 1min.

Este script rota modelos en producción real, recolectando métricas
en cada ciclo de trading para determinar el mejor modelo para cada agente.

Uso:
    python -m src.core.model_rotation_production --duration-hours 4 --cycles-per-model 10

Características:
- Rota automáticamente entre modelos disponibles
- Recolecta métricas de cada invocación (latencia, validación, coherencia)
- Ajusta dinámicamente según rendimiento
- Optimizado para timeframe 1min (prioriza velocidad)
- Guarda resultados en tiempo real
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import statistics

# Imports del proyecto
from src.core.langgraph_orchestrator import (
    FenixTradingGraph,
    validate_agent_response,
    AGENT_VALIDATION_RULES,
)
from src.tools.technical_tools import TechnicalAnalysisTools
from config.llm_provider_config import LLMProvidersConfig

logger = logging.getLogger(__name__)


@dataclass
class ProductionMetrics:
    """Métricas recolectadas en producción para un modelo-agente."""
    timestamp: str
    model: str
    agent: str
    cycle_number: int

    # Rendimiento
    latency_ms: float
    success: bool
    validation_passed: bool

    # Calidad de respuesta
    signal: str | None = None
    confidence: str | float | None = None
    reasoning_length: int = 0

    # Coherencia con mercado
    market_trend: str = "unknown"
    signal_coherent: bool = False

    # Errores
    error_type: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProductionModelRotator:
    """
    Rota modelos en producción y recolecta métricas de rendimiento real.
    """

    # Modelos disponibles organizados por velocidad esperada
    FAST_MODELS = [  # < 3s esperado
        "rnj-1:8b-cloud",
        "ministral-3:14b-cloud",
    ]

    MEDIUM_MODELS = [  # 3-7s esperado
        "devstral-small-2:24b-cloud",
        "nemotron-3-nano:30b-cloud",
        "gemini-3-flash-preview:cloud",
    ]

    SLOW_MODELS = [  # > 7s esperado (no recomendados para 1min)
        "kimi-k2.5:cloud",
        "deepseek-v3.2:cloud",
        "minimax-m2.1:cloud",
        "glm-4.7:cloud",
        "qwen3-next:80b-cloud",
        "gpt-oss:120b-cloud",
        "kimi-k2-thinking:cloud",
        "deepseek-v3.1:671b-cloud",
        "qwen3-coder:480b-cloud",
    ]

    # Para 1min, enfocarse en modelos rápidos
    RECOMMENDED_FOR_1MIN = FAST_MODELS + MEDIUM_MODELS

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        duration_hours: float = 4.0,
        cycles_per_model: int = 10,
        output_dir: str = "logs/production_rotation",
    ):
        self.symbol = symbol
        self.duration_hours = duration_hours
        self.cycles_per_model = cycles_per_model
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Métricas recolectadas
        self.all_metrics: list[ProductionMetrics] = []

        # Contador de ciclos
        self.total_cycles = 0

        # Tools para análisis técnico
        self.tech_tools = TechnicalAnalysisTools()

    def create_config_for_model(self, model: str) -> LLMProvidersConfig:
        """Crea configuración para un modelo específico."""
        base = {
            "provider_type": "ollama_cloud",
            "model_name": model,
            "temperature": 0.1,
            "max_tokens": 2000,
            "timeout": 15,  # 15s timeout para 1min timeframe
            "api_base": "http://localhost:11434",
        }

        config = LLMProvidersConfig()
        for agent in ["technical", "sentiment", "qabba", "decision", "risk_manager", "visual"]:
            agent_config = base.copy()

            # Ajustes específicos por agente
            if agent == "technical":
                agent_config.update({"temperature": 0.05, "max_tokens": 2500})
            elif agent == "sentiment":
                agent_config.update({"temperature": 0.15, "max_tokens": 1500})
            elif agent == "qabba":
                agent_config.update({"temperature": 0.05, "max_tokens": 1200})
            elif agent == "decision":
                agent_config.update({"temperature": 0.1, "max_tokens": 2000})
            elif agent == "risk_manager":
                agent_config.update({"temperature": 0.15, "max_tokens": 1500})
            elif agent == "visual":
                agent_config.update({"temperature": 0.05, "max_tokens": 1500, "supports_vision": True})

            setattr(config, agent, agent_config)

        return config

    async def fetch_market_data(self) -> dict[str, Any]:
        """Obtiene datos de mercado reales."""
        try:
            # Obtener datos técnicos
            indicators = await self.tech_tools.get_all_indicators(self.symbol, "1m", limit=100)

            # Determinar tendencia
            ema9 = indicators.get("ema_9", 0)
            ema21 = indicators.get("ema_21", 0)
            adx = indicators.get("adx", 0)

            if ema9 > ema21 and adx > 25:
                trend = "bullish"
            elif ema9 < ema21 and adx > 25:
                trend = "bearish"
            else:
                trend = "neutral"

            return {
                "symbol": self.symbol,
                "indicators": indicators,
                "current_price": indicators.get("last_price", 0),
                "current_volume": indicators.get("volume", 0),
                "obi": indicators.get("order_book_imbalance", 1.0),
                "cvd": indicators.get("cvd", 0),
                "spread": indicators.get("spread", 0.01),
                "market_trend": trend,
            }
        except Exception as e:
            logger.error(f"Error obteniendo datos de mercado: {e}")
            # Datos por defecto
            return {
                "symbol": self.symbol,
                "indicators": {"rsi": 50, "macd_line": 0, "ema_9": 50000, "ema_21": 50000},
                "current_price": 50000.0,
                "current_volume": 1000000.0,
                "market_trend": "neutral",
            }

    def check_signal_coherence(
        self,
        agent: str,
        report: dict[str, Any],
        market_trend: str,
    ) -> bool:
        """Verifica si la señal es coherente con la tendencia de mercado."""
        # Extraer señal según agente
        signal = None
        if agent == "technical":
            signal = report.get("signal", "HOLD")
        elif agent == "sentiment":
            sentiment = report.get("overall_sentiment", "NEUTRAL")
            signal = "BUY" if sentiment == "POSITIVE" else "SELL" if sentiment == "NEGATIVE" else "HOLD"
        elif agent == "visual":
            signal = report.get("action", "HOLD")
        elif agent == "qabba":
            signal = report.get("signal", "HOLD_QABBA").replace("_QABBA", "")
        elif agent == "decision":
            signal = report.get("final_decision", "HOLD")

        if not signal or signal == "HOLD":
            return True  # HOLD siempre coherente

        # Verificar coherencia
        if market_trend == "bullish" and signal == "BUY":
            return True
        if market_trend == "bearish" and signal == "SELL":
            return True
        if market_trend == "neutral":
            return True

        return False

    async def run_single_cycle(
        self,
        model: str,
        cycle_number: int,
    ) -> list[ProductionMetrics]:
        """
        Ejecuta un ciclo completo con un modelo.

        Returns:
            Lista de métricas por agente
        """
        logger.info(f"🔄 Ciclo {cycle_number} con modelo: {model}")

        metrics = []

        # Obtener datos de mercado
        market_data = await self.fetch_market_data()
        market_trend = market_data.pop("market_trend", "neutral")

        # Crear grafo con modelo
        config = self.create_config_for_model(model)
        graph = FenixTradingGraph(
            llm_config=config,
            enable_visual=False,  # Desactivado por velocidad en 1min
            enable_sentiment=True,
            enable_risk=True,
        )

        # Ejecutar pipeline
        start_time = time.time()

        try:
            result = await graph.ainvoke(
                **market_data,
                timeframe="1m",
                thread_id=f"prod_{model}_{cycle_number}_{int(time.time())}",
            )

            total_latency_ms = (time.time() - start_time) * 1000

            # Analizar resultados por agente
            agent_reports = {
                "technical": result.get("technical_report", {}),
                "sentiment": result.get("sentiment_report", {}),
                "qabba": result.get("qabba_report", {}),
                "decision": result.get("decision_report", {}),
                "risk_manager": result.get("risk_assessment", {}),
            }

            for agent, report in agent_reports.items():
                if not report:
                    continue

                # Validar
                validation_errors = validate_agent_response(agent, report)
                validation_passed = len(validation_errors) == 0

                # Extraer señal y confianza
                signal = None
                confidence = None
                if agent == "technical":
                    signal = report.get("signal")
                    confidence = report.get("confidence_level")
                elif agent == "sentiment":
                    signal = report.get("overall_sentiment")
                    confidence = report.get("confidence_score")
                elif agent == "qabba":
                    signal = report.get("signal")
                    confidence = report.get("qabba_confidence")
                elif agent == "decision":
                    signal = report.get("final_decision")
                    confidence = report.get("confidence_in_decision")
                elif agent == "risk_manager":
                    signal = report.get("verdict")
                    confidence = report.get("risk_score")

                # Verificar coherencia
                coherent = self.check_signal_coherence(agent, report, market_trend)

                # Estimar latencia del agente (distribución proporcional)
                agent_latency = total_latency_ms / len(agent_reports)

                metric = ProductionMetrics(
                    timestamp=datetime.now().isoformat(),
                    model=model,
                    agent=agent,
                    cycle_number=cycle_number,
                    latency_ms=agent_latency,
                    success=True,
                    validation_passed=validation_passed,
                    signal=signal,
                    confidence=confidence,
                    reasoning_length=len(str(report.get("reasoning", ""))),
                    market_trend=market_trend,
                    signal_coherent=coherent,
                    validation_errors=validation_errors,
                )

                metrics.append(metric)
                self.all_metrics.append(metric)

            logger.info(f"✅ Ciclo completado en {total_latency_ms:.0f}ms")

        except Exception as e:
            logger.error(f"❌ Error en ciclo: {e}")

            # Métricas de error para todos los agentes
            for agent in ["technical", "sentiment", "qabba", "decision", "risk_manager"]:
                metric = ProductionMetrics(
                    timestamp=datetime.now().isoformat(),
                    model=model,
                    agent=agent,
                    cycle_number=cycle_number,
                    latency_ms=0,
                    success=False,
                    validation_passed=False,
                    error_type=type(e).__name__,
                    market_trend=market_trend,
                )
                metrics.append(metric)
                self.all_metrics.append(metric)

        return metrics

    async def run_rotation(self) -> dict[str, Any]:
        """
        Ejecuta rotación completa de modelos.

        Returns:
            Resumen de resultados
        """
        start_time = time.time()
        max_duration_seconds = self.duration_hours * 3600

        # Seleccionar modelos a probar
        models_to_test = self.RECOMMENDED_FOR_1MIN

        logger.info(f"🚀 Iniciando rotación en producción")
        logger.info(f"   Duración: {self.duration_hours}h")
        logger.info(f"   Ciclos por modelo: {self.cycles_per_model}")
        logger.info(f"   Modelos a probar: {len(models_to_test)}")

        cycle = 0
        for model in models_to_test:
            logger.info(f"\n{'='*60}")
            logger.info(f"🔧 Probando modelo: {model}")
            logger.info(f"{'='*60}")

            for i in range(self.cycles_per_model):
                # Verificar tiempo límite
                elapsed = time.time() - start_time
                if elapsed > max_duration_seconds:
                    logger.info("⏰ Tiempo límite alcanzado")
                    break

                cycle += 1
                await self.run_single_cycle(model, cycle)

                # Guardar progreso cada 5 ciclos
                if cycle % 5 == 0:
                    self._save_progress()

                # Breve pausa entre ciclos
                await asyncio.sleep(2)

            # Guardar después de cada modelo
            self._save_progress()

        # Generar reporte final
        return self._generate_report()

    def _save_progress(self):
        """Guarda progreso actual a archivo."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.output_dir / f"rotation_progress_{timestamp}.json"

        data = {
            "timestamp": datetime.now().isoformat(),
            "total_cycles": len(self.all_metrics) // 5,  # Aproximado
            "metrics": [m.to_dict() for m in self.all_metrics[-100:]],  # Últimos 100
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info(f"💾 Progreso guardado: {filepath}")

    def _generate_report(self) -> dict[str, Any]:
        """Genera reporte final de rotación."""
        logger.info("\n📊 Generando reporte final...")

        # Agrupar métricas por modelo-agente
        grouped: dict[tuple[str, str], list[ProductionMetrics]] = {}
        for m in self.all_metrics:
            key = (m.model, m.agent)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(m)

        # Calcular estadísticas
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_cycles": len(self.all_metrics) // 5,
            "symbol": self.symbol,
            "timeframe": "1m",
            "model_performance": {},
            "best_by_agent": {},
        }

        for agent in ["technical", "sentiment", "qabba", "decision", "risk_manager"]:
            report["model_performance"][agent] = {}

            best_model = None
            best_score = -1

            for model in self.RECOMMENDED_FOR_1MIN:
                key = (model, agent)
                if key not in grouped:
                    continue

                metrics = grouped[key]
                if not metrics:
                    continue

                # Calcular estadísticas
                latencies = [m.latency_ms for m in metrics if m.success]
                success_rate = sum(1 for m in metrics if m.success) / len(metrics)
                validation_rate = sum(1 for m in metrics if m.validation_passed) / len(metrics)
                coherence_rate = sum(1 for m in metrics if m.signal_coherent) / len(metrics)

                avg_latency = statistics.mean(latencies) if latencies else 0
                p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else avg_latency

                # Score compuesto (ponderado para 1min)
                # Velocidad: 50%, Validación: 25%, Coherencia: 15%, Éxito: 10%
                speed_score = max(0, 100 - (avg_latency / 100))  # 100ms = 99pts, 10s = 0pts
                score = (
                    speed_score * 0.50 +
                    validation_rate * 100 * 0.25 +
                    coherence_rate * 100 * 0.15 +
                    success_rate * 100 * 0.10
                )

                perf = {
                    "model": model,
                    "total_invocations": len(metrics),
                    "avg_latency_ms": avg_latency,
                    "p95_latency_ms": p95_latency,
                    "success_rate": success_rate,
                    "validation_rate": validation_rate,
                    "coherence_rate": coherence_rate,
                    "composite_score": score,
                }

                report["model_performance"][agent][model] = perf

                if score > best_score:
                    best_score = score
                    best_model = model

            report["best_by_agent"][agent] = {
                "model": best_model,
                "score": best_score,
            }

        # Guardar reporte
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.output_dir / f"final_report_{timestamp}.json"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Reporte final guardado: {filepath}")

        # Imprimir resumen
        self._print_summary(report)

        return report

    def _print_summary(self, report: dict[str, Any]):
        """Imprime resumen en consola."""
        print("\n" + "="*80)
        print("🏆 RESULTADOS DE ROTACIÓN EN PRODUCCIÓN (Timeframe 1min)")
        print("="*80)

        for agent, best in report["best_by_agent"].items():
            model = best["model"]
            score = best["score"]

            if model and agent in report["model_performance"]:
                perf = report["model_performance"][agent][model]

                print(f"\n📊 {agent.upper()}")
                print(f"   🥇 Mejor modelo: {model}")
                print(f"   ⭐ Score: {score:.1f}/100")
                print(f"   ⚡ Latencia: {perf['avg_latency_ms']:.0f}ms (p95: {perf['p95_latency_ms']:.0f}ms)")
                print(f"   ✅ Validación: {perf['validation_rate']:.1%}")
                print(f"   🎯 Coherencia: {perf['coherence_rate']:.1%}")
                print(f"   📈 Invocaciones: {perf['total_invocations']}")

        print("\n" + "="*80)
        print("⚙️ CONFIGURACIÓN RECOMENDADA:")
        print("="*80)
        print("\nactive_profile: 'ollama_cloud_optimized'")
        print("\nollama_cloud_optimized:")
        for agent, best in report["best_by_agent"].items():
            model = best["model"]
            if model:
                print(f"  {agent}:")
                print(f"    provider_type: 'ollama_cloud'")
                print(f"    model_name: '{model}'")
                print(f"    api_base: 'http://localhost:11434'")

        print("\n" + "="*80)


def main():
    """Función principal."""
    parser = argparse.ArgumentParser(
        description="Rotación de modelos LLM en producción - Timeframe 1min"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Símbolo a analizar (default: BTCUSDT)"
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=4.0,
        help="Duración de la evaluación en horas (default: 4)"
    )
    parser.add_argument(
        "--cycles-per-model",
        type=int,
        default=10,
        help="Ciclos por modelo (default: 10)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="logs/production_rotation",
        help="Directorio de salida"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Modo verbose"
    )

    args = parser.parse_args()

    # Configurar logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("\n" + "="*80)
    print("🔄 SISTEMA DE ROTACIÓN DE MODELOS EN PRODUCCIÓN")
    print("="*80)
    print(f"\nConfiguración:")
    print(f"  Símbolo: {args.symbol}")
    print(f"  Duración: {args.duration_hours} horas")
    print(f"  Ciclos por modelo: {args.cycles_per_model}")
    print(f"  Modelos a probar: 6 (optimizados para 1min)")
    print(f"  Timeframe: 1min")
    print("\n" + "="*80)

    # Crear rotador
    rotator = ProductionModelRotator(
        symbol=args.symbol,
        duration_hours=args.duration_hours,
        cycles_per_model=args.cycles_per_model,
        output_dir=args.output_dir,
    )

    # Ejecutar
    try:
        asyncio.run(rotator.run_rotation())
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrumpido por usuario")
        print("Generando reporte parcial...")
        rotator._save_progress()
        rotator._generate_report()
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
