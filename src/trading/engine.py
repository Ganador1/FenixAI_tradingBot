# src/trading/engine.py
"""
Main Trading Engine for Fenix Trading Bot.

This is the refactored core that orchestrates:
- Receiving market data
- Executing the LangGraph agent graph
- Managing decisions and executing orders
- Logging and metrics
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.trading.market_data import MarketDataManager, get_market_data_manager
from src.tools.technical_tools import add_kline, get_current_indicators, close_buf, high_buf, low_buf, vol_buf
from src.tools.chart_generator import FenixChartGenerator
from src.tools.enhanced_news_scraper import EnhancedNewsScraper
from src.tools.twitter_scraper import TwitterScraper
from src.tools.reddit_scraper import RedditScraper
from src.tools.fear_greed import FearGreedTool
from src.memory.reasoning_bank import get_reasoning_bank
from src.prompts.agent_prompts import format_prompt
from src.trading.exchange_client import ExchangeClient

# Import LangGraph orchestrator
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

# Configuration
try:
    from src.config.config_loader import APP_CONFIG
except ImportError:
    APP_CONFIG = None

logger = logging.getLogger("FenixTradingEngine")


@dataclass
class TradingConfig:
    """Configuration for the trading engine."""
    exchange_id: str = "binance"
    symbol: str = "BTC/USDT"
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
    Main trading engine for Fenix.

    Operation flow:
    1. Receives market data (klines, orderbook, trades)
    2. Calculates technical indicators
    3. Executes the LangGraph agent graph
    4. Processes the decision and executes orders if applicable

    This class replaces the monolithic live_trading.py with a
    clean and modular architecture.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        symbol: str = "BTC/USDT",
        timeframe: str = "15m",
        use_testnet: bool = False,
        paper_trading: bool = True,
        enable_visual_agent: bool = True,
        enable_sentiment_agent: bool = True,
        allow_live_trading: bool = False,
    ):
        self.exchange_id = exchange_id
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.use_testnet = use_testnet
        self.paper_trading = paper_trading
        self.allow_live_trading = allow_live_trading

        # Components
        self.exchange_client = ExchangeClient(exchange_id=exchange_id, testnet=use_testnet)
        self.market_data = get_market_data_manager(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )
        self.chart_generator = FenixChartGenerator()
        self.news_scraper = EnhancedNewsScraper()
        self.twitter_scraper = TwitterScraper()
        self.reddit_scraper = RedditScraper()
        self.fear_greed_tool = FearGreedTool()
        self.reasoning_bank = get_reasoning_bank()
        self.on_agent_event = None  # Callback for frontend events

        # State
        self._running = False
        self._last_decision_time: datetime | None = None
        self._consecutive_holds = 0
        self._kline_count = 0
        self._min_klines_to_start = 20

        # LangGraph
        self._trading_graph: FenixTradingGraph | None = None
        self.enable_visual = enable_visual_agent
        self.enable_sentiment = enable_sentiment_agent

        # Logging
        self.signal_log_path = Path("logs/signal_trace.jsonl")
        self.signal_log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"TradingEngine initialized: {exchange_id} - {symbol}@{timeframe} "
            f"(paper={paper_trading}, testnet={use_testnet})"
        )

    async def initialize(self) -> bool:
        """Initializes all components."""
        logger.info("Initializing TradingEngine components...")

        try:
            # Initialize LangGraph
            if LANGGRAPH_AVAILABLE:
                logger.info("Creating LangGraph trading graph...")
                self._trading_graph = get_trading_graph(force_new=True)
                logger.info("✅ LangGraph trading graph created")
            else:
                logger.warning("⚠️ LangGraph not available, using fallback mode")

            # Connect to exchange
            if not await self.exchange_client.connect():
                logger.error(f"Failed to connect to {self.exchange_id}")
                return False

            # Register market data callbacks
            self.market_data.on_kline(self._on_kline_received)

            logger.info("✅ TradingEngine initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize TradingEngine: {e}", exc_info=True)
            return False

    async def start(self) -> None:
        """Starts the trading engine."""
        if self._running:
            logger.warning("TradingEngine already running")
            return

        logger.info("=" * 60)
        logger.info("🦅 FENIX TRADING BOT - Starting Engine")
        logger.info("=" * 60)
        logger.info(f"Exchange: {self.exchange_id}")
        logger.info(f"Symbol: {self.symbol}")
        logger.info(f"Timeframe: {self.timeframe}")
        logger.info(f"Mode: {'Paper Trading' if self.paper_trading else 'LIVE TRADING'}")
        logger.info("=" * 60)

        self._running = True

        # Initialize components
        if not await self.initialize():
            logger.error("Failed to initialize, aborting start")
            self._running = False
            return

        # Start market data streams
        await self.market_data.start()

        logger.info("🚀 TradingEngine started and listening for market data")

        # Keep the engine running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("TradingEngine received cancellation")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stops the trading engine."""
        logger.info("Stopping TradingEngine...")
        self._running = False

        # Stop market data
        await self.market_data.stop()

        # Close exchange connection
        await self.exchange_client.close()

        logger.info("TradingEngine stopped")

    async def _on_kline_received(self, kline_data: dict[str, Any]) -> None:
        """Callback when a new kline is received."""
        try:
            # Add kline to the indicator buffer
            add_kline(
                close=kline_data["close"],
                high=kline_data["high"],
                low=kline_data["low"],
                volume=kline_data["volume"],
            )
            self._kline_count += 1

            # Only process when the candle closes
            if not kline_data.get("is_closed", False):
                return

            logger.info(
                f"📊 Kline closed: {kline_data['close']:.2f} "
                f"(H:{kline_data['high']:.2f} L:{kline_data['low']:.2f})"
            )

            # Check for minimum number of candles
            if self._kline_count < self._min_klines_to_start:
                logger.info(
                    f"Warming up: {self._kline_count}/{self._min_klines_to_start} klines"
                )
                return

            # Run analysis
            await self._run_analysis_cycle()

        except Exception as e:
            logger.error(f"Error processing kline: {e}", exc_info=True)

    async def _run_analysis_cycle(self) -> None:
        """Executes a complete analysis cycle."""
        start_time = datetime.now(timezone.utc)
        logger.info("=" * 50)
        logger.info("🔄 Starting analysis cycle")

        try:
            # 1. Get technical indicators
            indicators = get_current_indicators()
            if not indicators:
                logger.warning("No indicators available, skipping cycle")
                return

            # 2. Get microstructure metrics
            micro = self.market_data.get_microstructure_metrics()

            # 3. Get news (if enabled)
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
            
            # 4. Get social data (Twitter/Reddit) and fear & greed
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

            # 5. Execute the agent graph
            if self._trading_graph:
                result = await self._execute_langgraph_analysis(indicators, micro, news_data, social_data, fear_greed_value)
            else:
                result = await self._execute_fallback_analysis(indicators, micro)

            # 5. Process decision
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
        """Executes analysis using LangGraph."""
        logger.info("🧠 Executing LangGraph multi-agent analysis...")

        try:
            # Generate chart for Visual Agent
            chart_b64 = None
            if self.enable_visual:
                try:
                    # Construct kline data from buffers
                    kline_data = {
                        "close": list(close_buf),
                        "high": list(high_buf),
                        "low": list(low_buf),
                        "volume": list(vol_buf),
                        # Simple index as datetime proxy if real timestamps not available in buffers
                        "datetime": [datetime.now(timezone.utc).isoformat() for _ in range(len(close_buf))] 
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

            # Log results of each agent
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
        """Fallback analysis when LangGraph is unavailable."""
        logger.warning("Using fallback analysis (LangGraph unavailable)")

        # Simple analysis based on indicators
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
        """Processes the final decision and executes if applicable."""
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

        # Structured log
        self._log_signal(decision, confidence, reasoning, result)

        # Execute if not HOLD
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
        """Executes a trade based on the decision."""
        logger.info(f"🎯 Executing {decision} trade...")

        self._consecutive_holds = 0
        self._last_decision_time = datetime.now(timezone.utc)

        if self.paper_trading:
            logger.info(f"📝 PAPER TRADE: Would {decision} at {self.market_data.current_price}")
            return

        if not self.allow_live_trading:
            logger.warning(
                "Live trading blocked: allow_live_trading=False. Run with the safe flag to operate."
            )
            return

        # Get risk parameters
        risk_data = decision_data.get("risk_assessment", {})
        entry_price = risk_data.get("entry_price", self.market_data.current_price)
        stop_loss = risk_data.get("stop_loss")
        take_profit = risk_data.get("take_profit")

        # Calculate position size (simplified - in production use RiskManager)
        balance = await self.exchange_client.get_balance()
        if balance is None:
            logger.error("Could not get balance, aborting trade")
            return

        # Calculate quantity based on risk
        position_size = balance * (APP_CONFIG.risk_management.base_risk_per_trade if APP_CONFIG else 0.01)
        quantity = position_size / entry_price
        
        # Execute order
        result = await self.exchange_client.place_order(
            symbol=self.symbol,
            side=decision.lower(),
            quantity=quantity,
            order_type="market",
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if result and result.get('id'):
            logger.info(
                f"✅ Trade executed: {decision} {result.get('amount')} @ {result.get('price')}"
            )
            # Update ReasoningBank with trade outcome (for audit and self-judgment)
            try:
                digest = decision_data.get('_reasoning_digest') or decision_data.get('reasoning_prompt_digest')
                if digest:
                    # For now, mark success as True and attach order id; reward will be computed asynchronously later
                    self.reasoning_bank.update_entry_outcome(
                        agent_name='Decision Agent',
                        prompt_digest=digest,
                        success=True,
                        reward=0.0,
                        trade_id=str(result['id']),
                    )
            except Exception as e:
                logger.debug(f"Failed to attach trade outcome to ReasoningBank: {e}")
        else:
            logger.error(f"❌ Trade failed: {result}")
            try:
                digest = decision_data.get('_reasoning_digest') or decision_data.get('reasoning_prompt_digest')
                if digest:
                    self.reasoning_bank.update_entry_outcome(
                        agent_name='Decision Agent',
                        prompt_digest=digest,
                        success=False,
                        reward=0.0,
                        trade_id=None,
                    )
            except Exception as e:
                logger.debug(f"Failed to attach failed trade outcome to ReasoningBank: {e}")

    def _log_signal(
        self,
        decision: str,
        confidence: str,
        reasoning: str,
        full_result: dict[str, Any],
    ) -> None:
        """Logs a signal for auditing."""
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
        """Returns the current status of the engine."""
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
# Main function to run the engine
# ============================================================================

async def run_trading_engine(
    exchange_id: str = "binance",
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    paper_trading: bool = True,
) -> None:
    """Main function to run the trading engine."""
    engine = TradingEngine(
        exchange_id=exchange_id,
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
    parser.add_argument("--exchange", default="binance", help="Exchange to trade on")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--timeframe", default="15m", help="Timeframe")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    asyncio.run(
        run_trading_engine(
            exchange_id=args.exchange,
            symbol=args.symbol,
            timeframe=args.timeframe,
            paper_trading=not args.live,
        )
    )
