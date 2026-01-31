# src/trading/engine.py
"""
Main Trading Engine for Fenix Trading Bot.

This is the refactored core that orchestrates:
- Market data reception
- LangGraph agent graph execution
- Decision management and order execution
- Logging and metrics
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.trading.market_data import MarketDataManager, get_market_data_manager
from src.trading.binance_client import BinanceClient
from src.trading.executor import OrderExecutor, OrderResult
from src.tools.technical_tools import add_kline, get_current_indicators, close_buf, high_buf, low_buf, vol_buf, open_buf, timestamp_buf
from src.tools.chart_generator import FenixChartGenerator
from src.tools.professional_chart_generator import ProfessionalChartGenerator
from src.tools.enhanced_news_scraper import EnhancedNewsScraper
from src.tools.twitter_scraper import TwitterScraper
from src.tools.reddit_scraper import RedditScraper
from src.tools.fear_greed import FearGreedTool
from src.memory.reasoning_bank import get_reasoning_bank
from src.prompts.agent_prompts import format_prompt

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

try:
    from src.risk.runtime_risk_manager import RuntimeRiskManager, get_risk_manager
    RISK_MANAGER_AVAILABLE = True
except ImportError:
    RISK_MANAGER_AVAILABLE = False
    get_risk_manager = None

logger = logging.getLogger("FenixTradingEngine")


@dataclass
class TradingConfig:
    """Trading engine configuration."""
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
    Main Fenix Trading Engine.

    Operation flow:
    1. Receives market data (klines, orderbook, trades)
    2. Calculates technical indicators
    3. Executes LangGraph agent graph
    4. Processes decision and executes orders if applicable

    This class replaces the monolithic live_trading.py with a
    clean and modular architecture.
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

        # Components
        self.market_data = get_market_data_manager(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )
        self.executor = OrderExecutor(symbol=symbol, testnet=use_testnet)
        self.chart_generator = FenixChartGenerator()
        self.pro_chart_generator = ProfessionalChartGenerator()  # New professional generator
        self.news_scraper = EnhancedNewsScraper()
        self.twitter_scraper = TwitterScraper()
        self.reddit_scraper = RedditScraper()
        self.fear_greed_tool = FearGreedTool()
        self.reasoning_bank = get_reasoning_bank()
        # Callback for frontend events - type hint for async callable
        self.on_agent_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

        # Signal log path for persistence
        project_root = Path(__file__).parent.parent.parent
        self.signal_log_path = project_root / "logs" / "signals" / f"{symbol}_{timeframe}_signals.jsonl"
        self.signal_log_path.parent.mkdir(parents=True, exist_ok=True)

        # State
        self._running = False
        self._last_decision_time: datetime | None = None
        self._consecutive_holds = 0
        self._kline_count = 0
        self._min_klines_to_start = int(os.getenv("FENIX_MIN_KLINES_TO_START", "20"))

        # LangGraph
        self._trading_graph: FenixTradingGraph | None = None
        self.enable_visual = enable_visual_agent
        self.enable_sentiment = enable_sentiment_agent

        # Initialize RiskManager
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

        # Keep engine running
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

        logger.info("TradingEngine stopped")

    async def _on_kline_received(self, kline_data: dict[str, Any]) -> None:
        """Callback when a new kline is received."""
        try:
            # Add kline to indicators buffer (with open and timestamp)
            add_kline(
                close=kline_data["close"],
                high=kline_data["high"],
                low=kline_data["low"],
                volume=kline_data["volume"],
                open_price=kline_data.get("open"),
                timestamp=kline_data.get("open_time"),
            )
            self._kline_count += 1

            # Only process when candle closes
            if not kline_data.get("is_closed", False):
                return

            logger.info(
                f"📊 Kline closed: {kline_data['close']:.2f} "
                f"(H:{kline_data['high']:.2f} L:{kline_data['low']:.2f})"
            )

            # Verify minimum candles
            if self._kline_count < self._min_klines_to_start:
                logger.info(
                    f"Warming up: {self._kline_count}/{self._min_klines_to_start} klines"
                )
                return

            # Execute analysis
            await self._run_analysis_cycle()

        except Exception as e:
            logger.error(f"Error processing kline: {e}", exc_info=True)

    async def _run_analysis_cycle(self) -> None:
        """Executes a full analysis cycle."""
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
                    logger.warning("Failed to fetch news: %s", e)
                # Send news update event to frontend
                if (callback := self.on_agent_event) is not None:
                    await callback("news_update", {
                        "news_data": news_data,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            
            # 4. Get social data (Twitter/Reddit) and Fear & Greed
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

            # 5. Execute agent graph
            if self._trading_graph:
                result = await self._execute_langgraph_analysis(indicators, micro, news_data, social_data, fear_greed_value)
            else:
                result = await self._execute_fallback_analysis(indicators, micro)

            # 6. Process decision
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
                    # If few candles or very short timeframe, use history to avoid empty charts
                    kline_data = None
                    timeframe_ms = {
                        "1m": 60_000,
                        "3m": 180_000,
                        "5m": 300_000,
                        "15m": 900_000,
                        "30m": 1_800_000,
                        "1h": 3_600_000,
                        "4h": 14_400_000,
                        "1d": 86_400_000,
                    }
                    base_ms = timeframe_ms.get(self.timeframe, 900_000)
                    timestamps = list(timestamp_buf)
                    span_ms = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0
                    need_history = len(close_buf) < 50 or span_ms < base_ms * 50

                    if need_history:
                        try:
                            client = BinanceClient(testnet=self.use_testnet)
                            if await client.connect():
                                klines = await client.get_klines(self.symbol, self.timeframe, limit=200)
                                await client.close()
                                if klines:
                                    kline_data = {
                                        "open": [k["open"] for k in klines],
                                        "close": [k["close"] for k in klines],
                                        "high": [k["high"] for k in klines],
                                        "low": [k["low"] for k in klines],
                                        "volume": [k["volume"] for k in klines],
                                        "datetime": [k["timestamp"] for k in klines],
                                    }
                                    logger.info("Using historical klines for chart (%d)", len(klines))
                        except Exception as hist_err:
                            logger.warning("Historical klines fetch failed: %s", hist_err)

                    # Construct kline data from buffers with proper OHLCV and timestamps
                    if not kline_data:
                        kline_data = {
                            "open": list(open_buf),
                            "close": list(close_buf),
                            "high": list(high_buf),
                            "low": list(low_buf),
                            "volume": list(vol_buf),
                            "datetime": list(timestamp_buf)  # Unix timestamps in milliseconds
                        }
                    
                    # Try professional generator first (TradingView style)
                    try:
                        pro_result = self.pro_chart_generator.generate_chart(
                            kline_data=kline_data,
                            symbol=self.symbol,
                            timeframe=self.timeframe,
                            show_indicators=['ema_9', 'ema_21', 'bb_bands', 'vwap'],
                            show_volume=True,
                            show_rsi=True,
                            show_macd=True
                        )
                        chart_b64 = pro_result.get("image_b64")
                        if chart_b64:
                            logger.info("🖼️ Professional chart generated (%d chars)", len(chart_b64))
                    except Exception as pro_err:
                        logger.warning("Professional chart failed, falling back: %s", pro_err)
                        chart_b64 = None
                    
                    # Fallback to original generator if professional fails
                    if not chart_b64:
                        chart_result = self.chart_generator.generate_chart(
                            kline_data=kline_data,
                            symbol=self.symbol,
                            timeframe=self.timeframe,
                            last_n_candles=50
                        )
                        chart_b64 = chart_result.get("image_b64")
                        if chart_b64:
                            logger.info("🖼️ Fallback chart generated (%d chars)", len(chart_b64))
                    
                    if not chart_b64:
                        logger.warning("🖼️ Chart generation returned no image")
                        # Create a placeholder chart image to keep visual agent behavior consistent
                        try:
                            placeholder = self.chart_generator.generate_placeholder(
                                message="Insufficient market data for chart",
                                symbol=self.symbol,
                                timeframe=self.timeframe
                            )
                            chart_b64 = placeholder.get('image_b64')
                            logger.info("🖼️ Placeholder chart generated for visual agent")
                        except Exception:
                            chart_b64 = None
                except Exception as e:
                    logger.error("Failed to generate chart: %s", e)

                result = await self._trading_graph.invoke(
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
                # thread_id argument removed as persistence is disabled
                # thread_id=f"{self.symbol}_{self.timeframe}",
            )

            # Emit agent outputs to frontend
            if (callback := self.on_agent_event) is not None:
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
                        await callback("agent_output", payload)
                        # If the report stored a ReasoningBank digest, emit a reasoning:new event
                        if result[report_key].get("_reasoning_digest"):
                            await callback("reasoning:new", {
                                "agent_name": agent_name,
                                "prompt_digest": result[report_key].get("_reasoning_digest"),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

            # Log individual agent results
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
        logger.info("📋 FINAL DECISION: %s (%s)", decision, confidence)
        logger.info("📝 Reasoning: %s...", reasoning[:200])

        # Emit final decision to frontend
        if (callback := self.on_agent_event) is not None:
            await callback("final_decision", {
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
        """Executes a trade based on decision with active RiskManager."""
        logger.info(f"🎯 Executing {decision} trade...")

        self._consecutive_holds = 0
        self._last_decision_time = datetime.now(timezone.utc)

        # --- CIRCUIT BREAKER: ADVANCED Risk Evaluation ---
        if self.risk_manager and RISK_MANAGER_AVAILABLE:
            # Update balance for risk metrics
            try:
                if self.executor.get_balance():
                    self.risk_manager.update_balance(self.executor.get_balance())
            except Exception as e:
                logger.warning("Could not update risk manager balance: %s", e)
            
            # Check if trade is allowed
            base_size = decision_data.get("position_size", 1000)  # Default $1000
            allowed, risk_status = self.risk_manager.check_trade_allowed(self.symbol, base_size)
            
            if not allowed:
                logger.critical("🚨 TRADE BLOCKED BY CIRCUIT BREAKER: %s", risk_status.describe())
                if (callback := self.on_agent_event) is not None:
                    await callback("risk:blocked", {
                        "status": risk_status.dict(),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                return
            
            # Apply risk_bias to size
            adjusted_size = self.risk_manager.get_adjusted_size(base_size)
            if adjusted_size != base_size:
                logger.info("Size adjusted by risk manager: $%.2f → $%.2f", base_size, adjusted_size)
        # --- END CIRCUIT BREAKER ---

        if self.paper_trading:
            logger.info("📝 PAPER TRADE: Would %s at %s", decision, self.market_data.current_price)
            return

        if not self.allow_live_trading:
            logger.warning(
                "Live trading blocked: allow_live_trading=False. Run with safety flag to operate."
            )
            return

        # Get risk parameters
        risk_data = decision_data.get("risk_assessment", {})
        entry_price = risk_data.get("entry_price", self.market_data.current_price)
        stop_loss = risk_data.get("stop_loss")
        take_profit = risk_data.get("take_profit")

        # Calculate position size (adjusted by RiskManager)
        balance = self.executor.get_balance()
        if balance is None:
            logger.error("Could not get balance, aborting trade")
            return
        logger.info(f"Account balance (USDT): {balance:.2f}")

        # Calculate quantity based on risk
        position_size = adjusted_size if 'adjusted_size' in locals() else (balance * (APP_CONFIG.risk_management.base_risk_per_trade if APP_CONFIG else 0.01))
        
        quantity = position_size / entry_price
        
        # Verify min notional
        notional = quantity * entry_price
        if notional < self.executor.min_notional:
            logger.warning(f"Trade skipped: Notional {notional:.2f} < Min {self.executor.min_notional}")
            return

        # Verify sufficient balance
        if decision == "BUY" and position_size > balance:
            logger.warning(f"Trade skipped: Insufficient balance {balance:.2f} < Required {position_size:.2f}")
            return

        # Execute order
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
            
            # --- UPDATE REASONING BANK ---
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
            
            # --- UPDATE RISK MANAGER ---
            if self.risk_manager and RISK_MANAGER_AVAILABLE:
                try:
                    # Create trade record for metrics
                    from src.risk.runtime_risk_manager import TradeRecord
                    trade_record = TradeRecord(
                        trade_id=str(result.order_id) if result.order_id else "paper_trade",
                        timestamp=datetime.now(timezone.utc),
                        symbol=self.symbol,
                        decision=decision,
                        entry_price=float(result.entry_price) if result.entry_price else 0.0,
                        exit_price=None,  # Will be updated on close
                        pnl=0.0,  # Will be updated on close
                        pnl_pct=0.0,
                        success=True,  # Will be updated when result is known
                        size=float(result.executed_qty) * float(result.entry_price) if result.executed_qty and result.entry_price else 0.0
                    )
                    self.risk_manager.record_trade(trade_record)
                    logger.info(f"Trade recorded in RiskManager: {self.risk_manager.current_status.describe()}")
                except Exception as e:
                    logger.warning(f"Could not record trade in RiskManager: {e}")
        else:
            logger.error(f"❌ Trade failed: {result.status} - {result.message}")
            
            # --- UPDATE REASONING BANK FOR FAILED TRADE ---
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
            
            # --- UPDATE RISK MANAGER FOR FAILED TRADE ---
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
        """Returns RiskManager status for dashboard."""
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
        """Logs signal for audit."""
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
        """Returns the current engine status."""
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
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    paper_trading: bool = True,
) -> None:
    """Main function to run the trading engine."""
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
