# src/trading/engine.py
"""
Motor de Trading Principal para Fenix Trading Bot.

Este es el núcleo refactorizado que orquesta:
- Recepción de datos de mercado
- Ejecución del grafo LangGraph de agentes
- Gestión de decisiones y ejecución de órdenes
- Logging y métricas
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.trading.market_data import MarketDataManager, get_market_data_manager
from src.trading.executor import OrderExecutor, OrderResult
from src.tools.technical_tools import add_kline, get_current_indicators, close_buf, high_buf, low_buf, vol_buf, open_buf, timestamp_buf
from src.tools.chart_generator import FenixChartGenerator
from src.tools.enhanced_news_scraper import EnhancedNewsScraper
from src.tools.twitter_scraper import TwitterScraper
from src.tools.reddit_scraper import RedditScraper
from src.tools.fear_greed import FearGreedTool
from src.memory.reasoning_bank import get_reasoning_bank
from src.prompts.agent_prompts import format_prompt

# Importar LangGraph orchestrator
try:
    from src.core.langgraph_orchestrator import (
        FenixTradingGraph,
        get_trading_graph,
        FenixAgentState,
        LANGGRAPH_AVAILABLE,
    )
except ImportError:
    LANGGRAPH_AVAILABLE = False
    FenixTradingGraph = None

# Configuración
try:
    from src.config.config_loader import APP_CONFIG
except ImportError:
    APP_CONFIG = None

try:
    from src.risk.runtime_risk_manager import RuntimeRiskManager, get_risk_manager
    RISK_MANAGER_AVAILABLE = True
except ImportError:
    RISK_MANAGER_AVAILABLE = False
    get_risk_manager = None

logger = logging.getLogger("FenixTradingEngine")


@dataclass
class TradingConfig:
    """Configuración del motor de trading."""
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    analysis_interval: int = 60
    use_visual: bool = True
    use_sentiment: bool = False
    max_risk_per_trade: float = 2.0
    testnet: bool = True
    dry_run: bool = False
    llm_model: str = "qwen2.5:7b"


class TradingEngine:
    """
    Motor principal de trading de Fenix.

    Flujo de operación:
    1. Recibe datos de mercado (klines, orderbook, trades)
    2. Calcula indicadores técnicos
    3. Ejecuta el grafo de agentes LangGraph
    4. Procesa la decisión y ejecuta órdenes si corresponde

    Esta clase reemplaza el monolito live_trading.py con una
    arquitectura limpia y modular.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        use_testnet: bool = False,
        paper_trading: bool = True,
        enable_visual_agent: bool = True,
        enable_sentiment_agent: bool = True,
        allow_live_trading: bool = False,
    ):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.use_testnet = use_testnet
        self.paper_trading = paper_trading
        self.allow_live_trading = allow_live_trading

        # Componentes
        self.market_data = get_market_data_manager(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )
        self.executor = OrderExecutor(symbol=symbol)
        self.chart_generator = FenixChartGenerator()
        self.news_scraper = EnhancedNewsScraper()
        self.twitter_scraper = TwitterScraper()
        self.reddit_scraper = RedditScraper()
        self.fear_greed_tool = FearGreedTool()
        self.reasoning_bank = get_reasoning_bank()
        self.on_agent_event = None  # Callback for frontend events

        # Estado
        self._running = False
        self._last_decision_time: datetime | None = None
        self._consecutive_holds = 0
        self._kline_count = 0
        self._min_klines_to_start = int(os.getenv("FENIX_MIN_KLINES_TO_START", "20"))

        # LangGraph
        self._trading_graph: FenixTradingGraph | None = None
        self.enable_visual = enable_visual_agent
        self.enable_sentiment = enable_sentiment_agent

        # Inicializar RiskManager
        self.risk_manager = get_risk_manager() if RISK_MANAGER_AVAILABLE else None
        if self.risk_manager:
            logger.info("✅ RuntimeRiskManager initialized")
        else:
            logger.warning("⚠️ RuntimeRiskManager not available")

        logger.info(
            f"TradingEngine initialized: {symbol}@{timeframe} "
            f"(paper={paper_trading}, testnet={use_testnet})"
        )

    async def initialize(self) -> bool:
        """Inicializa todos los componentes."""
        logger.info("Initializing TradingEngine components...")

        try:
            # Inicializar LangGraph
            if LANGGRAPH_AVAILABLE:
                logger.info("Creating LangGraph trading graph...")
                self._trading_graph = get_trading_graph(force_new=True)
                logger.info("✅ LangGraph trading graph created")
            else:
                logger.warning("⚠️ LangGraph not available, using fallback mode")

            # Registrar callbacks de market data
            self.market_data.on_kline(self._on_kline_received)

            logger.info("✅ TradingEngine initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize TradingEngine: {e}", exc_info=True)
            return False

    async def start(self) -> None:
        """Inicia el motor de trading."""
        if self._running:
            logger.warning("TradingEngine already running")
            return

        logger.info("=" * 60)
        logger.info("🦅 FENIX TRADING BOT - Starting Engine")
        logger.info("=" * 60)
        logger.info(f"Symbol: {self.symbol}")
        logger.info(f"Timeframe: {self.timeframe}")
        logger.info(f"Mode: {'Paper Trading' if self.paper_trading else 'LIVE TRADING'}")
        logger.info("=" * 60)

        self._running = True

        # Inicializar componentes
        if not await self.initialize():
            logger.error("Failed to initialize, aborting start")
            self._running = False
            return

        # Iniciar market data streams
        await self.market_data.start()

        logger.info("🚀 TradingEngine started and listening for market data")

        # Mantener el motor corriendo
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("TradingEngine received cancellation")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Detiene el motor de trading."""
        logger.info("Stopping TradingEngine...")
        self._running = False

        # Detener market data
        await self.market_data.stop()

        logger.info("TradingEngine stopped")

    async def _on_kline_received(self, kline_data: dict[str, Any]) -> None:
        """Callback cuando se recibe una nueva kline."""
        try:
            # Agregar kline al buffer de indicadores (with open and timestamp)
            add_kline(
                close=kline_data["close"],
                high=kline_data["high"],
                low=kline_data["low"],
                volume=kline_data["volume"],
                open_price=kline_data.get("open"),
                timestamp=kline_data.get("open_time"),
            )
            self._kline_count += 1

            # Solo procesar cuando la vela cierra
            if not kline_data.get("is_closed", False):
                return

            logger.info(
                f"📊 Kline closed: {kline_data['close']:.2f} "
                f"(H:{kline_data['high']:.2f} L:{kline_data['low']:.2f})"
            )

            # Verificar mínimo de velas
            if self._kline_count < self._min_klines_to_start:
                logger.info(
                    f"Warming up: {self._kline_count}/{self._min_klines_to_start} klines"
                )
                return

            # Ejecutar análisis
            await self._run_analysis_cycle()

        except Exception as e:
            logger.error(f"Error processing kline: {e}", exc_info=True)

    async def _run_analysis_cycle(self) -> None:
        """Ejecuta un ciclo completo de análisis."""
        start_time = datetime.now(timezone.utc)
        logger.info("=" * 50)
        logger.info("🔄 Starting analysis cycle")

        try:
            # 1. Obtener indicadores técnicos
            indicators = get_current_indicators()
            if not indicators:
                logger.warning("No indicators available, skipping cycle")
                return

            # 2. Obtener métricas de microestructura
            micro = self.market_data.get_microstructure_metrics()

            # 3. Obtener noticias (si está habilitado)
            news_data = []
            if self.enable_sentiment:
                try:
                    news_data = self.news_scraper.fetch_crypto_news(limit=10)
                    logger.info(f"📰 Fetched {len(news_data)} news articles")
                except Exception as e:
                    logger.warning(f"Failed to fetch news: {e}")
                # Send news update event to frontend
                if self.on_agent_event:
                    await self.on_agent_event("news_update", {
                        "news_data": news_data,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            
            # 4. Obtener social data (Twitter/Reddit) y fear & greed
            social_data = {}
            fear_greed_value = None
            if self.enable_sentiment:
                try:
                    twitter_data = self.twitter_scraper._run() if hasattr(self.twitter_scraper, '_run') else {}
                except Exception as e:
                    logger.warning(f"Twitter scraper failed: {e}")
                    twitter_data = {}

                try:
                    reddit_data = self.reddit_scraper._run() if hasattr(self.reddit_scraper, '_run') else {}
                except Exception as e:
                    logger.warning(f"Reddit scraper failed: {e}")
                    reddit_data = {}

                try:
                    fg = self.fear_greed_tool._run(1) if hasattr(self.fear_greed_tool, '_run') else None
                    fear_greed_value = fg if fg is not None else "N/A"
                except Exception as e:
                    logger.warning(f"FearGreed tool failed: {e}")
                    fear_greed_value = "N/A"

                social_data = {
                    "twitter": twitter_data,
                    "reddit": reddit_data,
                }

            # 5. Ejecutar el grafo de agentes
            if self._trading_graph:
                result = await self._execute_langgraph_analysis(indicators, micro, news_data, social_data, fear_greed_value)
            else:
                result = await self._execute_fallback_analysis(indicators, micro)

            # 5. Procesar decisión
            await self._process_decision(result)

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"⏱️ Analysis cycle completed in {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"Error in analysis cycle: {e}", exc_info=True)

    async def _execute_langgraph_analysis(
        self,
        indicators: dict[str, Any],
        micro: Any,
        news_data: list[dict[str, Any]] | None = None,
        social_data: dict[str, Any] | None = None,
        fear_greed_value: str | None = None,
    ) -> FenixAgentState | dict[str, Any]:
        """Ejecuta análisis usando LangGraph."""
        logger.info("🧠 Executing LangGraph multi-agent analysis...")

        try:
            # Generate chart for Visual Agent
            chart_b64 = None
            if self.enable_visual:
                try:
                    # Construct kline data from buffers with proper OHLCV and timestamps
                    kline_data = {
                        "open": list(open_buf),
                        "close": list(close_buf),
                        "high": list(high_buf),
                        "low": list(low_buf),
                        "volume": list(vol_buf),
                        "datetime": list(timestamp_buf)  # Unix timestamps in milliseconds
                    }
                    chart_result = self.chart_generator.generate_chart(
                        kline_data=kline_data,
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        last_n_candles=50
                    )
                    chart_b64 = chart_result.get("image_b64")
                    if chart_b64:
                        logger.info(f"🖼️ Chart generated successfully ({len(chart_b64)} chars)")
                    else:
                        logger.warning("🖼️ Chart generation returned no image")
                        # Create a placeholder chart image to keep visual agent behavior consistent
                        try:
                            placeholder = self.chart_generator.generate_placeholder(message="Insufficient market data for chart", symbol=self.symbol, timeframe=self.timeframe)
                            chart_b64 = placeholder.get('image_b64')
                            logger.info("🖼️ Placeholder chart generated for visual agent")
                        except Exception:
                            chart_b64 = None
                except Exception as e:
                    logger.error(f"Failed to generate chart: {e}")

                result = self._trading_graph.invoke(
                symbol=self.symbol,
                timeframe=self.timeframe,
                indicators=indicators,
                current_price=self.market_data.current_price,
                current_volume=self.market_data.current_volume,
                obi=micro.obi,
                cvd=micro.cvd,
                spread=micro.spread,
                orderbook_depth={
                    "bid_depth": micro.bid_depth,
                    "ask_depth": micro.ask_depth,
                },
                mtf_context={}, # Add empty context if needed
                chart_image_b64=chart_b64,
                    news_data=news_data or [],
                    social_data=social_data or {},
                    fear_greed_value=fear_greed_value or "N/A",
                thread_id=f"{self.symbol}_{self.timeframe}",
            )

            # Emit agent outputs to frontend
            if self.on_agent_event:
                # Emit individual agent reports
                for agent_name, report_key in [
                    ("Technical Analyst", "technical_report"),
                    ("QABBA Agent", "qabba_report"),
                    ("Sentiment Agent", "sentiment_report"),
                    ("Visual Agent", "visual_report"),
                    ("Risk Manager", "risk_report"),
                    ("Decision Agent", "final_trade_decision") # Decision is special
                ]:
                    if result.get(report_key):
                        payload = {
                            "agent_name": agent_name,
                            "data": result[report_key],
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        # Attach social_data and fear_greed_value for sentiment agent for richer frontend updates
                        if agent_name == "Sentiment Agent":
                            payload["social_data"] = result.get("social_data")
                            payload["fear_greed_value"] = result.get("fear_greed_value")
                        await self.on_agent_event("agent_output", payload)
                        # If the report stored a ReasoningBank digest, emit a reasoning:new event
                        if result[report_key].get("_reasoning_digest"):
                            await self.on_agent_event("reasoning:new", {
                                "agent_name": agent_name,
                                "prompt_digest": result[report_key].get("_reasoning_digest"),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

            # Log resultados de cada agente
            if result.get("technical_report"):
                logger.info(f"📈 Technical: {result['technical_report'].get('signal', 'N/A')}")
            if result.get("qabba_report"):
                logger.info(f"📊 QABBA: {result['qabba_report'].get('signal', 'N/A')}")
            if result.get("sentiment_report"):
                logger.info(f"💭 Sentiment: {result['sentiment_report'].get('overall_sentiment', 'N/A')}")
            if result.get("visual_report"):
                logger.info(f"👁️ Visual: {result['visual_report'].get('action', 'N/A')}")

            return result

        except Exception as e:
            logger.error(f"LangGraph analysis failed: {e}", exc_info=True)
            return {"final_trade_decision": {"final_decision": "HOLD", "error": str(e)}}

    async def _execute_fallback_analysis(
        self,
        indicators: dict[str, Any],
        micro: Any,
    ) -> dict[str, Any]:
        """Análisis de fallback cuando LangGraph no está disponible."""
        logger.warning("Using fallback analysis (LangGraph unavailable)")

        # Análisis simple basado en indicadores
        rsi = indicators.get("rsi", 50)
        macd_hist = indicators.get("macd_hist", 0)

        decision = "HOLD"
        confidence = "LOW"

        if rsi < 30 and macd_hist > 0:
            decision = "BUY"
            confidence = "MEDIUM"
        elif rsi > 70 and macd_hist < 0:
            decision = "SELL"
            confidence = "MEDIUM"

        return {
            "final_trade_decision": {
                "final_decision": decision,
                "confidence_in_decision": confidence,
                "combined_reasoning": f"Fallback: RSI={rsi:.1f}, MACD_hist={macd_hist:.4f}",
            }
        }

    async def _process_decision(self, result: dict[str, Any]) -> None:
        """Procesa la decisión final y ejecuta si corresponde."""
        decision_data = result.get("final_trade_decision", {})
        decision = decision_data.get("final_decision", "HOLD").upper()
        confidence = decision_data.get("confidence_in_decision", "LOW")
        reasoning = decision_data.get("combined_reasoning", "No reasoning")

        logger.info("=" * 50)
        logger.info(f"📋 FINAL DECISION: {decision} ({confidence})")
        logger.info(f"📝 Reasoning: {reasoning[:200]}...")

        # Emit final decision to frontend
        if self.on_agent_event:
            await self.on_agent_event("final_decision", {
                "decision": decision,
                "confidence": confidence,
                "reasoning": reasoning,
                "full_data": decision_data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Log estructurado
        self._log_signal(decision, confidence, reasoning, result)

        # Ejecutar si no es HOLD
        if decision in ["BUY", "SELL"]:
            await self._execute_trade(decision, confidence, decision_data)
        else:
            self._consecutive_holds += 1
            logger.info(f"⏸️ HOLD - Consecutive holds: {self._consecutive_holds}")

    async def _execute_trade(
        self,
        decision: str,
        confidence: str,
        decision_data: dict[str, Any],
    ) -> None:
        """Ejecuta un trade basado en la decisión con RiskManager activo."""
        logger.info(f"🎯 Executing {decision} trade...")

        self._consecutive_holds = 0
        self._last_decision_time = datetime.now(timezone.utc)

        # --- CIRCUIT BREAKER: Evaluar riesgo AVANZADO ---
        if self.risk_manager and RISK_MANAGER_AVAILABLE:
            # Actualizar balance para métricas de riesgo
            try:
                if self.executor.get_balance():
                    self.risk_manager.update_balance(self.executor.get_balance())
            except Exception as e:
                logger.warning(f"Could not update risk manager balance: {e}")
            
            # Verificar si el trade está permitido
            base_size = decision_data.get("position_size", 1000)  # Default $1000
            allowed, risk_status = self.risk_manager.check_trade_allowed(self.symbol, base_size)
            
            if not allowed:
                logger.critical(f"🚨 TRADE BLOCKED BY CIRCUIT BREAKER: {risk_status.describe()}")
                if self.on_agent_event:
                    await self.on_agent_event("risk:blocked", {
                        "status": risk_status.dict(),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                return
            
            # Aplicar risk_bias al tamaño
            adjusted_size = self.risk_manager.get_adjusted_size(base_size)
            if adjusted_size != base_size:
                logger.info(f"Size adjusted by risk manager: ${base_size:.2f} → ${adjusted_size:.2f}")
        # --- FIN CIRCUIT BREAKER ---

        if self.paper_trading:
            logger.info(f"📝 PAPER TRADE: Would {decision} at {self.market_data.current_price}")
            return

        if not self.allow_live_trading:
            logger.warning(
                "Live trading bloqueado: allow_live_trading=False. Ejecuta con flag seguro para operar."
            )
            return

        # Obtener parámetros de riesgo
        risk_data = decision_data.get("risk_assessment", {})
        entry_price = risk_data.get("entry_price", self.market_data.current_price)
        stop_loss = risk_data.get("stop_loss")
        take_profit = risk_data.get("take_profit")

        # Calcular tamaño de posición (ajustado por RiskManager)
        balance = self.executor.get_balance()
        if balance is None:
            logger.error("Could not get balance, aborting trade")
            return

        # Calcular cantidad basada en riesgo
        if hasattr(self, 'adjusted_size') and adjusted_size:
            position_size = adjusted_size
        else:
            position_size = balance * (APP_CONFIG.risk_management.base_risk_per_trade if APP_CONFIG else 0.01)
        
        quantity = position_size / entry_price
        
        # Verificar min notional
        notional = quantity * entry_price
        if notional < self.executor.min_notional:
            logger.warning(f"Trade skipped: Notional {notional:.2f} < Min {self.executor.min_notional}")
            return

        # Verificar balance suficiente
        if decision == "BUY" and position_size > balance:
            logger.warning(f"Trade skipped: Insufficient balance {balance:.2f} < Required {position_size:.2f}")
            return

        # Ejecutar orden
        result = await self.executor.execute_market_order(
            side=decision,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if result.success:
            logger.info(
                f"✅ Trade executed: {decision} {result.executed_qty} @ {result.entry_price}"
            )
            
            # --- ACTUALIZAR REASONING BANK ---
            try:
                digest = decision_data.get('_reasoning_digest') or decision_data.get('reasoning_prompt_digest')
                if digest:
                    # For now, mark success as True and attach order id; reward will be computed asynchronously later
                    self.reasoning_bank.update_entry_outcome(
                        agent_name='Decision Agent',
                        prompt_digest=digest,
                        success=True,
                        reward=0.0,
                        trade_id=str(result.order_id) if result.order_id else None,
                    )
            except Exception as e:
                logger.debug(f"Failed to attach trade outcome to ReasoningBank: {e}")
            
            # --- ACTUALIZAR RISK MANAGER ---
            if self.risk_manager and RISK_MANAGER_AVAILABLE:
                try:
                    # Crear record de trade para métricas
                    from src.risk.runtime_risk_manager import TradeRecord
                    trade_record = TradeRecord(
                        trade_id=str(result.order_id) if result.order_id else "paper_trade",
                        timestamp=datetime.now(timezone.utc),
                        symbol=self.symbol,
                        decision=decision,
                        entry_price=float(result.entry_price) if result.entry_price else 0.0,
                        exit_price=None,  # Se actualizará cuando cierre
                        pnl=0.0,  # Se actualizará cuando cierre
                        pnl_pct=0.0,
                        success=True,  # Se actualizará cuando sepa el resultado
                        size=float(result.executed_qty) * float(result.entry_price) if result.executed_qty and result.entry_price else 0.0
                    )
                    self.risk_manager.record_trade(trade_record)
                    logger.info(f"Trade recorded in RiskManager: {self.risk_manager.current_status.describe()}")
                except Exception as e:
                    logger.warning(f"Could not record trade in RiskManager: {e}")
        else:
            logger.error(f"❌ Trade failed: {result.status} - {result.message}")
            
            # --- ACTUALIZAR REASONING BANK PARA TRADE FALLIDO ---
            try:
                digest = decision_data.get('_reasoning_digest') or decision_data.get('reasoning_prompt_digest')
                if digest:
                    self.reasoning_bank.update_entry_outcome(
                        agent_name='Decision Agent',
                        prompt_digest=digest,
                        success=False,
                        reward=0.0,
                        trade_id=str(result.order_id) if result.order_id else None,
                    )
            except Exception as e:
                logger.debug(f"Failed to attach failed trade outcome to ReasoningBank: {e}")
            
            # --- ACTUALIZAR RISK MANAGER PARA TRADE FALLIDO ---
            if self.risk_manager and RISK_MANAGER_AVAILABLE:
                try:
                    from src.risk.runtime_risk_manager import TradeRecord
                    trade_record = TradeRecord(
                        trade_id=str(result.order_id) if result.order_id else "failed_trade",
                        timestamp=datetime.now(timezone.utc),
                        symbol=self.symbol,
                        decision=decision,
                        entry_price=float(result.entry_price) if result.entry_price else 0.0,
                        exit_price=None,
                        pnl=0.0,
                        pnl_pct=0.0,
                        success=False,
                        size=0.0
                    )
                    self.risk_manager.record_trade(trade_record)
                except Exception as e:
                    logger.warning(f"Could not record failed trade: {e}")

    def get_risk_status(self) -> Optional[Dict[str, Any]]:
        """Retorna el estado del RiskManager para el dashboard."""
        if self.risk_manager and RISK_MANAGER_AVAILABLE:
            return self.risk_manager.get_status_summary()
        return None

    def _log_signal(
        self,
        decision: str,
        confidence: str,
        reasoning: str,
        full_result: dict[str, Any],
    ) -> None:
        """Loguea señal para auditoría."""
        signal_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "price": self.market_data.current_price,
            "execution_times": full_result.get("execution_times", {}),
        }

        try:
            with open(self.signal_log_path, "a") as f:
                f.write(json.dumps(signal_data) + "\n")
        except Exception as e:
            logger.error(f"Failed to log signal: {e}")

    def get_status(self) -> dict[str, Any]:
        """Retorna el estado actual del motor."""
        return {
            "running": self._running,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "paper_trading": self.paper_trading,
            "kline_count": self._kline_count,
            "consecutive_holds": self._consecutive_holds,
            "last_decision_time": self._last_decision_time.isoformat() if self._last_decision_time else None,
            "current_price": self.market_data.current_price,
            "langgraph_available": self._trading_graph is not None,
        }


# ============================================================================
# Función principal para ejecutar el motor
# ============================================================================

async def run_trading_engine(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    paper_trading: bool = True,
) -> None:
    """Función principal para ejecutar el motor de trading."""
    engine = TradingEngine(
        symbol=symbol,
        timeframe=timeframe,
        paper_trading=paper_trading,
    )

    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await engine.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fenix Trading Engine")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument("--timeframe", default="15m", help="Timeframe")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    asyncio.run(
        run_trading_engine(
            symbol=args.symbol,
            timeframe=args.timeframe,
            paper_trading=not args.live,
        )
    )
