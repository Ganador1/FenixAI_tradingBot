# src/trading/realtime_integration.py
"""
Integración de datos en tiempo real con LangGraph.

Conecta el MarketDataManager con el FenixTradingGraph para
análisis continuo y señales de trading en vivo.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig
from src.core.langgraph_orchestrator import FenixAgentState, FenixTradingGraph
from src.trading.market_data import MarketDataManager, MicrostructureMetrics

logger = logging.getLogger(__name__)


class RealtimeTradingPipeline:
    """
    Pipeline de trading en tiempo real.

    Conecta:
    1. MarketDataManager (WebSocket) → datos en vivo
    2. FenixTradingGraph (LangGraph) → análisis multi-agente
    3. Callbacks → ejecución de señales

    Flujo:
    - Recibe klines cerradas vía WebSocket
    - Acumula indicadores calculados
    - Ejecuta el grafo de agentes
    - Emite señales de trading
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        llm_config: LLMProvidersConfig | None = None,
        use_testnet: bool = True,
        min_interval_seconds: int = 60,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.min_interval = min_interval_seconds
        self._last_analysis_time: datetime | None = None

        # Inicializar componentes
        self.market_data = MarketDataManager(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )

        # If no llm_config is provided, try to load from LLM Provider Loader (project-level config)
        if llm_config is None:
            try:
                from src.config.llm_provider_loader import get_provider_loader

                loader = get_provider_loader()
                llm_config = loader.get_config()
                logger.info(
                    f"RealtimeTradingPipeline: Using LLM config from provider loader ({loader.active_profile})"
                )
            except Exception:
                logger.warning(
                    "RealtimeTradingPipeline: Provider loader not available, using Ollama defaults"
                )
                llm_config = self._create_default_ollama_config()

        self.trading_graph = FenixTradingGraph(
            llm_config=llm_config,
            enable_visual=False,  # Deshabilitado en tiempo real por latencia
            enable_sentiment=True,
            enable_risk=True,
        )

        # Estado acumulado
        self._indicators: dict[str, Any] = {}
        self._kline_history: list[dict] = []
        self._microstructure: MicrostructureMetrics | None = None

        # Callbacks
        self._signal_callbacks: list[Callable[[FenixAgentState], None]] = []

        # Control
        self._running = False
        self._analysis_task: asyncio.Task | None = None

        logger.info(f"RealtimeTradingPipeline initialized: {symbol}@{timeframe}")

    def _create_default_ollama_config(self) -> LLMProvidersConfig:
        """Crea configuración por defecto usando Ollama local."""
        ollama_fast = AgentProviderConfig(
            provider_type="ollama_local",
            model_name="gemma3:1b",  # Rápido para tiempo real
            temperature=0.2,
            max_tokens=1000,
        )

        ollama_reasoning = AgentProviderConfig(
            provider_type="ollama_local",
            model_name="qwen2.5:7b",  # Mejor razonamiento
            temperature=0.3,
            max_tokens=2000,
        )

        return LLMProvidersConfig(
            technical=ollama_fast,
            sentiment=ollama_fast,
            visual=ollama_fast,
            qabba=ollama_fast,
            decision=ollama_reasoning,
            risk=ollama_fast,
        )

    def on_signal(self, callback: Callable[[FenixAgentState], None]) -> None:
        """Registra callback para nuevas señales de trading."""
        self._signal_callbacks.append(callback)

    async def start(self) -> None:
        """Inicia el pipeline completo."""
        if self._running:
            logger.warning("Pipeline already running")
            return

        self._running = True

        # Registrar callbacks en MarketDataManager
        self.market_data.on_kline(self._on_kline_closed)
        self.market_data.on_microstructure_update(self._on_microstructure_update)

        # Iniciar MarketDataManager
        await self.market_data.start()

        logger.info("RealtimeTradingPipeline started")

    async def stop(self) -> None:
        """Detiene el pipeline."""
        self._running = False

        if self._analysis_task:
            self._analysis_task.cancel()
            try:
                await self._analysis_task
            except asyncio.CancelledError:
                pass

        await self.market_data.stop()
        logger.info("RealtimeTradingPipeline stopped")

    async def _on_kline_closed(self, kline_data: dict) -> None:
        """Callback cuando se cierra una vela."""
        # Guardar en historial
        self._kline_history.append(kline_data)
        if len(self._kline_history) > 100:
            self._kline_history = self._kline_history[-100:]

        # Calcular indicadores básicos (TODO: usar ta-lib si disponible)
        self._update_indicators(kline_data)

        # Verificar si debemos analizar
        if self._should_analyze():
            await self._run_analysis()

    def _on_microstructure_update(self, metrics: MicrostructureMetrics) -> None:
        """Callback para actualizaciones de microestructura."""
        self._microstructure = metrics
        self._indicators["obi"] = metrics.obi
        self._indicators["cvd"] = metrics.cvd
        self._indicators["spread"] = metrics.spread

    def _update_indicators(self, kline: dict) -> None:
        """Actualiza indicadores con nueva vela."""
        # Precio y volumen actuales
        self._indicators["current_price"] = float(kline.get("close", 0))
        self._indicators["current_volume"] = float(kline.get("volume", 0))

        # RSI simple (14 períodos)
        if len(self._kline_history) >= 14:
            closes = [float(k.get("close", 0)) for k in self._kline_history[-15:]]
            gains = [max(0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
            losses = [max(0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]

            avg_gain = sum(gains) / len(gains) if gains else 0
            avg_loss = sum(losses) / len(losses) if losses else 0.001

            rs = avg_gain / avg_loss
            self._indicators["rsi"] = 100 - (100 / (1 + rs))

        # EMAs simples
        if len(self._kline_history) >= 21:
            closes = [float(k.get("close", 0)) for k in self._kline_history[-21:]]
            self._indicators["ema_9"] = sum(closes[-9:]) / 9
            self._indicators["ema_21"] = sum(closes) / 21

    def _should_analyze(self) -> bool:
        """Determina si es momento de ejecutar análisis."""
        if not self._running:
            return False

        now = datetime.now()

        if self._last_analysis_time is None:
            return True

        elapsed = (now - self._last_analysis_time).total_seconds()
        return elapsed >= self.min_interval

    async def _run_analysis(self) -> None:
        """Ejecuta el pipeline de análisis completo."""
        self._last_analysis_time = datetime.now()

        try:
            logger.info(f"Running analysis for {self.symbol}...")

            # Ejecutar grafo de agentes
            result = await self.trading_graph.ainvoke(
                symbol=self.symbol,
                timeframe=self.timeframe,
                indicators=self._indicators.copy(),
                current_price=self._indicators.get("current_price", 0),
                current_volume=self._indicators.get("current_volume", 0),
                obi=self._indicators.get("obi", 1.0),
                cvd=self._indicators.get("cvd", 0.0),
                spread=self._indicators.get("spread", 0.01),
            )

            # Emitir señal a todos los callbacks
            for callback in self._signal_callbacks:
                try:
                    callback(result)
                except Exception as e:
                    logger.error(f"Error in signal callback: {e}")

            # Log del resultado
            decision = result.get("final_trade_decision", {})
            risk = result.get("risk_assessment", {})

            logger.info(
                f"Analysis complete: "
                f"Decision={decision.get('final_decision', 'N/A')} | "
                f"Risk={risk.get('verdict', 'N/A')} | "
                f"Price={self._indicators.get('current_price', 0):.2f}"
            )

        except Exception as e:
            logger.error(f"Error in analysis pipeline: {e}")

    async def force_analysis(self) -> FenixAgentState:
        """Fuerza un análisis inmediato."""
        await self._run_analysis()
        return self.get_last_state()

    def get_last_state(self) -> dict:
        """Retorna el último estado conocido."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "indicators": self._indicators.copy(),
            "microstructure": self._microstructure,
            "last_analysis": self._last_analysis_time,
        }


# ============================================================================
# EJEMPLO DE USO
# ============================================================================


async def example_usage():
    """Ejemplo de uso del pipeline en tiempo real."""

    def on_signal(state: FenixAgentState):
        decision = state.get("final_trade_decision", {})
        action = decision.get("final_decision", "HOLD")
        confidence = decision.get("confidence_in_decision", "N/A")

        risk = state.get("risk_assessment", {})
        verdict = risk.get("verdict", "N/A")

        print(f"🚀 SIGNAL: {action} | Confidence: {confidence} | Risk: {verdict}")

    # Crear pipeline
    pipeline = RealtimeTradingPipeline(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        min_interval_seconds=60,
    )

    # Registrar callback
    pipeline.on_signal(on_signal)

    # Iniciar
    await pipeline.start()

    try:
        # Correr por 5 minutos
        await asyncio.sleep(300)
    finally:
        await pipeline.stop()


if __name__ == "__main__":
    asyncio.run(example_usage())
