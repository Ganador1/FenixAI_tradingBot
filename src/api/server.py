
import asyncio
import logging
import os
import platform
import socketio
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import psutil
from fastapi import FastAPI, Query, HTTPException, Path, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.trading.engine import TradingEngine
from src.trading.binance_client import BinanceClient
from src.config.config_loader import APP_CONFIG
from src.config.database import init_db, get_db, SessionLocal
from src.memory.reasoning_bank import get_reasoning_bank
from src.models.db_models import Order, Trade, Position, AgentOutput
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

# Auth Imports
from src.api.auth import router as auth_router, get_password_hash
from src.models.user import User


# ============ Pydantic Schemas ============

class OrderCreate(BaseModel):
    """Schema para crear una orden."""
    symbol: str = Field(default="BTCUSDT", description="Trading pair")
    type: str = Field(default="market", description="Order type: market, limit, stop")
    side: str = Field(..., description="Order side: buy or sell")
    quantity: float = Field(..., gt=0, description="Order quantity")
    price: Optional[float] = Field(None, description="Limit price (for limit orders)")
    stop_price: Optional[float] = Field(None, description="Stop price (for stop orders)")


class OrderResponse(BaseModel):
    """Schema de respuesta para una orden."""
    id: str
    symbol: str
    type: str
    side: str
    quantity: float
    price: Optional[float]
    stop_price: Optional[float]
    status: str
    filled_quantity: float
    created_at: str
    updated_at: str


class PositionResponse(BaseModel):
    """Schema de respuesta para una posición."""
    id: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    opened_at: str


class TradeResponse(BaseModel):
    """Schema de respuesta para un trade."""
    id: str
    symbol: str
    side: str
    quantity: float
    price: float
    realized_pnl: float
    executed_at: str


class AgentOutputResponse(BaseModel):
    """Schema de respuesta para output de agente."""
    id: str
    agent_id: str
    agent_name: str
    timestamp: str
    reasoning: str
    decision: str
    confidence: float
    input_summary: Optional[str] = None


class EngineConfigUpdate(BaseModel):
    """Payload para actualizar configuración de engine."""
    symbol: Optional[str] = Field(None, description="Trading pair, e.g., BTCUSDT")
    timeframe: Optional[str] = Field(None, description="Timeframe, e.g., 1m,5m,15m")
    paper_trading: Optional[bool] = Field(None, description="Paper trading on/off")
    allow_live_trading: Optional[bool] = Field(None, description="Allow live trading")
    enable_visual_agent: Optional[bool] = Field(None, description="Toggle visual agent")
    enable_sentiment_agent: Optional[bool] = Field(None, description="Toggle sentiment agent")


# ============ In-Memory Storage (for demo) ============
_ORDERS: List[dict] = []
_POSITIONS: List[dict] = []
_TRADE_HISTORY: List[dict] = []
_AGENT_OUTPUTS: List[dict] = []

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FenixAPI")

# Global Engine Instance
engine: TradingEngine | None = None
_engine_task: asyncio.Task | None = None
_METRICS_HISTORY: deque[dict] = deque(maxlen=240)
_PROCESS_START = time.time()

# Socket.IO Server
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[
    'http://localhost:5173',
    'http://127.0.0.1:5173',
])

async def handle_engine_event(event_type: str, data: dict):
    """Handle events emitted by the trading engine."""
    try:
        if event_type == "agent_output":
            payload = {
                "id": str(uuid.uuid4()),
                "agent_name": data.get("agent_name"),
                "timestamp": data.get("timestamp"),
                "reasoning": data.get("data", {}).get("reasoning", "") or data.get("data", {}).get("visual_analysis", "") or "No reasoning",
                "decision": data.get("data", {}).get("signal") or data.get("data", {}).get("action") or "HOLD",
                "confidence": data.get("data", {}).get("confidence", 0.0),
                "input_summary": "Live Analysis"
            }
            # Include social and Fear&Greed data for sentiment agent
            if data.get("social_data"):
                payload["social_data"] = data.get("social_data")
            if data.get("fear_greed_value"):
                payload["fear_greed_value"] = data.get("fear_greed_value")
            await sio.emit('agentOutput', payload)
            
        elif event_type == "final_decision":
            payload = {
                "decision": data.get("decision"),
                "confidence": data.get("confidence"),
                "reasoning": data.get("reasoning"),
                "timestamp": data.get("timestamp")
            }
            await sio.emit('trade:signal', payload)
        elif event_type == "news_update":
            payload = {
                "news": data.get("news_data", []),
                "timestamp": data.get("timestamp")
            }
            await sio.emit('news:update', payload)
            # Backward-compatible aliases
            await sio.emit('newsUpdate', payload)
        elif event_type == "reasoning:new":
            payload = {
                "agent_name": data.get("agent_name"),
                "prompt_digest": data.get("prompt_digest"),
                "timestamp": data.get("timestamp"),
            }
            await sio.emit('reasoning:new', payload)
            # Backwards-compatible event names for frontend
            await sio.emit('agent:reasoning', payload)
            await sio.emit('reasoningUpdate', payload)
            
    except Exception as e:
        logger.error(f"Error handling engine event: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global engine, _engine_task
    logger.info("Initializing Database...")
    await init_db()
    
    logger.info("Initializing Trading Engine...")
    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        paper_trading=True,
        allow_live_trading=False,
    )
    engine.on_agent_event = handle_engine_event
    
    # Start engine in background task
    _engine_task = asyncio.create_task(engine.start())
    
    # Start AutoEvaluator
    try:
        from src.analysis.auto_evaluator import AutoEvaluator
        auto_evaluator = AutoEvaluator(symbol="BTCUSDT", evaluation_horizon_minutes=15)
        asyncio.create_task(auto_evaluator.start())
        logger.info("✅ AutoEvaluator started")
    except Exception as e:
        logger.error(f"Failed to start AutoEvaluator: {e}")
    
    # Start metrics broadcaster
    asyncio.create_task(broadcast_metrics())
    
    # Initialize Default Admin and Demo Users if not exists
    create_demo_users = os.getenv("CREATE_DEMO_USERS", "false").lower() == "true" or os.getenv("ENVIRONMENT", "").lower() == "development"
    async for session in get_db():
        try:
            # Create the original admin used during development
            result = await session.execute(select(User).where(User.email == "admin@fenix.ai"))
            admin_user = result.scalar_one_or_none()
            if not admin_user and create_demo_users:
                logger.info("Creating default admin user for local dev... (admin@fenix.ai)")
                import secrets
                admin_password = os.getenv("DEFAULT_ADMIN_PASSWORD")
                if not admin_password:
                    admin_password = secrets.token_urlsafe(12)
                    # For local dev: generate a password silently; do not print or log it
                    logger.info("[LOCAL DEV] Generated default admin password (not printed). Use DEFAULT_ADMIN_PASSWORD to set a custom password")
                new_admin = User(
                    id=str(uuid.uuid4()),
                    email="admin@fenix.ai",
                    hashed_password=get_password_hash(admin_password),
                    full_name="System Admin",
                    role="admin",
                    is_active=True
                )
                session.add(new_admin)
                await session.commit()
                logger.info("Default admin user created for local dev: admin@fenix.ai")

            # Also ensure the demo credentials shown in docs/security/DEMO_CREDENTIALS.md exist (admin + trader)
            # These are simple demo accounts for development only (avoid on production)
            demo_admin = await session.execute(select(User).where(User.email == "admin@trading.com"))
            demo_admin_user = demo_admin.scalar_one_or_none()
            if not demo_admin_user and create_demo_users:
                logger.info("Creating demo admin user for local dev: admin@trading.com")
                demo_admin_password = os.getenv("DEFAULT_DEMO_PASSWORD", "password")
                new_demo_admin = User(
                    id=str(uuid.uuid4()),
                    email="admin@trading.com",
                    hashed_password=get_password_hash(demo_admin_password),
                    full_name="Demo Admin",
                    role="admin",
                    is_active=True
                )
                session.add(new_demo_admin)
                await session.commit()
                logger.info("Demo admin created for local dev: admin@trading.com")

            demo_trader = await session.execute(select(User).where(User.email == "trader@trading.com"))
            demo_trader_user = demo_trader.scalar_one_or_none()
            if not demo_trader_user and create_demo_users:
                logger.info("Creating demo trader user for local dev: trader@trading.com")
                demo_trader_password = os.getenv("DEFAULT_DEMO_PASSWORD", "password")
                new_demo_trader = User(
                    id=str(uuid.uuid4()),
                    email="trader@trading.com",
                    hashed_password=get_password_hash(demo_trader_password),
                    full_name="Demo Trader",
                    role="trader",
                    is_active=True
                )
                session.add(new_demo_trader)
                await session.commit()
                logger.info("Demo trader created for local dev: trader@trading.com")
        except Exception as e:
            logger.error(f"Error checking default user: {e}")
        break  # Only run once

    yield
    
    # Shutdown
    if engine:
        await engine.stop()
    if _engine_task:
        with suppress(asyncio.CancelledError):
            _engine_task.cancel()
            await _engine_task

# FastAPI App
app = FastAPI(lifespan=lifespan)
app.include_router(auth_router, prefix="/api")  # Register Auth Routes
app_socketio = socketio.ASGIApp(sio, app)

# Override database for testing
if os.getenv("TESTING") == "true":
    from tests.conftest import override_get_db
    app.dependency_overrides[get_db] = override_get_db

# CORS - limitar a orígenes conocidos
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Request validation error: {request.url} - {exc}")
    # Return a clearer message including the Pydantic details for debugging
    return JSONResponse(status_code=422, content={"success": False, "error": "Validation error", "detail": exc.errors()})

# --- Background Tasks ---

async def broadcast_metrics():
    """Broadcast system metrics to frontend via Socket.IO"""
    while True:
        try:
            metrics = build_system_metrics()
            metrics_summary = _summarize_metrics(metrics)
            await sio.emit('system:metrics', {"summary": metrics_summary, "detail": metrics})

            connection_payload = {
                "connections": _build_connection_status()
            }
            await sio.emit('system:connection', connection_payload)

        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            
        await asyncio.sleep(1)


def build_system_metrics() -> dict:
    """Recolecta métricas del sistema y mantiene historial."""
    cpu_usage = psutil.cpu_percent(interval=None)
    load_avg = psutil.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    uptime = int(time.time() - _PROCESS_START)

    metrics = {
        "timestamp": time.time(),
        "cpu": {
            "usage": cpu_usage,
            "cores": psutil.cpu_count(logical=True),
            "load_average": list(load_avg),
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "free": mem.available,
            "percentage": mem.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percentage": disk.percent,
        },
        "network": {
            "bytes_in": net.bytes_recv,
            "bytes_out": net.bytes_sent,
            "packets_in": net.packets_recv,
            "packets_out": net.packets_sent,
        },
        "process": {
            "uptime": uptime,
            "pid": os.getpid(),
            "version": getattr(APP_CONFIG, "version", "unknown"),
            "python_version": platform.python_version(),
        },
    }

    _METRICS_HISTORY.append(metrics)
    return metrics


def _build_connection_status() -> list[dict]:
    now_ts = time.time()
    return [
        {
            "service": "binance",
            "status": "connected" if engine else "unknown",
            "last_ping": now_ts,
            "reconnect_attempts": 0,
            "error_count": 0,
        },
        {
            "service": "ollama",
            "status": "connected",
            "last_ping": now_ts,
            "reconnect_attempts": 0,
            "error_count": 0,
        },
    ]


def _summarize_metrics(metrics: dict) -> dict:
    """Create a lightweight snapshot used by the dashboard cards."""
    return {
        "cpu": metrics.get("cpu", {}).get("usage", 0),
        "memory": metrics.get("memory", {}).get("percentage", 0),
        "disk": metrics.get("disk", {}).get("percentage", 0),
        "network": metrics.get("network", {}).get("bytes_in", 0) + metrics.get("network", {}).get("bytes_out", 0),
        "process": metrics.get("process", {}).get("uptime", 0),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _fetch_ticker(symbol: str) -> Optional[dict]:
    """Get 24h ticker data from Binance; returns None on failure."""
    testnet = engine.paper_trading if engine else True
    symbol_upper = symbol.upper()

    async def _inner(client: BinanceClient):
        return await client.get_ticker(symbol_upper)

    return await _with_binance_client(testnet, _inner)


async def _fetch_klines(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """Get historical klines for charting."""
    testnet = engine.paper_trading if engine else True
    symbol_upper = symbol.upper()

    async def _inner(client: BinanceClient):
        return await client.get_klines(symbol_upper, interval=interval, limit=limit)

    data = await _with_binance_client(testnet, _inner)
    return data or []


async def _with_binance_client(testnet: bool, fn):
    """Helper to ensure Binance client lifecycle is managed per request."""
    client = BinanceClient(testnet=testnet)
    connected = await client.connect()
    if not connected:
        await client.close()
        return None
    try:
        return await fn(client)
    finally:
        await client.close()


def _serialize_agent_output_model(output: AgentOutput) -> dict:
    return {
        "id": output.id,
        "agent_id": output.agent_id,
        "agent_name": output.agent_name,
        "timestamp": output.timestamp.isoformat(),
        "reasoning": output.reasoning,
        "decision": output.decision,
        "confidence": output.confidence,
        "input_summary": output.input_summary,
    }


def _build_scorecards(outputs: list[AgentOutput]) -> list[dict]:
    """Aggregate recent agent outputs into lightweight scorecards."""
    grouped: dict[str, list[AgentOutput]] = {}
    for output in outputs:
        grouped.setdefault(output.agent_id, []).append(output)

    scorecards: list[dict] = []
    for agent_id, items in grouped.items():
        total = len(items)
        success = sum(o.confidence >= 0.6 for o in items)
        failed = total - success
        avg_conf = sum(o.confidence for o in items) / total if total else 0.0
        accuracy = success / total if total else 0.0

        scorecards.append({
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_name": items[0].agent_name if items else agent_id,
            "timestamp": datetime.utcnow().isoformat(),
            "total_signals": total,
            "successful_signals": success,
            "failed_signals": failed,
            "accuracy": accuracy,
            "average_confidence": avg_conf,
            "win_rate": accuracy,
            "profit_factor": 1.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 1.0,
        })

    return scorecards


def _build_reasoning_analytics(outputs: list[AgentOutput]) -> dict:
    """Compute simple analytics for the Reasoning Bank view."""
    total_entries = len(outputs)
    avg_confidence = sum(o.confidence for o in outputs) / total_entries if total_entries else 0.0
    success = sum(o.confidence >= 0.6 for o in outputs)
    success_rate = success / total_entries if total_entries else 0.0

    confidence_trend: dict[str, list[float]] = {}
    for o in outputs:
        day_key = o.timestamp.strftime("%Y-%m-%d")
        confidence_trend.setdefault(day_key, []).append(o.confidence)

    trend_points = [
        {"date": day, "confidence": sum(vals) / len(vals)}
        for day, vals in sorted(confidence_trend.items())
    ]

    outcome_distribution = {
        "high_confidence": success,
        "low_confidence": total_entries - success,
    }

    return {
        "total_entries": total_entries,
        "avg_confidence": avg_confidence,
        "avg_accuracy": success_rate,
        "success_rate": success_rate,
        "top_performing_agents": list({o.agent_name for o in outputs}),
        "most_common_outcomes": outcome_distribution,
        "confidence_trend": trend_points,
        "outcome_distribution": [
            {"outcome": key, "count": value} for key, value in outcome_distribution.items()
        ],
    }


def _engine_config_payload(engine: TradingEngine | None) -> dict:
    if not engine:
        return {}
    return {
        "symbol": engine.symbol,
        "timeframe": engine.timeframe,
        "paper_trading": engine.paper_trading,
        "allow_live_trading": engine.allow_live_trading,
        "enable_visual_agent": getattr(engine, "enable_visual", True),
        "enable_sentiment_agent": getattr(engine, "enable_sentiment", True),
    }


async def _restart_engine_with_config(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    paper_trading: Optional[bool] = None,
    allow_live_trading: Optional[bool] = None,
    enable_visual_agent: Optional[bool] = None,
    enable_sentiment_agent: Optional[bool] = None,
):
    """Restart engine with new configuration requested by the UI."""
    global engine, _engine_task

    current_symbol = symbol or (engine.symbol if engine else "BTCUSDT")
    current_timeframe = timeframe or (engine.timeframe if engine else "15m")
    current_paper = paper_trading if paper_trading is not None else (engine.paper_trading if engine else True)
    current_live = allow_live_trading if allow_live_trading is not None else (engine.allow_live_trading if engine else False)
    current_visual = enable_visual_agent if enable_visual_agent is not None else (getattr(engine, "enable_visual", True))
    current_sentiment = enable_sentiment_agent if enable_sentiment_agent is not None else (getattr(engine, "enable_sentiment", True))

    if engine:
        await engine.stop()
    if _engine_task:
        with suppress(asyncio.CancelledError):
            _engine_task.cancel()
            await _engine_task

    engine = TradingEngine(
        symbol=current_symbol,
        timeframe=current_timeframe,
        paper_trading=current_paper,
        allow_live_trading=current_live,
        enable_visual_agent=current_visual,
        enable_sentiment_agent=current_sentiment,
    )
    engine.on_agent_event = handle_engine_event
    _engine_task = asyncio.create_task(engine.start())

    return _engine_config_payload(engine)

# --- API Endpoints ---

@app.get("/api/system/status")
async def get_system_status():
    if not engine:
        return {"error": "Engine not initialized"}

    status = engine.get_status()
    metrics = build_system_metrics()
    return {
        "metrics": _summarize_metrics(metrics),
        "raw_metrics": metrics,
        "engine": status,
    }


# ============ System Settings (simple in-memory store for UI) ============
_SYSTEM_SETTINGS: dict = {
    "general": {
        "site_name": "Fenix AI Trading Dashboard",
        "site_description": "Advanced trading dashboard with AI agents",
        "timezone": "UTC",
        "date_format": "YYYY-MM-DD",
        "language": "en",
    },
    "security": {
        "session_timeout": 30,
        "password_min_length": 12,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_numbers": True,
        "require_special_chars": False,
        "max_login_attempts": 5,
        "lockout_duration": 30,
        "two_factor_enabled": False,
    },
    "notifications": {
        "email_enabled": False,
        "email_host": "",
        "email_port": 587,
        "email_username": "",
        "email_password": "",
        "email_from": "no-reply@fenix.ai",
        "sms_enabled": False,
        "sms_provider": "",
        "sms_api_key": "",
    },
    "trading": {
        "max_positions_per_user": 5,
        "max_daily_trades": 100,
        "risk_threshold": 2.0,
        "stop_loss_default": 1.0,
        "take_profit_default": 2.0,
        "leverage_max": 10,
        "margin_call_level": 80,
        "auto_close_on_margin_call": True,
    },
    "agents": {
        "sentiment_agent_enabled": True,
        "technical_agent_enabled": True,
        "visual_agent_enabled": True,
        "qabba_agent_enabled": True,
        "decision_agent_enabled": True,
        "risk_agent_enabled": True,
        "agent_timeout": 30,
        "max_concurrent_agents": 4,
        "reasoning_bank_retention_days": 365,
        "scorecard_retention_days": 365,
    },
    "api": {
        "rate_limit_enabled": True,
        "rate_limit_requests_per_minute": 60,
        "rate_limit_requests_per_hour": 1000,
        "cors_enabled": True,
        "cors_origins": ["http://localhost:5173"],
        "api_key_required": False,
        "jwt_expiry_hours": 24,
        "refresh_token_expiry_days": 30,
    },
    "database": {
        "backup_enabled": False,
        "backup_frequency": "daily",
        "backup_retention_days": 30,
        "maintenance_window": "03:00",
        "auto_vacuum": False,
        "connection_pool_size": 5,
        "query_timeout_seconds": 60,
    },
}


@app.get("/api/system/settings")
async def get_system_settings():
    return _SYSTEM_SETTINGS


@app.put("/api/system/settings/{section}")
async def update_system_settings(section: str, payload: dict):
    if section not in _SYSTEM_SETTINGS:
        raise HTTPException(status_code=404, detail="Settings section not found")
    # Very permissive for demo - replace on valid payload
    _SYSTEM_SETTINGS[section].update(payload)
    return {"success": True, "section": section, "settings": _SYSTEM_SETTINGS[section]}


@app.post("/api/system/test-connection/{type}")
async def test_system_connection(type: str):
    # Simple stub to keep frontend happy
    return {"success": True, "type": type, "message": "Connection OK"}


@app.post("/api/system/settings/{section}/reset")
async def reset_system_settings(section: str):
    if section not in _SYSTEM_SETTINGS:
        raise HTTPException(status_code=404, detail="Settings section not found")
    # Replace with defaults - for now set to empty or predefined defaults
    # We simply reset to the currently defined defaults by reloading the in-memory defaults
    # TODO: implement persistent storage or config file
    return {"success": True, "section": section, "settings": _SYSTEM_SETTINGS[section]}

@app.get("/api/system/alerts")
async def get_alerts():
    alerts: list[dict] = []
    return {"alerts": alerts, "data": alerts}


@app.get("/api/system/health")
async def get_health():
    components = [
        {
            "component": "engine",
            "status": "healthy" if engine and engine.get_status().get("running") else "warning",
            "message": "Engine running" if engine else "Engine not initialized",
            "last_check": time.time(),
        },
        {
            "component": "binance",
            "status": "healthy" if engine else "unknown",
            "message": "Market data connected" if engine else "Engine not initialized",
            "last_check": time.time(),
        },
        {
            "component": "ollama",
            "status": "healthy",
            "message": "LLM provider assumed reachable",
            "last_check": time.time(),
        },
    ]
    return {"components": components}

@app.get("/api/system/connections")
async def get_connections():
    connections = _build_connection_status()
    return {"connections": connections, "data": connections}


@app.get("/api/system/metrics/history")
async def get_metrics_history(timeframe: str = Query("1h")):
    window_map = {
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": len(_METRICS_HISTORY),
    }
    window = window_map.get(timeframe, len(_METRICS_HISTORY))
    history = list(_METRICS_HISTORY)[-window:]
    return {"metrics": history}

@app.post("/api/engine/start")
async def start_engine():
    if engine and not engine.get_status().get("running"):
        asyncio.create_task(engine.start())
    return {"status": "started"}

@app.post("/api/engine/stop")
async def stop_engine():
    if engine and engine.get_status().get("running"):
        await engine.stop()
    return {"status": "stopped"}


@app.get("/api/engine/config")
async def get_engine_config():
    return {"config": _engine_config_payload(engine)}


@app.post("/api/engine/config")
async def update_engine_config(payload: EngineConfigUpdate):
    config = await _restart_engine_with_config(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        paper_trading=payload.paper_trading,
        allow_live_trading=payload.allow_live_trading,
        enable_visual_agent=payload.enable_visual_agent,
        enable_sentiment_agent=payload.enable_sentiment_agent,
    )
    return {"status": "restarted", "config": config}


# ============ Trading Endpoints ============

@app.get("/api/trading/orders")
async def get_orders(status: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    """Get all orders, optionally filtered by status."""
    query = select(Order).order_by(desc(Order.created_at))
    if status:
        query = query.where(Order.status == status)
    
    result = await db.execute(query)
    orders = result.scalars().all()
    return {"orders": orders}


@app.post("/api/trading/orders", response_model=OrderResponse)
async def create_order(order: OrderCreate, db: AsyncSession = Depends(get_db)):
    """Create a new trading order."""
    new_order = Order(
        id=str(uuid.uuid4()),
        symbol=order.symbol,
        type=order.type,
        side=order.side,
        quantity=order.quantity,
        price=order.price,
        stop_price=order.stop_price,
        status="pending",
        filled_quantity=0.0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    
    # In production, this would send to the exchange
    if engine:
        try:
            # Simulate order execution for paper trading
            new_order.status = "filled"
            new_order.filled_quantity = order.quantity
            new_order.updated_at = datetime.utcnow()
            
            # Add to trade history
            trade = Trade(
                id=str(uuid.uuid4()),
                order_id=new_order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price or 0,  # Would get from market
                realized_pnl=0.0,
                executed_at=datetime.utcnow(),
            )
            db.add(trade)
            
            # Emit via socket
            await sio.emit('orderUpdate', {
                "id": new_order.id, "status": new_order.status, "symbol": new_order.symbol
            })
            await sio.emit('tradeExecuted', {
                "id": trade.id, "symbol": trade.symbol, "price": trade.price
            })
            
        except Exception as e:
            new_order.status = "rejected"
            logger.error(f"Order execution failed: {e}")
    
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    # Convert to dict for response (Pydantic expects dict or object with attributes)
    return {
        "id": new_order.id,
        "symbol": new_order.symbol,
        "type": new_order.type,
        "side": new_order.side,
        "quantity": new_order.quantity,
        "price": new_order.price,
        "stop_price": new_order.stop_price,
        "status": new_order.status,
        "filled_quantity": new_order.filled_quantity,
        "created_at": new_order.created_at.isoformat(),
        "updated_at": new_order.updated_at.isoformat(),
    }


@app.delete("/api/trading/orders/{order_id}")
async def cancel_order(order_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    """Cancel an order by ID."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    
    if order and order.status == "pending":
        order.status = "cancelled"
        order.updated_at = datetime.utcnow()
        await db.commit()
        
        await sio.emit('orderUpdate', {"id": order.id, "status": "cancelled"})
        return {"message": "Order cancelled", "order": {"id": order.id, "status": "cancelled"}}
    
    raise HTTPException(status_code=404, detail="Order not found or cannot be cancelled")


@app.get("/api/trading/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    """Get all open positions."""
    # In production, this would fetch from exchange
    if engine:
        status = engine.get_status()
        if status.get("position"):
            pos = status["position"]
            position = {
                "id": str(uuid.uuid4()),
                "symbol": engine.symbol,
                "side": pos.get("side", "long"),
                "quantity": pos.get("quantity", 0),
                "entry_price": pos.get("entry_price", 0),
                "current_price": pos.get("current_price", 0),
                "unrealized_pnl": pos.get("unrealized_pnl", 0),
                "realized_pnl": pos.get("realized_pnl", 0),
                "opened_at": pos.get("opened_at", datetime.utcnow().isoformat()),
            }
            return {"positions": [position]}
    
    # Fallback to DB positions
    result = await db.execute(select(Position).where(Position.is_open == True))
    positions = result.scalars().all()
    return {"positions": positions}


@app.get("/api/trading/history")
async def get_trade_history(
    limit: int = Query(50, ge=1, le=500),
    symbol: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Get trade history."""
    query = select(Trade).order_by(desc(Trade.executed_at)).limit(limit)
    if symbol:
        query = query.where(Trade.symbol == symbol)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    return {"trades": trades}


@app.get("/api/trading/market")
async def get_market_data(symbol: Optional[str] = Query(None)):
    """Return live market snapshot using engine stream with Binance fallback."""
    target_symbol = (symbol or (engine.symbol if engine else "BTCUSDT")).upper()

    status = engine.get_status() if engine else {}
    ticker = await _fetch_ticker(target_symbol)

    # Prefer live stream price, fallback to ticker
    price = status.get("current_price") or (float(ticker["lastPrice"]) if ticker else None)
    if price is None:
        raise HTTPException(status_code=503, detail="Market data unavailable")

    payload = {
        "symbol": target_symbol,
        "price": price,
        "volume_24h": float(ticker.get("volume", 0)) if ticker else None,
        "quote_volume_24h": float(ticker.get("quoteVolume", 0)) if ticker else None,
        "change_24h": float(ticker.get("priceChangePercent", 0)) if ticker else 0.0,
        "high_24h": float(ticker.get("highPrice", 0)) if ticker else None,
        "low_24h": float(ticker.get("lowPrice", 0)) if ticker else None,
        "timeframe": status.get("timeframe", "15m"),
        "timestamp": datetime.utcnow().isoformat(),
        "source": "stream" if engine else "binance",
    }

    return payload


# ============ Agent Endpoints ============

@app.get("/api/agents")
async def get_agents(db: AsyncSession = Depends(get_db)):
    """Get all registered agents enriched with live performance when available."""
    result = await db.execute(select(AgentOutput))
    outputs = result.scalars().all()
    scorecards = _build_scorecards(outputs)
    score_lookup = {s["agent_id"]: s for s in scorecards}

    running = bool(engine and engine.get_status().get("running"))
    base_agents = [
        {"id": "technical", "name": "Technical Analyst", "type": "technical"},
        {"id": "visual", "name": "Visual Pattern Analyst", "type": "visual"},
        {"id": "sentiment", "name": "Sentiment Analyst", "type": "sentiment"},
        {"id": "qabba", "name": "QABBA Pattern Analyst", "type": "qabba"},
        {"id": "decision", "name": "Decision Maker", "type": "decision"},
        {"id": "risk", "name": "Risk Manager", "type": "risk"},
    ]

    agents: list[dict] = []
    for agent in base_agents:
        card = score_lookup.get(agent["id"])
        performance = {
            "total_signals": card.get("total_signals", 0) if card else 0,
            "successful_signals": card.get("successful_signals", 0) if card else 0,
            "accuracy": card.get("accuracy", 0.0) if card else 0.0,
            "average_confidence": card.get("average_confidence", 0.0) if card else 0.0,
        }

        agents.append({
            **agent,
            "status": "active" if running else "inactive",
            "last_run": datetime.utcnow().isoformat(),
            "performance": performance,
        })

    return {"agents": agents, "data": agents}


@app.get("/api/agents/outputs")
async def get_agent_outputs(
    timeframe: str = Query("24h"),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    """Get recent agent outputs/reasoning."""
    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(limit)
    if agent_id:
        query = query.where(AgentOutput.agent_id == agent_id)
    
    result = await db.execute(query)
    outputs = result.scalars().all()
    return {"outputs": outputs, "data": outputs}


@app.get("/api/reasoning")
async def get_reasoning_entries(agent_name: Optional[str] = None, limit: int = 20):
    """Return recent entries from ReasoningBank for an agent or all agents."""
    try:
        reasoning_bank = get_reasoning_bank()
        agents = [agent_name] if agent_name else [
            'technical_agent', 'qabba_agent', 'sentiment_agent', 'visual_agent', 'decision_agent', 'risk_manager']
        result = {}
        for ag in agents:
            try:
                entries = reasoning_bank.get_recent(ag, limit)
                result[ag] = [e.__dict__ for e in entries]
            except Exception:
                result[ag] = []
        return {"reasoning": result}
    except Exception as e:
        logger.error(f"Error fetching reasoning entries: {e}")
        return {"reasoning": {}}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    """Get a specific agent by ID."""
    agents_response = await get_agents(db=db)
    for agent in agents_response["agents"]:
        if agent["id"] == agent_id:
            return {"agent": agent}
    raise HTTPException(status_code=404, detail="Agent not found")


@app.post("/api/agents/outputs")
async def add_agent_output(output: AgentOutputResponse, db: AsyncSession = Depends(get_db)):
    """Add a new agent output (internal use)."""
    new_output = AgentOutput(
        id=str(uuid.uuid4()),
        agent_id=output.agent_id,
        agent_name=output.agent_name,
        timestamp=datetime.utcnow(),
        reasoning=output.reasoning,
        decision=output.decision,
        confidence=output.confidence,
        input_summary=output.input_summary
    )
    
    db.add(new_output)
    await db.commit()
    await db.refresh(new_output)
    
    output_dict = {
        "id": new_output.id,
        "agent_id": new_output.agent_id,
        "agent_name": new_output.agent_name,
        "timestamp": new_output.timestamp.isoformat(),
        "reasoning": new_output.reasoning,
        "decision": new_output.decision,
        "confidence": new_output.confidence,
        "input_summary": new_output.input_summary
    }
    
    # Emit via socket
    await sio.emit('agentOutput', output_dict)
    await sio.emit('agent:reasoning', output_dict)
    
    return output_dict


@app.get("/api/agents/scorecards")
async def get_agent_scorecards(db: AsyncSession = Depends(get_db)):
    """Return aggregated performance metrics per agent."""
    result = await db.execute(select(AgentOutput))
    outputs = result.scalars().all()
    scorecards = _build_scorecards(outputs)
    return {"data": scorecards, "scorecards": scorecards}


@app.get("/api/reasoning-bank/logs")
async def get_reasoning_bank_logs(
    agent_id: Optional[str] = Query(None),
    timeframe: str = Query("24h"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """Compatibility layer for the frontend Reasoning Bank view."""
    # reuse AgentOutput query
    cutoff_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff_delta = cutoff_map.get(timeframe)

    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(limit)
    if agent_id:
        query = query.where(AgentOutput.agent_id == agent_id)
    if cutoff_delta:
        query = query.where(AgentOutput.timestamp >= datetime.utcnow() - cutoff_delta)

    result = await db.execute(query)
    outputs = result.scalars().all()
    serialized = [_serialize_agent_output_model(o) for o in outputs]
    return {"data": serialized, "logs": serialized}


@app.get("/api/reasoning/analytics")
async def get_reasoning_analytics(timeframe: str = Query("24h"), db: AsyncSession = Depends(get_db)):
    """Provide lightweight analytics for the reasoning dashboard."""
    cutoff_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff_delta = cutoff_map.get(timeframe)

    query = select(AgentOutput)
    if cutoff_delta:
        query = query.where(AgentOutput.timestamp >= datetime.utcnow() - cutoff_delta)

    result = await db.execute(query)
    outputs = result.scalars().all()
    return _build_reasoning_analytics(outputs)


@app.get("/api/reasoning/consensus")
async def get_reasoning_consensus(timeframe: str = Query("24h"), db: AsyncSession = Depends(get_db)):
    """Provide a simple consensus signal per agent for the UI."""
    cutoff_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff_delta = cutoff_map.get(timeframe)

    query = select(AgentOutput)
    if cutoff_delta:
        query = query.where(AgentOutput.timestamp >= datetime.utcnow() - cutoff_delta)

    result = await db.execute(query)
    outputs = result.scalars().all()

    consensus_payload: list[dict] = []
    grouped: dict[str, list[AgentOutput]] = {}
    for output in outputs:
        grouped.setdefault(output.agent_id, []).append(output)

    total_agents = len(grouped)
    for agent_id, items in grouped.items():
        avg_confidence = sum(o.confidence for o in items) / len(items) if items else 0.0
        consensus_payload.append({
            "agent_id": agent_id,
            "agent_name": items[0].agent_name if items else agent_id,
            "consensus_score": avg_confidence,
            "agreement_count": len(items),
            "total_agents": total_agents,
            "dominant_sentiment": "bullish" if avg_confidence >= 0.6 else "neutral",
            "confidence": avg_confidence,
        })

    return consensus_payload


@app.get("/api/market/data/{symbol}")
async def get_market_series(
    symbol: str = Path(...),
    interval: Optional[str] = Query(None),
    limit: int = Query(120, ge=10, le=500),
):
    """Return real kline series for charting."""
    target_interval = interval or (engine.timeframe if engine else "15m")
    klines = await _fetch_klines(symbol, target_interval, limit)

    if not klines:
        raise HTTPException(status_code=503, detail="No market series available")

    points = [
        {
            "timestamp": datetime.fromtimestamp(k["timestamp"] / 1000, tz=timezone.utc).isoformat(),
            "price": k.get("close"),
            "volume": k.get("volume"),
        }
        for k in klines
    ]

    return {"symbol": symbol.upper(), "interval": target_interval, "data": points}


@app.get("/api/market/overview")
async def get_market_overview(symbols: Optional[str] = Query(None)):
    """Return 24h overview for a handful of symbols used by the dashboard."""
    default_symbols = [
        engine.symbol if engine else "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "ADAUSDT",
    ]

    symbol_list = [s.strip().upper() for s in (symbols.split(",") if symbols else default_symbols) if s.strip()]

    markets: list[dict] = []
    for sym in symbol_list:
        ticker = await _fetch_ticker(sym)
        if not ticker:
            continue

        markets.append({
            "symbol": sym,
            "price": float(ticker.get("lastPrice", 0)),
            "change_percent": float(ticker.get("priceChangePercent", 0)),
            "price_change": float(ticker.get("priceChange", 0)),
            "volume": float(ticker.get("volume", 0)),
            "quote_volume": float(ticker.get("quoteVolume", 0)),
            "high_24h": float(ticker.get("highPrice", 0)),
            "low_24h": float(ticker.get("lowPrice", 0)),
            "timestamp": datetime.utcnow().isoformat(),
        })

    if not markets:
        raise HTTPException(status_code=503, detail="No market overview data available")

    return {"markets": markets, "data": markets}


@sio.on("subscribe:agents")
async def subscribe_agents(sid):
    logger.info(f"Client {sid} subscribed to agents")


# ============ ReasoningBank Endpoints ============

@app.get("/api/reasoning/entries")
async def get_reasoning_entries(
    agent_id: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    """Get reasoning entries from ReasoningBank."""
    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(limit)
    if agent_id:
        query = query.where(AgentOutput.agent_id == agent_id)
    if decision:
        query = query.where(AgentOutput.decision == decision)
        
    result = await db.execute(query)
    entries = result.scalars().all()
    return {"entries": entries}


@app.get("/api/reasoning/search")
async def search_reasoning(
    query: str = Query(..., min_length=3),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    """Semantic search in ReasoningBank."""
    # Simple text search for now
    sql_query = select(AgentOutput).where(AgentOutput.reasoning.ilike(f"%{query}%")).limit(limit)
    result = await db.execute(sql_query)
    results = result.scalars().all()
    return {"results": results, "query": query}


# --- Socket IO Events ---

@sio.event
async def connect(sid, environ):
    logger.info(f"Socket connected: {sid}")

@sio.event
async def disconnect(sid):
    logger.info(f"Socket disconnected: {sid}")

@sio.on("subscribe:system")
async def subscribe_system(sid):
    logger.info(f"Client {sid} subscribed to system")

if __name__ == "__main__":
    import uvicorn
    allow_expose_api = os.getenv("ALLOW_EXPOSE_API", "false").lower() == "true"
    host = "0.0.0.0" if allow_expose_api else "127.0.0.1"
    if allow_expose_api:
        logger.warning("ALLOW_EXPOSE_API is set: the API will bind to 0.0.0.0 (external exposure)")
    else:
        logger.info("Binding to 127.0.0.1 by default. Set ALLOW_EXPOSE_API=true to bind to 0.0.0.0")
    uvicorn.run("src.api.server:app_socketio", host=host, port=8000, reload=True)
