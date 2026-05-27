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
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.memory.reasoning_bank import get_reasoning_bank
from src.tools.chart_generator import FenixChartGenerator
from src.tools.enhanced_news_scraper import EnhancedNewsScraper
from src.tools.fear_greed import FearGreedTool
from src.tools.professional_chart_generator import ProfessionalChartGenerator
from src.tools.reddit_scraper import RedditScraper
from src.tools.technical_tools import (
    add_kline,
    close_buf,
    get_current_indicators,
    high_buf,
    low_buf,
    open_buf,
    timestamp_buf,
    vol_buf,
)
from src.tools.twitter_scraper import TwitterScraper
from src.trading.binance_client import BinanceClient
from src.trading.executor import OrderExecutor
from src.trading.market_data import get_market_data_manager
from src.trading.trade_manager import ExitReason, get_trade_manager

# Import LangGraph orchestrator
try:
    from src.core.langgraph_orchestrator import (
        LANGGRAPH_AVAILABLE,
        FenixAgentState,
        FenixTradingGraph,
        get_trading_graph,
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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _env_float(name: str, default: float) -> float:
    value = _safe_float(os.getenv(name))
    if value is None:
        return default
    return value


def _confidence_label(value: Any) -> str:
    raw = str(value or "LOW").strip().upper()
    return raw if raw in {"LOW", "MEDIUM", "HIGH"} else "LOW"


def _confidence_rank(value: Any) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(_confidence_label(value), 1)


def _confidence_score_from_label(value: Any) -> float:
    return {"LOW": 0.35, "MEDIUM": 0.60, "HIGH": 0.85}.get(_confidence_label(value), 0.35)


def _confidence_label_from_score(value: Any) -> str:
    score = _safe_float(value)
    if score is None:
        return _confidence_label(value)
    if score >= 0.75:
        return "HIGH"
    if score >= 0.50:
        return "MEDIUM"
    return "LOW"


def _signed_confidence(signal: Any, confidence: Any) -> float:
    conf = _safe_float(confidence)
    if conf is None:
        conf = {"LOW": 0.35, "MEDIUM": 0.60, "HIGH": 0.85}.get(_confidence_label(confidence), 0.35)
    action = str(signal or "HOLD").strip().upper()
    if action == "BUY":
        return abs(conf)
    if action == "SELL":
        return -abs(conf)
    return 0.0


def _parse_utc_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_nanofenix_signal_result(raw: Any) -> tuple[dict[str, Any] | None, str]:
    if isinstance(raw, tuple) and len(raw) == 2:
        payload, status = raw
        if isinstance(payload, dict):
            return payload, str(status or "ok")
        return None, str(status or "invalid")
    if isinstance(raw, dict):
        return raw, "ok"
    return None, "missing"


def _floor_to_step(value: float, step: float | None) -> float:
    if value <= 0:
        return 0.0
    if not step or step <= 0:
        return value
    units = math.floor((value / step) + 1e-12)
    return max(0.0, units * step)


def _ceil_to_step(value: float, step: float | None) -> float:
    if value <= 0:
        return 0.0
    if not step or step <= 0:
        return value
    units = math.ceil((value / step) - 1e-12)
    return max(step, units * step)


def _is_plausible_price_level(
    entry_price: float | None,
    level: float | None,
    *,
    max_drift_pct: float,
) -> bool:
    if entry_price is None or entry_price <= 0 or level is None or level <= 0:
        return False
    drift_pct = abs(level - entry_price) / entry_price
    return drift_pct <= max(0.0, max_drift_pct)


def _is_directionally_valid_price_level(
    *,
    decision: str,
    field: str,
    entry_price: float | None,
    level: float | None,
) -> bool:
    if entry_price is None or entry_price <= 0 or level is None or level <= 0:
        return False
    normalized_decision = str(decision or "").upper()
    if normalized_decision == "BUY":
        if field == "stop_loss":
            return level < entry_price
        if field == "take_profit":
            return level > entry_price
    if normalized_decision == "SELL":
        if field == "stop_loss":
            return level > entry_price
        if field == "take_profit":
            return level < entry_price
    return False


def _is_plausible_approved_notional(
    approved_size: float | None,
    *,
    max_notional_usd: float,
) -> bool:
    if approved_size is None or approved_size <= 0:
        return False
    min_notional_usd = _env_float("FENIX_MIN_APPROVED_NOTIONAL_USD", 1.0)
    if approved_size < min_notional_usd:
        return False
    if max_notional_usd > 0 and approved_size > (max_notional_usd * 1.25):
        return False
    return True


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
        llm_config: Any = None,
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
        self.trade_manager = get_trade_manager()
        # Callback for frontend events - type hint for async callable
        self.on_agent_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

        # Signal log path for persistence
        project_root = Path(__file__).parent.parent.parent
        self.signal_log_path = (
            project_root / "logs" / "signals" / f"{symbol}_{timeframe}_signals.jsonl"
        )
        self.signal_log_path.parent.mkdir(parents=True, exist_ok=True)

        # NanoFenix companion observability (advisory mode).
        self._project_root = project_root
        self._nanofenix_companion_enabled = _env_flag("FENIX_ENABLE_NANOFENIX_COMPANION", False)
        raw_signal_path = os.getenv(
            "FENIX_NANOFENIX_SIGNAL_PATH",
            f"logs/nanofenixv3_companion_{self.symbol.lower()}.json",
        )
        signal_path = Path(raw_signal_path).expanduser()
        if not signal_path.is_absolute():
            signal_path = project_root / signal_path
        self._nanofenix_signal_path = signal_path
        self._nanofenix_max_signal_age_sec = float(
            os.getenv("FENIX_NANOFENIX_MAX_SIGNAL_AGE_SEC", "25")
        )
        self._nanofenix_min_conf = float(os.getenv("FENIX_NANOFENIX_MIN_CONF", "0.60"))
        self._nanofenix_min_pred_bps = float(os.getenv("FENIX_NANOFENIX_MIN_PRED_BPS", "2.0"))
        self._nanofenix_min_direction_accuracy = float(
            os.getenv("FENIX_NANOFENIX_MIN_DIRECTION_ACCURACY", "0.54")
        )
        self._nanofenix_min_actionable_edge_bps = float(
            os.getenv("FENIX_NANOFENIX_MIN_ACTIONABLE_EDGE_BPS", "0.8")
        )
        self._nanofenix_max_uncertainty_bps = float(
            os.getenv("FENIX_NANOFENIX_MAX_UNCERTAINTY_BPS", "3.0")
        )
        self._nanofenix_min_calibration_health = float(
            os.getenv("FENIX_NANOFENIX_MIN_CALIBRATION_HEALTH", "0.5")
        )
        self._nanofenix_uncertainty_size_reduce_threshold = float(
            os.getenv("FENIX_NANOFENIX_UNCERTAINTY_SIZE_REDUCE_THRESHOLD", "1.5")
        )
        self._nanofenix_marginal_short_size_cap = float(
            os.getenv("FENIX_NANOFENIX_MARGINAL_SHORT_SIZE_CAP", "0.35")
        )
        self._nanofenix_marginal_short_max_edge_bps = float(
            os.getenv("FENIX_NANOFENIX_MARGINAL_SHORT_MAX_EDGE_BPS", "0.5")
        )
        self._nanofenix_marginal_short_max_pred_bps = float(
            os.getenv("FENIX_NANOFENIX_MARGINAL_SHORT_MAX_PRED_BPS", "2.0")
        )
        self._nanofenix_require_allow_execute = _env_flag(
            "FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE", False
        )
        raw_hard_veto_reasons = os.getenv(
            "FENIX_NANOFENIX_HARD_VETO_REASONS",
            "",
        )
        self._nanofenix_hard_veto_reasons = {
            reason.strip() for reason in raw_hard_veto_reasons.split(",") if reason.strip()
        }
        self._technical_extension_guard_enabled = _env_flag("FENIX_TECHNICAL_EXTENSION_GUARD", True)
        self._technical_extension_min_conf = float(
            os.getenv("FENIX_TECHNICAL_EXTENSION_MIN_CONF", "0.70")
        )
        self._technical_extension_min_rr = float(
            os.getenv("FENIX_TECHNICAL_EXTENSION_MIN_RR", "1.60")
        )
        self._nanofenix_require_for_opposite_exit = _env_flag(
            "FENIX_NANOFENIX_REQUIRE_FOR_OPPOSITE_EXIT", False
        )
        self._nanofenix_force_reversal_exit = _env_flag(
            "FENIX_NANOFENIX_FORCE_REVERSAL_EXIT", False
        )
        self._nanofenix_strong_reversal_override = _env_flag(
            "FENIX_NANOFENIX_STRONG_REVERSAL_OVERRIDE", False
        )
        self._nanofenix_strong_reversal_override_score = _env_float(
            "FENIX_NANOFENIX_STRONG_REVERSAL_OVERRIDE_SCORE", 0.80
        )
        self._nanofenix_strong_reversal_override_confidence = (
            os.getenv(
                "FENIX_NANOFENIX_STRONG_REVERSAL_OVERRIDE_CONFIDENCE",
                "HIGH",
            )
            .strip()
            .upper()
        )
        self._nanofenix_timing_trigger_enabled = _env_flag(
            "FENIX_NANOFENIX_TIMING_TRIGGER_ENABLED", False
        )
        self._nanofenix_timing_trigger_ttl_sec = _env_float(
            "FENIX_NANOFENIX_TIMING_TRIGGER_TTL_SEC", 90.0
        )
        self._nanofenix_timing_trigger_min_fast_score = _env_float(
            "FENIX_NANOFENIX_TIMING_TRIGGER_MIN_FAST_SCORE", 1.2
        )
        self._nanofenix_timing_trigger_require_regime = _env_flag(
            "FENIX_NANOFENIX_TIMING_TRIGGER_REQUIRE_REGIME", True
        )
        self._nanofenix_timing_trigger_allow_countertrend = _env_flag(
            "FENIX_NANOFENIX_TIMING_TRIGGER_ALLOW_COUNTERTREND", False
        )
        self._nanofenix_timing_regime: dict[str, Any] | None = None

        # State
        self._running = False
        self._stopping = False
        self._stopped = False
        self._last_decision_time: datetime | None = None
        self._consecutive_holds = 0
        self._kline_count = 0
        self._min_klines_to_start = int(os.getenv("FENIX_MIN_KLINES_TO_START", "20"))
        self._fast_last_trade_ts: datetime | None = None
        self._short_tf_mode = timeframe in {"1m", "3m", "5m"}
        self._min_trade_cooldown_seconds = int(
            os.getenv(
                "FENIX_MIN_TRADE_COOLDOWN_SECONDS",
                "120" if self._short_tf_mode else "0",
            )
        )
        self._analyze_on_start = _env_flag("FENIX_ANALYZE_ON_START", True)
        self._analyze_on_start_delay_sec = max(
            0.0,
            _env_float("FENIX_ANALYZE_ON_START_DELAY_SEC", 2.0),
        )
        self._startup_analysis_task: asyncio.Task | None = None
        self._startup_analysis_done = False
        self._kline_watchdog_task: asyncio.Task | None = None
        self._last_closed_kline_at: datetime | None = None
        self._last_closed_kline_open_time: int | None = None
        self._closed_kline_lock = asyncio.Lock()
        self._analysis_cycle_lock = asyncio.Lock()
        self._kline_watchdog_enabled = _env_flag("FENIX_KLINE_WATCHDOG_ENABLED", True)
        self._kline_watchdog_interval_sec = max(
            15.0,
            _env_float("FENIX_KLINE_WATCHDOG_INTERVAL_SEC", 60.0),
        )
        self._kline_watchdog_grace_sec = max(
            30.0,
            _env_float(
                "FENIX_KLINE_WATCHDOG_GRACE_SEC",
                self._default_kline_watchdog_grace_sec(self.timeframe),
            ),
        )
        self._min_expected_net_edge_usd = _env_float(
            "FENIX_MIN_EXPECTED_NET_EDGE_USD",
            0.10 if self._short_tf_mode else 0.0,
        )
        self._min_expected_net_edge_multiple_of_fees = _env_float(
            "FENIX_MIN_EXPECTED_NET_EDGE_MULTIPLE_OF_FEES",
            1.0 if self._short_tf_mode else 0.0,
        )
        self._engine_cleanup_on_stop = _env_flag("FENIX_CLEANUP_ON_STOP", False)
        self._engine_enforce_llm_risk = _env_flag("FENIX_ENFORCE_LLM_RISK", False)
        self._engine_leverage = _env_float("FENIX_LEVERAGE", 1.0)
        self._risk_max_exposure_pct = _env_float("FENIX_MAX_EXPOSURE_PCT", 0.50)
        self._risk_exposure_leverage_multiplier = _env_float(
            "FENIX_EXPOSURE_LEVERAGE_MULTIPLIER",
            max(1.0, self._engine_leverage),
        )
        self._add_position_reserve_pct = _env_float(
            "FENIX_ADD_POSITION_RESERVE_PCT",
            0.25 if self._short_tf_mode else 0.0,
        )
        self._filter_block_counts: dict[str, int] = {}
        self._filter_adjust_counts: dict[str, int] = {}
        self._fast_reversal_exit_enabled = _env_flag("FENIX_FAST_REVERSAL_EXIT_ENABLED", True)
        self._fast_reversal_exit_score = _env_float("FENIX_FAST_REVERSAL_EXIT_SCORE", 1.9)
        self._fast_reversal_exit_min_adverse_pct = _env_float(
            "FENIX_FAST_REVERSAL_EXIT_MIN_ADVERSE_PCT", 0.12
        )
        self._filter_qabba_min_conf = _env_float("FENIX_FILTER_QABBA_MIN_CONF", 0.70)
        self._filter_qabba_opposite_veto_conf = _env_float(
            "FENIX_FILTER_QABBA_OPPOSITE_VETO_CONF", 0.80
        )
        self._filter_qabba_hold_veto_conf = _env_float("FENIX_FILTER_QABBA_HOLD_VETO_CONF", 0.95)
        self._filter_obi_buy = _env_float("FENIX_FILTER_OBI_BUY", 1.25)
        self._filter_obi_sell = _env_float("FENIX_FILTER_OBI_SELL", 0.80)
        self._filter_volume_imb_th = _env_float("FENIX_FILTER_VOLUME_IMB_TH", 0.15)
        self._filter_chop_size_mult = _env_float("FENIX_FILTER_CHOP_SIZE_MULT", 0.90)
        self._filter_chop_size_mult_short = _env_float("FENIX_FILTER_CHOP_SIZE_MULT_SHORT", 0.85)
        self._filter_chop_size_mult_short_low_conf = _env_float(
            "FENIX_FILTER_CHOP_SIZE_MULT_SHORT_LOW_CONF", 0.70
        )
        self._filter_vpin_high = _env_float("FENIX_FILTER_VPIN_HIGH", 0.90)
        self._filter_rsi_overbought = _env_float("FENIX_FILTER_RSI_OVERBOUGHT", 80.0)
        self._filter_rsi_oversold = _env_float("FENIX_FILTER_RSI_OVERSOLD", 20.0)
        self._filter_sr_prox_pct = _env_float("FENIX_FILTER_SR_PROX_PCT", 0.02)
        self._filter_sr_prox_pct_short = _env_float("FENIX_FILTER_SR_PROX_PCT_SHORT", 0.005)
        self._breakout_qabba_min_conf = _env_float("FENIX_BREAKOUT_QABBA_MIN_CONF", 0.80)
        self._breakout_technical_min_conf = _env_float("FENIX_BREAKOUT_TECHNICAL_MIN_CONF", 0.60)
        self._buy_hold_consolidation_guard = _env_flag("FENIX_BUY_HOLD_CONSOLIDATION_GUARD", False)
        self._buy_hold_consolidation_bandwidth_pct = _env_float(
            "FENIX_BUY_HOLD_CONSOLIDATION_BANDWIDTH_PCT", 0.03
        )
        self._long_confluence_guard = _env_flag("FENIX_LONG_CONFLUENCE_GUARD", False)
        self._long_confluence_qabba_min_conf = _env_float(
            "FENIX_LONG_CONFLUENCE_QABBA_MIN_CONF", 0.70
        )
        self._long_confluence_allow_high_conf = _env_flag(
            "FENIX_LONG_CONFLUENCE_ALLOW_HIGH_CONF", True
        )
        self._short_confluence_guard = _env_flag("FENIX_SHORT_CONFLUENCE_GUARD", False)
        self._short_confluence_qabba_min_conf = _env_float(
            "FENIX_SHORT_CONFLUENCE_QABBA_MIN_CONF", 0.70
        )
        self._short_confluence_allow_high_conf = _env_flag(
            "FENIX_SHORT_CONFLUENCE_ALLOW_HIGH_CONF", True
        )
        self._filter_min_buy_directional_score = _env_float("FENIX_MIN_BUY_DIRECTIONAL_SCORE", 0.0)
        self._filter_min_sell_directional_score = _env_float(
            "FENIX_MIN_SELL_DIRECTIONAL_SCORE", 0.0
        )
        self._medium_buy_strong_edge_enabled = _env_flag(
            "FENIX_MEDIUM_BUY_STRONG_EDGE_ENABLED", False
        )
        self._medium_sell_strong_edge_enabled = _env_flag(
            "FENIX_MEDIUM_SELL_STRONG_EDGE_ENABLED", False
        )
        self._medium_buy_strong_edge_score = _env_float("FENIX_MEDIUM_BUY_STRONG_EDGE_SCORE", 0.60)
        self._medium_sell_strong_edge_score = _env_float(
            "FENIX_MEDIUM_SELL_STRONG_EDGE_SCORE", 0.60
        )
        self._filter_block_trend_conflict_non_high = _env_flag(
            "FENIX_BLOCK_TREND_CONFLICT_NON_HIGH", False
        )
        self._eth3m_long_max_entries = int(os.getenv("FENIX_ETH3M_LONG_MAX_ENTRIES", "2"))
        self._eth3m_require_qabba_for_long_add = _env_flag(
            "FENIX_ETH3M_REQUIRE_QABBA_FOR_LONG_ADD", True
        )
        self._eth3m_long_add_qabba_min_conf = _env_float(
            "FENIX_ETH3M_LONG_ADD_QABBA_MIN_CONF", 0.85
        )
        self._eth3m_block_long_add_in_low_regime = _env_flag(
            "FENIX_ETH3M_BLOCK_LONG_ADD_IN_LOW_REGIME", True
        )
        self._eth3m_block_long_add_on_trend_conflict = _env_flag(
            "FENIX_ETH3M_BLOCK_LONG_ADD_ON_TREND_CONFLICT", True
        )
        self._last_chart_b64: str | None = None
        self._last_chart_ts: str | None = None

        # LangGraph
        self._trading_graph: FenixTradingGraph | None = None
        self._llm_config = llm_config
        self.enable_visual = enable_visual_agent
        self.enable_sentiment = enable_sentiment_agent

        # Initialize RiskManager
        self.risk_manager = get_risk_manager() if RISK_MANAGER_AVAILABLE else None
        if self.risk_manager:
            if hasattr(self.risk_manager, "set_max_exposure_pct"):
                try:
                    self.risk_manager.set_max_exposure_pct(self._risk_max_exposure_pct)
                except Exception as e:
                    logger.warning("Could not set max exposure pct on RuntimeRiskManager: %s", e)
            if hasattr(self.risk_manager, "set_exposure_leverage_multiplier"):
                try:
                    self.risk_manager.set_exposure_leverage_multiplier(
                        self._risk_exposure_leverage_multiplier
                    )
                except Exception as e:
                    logger.warning(
                        "Could not set exposure leverage multiplier on RuntimeRiskManager: %s",
                        e,
                    )
            logger.info("✅ RuntimeRiskManager initialized")
        else:
            logger.warning("⚠️ RuntimeRiskManager not available")

        logger.info(
            f"TradingEngine initialized: {symbol}@{timeframe} "
            f"(paper={paper_trading}, testnet={use_testnet})"
        )
        if self._nanofenix_companion_enabled:
            logger.info(
                "NanoFenix companion observer enabled: path=%s max_age=%.1fs",
                self._nanofenix_signal_path,
                self._nanofenix_max_signal_age_sec,
            )

    async def initialize(self) -> bool:
        """Initializes all components."""
        logger.info("Initializing TradingEngine components...")

        try:
            # Initialize LangGraph
            if LANGGRAPH_AVAILABLE:
                logger.info("Creating LangGraph trading graph...")
                enable_risk = not _env_flag("FENIX_DISABLE_RISK_MANAGER", False)
                self._trading_graph = get_trading_graph(
                    llm_config=self._llm_config,
                    force_new=True,
                    enable_visual=self.enable_visual,
                    enable_sentiment=self.enable_sentiment,
                    enable_risk=enable_risk,
                )
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
        if self._last_closed_kline_at is None:
            self._last_closed_kline_at = datetime.now(timezone.utc)

        if not self.paper_trading:
            try:
                await self._hydrate_tracked_position_from_exchange()
            except Exception:
                logger.warning(
                    "Could not hydrate existing exchange position on startup", exc_info=True
                )

        if self._analyze_on_start and not self._startup_analysis_done:
            self._startup_analysis_task = asyncio.create_task(self._run_startup_analysis_cycle())
        if self._kline_watchdog_enabled:
            logger.info(
                "Kline watchdog enabled: interval=%.1fs grace=%.1fs",
                self._kline_watchdog_interval_sec,
                self._kline_watchdog_grace_sec,
            )
            self._kline_watchdog_task = asyncio.create_task(self._run_kline_watchdog())

        logger.info("🚀 TradingEngine started and listening for market data")

        # Keep engine running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("TradingEngine received cancellation")
        finally:
            await self.stop()

    async def _run_startup_analysis_cycle(self) -> None:
        if self._startup_analysis_done:
            return
        self._startup_analysis_done = True

        delay = max(0.0, float(getattr(self, "_analyze_on_start_delay_sec", 0.0) or 0.0))
        if delay > 0:
            await asyncio.sleep(delay)
        if not self._running:
            return

        logger.info("🚦 Running startup analysis cycle")
        try:
            await self._run_analysis_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Startup analysis cycle failed: %s", exc)

    async def stop(self) -> None:
        """Stops the trading engine."""
        if getattr(self, "_stopped", False) or getattr(self, "_stopping", False):
            return

        logger.info("Stopping TradingEngine...")
        self._running = False
        self._stopping = True

        try:
            startup_task = getattr(self, "_startup_analysis_task", None)
            if startup_task is not None and not startup_task.done():
                startup_task.cancel()
                try:
                    await asyncio.gather(startup_task, return_exceptions=True)
                except Exception:
                    pass

            watchdog_task = getattr(self, "_kline_watchdog_task", None)
            if watchdog_task is not None and not watchdog_task.done():
                watchdog_task.cancel()
                try:
                    await asyncio.gather(watchdog_task, return_exceptions=True)
                except Exception:
                    pass

            tracked_position = None
            if getattr(self, "trade_manager", None) is not None and hasattr(
                self.trade_manager, "get_position"
            ):
                tracked_position = self.trade_manager.get_position(self.symbol)

            if getattr(self, "_engine_cleanup_on_stop", False) and tracked_position is not None:
                if hasattr(self.executor, "cancel_all_orders"):
                    await self.executor.cancel_all_orders()

                exchange_snapshot = self.executor.get_position() or {}
                position_amt = abs(_safe_float(exchange_snapshot.get("positionAmt")) or 0.0)
                if position_amt > 0:
                    close_side = "BUY" if str(tracked_position.side).upper() == "SHORT" else "SELL"
                    close_qty = abs(
                        _safe_float(getattr(tracked_position, "quantity", None)) or position_amt
                    )
                    try:
                        await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                            reduce_only=True,
                        )
                    except TypeError:
                        await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                        )
                    (
                        confirmed_snapshot,
                        _,
                        confirmed_flat,
                    ) = await self._confirm_exchange_flat_snapshot()
                else:
                    (
                        confirmed_snapshot,
                        _,
                        confirmed_flat,
                    ) = await self._confirm_exchange_flat_snapshot(exchange_snapshot)

                if confirmed_flat:
                    exit_price = (
                        _safe_float(confirmed_snapshot.get("markPrice"))
                        or _safe_float(getattr(self.market_data, "current_price", None))
                        or _safe_float(getattr(tracked_position, "entry_price", None))
                        or 0.0
                    )
                    close_result = self.trade_manager.close_position(
                        self.symbol,
                        exit_price,
                        ExitReason.EXCHANGE_RECONCILIATION,
                        "cleanup_on_stop",
                    )
                    await self._synchronize_live_exit(
                        close_result=close_result,
                        tracked_position=tracked_position,
                    )
                    await self._close_position_record(
                        close_result, tracked_position=tracked_position
                    )

            # Stop background/market data components.
            if getattr(self, "market_data", None) is not None:
                await self.market_data.stop()
        finally:
            self._stopping = False
            self._stopped = True
            logger.info("TradingEngine stopped")

    async def _refresh_chart_cache(self, *, force: bool = False, timeout_sec: float = 5.0) -> None:
        """Minimal chart cache hook used by tests and inline warmup paths."""
        del force, timeout_sec

    async def _get_chart_b64_for_analysis(self) -> str | None:
        if not self.enable_visual:
            return None
        chart_b64 = getattr(self, "_last_chart_b64", None)
        if chart_b64:
            return chart_b64
        await self._refresh_chart_cache(force=True, timeout_sec=5.0)
        return getattr(self, "_last_chart_b64", None)

    @staticmethod
    def _timeframe_to_seconds(timeframe: str) -> int:
        raw = str(timeframe or "").strip().lower()
        if len(raw) < 2:
            return 900
        unit = raw[-1]
        try:
            value = int(raw[:-1])
        except Exception:
            return 900
        multipliers = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
        }
        return max(1, value * multipliers.get(unit, 900))

    @classmethod
    def _default_kline_watchdog_grace_sec(cls, timeframe: str) -> float:
        timeframe_sec = float(cls._timeframe_to_seconds(timeframe))
        buffer_sec = max(45.0, min(120.0, timeframe_sec * 0.15))
        return max(90.0, timeframe_sec + buffer_sec)

    def _rest_kline_to_kline_data(self, kline: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": int(kline.get("timestamp") or 0),
            "close_time": int(kline.get("close_time") or 0),
            "open": float(kline.get("open", 0)),
            "high": float(kline.get("high", 0)),
            "low": float(kline.get("low", 0)),
            "close": float(kline.get("close", 0)),
            "volume": float(kline.get("volume", 0)),
            "is_closed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "rest_kline_watchdog",
        }

    async def _poll_closed_kline_fallback(self, *, limit: int = 3) -> int:
        client = BinanceClient(testnet=self.use_testnet)
        connected = False
        try:
            connected = await client.connect()
            if not connected:
                logger.warning("Kline watchdog REST fallback could not connect")
                return 0

            klines = await client.get_klines(self.symbol, self.timeframe, limit=limit)
            last_seen = getattr(self, "_last_closed_kline_open_time", None)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            closed_unseen: list[dict[str, Any]] = []
            for kline in klines or []:
                open_time = int(kline.get("timestamp") or 0)
                close_time = int(kline.get("close_time") or 0)
                if open_time <= 0:
                    continue
                if close_time > now_ms:
                    continue
                if last_seen is not None and open_time <= int(last_seen):
                    continue
                closed_unseen.append(kline)

            if last_seen is None and closed_unseen:
                closed_unseen = [closed_unseen[-1]]

            injected = 0
            for kline in closed_unseen:
                await self._on_kline_received(self._rest_kline_to_kline_data(kline))
                injected += 1
            return injected
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Kline watchdog REST fallback failed: %s", exc)
            return 0
        finally:
            if connected:
                try:
                    await client.close()
                except Exception:
                    pass

    async def _run_kline_watchdog(self) -> None:
        await asyncio.sleep(max(5.0, float(getattr(self, "_kline_watchdog_interval_sec", 60.0))))
        while self._running:
            try:
                last_closed = getattr(self, "_last_closed_kline_at", None)
                if last_closed is None:
                    age_sec = float("inf")
                else:
                    age_sec = (datetime.now(timezone.utc) - last_closed).total_seconds()

                grace_sec = float(getattr(self, "_kline_watchdog_grace_sec", 1200.0))
                if age_sec > grace_sec:
                    if (callback := self.on_agent_event) is not None:
                        await callback(
                            "kline_watchdog:stale",
                            {
                                "symbol": self.symbol,
                                "timeframe": self.timeframe,
                                "age_sec": None if age_sec == float("inf") else round(age_sec, 2),
                                "grace_sec": grace_sec,
                                "last_closed_kline_open_time": getattr(
                                    self,
                                    "_last_closed_kline_open_time",
                                    None,
                                ),
                            },
                        )
                    injected = await self._poll_closed_kline_fallback(limit=3)
                    if injected > 0 and (callback := self.on_agent_event) is not None:
                        await callback(
                            "kline_watchdog:injected",
                            {
                                "symbol": self.symbol,
                                "timeframe": self.timeframe,
                                "injected": injected,
                                "last_closed_kline_open_time": getattr(
                                    self,
                                    "_last_closed_kline_open_time",
                                    None,
                                ),
                            },
                        )
                await asyncio.sleep(
                    max(5.0, float(getattr(self, "_kline_watchdog_interval_sec", 60.0)))
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Kline watchdog loop failed: %s", exc)
                await asyncio.sleep(
                    max(5.0, float(getattr(self, "_kline_watchdog_interval_sec", 60.0)))
                )

    def _ingest_kline(self, kline_data: dict[str, Any]) -> None:
        add_kline(
            close=kline_data["close"],
            high=kline_data["high"],
            low=kline_data["low"],
            volume=kline_data["volume"],
            open_price=kline_data.get("open"),
            timestamp=kline_data.get("open_time"),
        )
        try:
            close_price = _safe_float(kline_data.get("close"))
            if close_price > 0 and self.market_data is not None:
                self.market_data.current_price = close_price
        except Exception:
            pass
        self._kline_count += 1

    async def _on_kline_received(self, kline_data: dict[str, Any]) -> None:
        """Callback when a new kline is received."""
        try:
            if not kline_data.get("is_closed", False):
                self._ingest_kline(kline_data)
                return

            close_lock = getattr(self, "_closed_kline_lock", None)
            if close_lock is None:
                close_lock = asyncio.Lock()
                self._closed_kline_lock = close_lock

            should_analyze = False
            async with close_lock:
                try:
                    open_time = int(kline_data.get("open_time") or 0)
                except Exception:
                    open_time = 0
                last_seen = getattr(self, "_last_closed_kline_open_time", None)
                if open_time > 0 and last_seen is not None and open_time <= int(last_seen):
                    logger.info(
                        "Skipping duplicate/stale closed kline for %s %s: open_time=%s last_seen=%s",
                        self.symbol,
                        self.timeframe,
                        open_time,
                        last_seen,
                    )
                    return

                self._ingest_kline(kline_data)
                logger.info(
                    f"📊 Kline closed: {kline_data['close']:.2f} "
                    f"(H:{kline_data['high']:.2f} L:{kline_data['low']:.2f})"
                )
                self._last_closed_kline_at = datetime.now(timezone.utc)
                self._last_closed_kline_open_time = open_time or None

                if self._kline_count < self._min_klines_to_start:
                    logger.info(
                        f"Warming up: {self._kline_count}/{self._min_klines_to_start} klines"
                    )
                    return
                should_analyze = True

            if should_analyze:
                await self._run_analysis_cycle()

        except Exception as e:
            logger.error(f"Error processing kline: {e}", exc_info=True)

    async def _run_analysis_cycle(self) -> None:
        """Executes a full analysis cycle."""
        analysis_lock = getattr(self, "_analysis_cycle_lock", None)
        if analysis_lock is None:
            analysis_lock = asyncio.Lock()
            self._analysis_cycle_lock = analysis_lock
        if analysis_lock.locked():
            logger.warning("Skipping analysis cycle because the previous cycle is still running")
            if (callback := self.on_agent_event) is not None:
                try:
                    await callback(
                        "analysis_cycle_skipped",
                        {
                            "symbol": self.symbol,
                            "timeframe": self.timeframe,
                            "reason": "previous_cycle_running",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                except Exception:
                    pass
            return

        await analysis_lock.acquire()
        start_time = datetime.now(timezone.utc)
        logger.info("=" * 50)
        logger.info("🔄 Starting analysis cycle")
        if (callback := self.on_agent_event) is not None:
            try:
                await callback(
                    "analysis_cycle_started",
                    {
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "kline_count": self._kline_count,
                        "current_price": _safe_float(
                            getattr(self.market_data, "current_price", None)
                        ),
                        "timestamp": start_time.isoformat(),
                    },
                )
            except Exception:
                pass

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
                    await callback(
                        "news_update",
                        {
                            "news_data": news_data,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )

            # 4. Get social data (Twitter/Reddit) and Fear & Greed
            social_data = {}
            fear_greed_value = None
            if self.enable_sentiment:
                try:
                    twitter_data = (
                        self.twitter_scraper._run() if hasattr(self.twitter_scraper, "_run") else {}
                    )
                except Exception as e:
                    logger.warning(f"Twitter scraper failed: {e}")
                    twitter_data = {}

                try:
                    reddit_data = (
                        self.reddit_scraper._run() if hasattr(self.reddit_scraper, "_run") else {}
                    )
                except Exception as e:
                    logger.warning(f"Reddit scraper failed: {e}")
                    reddit_data = {}

                try:
                    fg = (
                        self.fear_greed_tool._run(1)
                        if hasattr(self.fear_greed_tool, "_run")
                        else None
                    )
                    fear_greed_value = fg if fg is not None else "N/A"
                except Exception as e:
                    logger.warning(f"FearGreed tool failed: {e}")
                    fear_greed_value = "N/A"

                social_data = {
                    "twitter": twitter_data,
                    "reddit": reddit_data,
                }

            # 5. Execute agent graph
            if (
                _env_flag("FENIX_LITE_PIPELINE", False)
                and not self.enable_visual
                and not self.enable_sentiment
            ):
                result = await self._execute_lite_analysis(indicators, micro)
            elif self._trading_graph:
                result = await self._execute_langgraph_analysis(
                    indicators, micro, news_data, social_data, fear_greed_value
                )
            else:
                result = await self._execute_fallback_analysis(indicators, micro)

            # 6. Process decision
            await self._process_decision(result)

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"⏱️ Analysis cycle completed in {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"Error in analysis cycle: {e}", exc_info=True)
        finally:
            analysis_lock.release()

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
                                klines = await client.get_klines(
                                    self.symbol, self.timeframe, limit=200
                                )
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
                                    logger.info(
                                        "Using historical klines for chart (%d)", len(klines)
                                    )
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
                            "datetime": list(timestamp_buf),  # Unix timestamps in milliseconds
                        }

                    # Try professional generator first (TradingView style)
                    try:
                        pro_result = self.pro_chart_generator.generate_chart(
                            kline_data=kline_data,
                            symbol=self.symbol,
                            timeframe=self.timeframe,
                            show_indicators=["ema_9", "ema_21", "bb_bands", "vwap"],
                            show_volume=True,
                            show_rsi=True,
                            show_macd=True,
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
                            last_n_candles=50,
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
                                timeframe=self.timeframe,
                            )
                            chart_b64 = placeholder.get("image_b64")
                            logger.info("🖼️ Placeholder chart generated for visual agent")
                        except Exception:
                            chart_b64 = None
                except Exception as e:
                    logger.error("Failed to generate chart: %s", e)

            # Execute LangGraph analysis (always, even without visual)
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
                mtf_context={},  # Add empty context if needed
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
                    ("Decision Agent", "final_trade_decision"),  # Decision is special
                ]:
                    if result.get(report_key):
                        report_data = dict(result[report_key])
                        # Normalize keys for consistent event logging
                        # Decision Agent uses "final_decision" → normalize to "signal"
                        if "final_decision" in report_data and "signal" not in report_data:
                            report_data["signal"] = report_data["final_decision"]
                        if (
                            "confidence_in_decision" in report_data
                            and "confidence" not in report_data
                        ):
                            report_data["confidence"] = report_data["confidence_in_decision"]
                        # Sentiment Agent uses "overall_sentiment" → normalize to "signal"
                        if "overall_sentiment" in report_data and "signal" not in report_data:
                            report_data["signal"] = report_data["overall_sentiment"]
                        if "confidence_score" in report_data and "confidence" not in report_data:
                            report_data["confidence"] = report_data["confidence_score"]
                        # Risk Manager uses "action" → normalize to "signal"
                        if "action" in report_data and "signal" not in report_data:
                            report_data["signal"] = report_data["action"]
                        payload = {
                            "agent_name": agent_name,
                            "data": report_data,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        # Attach social_data and fear_greed_value for sentiment agent for richer frontend updates
                        if agent_name == "Sentiment Agent":
                            payload["social_data"] = result.get("social_data")
                            payload["fear_greed_value"] = result.get("fear_greed_value")
                        await callback("agent_output", payload)
                        # If the report stored a ReasoningBank digest, emit a reasoning:new event
                        if result[report_key].get("_reasoning_digest"):
                            await callback(
                                "reasoning:new",
                                {
                                    "agent_name": agent_name,
                                    "prompt_digest": result[report_key].get("_reasoning_digest"),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )

            # Log individual agent results
            if result.get("technical_report"):
                logger.info(f"📈 Technical: {result['technical_report'].get('signal', 'N/A')}")
            if result.get("qabba_report"):
                logger.info(f"📊 QABBA: {result['qabba_report'].get('signal', 'N/A')}")
            if result.get("sentiment_report"):
                logger.info(
                    f"💭 Sentiment: {result['sentiment_report'].get('overall_sentiment', 'N/A')}"
                )
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

    def _get_lite_nodes(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
        nodes = getattr(self, "_lite_nodes", None)
        if isinstance(nodes, dict) and nodes:
            return nodes

        graph = getattr(self, "_trading_graph", None)
        llm_factory = getattr(graph, "llm_factory", None)
        if graph is None or llm_factory is None:
            return {}

        try:
            from src.core.langgraph_orchestrator import (
                create_qabba_agent_node,
                create_technical_agent_node,
            )

            reasoning_bank = getattr(graph, "reasoning_bank", None) or getattr(
                self,
                "reasoning_bank",
                None,
            )
            built_nodes = {
                "technical": create_technical_agent_node(
                    llm_factory.get_llm_for_agent("technical"),
                    reasoning_bank,
                ),
                "qabba": create_qabba_agent_node(
                    llm_factory.get_llm_for_agent("qabba"),
                    reasoning_bank,
                ),
            }
            self._lite_nodes = built_nodes
            return built_nodes
        except Exception as exc:
            logger.warning("Could not build lite pipeline nodes: %s", exc)
            return {}

    @staticmethod
    def _normalize_lite_signal(signal: Any) -> str:
        normalized = str(signal or "HOLD").strip().upper()
        if normalized.endswith("_QABBA"):
            normalized = normalized.removesuffix("_QABBA")
        if normalized in {"LONG", "UP"}:
            return "BUY"
        if normalized in {"SHORT", "DOWN"}:
            return "SELL"
        if normalized in {"BUY", "SELL"}:
            return normalized
        return "HOLD"

    @staticmethod
    def _extract_lite_confidence(report: dict[str, Any]) -> float:
        for key in ("confidence", "qabba_confidence", "confidence_score", "convergence_score"):
            value = _safe_float(report.get(key))
            if value is not None:
                return max(0.0, min(1.0, value))
        return _confidence_score_from_label(
            report.get("confidence_level") or report.get("confidence_in_decision")
        )

    async def _call_lite_node(
        self,
        node: Callable[[dict[str, Any]], Any] | None,
        state: dict[str, Any],
        *,
        fallback_key: str,
        fallback_signal: str,
    ) -> dict[str, Any]:
        if node is None:
            return {fallback_key: {"signal": fallback_signal, "error": "lite_node_unavailable"}}
        result = node(state)
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            result = await result
        return dict(result or {})

    async def _call_lite_node_guarded(
        self,
        name: str,
        node: Callable[[dict[str, Any]], Any] | None,
        state: dict[str, Any],
        *,
        fallback_key: str,
        fallback_signal: str,
    ) -> dict[str, Any]:
        timeout_sec = _env_float("FENIX_LITE_NODE_TIMEOUT_SEC", 60.0)
        try:
            return await asyncio.wait_for(
                self._call_lite_node(
                    node,
                    state,
                    fallback_key=fallback_key,
                    fallback_signal=fallback_signal,
                ),
                timeout=max(1.0, timeout_sec),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Lite pipeline %s node timed out after %.1fs; using %s fallback",
                name,
                timeout_sec,
                fallback_signal,
            )
            return {
                fallback_key: {
                    "signal": fallback_signal,
                    "confidence": 0.0,
                    "error": f"{name}_lite_node_timeout",
                },
                "errors": [f"{name}_lite_node_timeout"],
                "execution_times": {name: timeout_sec},
            }
        except Exception as exc:
            logger.warning("Lite pipeline %s node failed: %s", name, exc)
            return {
                fallback_key: {
                    "signal": fallback_signal,
                    "confidence": 0.0,
                    "error": f"{name}_lite_node_error",
                },
                "errors": [f"{name}_lite_node_error:{exc}"],
            }

    async def _get_lite_mtf_bias_context(self) -> dict[str, Any]:
        bias_func = getattr(self, "_get_strict_mtf_bias_context", None)
        if bias_func is None:
            return {}
        try:
            result = bias_func()
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                result = await result
            return dict(result or {}) if isinstance(result, dict) else {}
        except Exception as exc:
            logger.warning("Strict MTF bias context failed: %s", exc)
            return {}

    @staticmethod
    def _ema_scalar(values: list[float], period: int) -> float | None:
        if len(values) < period or period <= 0:
            return None
        alpha = 2.0 / (period + 1.0)
        ema = values[0]
        for value in values[1:]:
            ema = (float(value) * alpha) + (ema * (1.0 - alpha))
        return float(ema)

    def _build_strict_mtf_bias_from_klines(
        self,
        *,
        timeframe: str,
        klines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        closes = [
            close
            for close in (_safe_float(kline.get("close")) for kline in klines or [])
            if close is not None and close > 0
        ]
        if len(closes) < 60:
            return {
                "timeframe": timeframe,
                "signal": "HOLD",
                "confidence": 0.0,
                "source": "deterministic_ema_momentum",
                "reason": "insufficient_klines",
            }

        last_close = closes[-1]
        ema20 = self._ema_scalar(closes, 20)
        ema50 = self._ema_scalar(closes, 50)
        lookback = min(20, len(closes) - 1)
        previous = closes[-1 - lookback]
        momentum = ((last_close - previous) / previous) if previous > 0 else 0.0
        ema_gap = ((ema20 - ema50) / last_close) if ema20 is not None and ema50 is not None else 0.0

        signal = "HOLD"
        if ema20 is not None and ema50 is not None:
            if last_close > ema20 > ema50 and momentum > 0:
                signal = "BUY"
            elif last_close < ema20 < ema50 and momentum < 0:
                signal = "SELL"

        if signal == "HOLD":
            confidence = 0.45
        else:
            trend_strength = min(0.35, abs(momentum) * 20.0 + abs(ema_gap) * 25.0)
            confidence = min(0.95, 0.55 + trend_strength)

        return {
            "timeframe": timeframe,
            "signal": signal,
            "confidence": round(confidence, 4),
            "source": "deterministic_ema_momentum",
            "last_close": round(last_close, 8),
            "ema20": round(float(ema20 or 0.0), 8),
            "ema50": round(float(ema50 or 0.0), 8),
            "momentum": round(momentum, 8),
            "ema_gap": round(ema_gap, 8),
        }

    async def _get_strict_mtf_bias_context(self) -> dict[str, Any]:
        timeframe = os.getenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", "").strip()
        if not timeframe:
            return {}

        ttl_sec = max(0.0, _env_float("FENIX_STRICT_MTF_BIAS_CACHE_SEC", 120.0))
        now_ts = datetime.now(timezone.utc).timestamp()
        cached = getattr(self, "_strict_mtf_bias_cache", None)
        if isinstance(cached, dict):
            cached_tf = cached.get("timeframe")
            cached_at = _safe_float(cached.get("cached_at"))
            cached_payload = cached.get("payload")
            if (
                cached_tf == timeframe
                and cached_at is not None
                and now_ts - cached_at <= ttl_sec
                and isinstance(cached_payload, dict)
            ):
                return dict(cached_payload)

        limit = int(max(80, _env_float("FENIX_STRICT_MTF_BIAS_KLINES", 120.0)))
        client = BinanceClient(testnet=self.use_testnet)
        connected = False
        try:
            connected = await client.connect()
            if not connected:
                return {
                    "timeframe": timeframe,
                    "signal": "HOLD",
                    "confidence": 0.0,
                    "source": "deterministic_ema_momentum",
                    "reason": "binance_connect_failed",
                }
            klines = await client.get_klines(self.symbol, timeframe, limit=limit)
            payload = self._build_strict_mtf_bias_from_klines(
                timeframe=timeframe,
                klines=klines,
            )
            self._strict_mtf_bias_cache = {
                "timeframe": timeframe,
                "cached_at": now_ts,
                "payload": payload,
            }
            return payload
        except Exception as exc:
            logger.warning("Could not build strict MTF bias context: %s", exc)
            return {
                "timeframe": timeframe,
                "signal": "HOLD",
                "confidence": 0.0,
                "source": "deterministic_ema_momentum",
                "reason": "mtf_bias_error",
            }
        finally:
            if connected:
                try:
                    await client.close()
                except Exception:
                    pass

    async def _execute_lite_analysis(
        self,
        indicators: dict[str, Any],
        micro: Any,
    ) -> dict[str, Any]:
        """Fast path: run Technical and QABBA in parallel, then script decision/risk."""
        nodes = self._get_lite_nodes()
        current_volume = _safe_float(getattr(self.market_data, "current_volume", None))
        if current_volume is None or current_volume <= 0:
            current_volume = (
                _safe_float(indicators.get("curr_vol"))
                or _safe_float(indicators.get("volume"))
                or _safe_float(indicators.get("volume_sma"))
                or 0.0
            )
        state = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "indicators": indicators,
            "current_price": _safe_float(getattr(self.market_data, "current_price", None)) or 0.0,
            "current_volume": current_volume,
            "obi": _safe_float(getattr(micro, "obi", None)) or 1.0,
            "cvd": _safe_float(getattr(micro, "cvd", None)) or 0.0,
            "spread": _safe_float(getattr(micro, "spread", None)) or 0.0,
            "spread_pct": _safe_float(getattr(micro, "spread_pct", None)) or 0.0,
            "ofi": _safe_float(getattr(micro, "ofi", None)) or 0.0,
            "qi": _safe_float(getattr(micro, "qi", None)) or 0.0,
            "mlofi": _safe_float(getattr(micro, "mlofi", None)) or 0.0,
            "microprice": _safe_float(getattr(micro, "microprice", None)) or 0.0,
            "trade_imbalance_5s": _safe_float(getattr(micro, "trade_imbalance_5s", None)) or 0.0,
            "trade_volume_5s": _safe_float(getattr(micro, "trade_volume_5s", None)) or 0.0,
            "trade_count_5s": int(_safe_float(getattr(micro, "trade_count_5s", None)) or 0),
            "trade_buy_vol_5s": _safe_float(getattr(micro, "trade_buy_vol_5s", None)) or 0.0,
            "trade_sell_vol_5s": _safe_float(getattr(micro, "trade_sell_vol_5s", None)) or 0.0,
            "cvd_delta_5s": _safe_float(getattr(micro, "cvd_delta_5s", None)) or 0.0,
            "trade_intensity_5s": _safe_float(getattr(micro, "trade_intensity_5s", None)) or 0.0,
            "avg_trade_size_5s": _safe_float(getattr(micro, "avg_trade_size_5s", None)) or 0.0,
            "recent_trades_5s": list(getattr(micro, "recent_trades_5s", []) or [])[-20:],
            "orderbook_depth": {
                "bid_depth": _safe_float(getattr(micro, "bid_depth", None)) or 0.0,
                "ask_depth": _safe_float(getattr(micro, "ask_depth", None)) or 0.0,
            },
            "mtf_context": {},
            "chart_image_b64": None,
            "news_data": [],
            "social_data": {},
            "fear_greed_value": "N/A",
            "messages": [],
            "errors": [],
            "execution_times": {},
        }

        technical_result, qabba_result = await asyncio.gather(
            self._call_lite_node_guarded(
                "technical",
                nodes.get("technical"),
                state,
                fallback_key="technical_report",
                fallback_signal="HOLD",
            ),
            self._call_lite_node_guarded(
                "qabba",
                nodes.get("qabba"),
                state,
                fallback_key="qabba_report",
                fallback_signal="HOLD_QABBA",
            ),
        )

        result: dict[str, Any] = {
            "indicators": indicators,
            "technical_report": dict(technical_result.get("technical_report", {}) or {}),
            "qabba_report": dict(qabba_result.get("qabba_report", {}) or {}),
            "messages": [
                *technical_result.get("messages", []),
                *qabba_result.get("messages", []),
            ],
            "errors": [
                *technical_result.get("errors", []),
                *qabba_result.get("errors", []),
            ],
            "execution_times": {
                **technical_result.get("execution_times", {}),
                **qabba_result.get("execution_times", {}),
            },
            "risk_assessment": {
                "verdict": "APPROVE",
                "risk_score": 0.0,
                "reason": "Lite pipeline uses deterministic execution guards downstream",
            },
        }

        technical_signal = self._normalize_lite_signal(result["technical_report"].get("signal"))
        qabba_signal = self._normalize_lite_signal(result["qabba_report"].get("signal"))
        technical_confidence = self._extract_lite_confidence(result["technical_report"])
        qabba_confidence = self._extract_lite_confidence(result["qabba_report"])
        technical_error = str(result["technical_report"].get("error") or "").strip()
        consensus_mode = os.getenv("FENIX_LITE_CONSENSUS_MODE", "strict_tech_qabba").strip()

        conflicts: list[str] = []
        decision = "HOLD"
        confidence = "LOW"
        directional_score = 0.0

        mtf_bias = await self._get_lite_mtf_bias_context()
        if consensus_mode == "technical_mtf_qabba_guard" and not mtf_bias:
            mtf_bias = {
                "timeframe": os.getenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", "").strip(),
                "signal": "HOLD",
                "confidence": 0.0,
                "source": "strict_mtf_bias_context",
                "reason": "strict_mtf_bias_timeframe_unset",
            }
        mtf_signal = self._normalize_lite_signal((mtf_bias or {}).get("signal"))
        mtf_confidence = _safe_float((mtf_bias or {}).get("confidence")) or 0.0
        mtf_unavailable_reason = ""
        if (
            consensus_mode == "technical_mtf_qabba_guard"
            and mtf_signal == "HOLD"
            and mtf_confidence <= 0
        ):
            mtf_unavailable_reason = str((mtf_bias or {}).get("reason") or "").strip()
            if mtf_unavailable_reason:
                conflicts.append(f"Strict MTF bias unavailable: {mtf_unavailable_reason}")

        if (
            consensus_mode == "strict_tech_qabba"
            and technical_signal in {"BUY", "SELL"}
            and technical_signal == qabba_signal
        ):
            decision = technical_signal
            confidence = "MEDIUM"
            directional_score = (technical_confidence + qabba_confidence) / 2.0
            obi = _safe_float(getattr(micro, "obi", None)) or 1.0
            if decision == "BUY" and obi >= float(getattr(self, "_filter_obi_buy", 1.25)):
                directional_score *= 1.15
            elif decision == "SELL" and obi <= float(getattr(self, "_filter_obi_sell", 0.80)):
                directional_score *= 1.15
        elif consensus_mode == "technical_mtf_qabba_guard":
            strong_qabba_opposes = (
                technical_signal in {"BUY", "SELL"}
                and qabba_signal in {"BUY", "SELL"}
                and qabba_signal != technical_signal
                and qabba_confidence >= _env_float("FENIX_LITE_QABBA_OPPOSE_CONF", 0.72)
            )
            if (
                technical_signal in {"BUY", "SELL"}
                and mtf_signal == technical_signal
                and mtf_confidence >= _env_float("FENIX_LITE_MTF_CONFIRM_CONF", 0.55)
                and not strong_qabba_opposes
            ):
                decision = technical_signal
                confidence = "MEDIUM"
                directional_score = (technical_confidence * 0.65) + (mtf_confidence * 0.35)
                if qabba_signal == technical_signal:
                    directional_score = (directional_score + qabba_confidence) / 2.0
                obi = _safe_float(getattr(micro, "obi", None)) or 1.0
                if decision == "BUY" and obi >= float(getattr(self, "_filter_obi_buy", 1.25)):
                    directional_score *= 1.10
                elif decision == "SELL" and obi <= float(getattr(self, "_filter_obi_sell", 0.80)):
                    directional_score *= 1.10
            elif (
                _env_flag("FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD", False)
                and technical_signal == "HOLD"
                and not technical_error
                and qabba_signal in {"BUY", "SELL"}
                and mtf_signal == qabba_signal
                and mtf_confidence >= _env_float("FENIX_LITE_MTF_CONFIRM_CONF", 0.55)
                and qabba_confidence >= _env_float("FENIX_LITE_MTF_QABBA_MIN_CONF", 0.70)
            ):
                decision = qabba_signal
                confidence = "MEDIUM"
                directional_score = (qabba_confidence * 0.55) + (mtf_confidence * 0.45)
                obi = _safe_float(getattr(micro, "obi", None)) or 1.0
                if decision == "BUY" and obi >= float(getattr(self, "_filter_obi_buy", 1.25)):
                    directional_score *= 1.10
                elif decision == "SELL" and obi <= float(getattr(self, "_filter_obi_sell", 0.80)):
                    directional_score *= 1.10
            elif (
                _env_flag("FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD", False)
                and technical_signal == "HOLD"
                and technical_error
                and qabba_signal in {"BUY", "SELL"}
                and mtf_signal == qabba_signal
            ):
                conflicts.append(f"Technical fallback blocks MTF+QABBA entry: {technical_error}")
            elif strong_qabba_opposes:
                conflicts.append(
                    "QABBA guard blocks opposing strong signal: "
                    f"tech={technical_signal}, mtf={mtf_signal}({mtf_confidence:.2f}), "
                    f"qabba={qabba_signal}({qabba_confidence:.2f})"
                )
            else:
                conflicts.append(
                    "Technical+MTF confirmation required: "
                    f"tech={technical_signal}, mtf={mtf_signal}({mtf_confidence:.2f}), "
                    f"qabba={qabba_signal}"
                )
        elif consensus_mode == "strict_tech_qabba":
            conflicts.append(
                f"Strict consensus requires agreement: tech={technical_signal}, qabba={qabba_signal}"
            )

        if mtf_bias:
            bias_signal = self._normalize_lite_signal(mtf_bias.get("signal"))
            bias_confidence = _safe_float(mtf_bias.get("confidence")) or 0.0
            veto_threshold = _env_float("FENIX_STRICT_MTF_OPPOSING_VETO_CONF", 0.90)
            if (
                decision in {"BUY", "SELL"}
                and bias_signal in {"BUY", "SELL"}
                and bias_signal != decision
                and bias_confidence >= veto_threshold
            ):
                conflicts.append(
                    "Strict MTF bias veto: "
                    f"{mtf_bias.get('timeframe', os.getenv('FENIX_STRICT_MTF_BIAS_TIMEFRAME', 'HTF'))} "
                    f"bias={bias_signal}({bias_confidence:.2f}) opposes entry={decision}"
                )
                decision = "HOLD"
                confidence = "LOW"
                directional_score = 0.0

        result["final_trade_decision"] = {
            "final_decision": decision,
            "confidence_in_decision": confidence,
            "combined_reasoning": (
                "Lite strict consensus from Technical and QABBA"
                if decision in {"BUY", "SELL"}
                else "Lite strict consensus did not approve an entry"
            ),
            "key_conflicting_signals": conflicts,
            "_scripted": True,
            "_scripted_mode": consensus_mode,
            "_directional_score": directional_score,
            "_directional_score_source": f"lite_{consensus_mode}",
            "_mtf_bias": mtf_bias,
        }
        return result

    def _get_tracked_position(self) -> Any | None:
        manager = getattr(self, "trade_manager", None)
        if manager is None or not hasattr(manager, "get_position"):
            return None
        try:
            position = manager.get_position(self.symbol)
        except TypeError:
            position = manager.get_position()
        side = str(getattr(position, "side", "") or "").upper()
        quantity = _safe_float(getattr(position, "quantity", None))
        if side not in {"LONG", "SHORT"} or quantity is None or quantity <= 0:
            return None
        return position

    def _get_last_wick_ratios(self) -> tuple[float, float]:
        """Return lower/upper wick ratios for the latest buffered candle."""
        if not open_buf or not close_buf or not high_buf or not low_buf:
            return 0.0, 0.0
        open_price = float(open_buf[-1])
        close_price = float(close_buf[-1])
        high_price = float(high_buf[-1])
        low_price = float(low_buf[-1])
        candle_range = max(high_price - low_price, 0.0)
        if candle_range <= 0:
            return 0.0, 0.0
        body_low = min(open_price, close_price)
        body_high = max(open_price, close_price)
        lower_wick_ratio = max(0.0, body_low - low_price) / candle_range
        upper_wick_ratio = max(0.0, high_price - body_high) / candle_range
        return lower_wick_ratio, upper_wick_ratio

    def _should_block_sell_rebound(
        self,
        *,
        chop: float,
        chop_regime: str,
        rsi: float,
        percent_b: float,
        lower_wick_ratio: float,
        obi: float,
        wdi: float,
        vol_imb: float,
    ) -> bool:
        choppy = str(chop_regime or "").upper() == "CHOPPY" or float(chop) >= 55.0
        near_lower_band = float(percent_b) <= 0.10 or float(rsi) <= 32.0
        strong_lower_rejection = float(lower_wick_ratio) >= 0.40
        bullish_microstructure = float(obi) >= 1.10 and (float(wdi) > 0.0 or float(vol_imb) > 0.0)
        return bool(
            choppy and near_lower_band and strong_lower_rejection and bullish_microstructure
        )

    def _should_block_buy_rejection(
        self,
        *,
        chop: float,
        chop_regime: str,
        rsi: float,
        percent_b: float,
        upper_wick_ratio: float,
        obi: float,
        wdi: float,
        vol_imb: float,
    ) -> bool:
        choppy = str(chop_regime or "").upper() == "CHOPPY" or float(chop) >= 55.0
        near_upper_band = float(percent_b) >= 0.90 or float(rsi) >= 68.0
        strong_upper_rejection = float(upper_wick_ratio) >= 0.40
        bearish_microstructure = float(obi) <= 0.90 and (float(wdi) < 0.0 or float(vol_imb) < 0.0)
        return bool(
            choppy and near_upper_band and strong_upper_rejection and bearish_microstructure
        )

    async def _hydrate_tracked_position_from_exchange(self) -> bool:
        """Recover local position tracking from an already-open exchange position."""
        if self.paper_trading:
            return False
        if self._get_tracked_position() is not None:
            return False
        if not hasattr(self.executor, "get_position"):
            return False
        if getattr(self, "trade_manager", None) is None or not hasattr(
            self.trade_manager, "open_position"
        ):
            return False

        snapshot = self.executor.get_position() or {}
        position_amt = _safe_float(snapshot.get("positionAmt")) or 0.0
        if abs(position_amt) <= 1e-9:
            return False

        side = "LONG" if position_amt > 0 else "SHORT"
        quantity = abs(position_amt)
        entry_price = (
            _safe_float(snapshot.get("entryPrice"))
            or _safe_float(snapshot.get("breakEvenPrice"))
            or _safe_float(snapshot.get("markPrice"))
            or _safe_float(getattr(self.market_data, "current_price", None))
            or 0.0
        )
        if entry_price <= 0 or quantity <= 0:
            logger.warning(
                "Exchange position hydration skipped for %s: invalid qty/entry qty=%s entry=%s",
                self.symbol,
                quantity,
                entry_price,
            )
            return False

        stop_loss = None
        take_profit = None
        sl_order_id = None
        tp_order_id = None
        service = getattr(self.executor, "service", None)
        if service is not None and hasattr(service, "get_open_algo_orders"):
            try:
                for order in service.get_open_algo_orders(self.symbol) or []:
                    order_type = str(order.get("orderType") or order.get("type") or "").upper()
                    trigger_price = _safe_float(order.get("triggerPrice") or order.get("stopPrice"))
                    order_id = (
                        order.get("algoId") or order.get("orderId") or order.get("clientAlgoId")
                    )
                    if "TAKE_PROFIT" in order_type:
                        take_profit = trigger_price or take_profit
                        tp_order_id = order_id or tp_order_id
                    elif "STOP" in order_type:
                        stop_loss = trigger_price or stop_loss
                        sl_order_id = order_id or sl_order_id
            except Exception as exc:
                logger.debug("Could not inspect open algo orders during hydration: %s", exc)

        trade_id = f"hydrated:{self.symbol}:{datetime.now(timezone.utc).isoformat()}"
        position = self.trade_manager.open_position(
            symbol=self.symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            signal_timestamp=datetime.now(timezone.utc).isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trade_id=trade_id,
            reasoning_digest=None,
            decision_agent_name="Exchange Hydration",
            protection_position_id=None,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

        if self.risk_manager and RISK_MANAGER_AVAILABLE:
            try:
                from src.risk.runtime_risk_manager import TradeRecord

                trade_record = TradeRecord(
                    trade_id=trade_id,
                    timestamp=datetime.now(timezone.utc),
                    symbol=self.symbol,
                    decision="BUY" if side == "LONG" else "SELL",
                    entry_price=entry_price,
                    exit_price=None,
                    pnl=0.0,
                    pnl_pct=0.0,
                    success=True,
                    size=quantity * entry_price,
                )
                if hasattr(self.risk_manager, "open_trade"):
                    self.risk_manager.open_trade(trade_record)
                elif hasattr(self.risk_manager, "update_open_position"):
                    self.risk_manager.update_open_position(
                        self.symbol,
                        size=quantity * entry_price,
                        notional=quantity * entry_price,
                        side=side.lower(),
                    )
            except Exception:
                logger.debug("Could not hydrate RuntimeRiskManager position", exc_info=True)

        logger.warning(
            "Hydrated existing exchange position: %s %s %.8f @ %.8f (SL=%s TP=%s)",
            self.symbol,
            side,
            quantity,
            entry_price,
            stop_loss,
            take_profit,
        )
        if (callback := self.on_agent_event) is not None:
            await callback(
                "position:hydrated",
                {
                    "symbol": self.symbol,
                    "side": side,
                    "price": entry_price,
                    "qty": quantity,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "trade_id": getattr(position, "trade_id", trade_id),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        return True

    async def _emit_filter_adjusted(self, name: str, details: dict[str, Any] | None = None) -> None:
        counts = getattr(self, "_filter_adjust_counts", None)
        if isinstance(counts, dict):
            counts[name] = counts.get(name, 0) + 1
        if (callback := self.on_agent_event) is not None:
            payload = {"filter": name, "timestamp": datetime.now(timezone.utc).isoformat()}
            if details:
                payload.update(details)
            await callback("filter:adjusted", payload)

    async def _emit_filter_blocked(self, name: str, details: dict[str, Any] | None = None) -> None:
        counts = getattr(self, "_filter_block_counts", None)
        if isinstance(counts, dict):
            counts[name] = counts.get(name, 0) + 1
        if (callback := self.on_agent_event) is not None:
            payload = {"filter": name, "timestamp": datetime.now(timezone.utc).isoformat()}
            if details:
                payload.update(details)
            await callback("filter:blocked", payload)

    def _micro_is_strong(self, decision: str, micro: Any) -> bool:
        obi = _safe_float(getattr(micro, "obi", None)) or 1.0
        volume_imbalance = _safe_float(getattr(micro, "volume_imbalance", None)) or 0.0
        vpin_proxy = _safe_float(getattr(micro, "vpin_proxy", None)) or 0.0
        if decision == "BUY":
            return (
                obi >= float(getattr(self, "_filter_obi_buy", 1.25))
                and volume_imbalance >= float(getattr(self, "_filter_volume_imb_th", 0.15))
                and vpin_proxy <= float(getattr(self, "_filter_vpin_high", 0.90))
            )
        if decision == "SELL":
            return (
                obi <= float(getattr(self, "_filter_obi_sell", 0.80))
                and volume_imbalance <= -float(getattr(self, "_filter_volume_imb_th", 0.15))
                and vpin_proxy <= float(getattr(self, "_filter_vpin_high", 0.90))
            )
        return False

    def _sr_breakout_is_confirmed(
        self,
        decision: str,
        qabba_signal: str,
        qabba_confidence: float,
        technical_report: dict[str, Any],
        companion_policy: dict[str, Any] | None,
    ) -> bool:
        if decision not in {"BUY", "SELL"}:
            return False
        if qabba_signal != decision:
            return False
        if qabba_confidence < float(getattr(self, "_breakout_qabba_min_conf", 0.80)):
            return False
        if not isinstance(companion_policy, dict):
            return False

        companion_action = self._nanofenix_signal_to_action(
            companion_policy.get("action") or companion_policy.get("signal") or "HOLD"
        )
        if companion_action in {"BUY", "SELL"}:
            if companion_action != decision:
                return False
            if not self._nanofenix_confirms_action(decision, companion_policy):
                return False

        technical_confidence = _safe_float(technical_report.get("confidence")) or 0.0
        if technical_confidence < float(getattr(self, "_breakout_technical_min_conf", 0.60)):
            return False

        technical_signal = str(technical_report.get("signal") or "HOLD").upper()
        if technical_signal not in {"HOLD", decision}:
            return False

        return True

    def _is_buy_hold_consolidation_setup(
        self,
        indicators: dict[str, Any],
        technical_report: dict[str, Any],
    ) -> bool:
        technical_signal = str(technical_report.get("signal") or "HOLD").upper()
        if technical_signal != "HOLD":
            return False

        market_condition = str(indicators.get("market_condition") or "").upper()
        if market_condition in {"CONSOLIDATION", "EXTREME_CONSOLIDATION"}:
            return True
        if bool(indicators.get("bb_squeeze")):
            return True

        bandwidth_pct = _safe_float(indicators.get("bandwidth_pct"))
        if bandwidth_pct is None:
            return False
        return bandwidth_pct <= float(getattr(self, "_buy_hold_consolidation_bandwidth_pct", 0.03))

    def _build_directional_score(
        self, result: dict[str, Any], decision_data: dict[str, Any]
    ) -> tuple[float, str]:
        raw_score = _safe_float(decision_data.get("_directional_score"))
        if raw_score is not None:
            return raw_score, str(decision_data.get("_directional_score_source") or "llm_decision")

        technical = result.get("technical_report", {}) or {}
        qabba = result.get("qabba_report", {}) or {}
        score = 0.45 * _signed_confidence(
            technical.get("signal"), technical.get("confidence")
        ) + 0.55 * _signed_confidence(qabba.get("signal"), qabba.get("confidence"))
        return score, "fallback_weighted_reports"

    def _get_min_entry_confidence(self) -> str:
        return os.getenv("FENIX_MIN_ENTRY_CONFIDENCE", "MEDIUM").strip().upper()

    def _get_flip_min_confidence(self) -> str:
        return os.getenv("FENIX_MIN_FLIP_CONFIDENCE", "MEDIUM").strip().upper()

    def _normalize_technical_report(
        self, technical_report: dict[str, Any] | None
    ) -> dict[str, Any]:
        normalized = dict(technical_report or {})
        confidence = _safe_float(normalized.get("confidence"))
        if confidence is None:
            confidence = _confidence_score_from_label(normalized.get("confidence_level"))
        confidence = max(0.0, min(1.0, float(confidence or 0.0)))
        normalized["confidence"] = confidence
        normalized["confidence_level"] = _confidence_label_from_score(confidence)
        return normalized

    @staticmethod
    def _nanofenix_signal_to_action(signal: Any) -> str:
        normalized = str(signal or "").strip().upper()
        if normalized in {"LONG", "BUY", "UP"}:
            return "BUY"
        if normalized in {"SHORT", "SELL", "DOWN"}:
            return "SELL"
        return "HOLD"

    def _nanofenix_side_metrics(
        self, action: str, companion: dict[str, Any]
    ) -> tuple[bool, float | None, float | None]:
        action = str(action or "").upper()
        short_tf = bool(getattr(self, "_short_tf_mode", False))
        short_ready = bool(companion.get("short_companion_ready", False))
        short_accuracy = _safe_float(companion.get("short_direction_accuracy"))
        short_utility = _safe_float(companion.get("short_utility_score"))
        if short_tf and short_ready:
            direction_accuracy = short_accuracy
            if direction_accuracy is None:
                direction_accuracy = _safe_float(companion.get("direction_accuracy"))
            return True, direction_accuracy, short_utility
        if action == "BUY":
            ready = bool(
                companion.get("long_companion_ready", companion.get("companion_ready", False))
            )
            direction_accuracy = _safe_float(companion.get("long_direction_accuracy"))
            if direction_accuracy is None:
                direction_accuracy = _safe_float(companion.get("direction_accuracy"))
            return ready, direction_accuracy, _safe_float(companion.get("long_utility_score"))
        if action == "SELL":
            ready = bool(
                companion.get("short_companion_ready", companion.get("companion_ready", False))
            )
            direction_accuracy = short_accuracy
            if direction_accuracy is None:
                direction_accuracy = _safe_float(companion.get("direction_accuracy"))
            return ready, direction_accuracy, short_utility
        return (
            bool(companion.get("companion_ready", False)),
            _safe_float(companion.get("direction_accuracy")),
            _safe_float(companion.get("utility_score")),
        )

    @staticmethod
    def _collect_price_levels(raw_levels: Any) -> list[float]:
        if raw_levels is None:
            return []
        values = raw_levels if isinstance(raw_levels, (list, tuple, set)) else [raw_levels]
        parsed: list[float] = []
        for item in values:
            if isinstance(item, dict):
                for key in ("price", "level", "value"):
                    level = _safe_float(item.get(key))
                    if level is not None and level > 0:
                        parsed.append(level)
                        break
                continue
            level = _safe_float(item)
            if level is not None and level > 0:
                parsed.append(level)
        return parsed

    def _is_same_side(self, position_side: str, decision: str) -> bool:
        return (position_side == "LONG" and decision == "BUY") or (
            position_side == "SHORT" and decision == "SELL"
        )

    def _is_opposite_side(self, position_side: str, decision: str) -> bool:
        return (position_side == "LONG" and decision == "SELL") or (
            position_side == "SHORT" and decision == "BUY"
        )

    def _nanofenix_confirms_action(self, action: str, companion: dict[str, Any] | None) -> bool:
        if not isinstance(companion, dict):
            return False

        action = str(action or "").upper()
        companion_action = self._nanofenix_signal_to_action(
            companion.get("action") or companion.get("signal") or "HOLD"
        )
        if action not in {"BUY", "SELL"} or companion_action != action:
            return False

        companion_ready, direction_accuracy, _ = self._nanofenix_side_metrics(action, companion)

        confidence = _safe_float(companion.get("confidence")) or 0.0
        pred_bps = _safe_float(companion.get("pred_bps")) or 0.0
        min_conf = float(getattr(self, "_nanofenix_min_conf", 0.60))
        min_pred_bps = float(getattr(self, "_nanofenix_min_pred_bps", 2.0))
        if bool(getattr(self, "_short_tf_mode", False)) and bool(
            companion.get("short_companion_ready", False)
        ):
            min_conf = min(min_conf, 0.60)
            min_pred_bps = min(min_pred_bps, 2.0)
        if not companion_ready:
            return False
        if confidence < min_conf:
            return False
        if abs(pred_bps) < min_pred_bps:
            return False
        if action == "BUY" and pred_bps <= 0:
            return False
        if action == "SELL" and pred_bps >= 0:
            return False
        if (direction_accuracy or 0.0) < float(
            getattr(self, "_nanofenix_min_direction_accuracy", 0.54)
        ):
            return False
        edge_net_bps = _safe_float(companion.get("edge_net_bps"))
        if edge_net_bps is not None and edge_net_bps < float(
            getattr(self, "_nanofenix_min_actionable_edge_bps", 0.8)
        ):
            return False
        uncertainty_bps = _safe_float(companion.get("uncertainty_bps"))
        if uncertainty_bps is not None and uncertainty_bps > float(
            getattr(self, "_nanofenix_max_uncertainty_bps", 3.0)
        ):
            return False
        calibration_health = _safe_float(companion.get("calibration_health"))
        if calibration_health is not None and calibration_health < float(
            getattr(self, "_nanofenix_min_calibration_health", 0.5)
        ):
            return False
        return True

    async def _close_position_record(
        self,
        close_result: dict[str, Any] | None,
        *,
        tracked_position: Any | None = None,
    ) -> None:
        if not close_result:
            return

        trade_id = close_result.get("trade_id")
        realized_pnl = float(close_result.get("pnl", 0.0) or 0.0)
        realized_pnl_pct = float(close_result.get("pnl_pct", 0.0) or 0.0)
        realized_exit_price = _safe_float(close_result.get("exit_price"))
        realized_success = realized_pnl >= 0.0
        if self.risk_manager is not None:
            if trade_id and hasattr(self.risk_manager, "close_trade"):
                try:
                    self.risk_manager.close_trade(
                        trade_id,
                        exit_price=realized_exit_price,
                        pnl=realized_pnl,
                        pnl_pct=realized_pnl_pct,
                        success=realized_success,
                        symbol=self.symbol,
                    )
                except Exception:
                    pass
            elif hasattr(self.risk_manager, "close_trade_by_symbol"):
                try:
                    self.risk_manager.close_trade_by_symbol(
                        self.symbol,
                        exit_price=realized_exit_price,
                        pnl=realized_pnl,
                        pnl_pct=realized_pnl_pct,
                        success=realized_success,
                    )
                except Exception:
                    pass

        digest = close_result.get("reasoning_digest") or getattr(
            tracked_position, "reasoning_digest", None
        )
        agent_name = close_result.get("decision_agent_name") or getattr(
            tracked_position, "decision_agent_name", None
        )
        if digest:
            try:
                self.reasoning_bank.update_entry_outcome(
                    agent_name=agent_name or "Decision Agent",
                    prompt_digest=digest,
                    success=(float(close_result.get("pnl", 0.0)) >= 0.0),
                    reward=float(close_result.get("pnl", 0.0)),
                    trade_id=trade_id,
                )
            except Exception:
                pass

        if (callback := self.on_agent_event) is not None:
            await callback(
                "position:closed",
                {
                    **close_result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        self._fast_last_trade_ts = datetime.now(timezone.utc)

    async def _refresh_exchange_protection_if_needed(self, position: Any) -> None:
        if not bool(getattr(position, "protection_refresh_pending", False)):
            return
        if not hasattr(self.executor, "refresh_position_protection"):
            return

        refresh_result = await self.executor.refresh_position_protection(
            position_id=getattr(position, "protection_position_id", None),
            side=str(getattr(position, "side", "LONG")).upper(),
            quantity=float(getattr(position, "quantity", 0.0) or 0.0),
            stop_loss=_safe_float(getattr(position, "stop_loss", None)),
            take_profit=_safe_float(getattr(position, "take_profit", None)),
        )
        if not getattr(refresh_result, "success", False):
            return

        if hasattr(position, "mark_protection_synced"):
            position.mark_protection_synced(
                stop_loss=_safe_float(getattr(position, "stop_loss", None)),
                take_profit=_safe_float(getattr(position, "take_profit", None)),
                position_id=getattr(refresh_result, "position_id", None),
                sl_order_id=getattr(refresh_result, "sl_order_id", None),
                tp_order_id=getattr(refresh_result, "tp_order_id", None),
            )
        if (callback := self.on_agent_event) is not None:
            await callback(
                "position:protection_refreshed",
                {
                    "symbol": self.symbol,
                    "position_id": getattr(refresh_result, "position_id", None),
                    "sl_order_id": str(getattr(refresh_result, "sl_order_id", None) or ""),
                    "tp_order_id": str(getattr(refresh_result, "tp_order_id", None) or ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

    async def _manage_open_position(self, new_signal: str | None = None) -> dict[str, Any] | None:
        tracked_position = self._get_tracked_position()
        if tracked_position is None:
            return None

        current_price = (
            _safe_float(getattr(self.market_data, "current_price", None))
            or _safe_float(getattr(tracked_position, "entry_price", None))
            or 0.0
        )
        if current_price <= 0:
            return None

        position_side = str(getattr(tracked_position, "side", "") or "").upper()
        opposite_signal_exit = str(new_signal or "").upper() in {
            "BUY",
            "SELL",
        } and self._is_opposite_side(position_side, str(new_signal).upper())

        if (
            opposite_signal_exit
            and not self.paper_trading
            and hasattr(self.executor, "execute_market_order")
        ):
            close_side = "BUY" if position_side == "SHORT" else "SELL"
            close_qty = abs(_safe_float(getattr(tracked_position, "quantity", None)) or 0.0)
            if close_qty > 0:
                close_order = None
                try:
                    try:
                        close_order = await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                            reduce_only=True,
                        )
                    except TypeError:
                        close_order = await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                        )
                except Exception:
                    logger.exception(
                        "Live opposite-signal exit failed for %s %s qty=%.6f",
                        self.symbol,
                        close_side,
                        close_qty,
                    )
                    return None

                if close_order is not None and not bool(getattr(close_order, "success", False)):
                    logger.warning(
                        "Live opposite-signal exit rejected for %s %s qty=%.6f: %s",
                        self.symbol,
                        close_side,
                        close_qty,
                        getattr(close_order, "message", "unknown error"),
                    )
                    if hasattr(self, "_reconcile_tracked_position_with_exchange"):
                        try:
                            await self._reconcile_tracked_position_with_exchange()
                        except Exception:
                            logger.debug(
                                "Live opposite-signal rejection reconciliation failed",
                                exc_info=True,
                            )
                    return None

                close_result = self.trade_manager.close_position(
                    self.symbol,
                    _safe_float(getattr(close_order, "entry_price", None)) or current_price,
                    ExitReason.OPPOSITE_SIGNAL,
                    f"Opposite signal: {new_signal}",
                )
                if hasattr(self, "_synchronize_live_exit"):
                    try:
                        await self._synchronize_live_exit(
                            close_result=close_result,
                            tracked_position=tracked_position,
                        )
                    except Exception:
                        logger.debug(
                            "Live opposite-signal exit reconciliation failed", exc_info=True
                        )
                await self._close_position_record(close_result, tracked_position=tracked_position)
                return close_result

        close_result = self.trade_manager.check_exit_conditions(
            self.symbol,
            current_price,
            new_signal=new_signal,
        )
        if close_result:
            if not self.paper_trading:
                close_side = "BUY" if position_side == "SHORT" else "SELL"
                close_qty = abs(_safe_float(getattr(tracked_position, "quantity", None)) or 0.0)
                if close_qty <= 0:
                    logger.warning(
                        "Live exit skipped for %s: invalid tracked quantity %.8f",
                        self.symbol,
                        close_qty,
                    )
                    await self._hydrate_tracked_position_from_exchange()
                    return None

                close_order = None
                try:
                    try:
                        close_order = await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                            reduce_only=True,
                        )
                    except TypeError:
                        close_order = await self.executor.execute_market_order(
                            side=close_side,
                            quantity=close_qty,
                        )
                except Exception:
                    logger.exception(
                        "Live rule-based exit failed for %s %s qty=%.6f",
                        self.symbol,
                        close_side,
                        close_qty,
                    )
                    await self._hydrate_tracked_position_from_exchange()
                    return None

                if close_order is not None and not bool(getattr(close_order, "success", False)):
                    logger.warning(
                        "Live rule-based exit rejected for %s %s qty=%.6f: %s",
                        self.symbol,
                        close_side,
                        close_qty,
                        getattr(close_order, "message", "unknown error"),
                    )
                    await self._hydrate_tracked_position_from_exchange()
                    return None

                confirmed_snapshot, _, confirmed_flat = await self._confirm_exchange_flat_snapshot()
                if not confirmed_flat:
                    logger.warning(
                        "Live rule-based exit did not confirm exchange flat for %s; rehydrating",
                        self.symbol,
                    )
                    await self._hydrate_tracked_position_from_exchange()
                    return None

            if hasattr(self, "_synchronize_live_exit") and not self.paper_trading:
                try:
                    await self._synchronize_live_exit(
                        close_result=close_result, tracked_position=tracked_position
                    )
                except TypeError:
                    try:
                        await self._synchronize_live_exit(close_result)
                    except Exception:
                        pass
                except Exception:
                    pass
            await self._close_position_record(close_result, tracked_position=tracked_position)
            return close_result

        await self._refresh_exchange_protection_if_needed(tracked_position)
        return None

    async def _run_fast_decision_cycle(self) -> None:
        await self._manage_open_position()

    async def _confirm_exchange_flat_snapshot(
        self,
        snapshot: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, bool]:
        first = snapshot or self.executor.get_position() or {}
        amount = abs(_safe_float(first.get("positionAmt")) or 0.0)
        if amount <= 1e-9:
            return first, amount, True
        second = self.executor.get_position() or {}
        second_amount = abs(_safe_float(second.get("positionAmt")) or 0.0)
        return second, second_amount, second_amount <= 1e-9

    async def _synchronize_live_exit(
        self,
        close_result: dict[str, Any] | None,
        tracked_position: Any | None = None,
    ) -> bool:
        if self.paper_trading or not isinstance(close_result, dict):
            return False
        if tracked_position is None:
            tracked_position = self._get_tracked_position()
        if tracked_position is None or not hasattr(self.executor, "get_recent_trades"):
            return False

        position_side = str(getattr(tracked_position, "side", "") or "").upper()
        if position_side not in {"LONG", "SHORT"}:
            return False
        close_side = "BUY" if position_side == "SHORT" else "SELL"

        entry_time = getattr(tracked_position, "entry_time", None)
        entry_dt = entry_time if isinstance(entry_time, datetime) else _parse_utc_iso(entry_time)
        exit_dt = _parse_utc_iso(close_result.get("exit_time")) or datetime.now(timezone.utc)
        if entry_dt is None:
            entry_dt = exit_dt - timedelta(hours=6)

        start_ms = int((entry_dt - timedelta(seconds=5)).timestamp() * 1000)
        end_ms = int((exit_dt + timedelta(minutes=2)).timestamp() * 1000)
        trades = (
            self.executor.get_recent_trades(start_time=start_ms, end_time=end_ms, limit=50) or []
        )
        if not trades:
            return False

        filtered: list[dict[str, Any]] = []
        for trade in sorted(trades, key=lambda item: int(item.get("time", 0) or 0)):
            if str(trade.get("side") or "").upper() != close_side:
                continue
            trade_time = int(trade.get("time", 0) or 0)
            if trade_time < start_ms or trade_time > end_ms:
                continue
            filtered.append(trade)
        if not filtered:
            return False

        latest_time = int(filtered[-1].get("time", 0) or 0)
        target_qty = abs(_safe_float(getattr(tracked_position, "quantity", None)) or 0.0)
        selected: list[dict[str, Any]] = []
        cum_qty = 0.0
        for trade in reversed(filtered):
            trade_time = int(trade.get("time", 0) or 0)
            if selected and (latest_time - trade_time) > 120_000:
                break
            qty = abs(_safe_float(trade.get("qty")) or 0.0)
            if qty <= 0:
                continue
            selected.append(trade)
            cum_qty += qty
            if target_qty > 0 and cum_qty >= target_qty * 0.98:
                break
        if not selected:
            return False
        selected.reverse()

        total_qty = sum(abs(_safe_float(trade.get("qty")) or 0.0) for trade in selected)
        if total_qty <= 0:
            return False
        weighted_exit = (
            sum(
                (
                    abs(_safe_float(trade.get("qty")) or 0.0)
                    * (_safe_float(trade.get("price")) or 0.0)
                )
                for trade in selected
            )
            / total_qty
        )
        realized_pnl = sum(_safe_float(trade.get("realizedPnl")) or 0.0 for trade in selected)
        commission = sum(abs(_safe_float(trade.get("commission")) or 0.0) for trade in selected)

        close_result["exit_price"] = round(weighted_exit, 8)
        close_result["exchange_exit_price"] = round(weighted_exit, 8)
        close_result["exchange_realized_pnl"] = round(realized_pnl, 8)
        close_result["exchange_commission"] = round(commission, 8)
        close_result["exchange_fill_reconciled"] = True

        close_result["pnl"] = realized_pnl
        entry_price = _safe_float(getattr(tracked_position, "entry_price", None)) or 0.0
        if entry_price > 0:
            if position_side == "SHORT":
                close_result["pnl_pct"] = ((entry_price - weighted_exit) / entry_price) * 100.0
            else:
                close_result["pnl_pct"] = ((weighted_exit - entry_price) / entry_price) * 100.0

        logger.info(
            "Synchronized live exit from exchange fills: side=%s qty=%.6f exit=%.8f pnl=%.8f commission=%.8f",
            close_side,
            total_qty,
            weighted_exit,
            realized_pnl,
            commission,
        )
        return True

    async def _cleanup_flat_symbol_orders(
        self,
        *,
        tracked_position: Any | None,
        source: str,
    ) -> bool:
        del source
        if tracked_position is None:
            return True

        protection_id = getattr(tracked_position, "protection_position_id", None)
        if protection_id and hasattr(self.executor, "cancel_position_protection"):
            cancelled = await self.executor.cancel_position_protection(protection_id)
            if cancelled:
                return True

        if hasattr(self.executor, "cancel_all_orders"):
            await self.executor.cancel_all_orders()
        return True

    async def _reconcile_tracked_position_with_exchange(self) -> None:
        tracked_position = self._get_tracked_position()
        if tracked_position is None:
            return

        snapshot = self.executor.get_position() or {}
        confirmed_snapshot, _, confirmed_flat = await self._confirm_exchange_flat_snapshot(snapshot)
        if not confirmed_flat:
            return

        await self._cleanup_flat_symbol_orders(
            tracked_position=tracked_position,
            source="exchange_reconciliation",
        )
        exit_price = (
            _safe_float(confirmed_snapshot.get("markPrice"))
            or _safe_float(getattr(self.market_data, "current_price", None))
            or float(getattr(tracked_position, "entry_price", 0.0) or 0.0)
        )
        close_result = self.trade_manager.close_position(
            self.symbol,
            exit_price,
            ExitReason.EXCHANGE_RECONCILIATION,
            "Exchange flat snapshot confirmed",
        )
        await self._synchronize_live_exit(
            close_result=close_result,
            tracked_position=tracked_position,
        )
        await self._close_position_record(close_result, tracked_position=tracked_position)

    def _compute_fast_signal(
        self, indicators: dict[str, Any], micro: Any
    ) -> tuple[str, float, str]:
        del indicators, micro
        return "HOLD", 0.0, "fast_signal_unavailable"

    def _apply_fast_reversal_exit_policy(
        self,
        *,
        position_side: str,
        entry_price: float,
        current_price: float,
        decision: str,
        new_signal: str | None,
        qabba_signal: str | None,
        qabba_confidence: float | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        if not bool(getattr(self, "_fast_reversal_exit_enabled", False)):
            return new_signal, None
        if str(decision).upper() != "HOLD" or new_signal is not None:
            return new_signal, None

        position_side = str(position_side or "").upper()
        if position_side not in {"LONG", "SHORT"}:
            return new_signal, None

        adverse_move_pct = 0.0
        if entry_price > 0:
            if position_side == "LONG":
                adverse_move_pct = max(0.0, ((entry_price - current_price) / entry_price) * 100.0)
            else:
                adverse_move_pct = max(0.0, ((current_price - entry_price) / entry_price) * 100.0)
        if adverse_move_pct < float(getattr(self, "_fast_reversal_exit_min_adverse_pct", 0.12)):
            return new_signal, None

        opposite_signal = "SELL" if position_side == "LONG" else "BUY"
        qabba_signal = str(qabba_signal or "HOLD").upper()
        qabba_confidence = _safe_float(qabba_confidence) or 0.0
        if qabba_signal != opposite_signal or qabba_confidence < float(
            getattr(self, "_filter_qabba_hold_veto_conf", 0.95)
        ):
            return new_signal, None

        fast_signal, fast_score, fast_reason = self._compute_fast_signal(
            get_current_indicators() or {},
            self.market_data.get_microstructure_metrics(),
        )
        if fast_signal != opposite_signal or fast_score < float(
            getattr(self, "_fast_reversal_exit_score", 1.9)
        ):
            return new_signal, None

        companion, _ = _coerce_nanofenix_signal_result(self._read_nanofenix_companion_signal())
        if bool(
            getattr(self, "_nanofenix_companion_enabled", False)
        ) and not self._nanofenix_confirms_action(
            opposite_signal,
            companion,
        ):
            return None, {
                "policy": "fast_reversal_exit",
                "blocked": True,
                "reason": "hold_exit_not_confirmed_by_nanofenix",
                "forced_signal": opposite_signal,
                "fast_reason": fast_reason,
            }

        return opposite_signal, {
            "policy": "fast_reversal_exit",
            "blocked": False,
            "reason": "fast_reversal_confirmed",
            "forced_signal": opposite_signal,
            "fast_reason": fast_reason,
        }

    def _apply_nanofenix_exit_policy(
        self,
        *,
        position_side: str,
        decision: str,
        new_signal: str | None,
        confidence: str,
        directional_score: float,
    ) -> tuple[str | None, dict[str, Any] | None]:
        if not bool(getattr(self, "_nanofenix_companion_enabled", False)):
            return new_signal, None
        if new_signal is None:
            return new_signal, None
        if not self._is_opposite_side(str(position_side).upper(), str(new_signal).upper()):
            return new_signal, None

        companion, _ = _coerce_nanofenix_signal_result(self._read_nanofenix_companion_signal())
        if self._nanofenix_confirms_action(new_signal, companion):
            return new_signal, {
                "policy": "nanofenix_companion",
                "blocked": False,
                "reason": "opposite_exit_confirmed",
            }

        override_conf_ok = _confidence_rank(confidence) >= _confidence_rank(
            getattr(self, "_nanofenix_strong_reversal_override_confidence", "HIGH")
        )
        override_score_ok = abs(directional_score) >= float(
            getattr(self, "_nanofenix_strong_reversal_override_score", 0.80)
        )
        if (
            bool(getattr(self, "_nanofenix_strong_reversal_override", False))
            and override_conf_ok
            and override_score_ok
        ):
            return new_signal, {
                "policy": "nanofenix_companion",
                "blocked": False,
                "reason": "strong_reversal_override_without_companion",
            }

        if bool(getattr(self, "_nanofenix_require_for_opposite_exit", False)) or bool(
            getattr(self, "_nanofenix_force_reversal_exit", False)
        ):
            return None, {
                "policy": "nanofenix_companion",
                "blocked": True,
                "reason": "opposite_exit_not_confirmed",
            }

        return new_signal, None

    def _update_nanofenix_timing_regime(
        self,
        *,
        technical_report: dict[str, Any] | None,
        qabba_report: dict[str, Any] | None,
        decision_data: dict[str, Any] | None,
    ) -> None:
        if not bool(getattr(self, "_nanofenix_timing_trigger_enabled", False)):
            return

        technical_report = technical_report or {}
        qabba_report = qabba_report or {}
        tech_signal = str(technical_report.get("signal") or "HOLD").upper()
        qabba_signal = str(qabba_report.get("signal") or "HOLD").upper()
        tech_conf = _safe_float(technical_report.get("confidence")) or 0.0
        qabba_conf = _safe_float(qabba_report.get("confidence")) or 0.0
        bias = None
        if (
            tech_signal == "BUY"
            and qabba_signal == "BUY"
            and tech_conf >= 0.75
            and qabba_conf >= 0.75
        ):
            bias = "LONG"
        elif (
            tech_signal == "SELL"
            and qabba_signal == "SELL"
            and tech_conf >= 0.75
            and qabba_conf >= 0.75
        ):
            bias = "SHORT"
        if bias is None:
            return

        self._nanofenix_timing_regime = {
            "bias": bias,
            "source": "tech_qabba_alignment",
            "technical_signal": tech_signal,
            "qabba_signal": qabba_signal,
            "decision": str((decision_data or {}).get("final_decision") or "").upper() or None,
            "expires_at_utc": (
                datetime.now(timezone.utc)
                + timedelta(seconds=float(getattr(self, "_nanofenix_timing_trigger_ttl_sec", 90.0)))
            ).isoformat(),
        }

    def _get_active_nanofenix_timing_regime(self) -> dict[str, Any] | None:
        regime = getattr(self, "_nanofenix_timing_regime", None)
        if not isinstance(regime, dict):
            return None
        expires_at = _parse_utc_iso(regime.get("expires_at_utc"))
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            self._nanofenix_timing_regime = None
            return None
        return regime

    def _compute_nanofenix_fast_trigger(
        self,
        indicators: dict[str, Any],
        micro: Any,
    ) -> tuple[str, float, str]:
        if not bool(getattr(self, "_nanofenix_timing_trigger_enabled", False)):
            return "HOLD", 0.0, "FAST_NANO_TRIGGER disabled"

        regime = self._get_active_nanofenix_timing_regime()
        if bool(getattr(self, "_nanofenix_timing_trigger_require_regime", True)) and regime is None:
            return "HOLD", 0.0, "FAST_NANO_TRIGGER missing slow regime"

        companion, _ = _coerce_nanofenix_signal_result(self._read_nanofenix_companion_signal())
        action = str(
            (companion or {}).get("action") or (companion or {}).get("signal") or "HOLD"
        ).upper()
        if action not in {"BUY", "SELL"}:
            return "HOLD", 0.0, "FAST_NANO_TRIGGER companion HOLD"
        if not self._nanofenix_confirms_action(action, companion):
            return "HOLD", 0.0, "FAST_NANO_TRIGGER companion not ready"

        if action == "BUY":
            short_tf = bool(getattr(self, "_short_tf_mode", False))
            if short_tf:
                short_acc = _safe_float(
                    (companion or {}).get("short_companion_direction_accuracy")
                ) or _safe_float((companion or {}).get("short_direction_accuracy"))
                if short_acc is not None and short_acc < 0.55:
                    return "HOLD", 0.0, f"FAST_NANO_TRIGGER short_acc={short_acc:.1%} < 55%"
            else:
                long_acc = _safe_float((companion or {}).get("long_companion_direction_accuracy"))
                if long_acc is not None and long_acc < 0.55:
                    return "HOLD", 0.0, f"FAST_NANO_TRIGGER long_acc={long_acc:.1%} < 55%"

        fast_signal, fast_score, fast_reason = self._compute_fast_signal(indicators, micro)
        if fast_signal != action:
            return "HOLD", 0.0, "FAST_NANO_TRIGGER fast signal mismatch"
        if fast_score < float(getattr(self, "_nanofenix_timing_trigger_min_fast_score", 1.2)):
            return "HOLD", 0.0, "FAST_NANO_TRIGGER fast score too low"

        if regime is not None:
            bias = str(regime.get("bias") or "").upper()
            if not bool(getattr(self, "_nanofenix_timing_trigger_allow_countertrend", False)) and (
                (bias == "LONG" and action == "SELL") or (bias == "SHORT" and action == "BUY")
            ):
                return "HOLD", 0.0, "FAST_NANO_TRIGGER opposes slow regime"

        utility_score = _safe_float((companion or {}).get("short_utility_score")) or 0.0
        return action, max(fast_score, utility_score), f"FAST_NANO_TRIGGER {fast_reason}"

    async def _process_decision(self, result: dict[str, Any]) -> None:
        """Processes the final decision, applying guards before execution."""
        decision_data = dict(result.get("final_trade_decision", {}) or {})
        result["final_trade_decision"] = decision_data
        risk_report = dict(result.get("risk_assessment", {}) or {})
        indicators = dict(result.get("indicators", {}) or {})
        technical_report = self._normalize_technical_report(
            dict(result.get("technical_report", {}) or {})
        )
        result["technical_report"] = technical_report
        qabba_report = dict(result.get("qabba_report", {}) or {})
        micro = self.market_data.get_microstructure_metrics()

        decision = str(decision_data.get("final_decision", "HOLD") or "HOLD").upper()
        confidence = _confidence_label(decision_data.get("confidence_in_decision", "LOW"))
        reasoning = decision_data.get("combined_reasoning", "No reasoning")

        risk_assessment = dict(decision_data.get("risk_assessment", {}) or {})
        order_details = dict(risk_report.get("order_details", {}) or {})
        dynamic_levels = dict(risk_report.get("dynamic_risk_levels", {}) or {})
        current_price = _safe_float(getattr(self.market_data, "current_price", None))
        if "entry_price" not in risk_assessment:
            risk_assessment["entry_price"] = current_price
        entry_price = _safe_float(risk_assessment.get("entry_price")) or current_price
        max_risk_level_drift_pct = _env_float(
            "FENIX_MAX_RISK_LEVEL_DRIFT_PCT",
            0.35 if bool(getattr(self, "_short_tf_mode", False)) else 0.60,
        )

        def _merge_price_level(field: str) -> None:
            candidates = [
                ("risk_manager.order_details", _safe_float(order_details.get(field))),
                ("risk_manager.dynamic_levels", _safe_float(dynamic_levels.get(field))),
            ]
            for source, level in candidates:
                if level is None:
                    continue
                if _is_plausible_price_level(
                    entry_price,
                    level,
                    max_drift_pct=max_risk_level_drift_pct,
                ) and _is_directionally_valid_price_level(
                    decision=decision,
                    field=field,
                    entry_price=entry_price,
                    level=level,
                ):
                    risk_assessment[field] = level
                    return
                logger.warning(
                    "Ignoring implausible or directionally invalid %s=%s from %s for %s %s entry=%s",
                    field,
                    level,
                    source,
                    self.symbol,
                    decision,
                    entry_price,
                )

        _merge_price_level("stop_loss")
        _merge_price_level("take_profit")

        approved_size = _safe_float(order_details.get("approved_size"))
        max_notional_usd = _env_float("FENIX_MAX_NOTIONAL_USD", 0.0)
        if approved_size is not None:
            if _is_plausible_approved_notional(approved_size, max_notional_usd=max_notional_usd):
                decision_data["position_size"] = approved_size
            else:
                logger.warning(
                    "Ignoring implausible approved_size=%s from risk_manager for %s",
                    approved_size,
                    self.symbol,
                )

        if dynamic_levels.get("risk_reward_ratio") is not None:
            risk_assessment["risk_reward_ratio"] = _safe_float(
                dynamic_levels.get("risk_reward_ratio")
            )
        decision_data["risk_assessment"] = risk_assessment

        directional_score, score_source = self._build_directional_score(result, decision_data)
        decision_data["_directional_score"] = directional_score
        decision_data["_directional_score_source"] = score_source
        self._update_nanofenix_timing_regime(
            technical_report=technical_report,
            qabba_report=qabba_report,
            decision_data=decision_data,
        )
        companion_policy = self._build_nanofenix_policy_payload(decision)

        logger.info("=" * 50)
        logger.info("📋 FINAL DECISION: %s (%s)", decision, confidence)
        logger.info("📝 Reasoning: %s...", reasoning[:200])

        if (callback := self.on_agent_event) is not None:
            await callback(
                "final_decision",
                {
                    "decision": decision,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "full_data": decision_data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        if companion_policy is not None:
            if (callback := self.on_agent_event) is not None:
                await callback("nanofenix:policy", companion_policy)
            decision_data["nanofenix_policy"] = companion_policy

        self._log_signal(decision, confidence, reasoning, result)

        async def _hold(reason: str | None = None, *, filter_name: str | None = None) -> None:
            nonlocal decision
            if filter_name:
                await self._emit_filter_blocked(filter_name, {"reason": reason} if reason else None)
            decision = "HOLD"
            decision_data["effective_decision"] = "HOLD"
            if reason:
                decision_data["hold_reason"] = reason
            self._consecutive_holds += 1
            await self._manage_open_position(new_signal="HOLD")

        if decision in {"BUY", "SELL"} and bool(getattr(self, "_engine_enforce_llm_risk", False)):
            verdict = str(risk_report.get("verdict") or "").strip().upper()
            if risk_report.get("parse_error") or verdict not in {
                "APPROVE",
                "APPROVE_REDUCED",
                "ALLOW",
            }:
                await _hold("llm_risk_verdict_missing_or_reject")
                return

        if (
            decision in {"BUY", "SELL"}
            and bool(getattr(self, "_nanofenix_companion_enabled", False))
            and bool(getattr(self, "_nanofenix_require_allow_execute", False))
            and isinstance(companion_policy, dict)
            and self._nanofenix_policy_hard_vetoes_entry(companion_policy)
        ):
            await _hold(
                f"nanofenix_hard_veto:{companion_policy.get('reason', 'blocked')}",
                filter_name="NANOFENIX",
            )
            return

        if decision in {"BUY", "SELL"}:
            trend_conflict = bool(indicators.get("trend_conflict"))
            qabba_signal = str(qabba_report.get("signal") or "HOLD").upper()
            qabba_confidence = _safe_float(qabba_report.get("confidence")) or 0.0
            confluence_rescued = False

            if decision == "BUY" and directional_score < float(
                getattr(self, "_filter_min_buy_directional_score", 0.0)
            ):
                await _hold("directional_score_too_low", filter_name="DIRECTIONAL_SCORE")
                return
            if decision == "SELL" and abs(directional_score) < float(
                getattr(self, "_filter_min_sell_directional_score", 0.0)
            ):
                await _hold("directional_score_too_low", filter_name="DIRECTIONAL_SCORE")
                return

            min_conf = self._get_min_entry_confidence()
            if _confidence_rank(confidence) < _confidence_rank(min_conf):
                strong_edge = False
                if (
                    decision == "BUY"
                    and confidence == "MEDIUM"
                    and bool(getattr(self, "_medium_buy_strong_edge_enabled", False))
                    and directional_score
                    >= float(getattr(self, "_medium_buy_strong_edge_score", 0.60))
                ):
                    strong_edge = True
                if (
                    decision == "SELL"
                    and confidence == "MEDIUM"
                    and bool(getattr(self, "_medium_sell_strong_edge_enabled", False))
                    and abs(directional_score)
                    >= float(getattr(self, "_medium_sell_strong_edge_score", 0.60))
                ):
                    strong_edge = True

                if strong_edge:
                    decision_data["confidence_override_reason"] = "strong_directional_edge"
                    await self._emit_filter_adjusted(
                        "MIN_ENTRY_CONFIDENCE",
                        {"reason": "strong_directional_edge"},
                    )
                else:
                    await _hold("min_entry_confidence", filter_name="MIN_ENTRY_CONFIDENCE")
                    return

            rsi = _safe_float(indicators.get("rsi")) or 50.0
            if decision == "BUY" and rsi >= float(getattr(self, "_filter_rsi_overbought", 80.0)):
                await _hold("rsi_extreme_buy", filter_name="RSI_EXTREME")
                return
            if decision == "SELL" and rsi <= float(getattr(self, "_filter_rsi_oversold", 20.0)):
                await _hold("rsi_extreme_sell", filter_name="RSI_EXTREME")
                return

            if (
                decision == "BUY"
                and bool(getattr(self, "_long_confluence_guard", False))
                and trend_conflict
                and not (
                    qabba_signal == "BUY"
                    and qabba_confidence
                    >= float(getattr(self, "_long_confluence_qabba_min_conf", 0.70))
                )
                and not (
                    bool(getattr(self, "_long_confluence_allow_high_conf", True))
                    and confidence == "HIGH"
                )
            ):
                await _hold("long_confluence_missing", filter_name="LONG_CONFLUENCE")
                return
            if (
                decision == "BUY"
                and bool(getattr(self, "_long_confluence_guard", False))
                and trend_conflict
                and qabba_signal == "BUY"
                and qabba_confidence
                >= float(getattr(self, "_long_confluence_qabba_min_conf", 0.70))
            ):
                confluence_rescued = True

            book_confirms_sell = (_safe_float(getattr(micro, "obi", None)) or 1.0) <= float(
                getattr(self, "_filter_obi_sell", 0.80)
            ) and (_safe_float(getattr(micro, "volume_imbalance", None)) or 0.0) <= -float(
                getattr(self, "_filter_volume_imb_th", 0.15)
            )
            if (
                decision == "SELL"
                and bool(getattr(self, "_short_confluence_guard", False))
                and trend_conflict
                and not (
                    qabba_signal == "SELL"
                    and qabba_confidence
                    >= float(getattr(self, "_short_confluence_qabba_min_conf", 0.70))
                    and book_confirms_sell
                )
                and not (
                    bool(getattr(self, "_short_confluence_allow_high_conf", True))
                    and confidence == "HIGH"
                )
            ):
                await _hold("short_confluence_missing", filter_name="SHORT_CONFLUENCE")
                return
            if (
                decision == "SELL"
                and bool(getattr(self, "_short_confluence_guard", False))
                and trend_conflict
                and qabba_signal == "SELL"
                and qabba_confidence
                >= float(getattr(self, "_short_confluence_qabba_min_conf", 0.70))
                and book_confirms_sell
            ):
                confluence_rescued = True

            if (
                bool(getattr(self, "_filter_block_trend_conflict_non_high", False))
                and trend_conflict
                and confidence != "HIGH"
            ):
                await _hold("trend_conflict_non_high", filter_name="TREND_CONFLICT")
                return

            chop = _safe_float(indicators.get("chop")) or 0.0
            micro_strong = self._micro_is_strong(decision, micro)
            if chop >= 45.0:
                if micro_strong or confluence_rescued:
                    current_mult = max(
                        0.0, _safe_float(decision_data.get("size_multiplier")) or 1.0
                    )
                    if bool(getattr(self, "_short_tf_mode", False)):
                        target_mult = float(
                            getattr(
                                self,
                                "_filter_chop_size_mult_short_low_conf",
                                0.70,
                            )
                            if confidence == "LOW"
                            else getattr(self, "_filter_chop_size_mult_short", 0.85)
                        )
                    else:
                        target_mult = float(getattr(self, "_filter_chop_size_mult", 0.90))
                    decision_data["size_multiplier"] = min(current_mult, target_mult)
                    await self._emit_filter_adjusted(
                        "CHOP", {"size_multiplier": decision_data["size_multiplier"]}
                    )
                else:
                    await _hold("chop_regime_without_micro_confirmation", filter_name="CHOP")
                    return

            technical_signal = str(technical_report.get("signal") or "HOLD").upper()
            if (
                decision == "BUY"
                and bool(getattr(self, "_buy_hold_consolidation_guard", False))
                and technical_signal == "HOLD"
                and self._is_buy_hold_consolidation_setup(indicators, technical_report)
                and bool(getattr(self, "_nanofenix_companion_enabled", False))
            ):
                companion_action = self._nanofenix_signal_to_action(
                    (companion_policy or {}).get("action")
                    or (companion_policy or {}).get("signal")
                    or "HOLD"
                )
                if companion_action == "SELL":
                    await _hold(
                        "buy_hold_consolidation_blocked_by_nanofenix",
                        filter_name="NANOFENIX",
                    )
                    return
                if companion_action == "BUY" and not self._nanofenix_confirms_action(
                    "BUY", companion_policy
                ):
                    await _hold(
                        "buy_hold_consolidation_blocked_by_nanofenix",
                        filter_name="NANOFENIX",
                    )
                    return

            current_price = _safe_float(getattr(self.market_data, "current_price", None)) or 0.0
            proximity_pct = float(
                getattr(self, "_filter_sr_prox_pct_short", 0.005)
                if bool(getattr(self, "_short_tf_mode", False))
                else getattr(self, "_filter_sr_prox_pct", 0.02)
            )
            technical_confidence = _safe_float(technical_report.get("confidence")) or 0.0
            technical_rr = _safe_float(technical_report.get("risk_reward_ratio"))
            technical_resistances = self._collect_price_levels(
                technical_report.get("resistance_level")
            )
            technical_supports = self._collect_price_levels(technical_report.get("support_level"))
            technical_tradeability_context = any(
                _safe_float(technical_report.get(field)) is not None
                for field in ("support_level", "resistance_level", "risk_reward_ratio")
            )
            technical_context_relaxed = bool(
                technical_tradeability_context
                and technical_confidence
                >= float(getattr(self, "_technical_extension_min_conf", 0.70)) * 0.90 - 1e-9
                and technical_rr is not None
                and technical_rr
                >= float(getattr(self, "_technical_extension_min_rr", 1.60)) * 0.75 - 1e-9
            )
            if decision == "BUY" and current_price > 0:
                resistances = [
                    _safe_float(level)
                    for level in (indicators.get("resistances") or [])
                    if _safe_float(level) is not None
                ]
                resistances.extend(technical_resistances)
                near_resistance = any(
                    abs((level - current_price) / current_price) <= proximity_pct
                    for level in resistances
                )
                if near_resistance:
                    breakout_confirmed = self._sr_breakout_is_confirmed(
                        decision,
                        qabba_signal,
                        qabba_confidence,
                        technical_report,
                        companion_policy,
                    )
                    if breakout_confirmed:
                        await self._emit_filter_adjusted(
                            "RESISTANCE",
                            {"reason": "breakout_confirmed_by_qabba_nanofenix"},
                        )
                    elif not micro_strong and not technical_context_relaxed:
                        await _hold(
                            "near_resistance_without_micro_confirmation", filter_name="RESISTANCE"
                        )
                        return
                    if (
                        not breakout_confirmed
                        and bool(getattr(self, "_technical_extension_guard_enabled", True))
                        and technical_tradeability_context
                        and not technical_context_relaxed
                    ):
                        if (
                            technical_confidence
                            < float(getattr(self, "_technical_extension_min_conf", 0.70))
                            or technical_rr is None
                            or technical_rr
                            < float(getattr(self, "_technical_extension_min_rr", 1.60))
                        ):
                            await _hold(
                                "technical_extension_risk_near_resistance",
                                filter_name="TECHNICAL_EXTENSION",
                            )
                            return
                    nanofenix_blocks_entry = bool(
                        bool(getattr(self, "_nanofenix_companion_enabled", False))
                        and companion_policy is not None
                        and not bool(companion_policy.get("allow_execute", False))
                    )
                    if (
                        nanofenix_blocks_entry
                        and not breakout_confirmed
                        and not technical_context_relaxed
                    ):
                        await _hold(
                            f"near_resistance_blocked_by_nanofenix:{companion_policy.get('reason', 'blocked')}",
                            filter_name="NANOFENIX",
                        )
                        return
            if decision == "SELL" and current_price > 0:
                supports = [
                    _safe_float(level)
                    for level in (indicators.get("supports") or [])
                    if _safe_float(level) is not None
                ]
                supports.extend(technical_supports)
                near_support = any(
                    abs((current_price - level) / current_price) <= proximity_pct
                    for level in supports
                )
                if near_support:
                    breakout_confirmed = self._sr_breakout_is_confirmed(
                        decision,
                        qabba_signal,
                        qabba_confidence,
                        technical_report,
                        companion_policy,
                    )
                    if breakout_confirmed:
                        await self._emit_filter_adjusted(
                            "SUPPORT",
                            {"reason": "breakdown_confirmed_by_qabba_nanofenix"},
                        )
                    elif not (micro_strong or book_confirms_sell or technical_context_relaxed):
                        await _hold(
                            "near_support_without_micro_confirmation", filter_name="SUPPORT"
                        )
                        return
                    if (
                        not breakout_confirmed
                        and bool(getattr(self, "_technical_extension_guard_enabled", True))
                        and technical_tradeability_context
                        and not technical_context_relaxed
                    ):
                        if (
                            technical_confidence
                            < float(getattr(self, "_technical_extension_min_conf", 0.70))
                            or technical_rr is None
                            or technical_rr
                            < float(getattr(self, "_technical_extension_min_rr", 1.60))
                        ):
                            await _hold(
                                "technical_extension_risk_near_support",
                                filter_name="TECHNICAL_EXTENSION",
                            )
                            return

        tracked_position = self._get_tracked_position()
        if tracked_position is not None:
            position_side = str(getattr(tracked_position, "side", "")).upper()
            current_price = (
                _safe_float(getattr(self.market_data, "current_price", None))
                or _safe_float(getattr(tracked_position, "entry_price", None))
                or 0.0
            )
            if decision == "HOLD":
                new_signal, fast_policy = self._apply_fast_reversal_exit_policy(
                    position_side=position_side,
                    entry_price=float(
                        getattr(tracked_position, "entry_price", current_price) or current_price
                    ),
                    current_price=current_price,
                    decision=decision,
                    new_signal=None,
                    qabba_signal=qabba_report.get("signal"),
                    qabba_confidence=qabba_report.get("confidence"),
                )
                if fast_policy is not None:
                    decision_data["fast_reversal_policy"] = fast_policy
                if new_signal in {"BUY", "SELL"}:
                    decision = new_signal

            if decision in {"BUY", "SELL"} and self._is_opposite_side(position_side, decision):
                if _confidence_rank(confidence) < _confidence_rank(self._get_flip_min_confidence()):
                    await _hold("flip_confidence_below_min")
                    return

                decision, nanofenix_exit_policy = self._apply_nanofenix_exit_policy(
                    position_side=position_side,
                    decision=decision,
                    new_signal=decision,
                    confidence=confidence,
                    directional_score=directional_score,
                )
                if nanofenix_exit_policy is not None:
                    decision_data["nanofenix_exit_policy"] = nanofenix_exit_policy
                if decision is None:
                    await _hold("nanofenix_reverse_block")
                    return

            await self._manage_open_position(
                new_signal=decision if decision in {"BUY", "SELL"} else "HOLD"
            )
            if decision in {"BUY", "SELL"} and self._is_opposite_side(position_side, decision):
                if self._get_tracked_position() is not None:
                    await _hold("opposite_exit_not_confirmed")
                    return

        effective_decision = decision if decision in {"BUY", "SELL"} else "HOLD"
        decision_data["effective_decision"] = effective_decision

        if effective_decision in {"BUY", "SELL"}:
            cooldown_seconds = max(0, int(getattr(self, "_min_trade_cooldown_seconds", 0) or 0))
            if cooldown_seconds > 0 and self._fast_last_trade_ts is not None:
                elapsed = (datetime.now(timezone.utc) - self._fast_last_trade_ts).total_seconds()
                if elapsed < cooldown_seconds:
                    await _hold(
                        f"trade_cooldown_{elapsed:.0f}s<{cooldown_seconds}s",
                        filter_name="TRADE_COOLDOWN",
                    )
                    return

            risk_edge_usd = _safe_float(dynamic_levels.get("net_profit_potential"))
            estimated_fees_usd = _safe_float(dynamic_levels.get("fees_usd"))
            if risk_edge_usd is not None and estimated_fees_usd is not None:
                min_edge_usd = max(
                    float(getattr(self, "_min_expected_net_edge_usd", 0.0) or 0.0),
                    estimated_fees_usd
                    * float(getattr(self, "_min_expected_net_edge_multiple_of_fees", 0.0) or 0.0),
                )
                if risk_edge_usd < min_edge_usd:
                    await _hold(
                        f"expected_net_edge_too_low_{risk_edge_usd:.4f}<{min_edge_usd:.4f}",
                        filter_name="EXPECTED_NET_EDGE",
                    )
                    return

        if effective_decision in {"BUY", "SELL"}:
            await self._execute_trade(effective_decision, confidence, decision_data)
        else:
            self._consecutive_holds += 1
            logger.info("⏸️ HOLD - Consecutive holds: %s", self._consecutive_holds)

    def _read_nanofenix_companion_signal(self) -> tuple[dict[str, Any] | None, str]:
        if not bool(getattr(self, "_nanofenix_companion_enabled", False)):
            return None, "disabled"

        path = getattr(self, "_nanofenix_signal_path", None)
        if path is None:
            return None, "signal_path_missing"
        if not path.exists():
            return None, "signal_file_missing"

        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return None, "signal_file_empty"
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None, "signal_not_object"
        except Exception:
            return None, "signal_parse_error"

        ts_raw = payload.get("timestamp_utc")
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                payload["_signal_age_sec"] = max(
                    0.0,
                    (datetime.now(timezone.utc) - ts).total_seconds(),
                )
            except Exception:
                payload["_signal_age_sec"] = None
        else:
            payload["_signal_age_sec"] = None

        return payload, "ok"

    def _build_nanofenix_policy_payload(self, decision: str) -> dict[str, Any] | None:
        if not bool(getattr(self, "_nanofenix_companion_enabled", False)):
            return None

        signal, status = _coerce_nanofenix_signal_result(self._read_nanofenix_companion_signal())
        reasons: list[str] = []
        if status != "ok" or signal is None:
            reasons.append(status)
            signal = {}

        signal_age = _safe_float(signal.get("_signal_age_sec"))
        if signal_age is None:
            reasons.append("missing_or_invalid_timestamp")
        elif signal_age > float(getattr(self, "_nanofenix_max_signal_age_sec", 25.0)):
            reasons.append("stale_signal")

        symbol = str(signal.get("symbol") or "").upper()
        if symbol and symbol != self.symbol:
            reasons.append("symbol_mismatch")

        companion_action = self._nanofenix_signal_to_action(
            signal.get("action") or signal.get("signal") or "HOLD"
        )
        companion_ready, direction_accuracy, _ = self._nanofenix_side_metrics(decision, signal)
        if not companion_ready:
            reasons.append("companion_not_ready")

        confidence = _safe_float(signal.get("confidence"))
        if confidence is None:
            reasons.append("missing_confidence")
        elif confidence < float(getattr(self, "_nanofenix_min_conf", 0.60)):
            reasons.append("low_confidence")

        pred_bps = _safe_float(signal.get("pred_bps"))
        if pred_bps is None:
            reasons.append("missing_pred_bps")
        elif abs(pred_bps) < float(getattr(self, "_nanofenix_min_pred_bps", 2.0)):
            reasons.append("low_pred_bps")

        edge_net_bps = _safe_float(signal.get("edge_net_bps"))
        if edge_net_bps is not None and edge_net_bps < float(
            getattr(self, "_nanofenix_min_actionable_edge_bps", 0.8)
        ):
            reasons.append("low_actionable_edge")

        uncertainty_bps = _safe_float(signal.get("uncertainty_bps"))
        if uncertainty_bps is not None and uncertainty_bps > float(
            getattr(self, "_nanofenix_max_uncertainty_bps", 3.0)
        ):
            reasons.append("high_uncertainty")

        calibration_health = _safe_float(signal.get("calibration_health"))
        if calibration_health is not None and calibration_health < float(
            getattr(self, "_nanofenix_min_calibration_health", 0.5)
        ):
            reasons.append("low_calibration_health")

        if direction_accuracy is None:
            reasons.append("missing_direction_accuracy")
        elif direction_accuracy < float(getattr(self, "_nanofenix_min_direction_accuracy", 0.54)):
            reasons.append("low_direction_accuracy")

        if decision in {"BUY", "SELL"}:
            if companion_action not in {"BUY", "SELL"}:
                reasons.append("no_directional_signal")
            elif companion_action != decision:
                reasons.append("direction_mismatch")
            elif not self._nanofenix_confirms_action(decision, signal):
                reasons.append("direction_not_confirmed")

        reasons = sorted(set(reasons))
        return {
            "enabled": True,
            "signal_path": str(getattr(self, "_nanofenix_signal_path", "")),
            "decision": decision,
            "effective_signal": decision,
            "allow_execute": len(reasons) == 0,
            "reason": "ok" if not reasons else ",".join(reasons),
            "reasons": reasons,
            "signal": str(signal.get("signal") or "").upper() or None,
            "action": companion_action,
            "raw_signal": str(signal.get("signal") or "").upper() or None,
            "companion_ready": companion_ready,
            "short_companion_ready": bool(signal.get("short_companion_ready", False)),
            "long_companion_ready": bool(signal.get("long_companion_ready", False)),
            "signal_age_sec": signal_age,
            "confidence": confidence,
            "pred_bps": pred_bps,
            "expected_bps": _safe_float(signal.get("expected_bps")),
            "uncertainty_bps": uncertainty_bps,
            "bias_correction_bps": _safe_float(signal.get("bias_correction_bps")),
            "edge_net_bps": edge_net_bps,
            "actionable_edge_bps": _safe_float(signal.get("actionable_edge_bps")),
            "calibration_health": calibration_health,
            "fast_weight": _safe_float(signal.get("fast_weight")),
            "slow_weight": _safe_float(signal.get("slow_weight")),
            "drift_score": _safe_float(signal.get("drift_score")),
            "allow_add_to_position": bool(signal.get("allow_add_to_position", False)),
            "size_multiplier_hint": _safe_float(signal.get("size_multiplier_hint")),
            "require_reversal_confirmation": bool(
                signal.get("require_reversal_confirmation", False)
            ),
            "source": str(signal.get("source") or "none"),
            "direction_accuracy": direction_accuracy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _nanofenix_policy_hard_vetoes_entry(self, companion_policy: dict[str, Any] | None) -> bool:
        if not isinstance(companion_policy, dict):
            return False
        if bool(companion_policy.get("allow_execute", False)):
            return False

        configured_reasons = set(getattr(self, "_nanofenix_hard_veto_reasons", set()) or set())
        if not configured_reasons:
            return True

        raw_reasons = companion_policy.get("reasons")
        if isinstance(raw_reasons, list):
            reasons = {str(reason).strip() for reason in raw_reasons if str(reason).strip()}
        else:
            reason_text = str(companion_policy.get("reason") or "")
            reasons = {reason.strip() for reason in reason_text.split(",") if reason.strip()}

        return bool(reasons & configured_reasons)

    def _apply_nanofenix_marginal_short_size_cap(
        self,
        *,
        decision: str,
        size_multiplier: float,
        companion_policy: dict[str, Any] | None,
    ) -> float:
        if decision != "SELL" or not isinstance(companion_policy, dict):
            return size_multiplier

        source = str(companion_policy.get("source") or "").strip().lower()
        if source not in {"consensus", "fused"}:
            return size_multiplier

        edge_net_bps = _safe_float(companion_policy.get("edge_net_bps"))
        pred_bps = _safe_float(companion_policy.get("pred_bps"))
        if edge_net_bps is None or pred_bps is None:
            return size_multiplier

        max_edge = float(getattr(self, "_nanofenix_marginal_short_max_edge_bps", 0.5))
        max_pred = float(getattr(self, "_nanofenix_marginal_short_max_pred_bps", 2.0))
        if edge_net_bps > max_edge or abs(pred_bps) >= max_pred:
            return size_multiplier

        size_cap = float(getattr(self, "_nanofenix_marginal_short_size_cap", 0.35))
        if size_cap <= 0:
            return size_multiplier

        capped = min(size_multiplier, size_cap)
        if capped < size_multiplier:
            logger.info(
                "NanoFenix marginal short size cap applied: multiplier %.3f -> %.3f "
                "(source=%s edge=%.3f pred=%.3f)",
                size_multiplier,
                capped,
                source,
                edge_net_bps,
                pred_bps,
            )
        return capped

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

        risk_data = decision_data.get("risk_assessment", {}) or {}
        order_details = risk_data.get("order_details", {}) or {}
        stop_loss = _safe_float(risk_data.get("stop_loss")) or _safe_float(
            order_details.get("stop_loss")
        )
        take_profit = _safe_float(risk_data.get("take_profit")) or _safe_float(
            order_details.get("take_profit")
        )
        entry_price = (
            _safe_float(risk_data.get("entry_price"))
            or _safe_float(order_details.get("entry_price"))
            or _safe_float(getattr(self.market_data, "current_price", None))
            or 0.0
        )
        if entry_price <= 0:
            logger.error("Trade skipped: invalid entry price %s", entry_price)
            return

        if self.paper_trading:
            balance = _env_float("FENIX_BALANCE_FALLBACK_USDT", 100.0)
            if balance <= 0:
                logger.warning(
                    "Invalid FENIX_BALANCE_FALLBACK_USDT %.2f in paper mode; using 100.00",
                    balance,
                )
                balance = 100.0
            logger.info("Paper balance fallback (USDT): %.2f", balance)
        else:
            balance = self.executor.get_balance()
            if balance is None:
                logger.error("Could not get balance, aborting trade")
                return
            balance = float(balance)
            logger.info("Account balance (USDT): %.2f", balance)

        leverage = max(
            1.0,
            _env_float(
                "FENIX_LEVERAGE", _safe_float(getattr(self, "_engine_leverage", 1.0)) or 1.0
            ),
        )
        risk_fraction = _env_float(
            "FENIX_MAX_RISK_PER_TRADE",
            APP_CONFIG.risk_management.base_risk_per_trade if APP_CONFIG else 0.01,
        )
        fallback_notional = balance * max(0.0, risk_fraction) * leverage
        requested_notional = (
            _safe_float(decision_data.get("position_size"))
            or _safe_float(order_details.get("approved_size"))
            or _safe_float(risk_data.get("adjusted_position_size"))
            or _safe_float(risk_data.get("approved_size"))
            or fallback_notional
        )
        size_multiplier = max(0.0, _safe_float(decision_data.get("size_multiplier")) or 1.0)
        companion_policy = (
            decision_data.get("nanofenix_policy") if isinstance(decision_data, dict) else None
        )
        if isinstance(companion_policy, dict):
            policy_hint = _safe_float(companion_policy.get("size_multiplier_hint"))
            if policy_hint is not None and policy_hint > 0:
                size_multiplier = min(size_multiplier, policy_hint)
            size_multiplier = self._apply_nanofenix_marginal_short_size_cap(
                decision=decision,
                size_multiplier=size_multiplier,
                companion_policy=companion_policy,
            )
        base_size = (
            requested_notional * size_multiplier if requested_notional > 0 else fallback_notional
        )
        if base_size <= 0:
            logger.warning("Trade skipped: computed non-positive notional %.4f", base_size)
            return

        if _env_flag("FENIX_CAP_NOTIONAL_TO_AVAILABLE_MARGIN", True):
            max_entry_margin_pct = min(
                max(0.0, _env_float("FENIX_MAX_ENTRY_MARGIN_PCT", 0.90)),
                1.0,
            )
            available_notional_cap = balance * leverage * max_entry_margin_pct
            explicit_available_cap = _safe_float(os.getenv("FENIX_MAX_AVAILABLE_NOTIONAL_USD"))
            if explicit_available_cap is not None and explicit_available_cap > 0:
                available_notional_cap = min(available_notional_cap, explicit_available_cap)
            if available_notional_cap > 0 and base_size > available_notional_cap:
                logger.info(
                    "Capping requested notional to available margin: %.2f -> %.2f "
                    "(balance=%.2f leverage=%.2f max_margin_pct=%.2f)",
                    base_size,
                    available_notional_cap,
                    balance,
                    leverage,
                    max_entry_margin_pct,
                )
                base_size = available_notional_cap

        adjusted_size = base_size
        if self.risk_manager and RISK_MANAGER_AVAILABLE:
            try:
                self.risk_manager.update_balance(balance)
            except Exception as e:
                logger.warning("Could not update risk manager balance: %s", e)

            allowed, risk_status = self.risk_manager.check_trade_allowed(self.symbol, base_size)
            if not allowed:
                logger.critical("🚨 TRADE BLOCKED BY CIRCUIT BREAKER: %s", risk_status.describe())
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "risk:blocked",
                        {
                            "status": risk_status.dict(),
                            "reason": risk_status.describe(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

            adjusted_size = float(self.risk_manager.get_adjusted_size(base_size))
            if adjusted_size != base_size:
                logger.info(
                    "Size adjusted by risk manager: $%.2f → $%.2f", base_size, adjusted_size
                )

        if _env_flag("FENIX_ALLOW_ADD_TO_POSITION", False):
            reserve_pct = min(
                max(0.0, _safe_float(getattr(self, "_add_position_reserve_pct", 0.0)) or 0.0), 0.95
            )
            if reserve_pct > 0:
                adjusted_size *= 1.0 - reserve_pct

        max_notional = _safe_float(os.getenv("FENIX_MAX_NOTIONAL_USD"))
        if max_notional and max_notional > 0:
            adjusted_size = min(adjusted_size, max_notional)

        if self.paper_trading:
            simulated_price = _safe_float(getattr(self.market_data, "current_price", None))
            if simulated_price is None or simulated_price <= 0:
                simulated_price = entry_price
            logger.info("📝 PAPER TRADE: Would %s at %s", decision, simulated_price)
            if (callback := self.on_agent_event) is not None:
                await callback(
                    "trade:simulated",
                    {
                        "side": decision,
                        "price": simulated_price,
                        "confidence": confidence,
                        "notional_usd": adjusted_size,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return

        if not self.allow_live_trading:
            logger.warning(
                "Live trading blocked: allow_live_trading=False. Run with safety flag to operate."
            )
            return

        if hasattr(self.executor, "get_position"):
            try:
                current_position = self.executor.get_position() or {}
            except Exception as e:
                logger.warning("Trade skipped: could not confirm current position state: %s", e)
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "risk:blocked",
                        {
                            "reason": "position_check_failed",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return
        else:
            current_position = {}

        current_position_amt = _safe_float(current_position.get("positionAmt")) or 0.0
        tracked_position = self._get_tracked_position()
        if abs(current_position_amt) <= 1e-9 and tracked_position is not None:
            try:
                await self._reconcile_tracked_position_with_exchange()
            except Exception as e:
                logger.warning(
                    "Trade skipped: stale local position reconciliation failed for %s: %s",
                    self.symbol,
                    e,
                )
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "risk:blocked",
                        {
                            "reason": "stale_local_position_reconciliation_failed",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

            tracked_position = self._get_tracked_position()
            if tracked_position is not None:
                logger.warning(
                    "Trade skipped: local tracked position still exists after exchange-flat reconciliation for %s",
                    self.symbol,
                )
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "risk:blocked",
                        {
                            "reason": "local_position_stale_after_reconciliation",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

        same_side_position = (current_position_amt > 0 and decision == "BUY") or (
            current_position_amt < 0 and decision == "SELL"
        )
        if (
            same_side_position
            and isinstance(companion_policy, dict)
            and not bool(companion_policy.get("allow_add_to_position", False))
        ):
            logger.info(
                "Trade skipped: NanoFenix policy disallows same-side add for %s", self.symbol
            )
            if (callback := self.on_agent_event) is not None:
                await callback(
                    "position:skip_same_side",
                    {
                        "reason": companion_policy.get("reason")
                        or "nanofenix_policy_disallows_same_side_add",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return
        if same_side_position and not _env_flag("FENIX_ALLOW_ADD_TO_POSITION", False):
            logger.info("Trade skipped: same-side position already open for %s", self.symbol)
            if (callback := self.on_agent_event) is not None:
                await callback(
                    "position:skip_same_side",
                    {
                        "reason": "same-side position already open",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return

        tracked_position = tracked_position or self._get_tracked_position()
        if (
            same_side_position
            and _env_flag("FENIX_ALLOW_ADD_TO_POSITION", False)
            and self.symbol == "ETHUSDT"
            and self.timeframe == "3m"
            and decision == "BUY"
            and tracked_position is not None
        ):
            entry_count = int(getattr(tracked_position, "entry_count", 1) or 1)
            if entry_count >= int(getattr(self, "_eth3m_long_max_entries", 2)):
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "position:skip_same_side",
                        {
                            "reason": "ETH 3m long entry cap reached",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

            qabba_signal = str(decision_data.get("_execution_qabba_signal") or "HOLD").upper()
            qabba_confidence = _safe_float(decision_data.get("_execution_qabba_confidence")) or 0.0
            if bool(getattr(self, "_eth3m_require_qabba_for_long_add", True)) and (
                qabba_signal != "BUY"
                or qabba_confidence < float(getattr(self, "_eth3m_long_add_qabba_min_conf", 0.85))
            ):
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "position:skip_same_side",
                        {
                            "reason": "ETH 3m add requires fresh QABBA BUY",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

            market_condition = str(decision_data.get("_execution_market_condition") or "").upper()
            chop_regime = str(decision_data.get("_execution_chop_regime") or "").upper()
            trend_conflict = bool(decision_data.get("_execution_trend_conflict"))
            if (
                bool(getattr(self, "_eth3m_block_long_add_in_low_regime", True))
                and market_condition == "LOW_VOLATILITY"
                and chop_regime == "TRANSITION"
            ):
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "position:skip_same_side",
                        {
                            "reason": "ETH 3m long add blocked in low-vol transition",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

            if (
                bool(getattr(self, "_eth3m_block_long_add_on_trend_conflict", True))
                and trend_conflict
                and market_condition == "LOW_VOLATILITY"
            ):
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "position:skip_same_side",
                        {
                            "reason": "ETH 3m long add blocked by low-vol trend conflict",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

        if (
            same_side_position
            and self.risk_manager
            and hasattr(self.risk_manager, "get_total_exposure")
        ):
            try:
                exposure = self.risk_manager.get_total_exposure()
                max_exposure = _safe_float((exposure or {}).get("max_exposure")) or 0.0
                current_notional = abs(current_position_amt) * entry_price
                if max_exposure > 0 and (current_notional + adjusted_size) > max_exposure:
                    if (callback := self.on_agent_event) is not None:
                        await callback(
                            "risk:blocked",
                            {
                                "reason": "Live exposure cap reached",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    logger.warning(
                        "Trade skipped: exposure cap reached %.2f > %.2f",
                        current_notional + adjusted_size,
                        max_exposure,
                    )
                    return
            except Exception as e:
                logger.debug("Exposure cap check failed: %s", e)

        symbol_config = None
        if getattr(self.executor, "service", None) is not None and hasattr(
            self.executor.service, "get_symbol_config"
        ):
            try:
                symbol_config = self.executor.service.get_symbol_config(self.symbol)
            except Exception as e:
                logger.debug("Could not load symbol config for %s: %s", self.symbol, e)

        raw_quantity = adjusted_size / entry_price
        step_size = _safe_float(getattr(symbol_config, "step_size", None))
        min_notional = _safe_float(getattr(symbol_config, "min_notional", None)) or float(
            self.executor.min_notional
        )
        quantity = _floor_to_step(raw_quantity, step_size)
        notional = quantity * entry_price
        needs_exchange_floor = quantity <= 0 or (min_notional > 0 and notional < min_notional)
        if (
            needs_exchange_floor
            and step_size
            and _env_flag("FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR", False)
        ):
            floor_qty = max(
                step_size,
                _ceil_to_step(min_notional / entry_price, step_size)
                if min_notional > 0
                else step_size,
            )
            floor_notional = floor_qty * entry_price
            max_margin_for_floor = _env_float("FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD", 0.0)
            max_loss_for_floor = _env_float("FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD", 0.0)
            max_fees_for_floor = _env_float("FENIX_MIN_QTY_FLOOR_MAX_FEES_USD", 0.0)
            round_trip_fee_pct = max(0.0, _env_float("FENIX_ESTIMATED_ROUND_TRIP_FEE_PCT", 0.0008))
            estimated_margin = floor_notional / leverage
            estimated_loss = (
                abs((entry_price - stop_loss) if stop_loss is not None else 0.0) * floor_qty
            )
            estimated_fees = floor_notional * round_trip_fee_pct
            estimated_total_risk = estimated_loss + estimated_fees
            can_floor = True
            if max_margin_for_floor > 0 and estimated_margin > max_margin_for_floor:
                can_floor = False
            if max_loss_for_floor > 0 and estimated_total_risk > max_loss_for_floor:
                can_floor = False
            if max_fees_for_floor > 0 and estimated_fees > max_fees_for_floor:
                can_floor = False
            if self.risk_manager and hasattr(self.risk_manager, "get_total_exposure"):
                try:
                    exposure = self.risk_manager.get_total_exposure() or {}
                    total_exposure = _safe_float(exposure.get("total_exposure")) or 0.0
                    max_exposure = _safe_float(exposure.get("max_exposure")) or 0.0
                    if max_exposure > 0 and (total_exposure + floor_notional) > max_exposure:
                        can_floor = False
                except Exception:
                    pass
            if can_floor:
                quantity = floor_qty
                adjusted_size = floor_notional
                notional = floor_notional
            else:
                logger.warning(
                    "Trade skipped: exchange min qty floor would exceed live constraints "
                    "(margin=%.2f fees=%.4f total_risk=%.4f)",
                    estimated_margin,
                    estimated_fees,
                    estimated_total_risk,
                )
                if (callback := self.on_agent_event) is not None:
                    await callback(
                        "risk:blocked",
                        {
                            "reason": "exchange_min_qty_floor_blocked",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return

        if quantity <= 0:
            logger.warning(
                "Trade skipped: quantity rounds to zero (raw=%.8f step=%s)", raw_quantity, step_size
            )
            return

        notional = quantity * entry_price
        if notional < min_notional:
            logger.warning("Trade skipped: Notional %.2f < Min %.2f", notional, min_notional)
            return

        required_margin = notional / leverage
        if required_margin > balance:
            logger.warning(
                "Trade skipped: Insufficient margin %.2f < Required %.2f",
                balance,
                required_margin,
            )
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
            if getattr(self, "trade_manager", None) is not None and hasattr(
                self.trade_manager, "open_position"
            ):
                try:
                    self.trade_manager.open_position(
                        symbol=self.symbol,
                        side="LONG" if decision == "BUY" else "SHORT",
                        entry_price=float(result.entry_price)
                        if result.entry_price
                        else entry_price,
                        quantity=float(result.executed_qty) if result.executed_qty else quantity,
                        signal_timestamp=datetime.now(timezone.utc).isoformat(),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trade_id=str(result.order_id) if result.order_id else None,
                        reasoning_digest=decision_data.get("_reasoning_digest")
                        or decision_data.get("reasoning_prompt_digest"),
                        decision_agent_name="Decision Agent",
                        protection_position_id=getattr(result, "protection_position_id", None),
                        sl_order_id=getattr(result, "sl_order_id", None),
                        tp_order_id=getattr(result, "tp_order_id", None),
                    )
                except Exception as e:
                    logger.debug("Could not open tracked position: %s", e)

            if (callback := self.on_agent_event) is not None:
                await callback(
                    "trade_executed",
                    {
                        "side": decision,
                        "price": result.entry_price,
                        "qty": result.executed_qty,
                        "order_id": str(result.order_id) if result.order_id else None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                await callback(
                    "position:opened",
                    {
                        "side": decision,
                        "price": result.entry_price,
                        "qty": result.executed_qty,
                        "order_id": str(result.order_id) if result.order_id else None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

            # --- UPDATE REASONING BANK ---
            try:
                digest = decision_data.get("_reasoning_digest") or decision_data.get(
                    "reasoning_prompt_digest"
                )
                if digest:
                    # For now, mark success as True and attach order id; reward will be computed asynchronously later
                    self.reasoning_bank.update_entry_outcome(
                        agent_name="Decision Agent",
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
                        size=float(result.executed_qty) * float(result.entry_price)
                        if result.executed_qty and result.entry_price
                        else 0.0,
                    )
                    if hasattr(self.risk_manager, "open_trade"):
                        self.risk_manager.open_trade(trade_record)
                    else:
                        self.risk_manager.record_trade(trade_record)
                    logger.info(
                        f"Trade registered in RiskManager: {self.risk_manager.current_status.describe()}"
                    )
                except Exception as e:
                    logger.warning(f"Could not record trade in RiskManager: {e}")
                if hasattr(self.risk_manager, "update_open_position"):
                    try:
                        self.risk_manager.update_open_position(
                            self.symbol,
                            size=notional,
                            notional=notional,
                            side=decision.lower(),
                        )
                    except Exception:
                        pass
        else:
            logger.error(f"❌ Trade failed: {result.status} - {result.message}")

            # --- UPDATE REASONING BANK FOR FAILED TRADE ---
            try:
                digest = decision_data.get("_reasoning_digest") or decision_data.get(
                    "reasoning_prompt_digest"
                )
                if digest:
                    self.reasoning_bank.update_entry_outcome(
                        agent_name="Decision Agent",
                        prompt_digest=digest,
                        success=False,
                        reward=0.0,
                        trade_id=str(result.order_id) if result.order_id else None,
                    )
            except Exception as e:
                logger.debug(f"Failed to attach failed trade outcome to ReasoningBank: {e}")

            logger.info(
                "Failed execution was not recorded as a RuntimeRiskManager loss "
                "(status=%s, order_id=%s, executed_qty=%s).",
                getattr(result, "status", None),
                getattr(result, "order_id", None),
                getattr(result, "executed_qty", None),
            )

    def get_risk_status(self) -> dict[str, Any] | None:
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
            "last_decision_time": self._last_decision_time.isoformat()
            if self._last_decision_time
            else None,
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
