import asyncio
import hmac
import logging
import math
import os
import platform
import smtplib
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import psutil
import socketio
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import (
    SECRET_KEY,
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    get_password_hash,
)

# Auth Imports
from src.api.auth import require_control_access
from src.api.auth import router as auth_router
from src.api.nano_routes import (
    RELEASE_INFO as _NANO_RELEASE_INFO,
)
from src.api.nano_routes import (
    _load_signal as _nano_load_signal,
)
from src.api.nano_routes import (
    _signal_path_for as _nano_signal_path_for,
)
from src.api.nano_routes import (
    router as nano_router,
)
from src.api.system_settings import (
    SettingsValidationError,
    load_system_settings,
    public_system_settings,
    reset_system_settings as reset_persisted_system_settings,
    update_system_settings as update_persisted_system_settings,
)
from src.security.smtp_client import SMTPDestinationError, test_smtp_connection
from src.config.config_loader import APP_CONFIG
from src.config.database import SessionLocal, get_db, init_db
from src.memory.reasoning_bank import get_reasoning_bank
from src.models.db_models import AgentOutput, Order, Position, Trade
from src.models.user import User
from src.trading.binance_client import BinanceClient
from src.trading.engine import TradingEngine
from src.trading.engine import env_flag as _engine_env_flag
from src.trading.operational_audit import read_runtime_instances

# ============ Pydantic Schemas ============


class _StrictApiInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class OrderCreate(_StrictApiInput):
    """Schema para crear una orden."""

    symbol: str = Field(
        default="BTCUSDT",
        min_length=5,
        max_length=20,
        pattern=r"^[A-Z0-9]+$",
        description="Trading pair",
    )
    type: Literal["market", "limit", "stop"] = "market"
    side: Literal["buy", "sell"]
    quantity: float = Field(..., gt=0, le=1_000_000_000, description="Order quantity")
    price: float | None = Field(None, gt=0, le=1_000_000_000_000)
    stop_price: float | None = Field(None, gt=0, le=1_000_000_000_000)

    @model_validator(mode="after")
    def validate_prices_for_type(self):
        if self.type == "limit" and self.price is None:
            raise ValueError("price is required for limit orders")
        if self.type == "stop" and self.stop_price is None:
            raise ValueError("stop_price is required for stop orders")
        return self


class OrderResponse(BaseModel):
    """Schema de respuesta para una orden."""

    id: str
    symbol: str
    type: str
    side: str
    quantity: float
    price: float | None
    stop_price: float | None
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


class AgentOutputResponse(_StrictApiInput):
    """Schema de respuesta para output de agente."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    agent_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    agent_name: str = Field(min_length=1, max_length=120)
    timestamp: str = Field(min_length=1, max_length=64)
    reasoning: str = Field(min_length=1, max_length=100_000)
    decision: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    confidence: float = Field(ge=0, le=1)
    input_summary: str | None = Field(None, max_length=100_000)


class EngineConfigUpdate(_StrictApiInput):
    """Payload para actualizar configuración de engine."""

    symbol: str | None = Field(
        None,
        min_length=5,
        max_length=20,
        pattern=r"^[A-Z0-9]+$",
        description="Trading pair, e.g., BTCUSDT",
    )
    timeframe: str | None = Field(
        None,
        pattern=r"^(?:[1-9][0-9]*)(?:m|h|d|w|M)$",
        max_length=8,
        description="Timeframe, e.g., 1m,5m,15m",
    )
    paper_trading: bool | None = Field(None, description="Paper trading on/off")
    allow_live_trading: bool | None = Field(None, description="Allow live trading")
    enable_visual_agent: bool | None = Field(None, description="Toggle visual agent")
    enable_sentiment_agent: bool | None = Field(None, description="Toggle sentiment agent")


class RiskFlagsUpdate(_StrictApiInput):
    """Payload para actualizar flags de riesgo del engine."""

    macro_riskoff_enabled: bool = Field(
        ..., description="Enable/disable the macro risk-off BUY-blocking filter"
    )


# ============ In-Memory Storage (for demo) ============
_ORDERS: list[dict] = []
_POSITIONS: list[dict] = []
_TRADE_HISTORY: list[dict] = []
_AGENT_OUTPUTS: list[dict] = []

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FenixAPI")

# Global Engine Instance
engine: TradingEngine | None = None
_engine_task: asyncio.Task | None = None
_engine_start_lock = asyncio.Lock()
_api_observer_mode = False
# Keep references to fire-and-forget background tasks so they are not GC'd
# and their exceptions are surfaced in logs.
_background_tasks: set[asyncio.Task] = set()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _env_csv(name: str, default: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def _allowed_cors_origins() -> list[str]:
    origins: list[str] = []
    for origin in _env_csv(
        "FENIX_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ):
        if len(origin) > 2048 or len(origins) >= 32:
            raise RuntimeError("FENIX_CORS_ORIGINS contains too many or oversized origins")
        try:
            parsed = urlsplit(origin)
            parsed_port = parsed.port
        except ValueError as exc:
            raise RuntimeError(f"Invalid origin in FENIX_CORS_ORIGINS: {origin!r}") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError(f"Invalid origin in FENIX_CORS_ORIGINS: {origin!r}")
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise RuntimeError(f"CORS origins must be bare http(s) origins: {origin!r}")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        normalized = f"{parsed.scheme}://{host}"
        if parsed_port is not None:
            normalized = f"{normalized}:{parsed_port}"
        if normalized not in origins:
            origins.append(normalized)
    if not origins:
        raise RuntimeError("FENIX_CORS_ORIGINS must contain at least one explicit origin")
    return origins


_CORS_ORIGINS = _allowed_cors_origins()


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _api_live_capability_enabled() -> bool:
    """Return the API-specific live mutation capability; generic live flags never grant it."""
    return os.getenv("FENIX_API_ALLOW_LIVE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_MAX_REQUEST_BYTES = _bounded_int_env(
    "FENIX_MAX_REQUEST_BYTES",
    1_048_576,
    16_384,
    10_485_760,
)


async def require_metrics_access(request: Request) -> None:
    """Protect operational metrics with a dedicated bearer token or loopback."""
    client_host = request.client.host if request.client else ""
    configured_token = os.getenv("FENIX_METRICS_TOKEN", "")
    if configured_token:
        if len(configured_token.encode("utf-8")) < 32:
            raise HTTPException(
                status_code=503,
                detail="FENIX_METRICS_TOKEN must contain at least 32 bytes",
            )
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {configured_token}"
        if hmac.compare_digest(supplied.encode(), expected.encode()):
            return
        raise HTTPException(status_code=401, detail="Metrics authentication required")
    if client_host in _LOOPBACK_HOSTS:
        return
    raise HTTPException(
        status_code=503,
        detail="External metrics access requires FENIX_METRICS_TOKEN",
    )


def _track_task(task: asyncio.Task) -> asyncio.Task:
    """Retain a reference to a background task and log unhandled exceptions."""
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("Background task failed: %r", t.exception())

    task.add_done_callback(_done)
    return task
_METRICS_HISTORY: deque[dict] = deque(maxlen=240)
_PROCESS_START = time.time()


def _redact_url_password(url: str) -> str:
    """Return a URL safe for logs by removing embedded credentials."""
    try:
        parsed = urlsplit(url)
        if not parsed.password:
            return url
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        username = parsed.username or ""
        netloc = f"{username}:***@{host}" if username else f"***@{host}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "<redacted-url>"


def _escape_sql_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user search text is treated literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_BINANCE_INTERVALS = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
}


def _market_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if (
        not 5 <= len(normalized) <= 20
        or not normalized.isascii()
        or not normalized.isalnum()
    ):
        raise HTTPException(status_code=422, detail="Invalid market symbol")
    return normalized


def _market_interval(value: str) -> str:
    if value not in _BINANCE_INTERVALS:
        raise HTTPException(status_code=422, detail="Unsupported market interval")
    return value


# Socket.IO Server — with optional Redis message queue for multi-process communication
# When REDIS_URL is set, the API server can receive events from external processes
# (e.g. the live trading engine running separately via `run_fenix.py --mode live`).
# The live process uses socketio.AsyncRedisManager(write_only=True) to emit events
# that are broadcast to all connected frontend clients through this server.
_redis_url = os.getenv("REDIS_URL", os.getenv("FENIX_REDIS_URL", ""))
if _redis_url:
    try:
        _redis_channel = os.getenv("FENIX_REDIS_CHANNEL", "fenix_socketio")
        _redis_mgr = socketio.AsyncRedisManager(_redis_url, channel=_redis_channel)
        sio = socketio.AsyncServer(
            async_mode="asgi",
            client_manager=_redis_mgr,
            cors_allowed_origins=_CORS_ORIGINS,
            max_http_buffer_size=_MAX_REQUEST_BYTES,
        )
        logger.info(
            "Socket.IO using Redis message queue: %s (channel=%s)",
            _redact_url_password(_redis_url),
            _redis_channel,
        )
    except Exception:
        logger.warning("Failed to initialize Redis manager; using in-memory transport")
        sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=_CORS_ORIGINS,
            max_http_buffer_size=_MAX_REQUEST_BYTES,
        )
else:
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=_CORS_ORIGINS,
        max_http_buffer_size=_MAX_REQUEST_BYTES,
    )


# Shared engine-event handler (same transformation + DB persistence used by
# the live CLI process through the Redis bridge). See src/api/engine_events.py.
from src.api.engine_events import create_engine_event_handler

handle_engine_event = create_engine_event_handler(sio.emit, persist=True)


async def _bootstrap_explicit_demo_users() -> None:
    """Create development users only after an explicit, strong-password opt-in."""
    if os.getenv("CREATE_DEMO_USERS", "false").lower() != "true":
        return

    admin_password = os.getenv("DEFAULT_ADMIN_PASSWORD", "")
    demo_password = os.getenv("DEFAULT_DEMO_PASSWORD", "")
    if len(admin_password) < 16 or len(demo_password) < 16:
        raise RuntimeError(
            "CREATE_DEMO_USERS requires DEFAULT_ADMIN_PASSWORD and "
            "DEFAULT_DEMO_PASSWORD with at least 16 characters"
        )

    requested_users = (
        ("admin@fenix.ai", admin_password, "System Admin", "admin"),
        ("admin@trading.com", demo_password, "Demo Admin", "admin"),
        ("trader@trading.com", demo_password, "Demo Trader", "trader"),
    )
    async with SessionLocal() as session:
        try:
            for email, password, full_name, role in requested_users:
                result = await session.execute(select(User).where(User.email == email))
                if result.scalar_one_or_none() is None:
                    session.add(
                        User(
                            id=str(uuid.uuid4()),
                            email=email,
                            hashed_password=get_password_hash(password),
                            full_name=full_name,
                            role=role,
                            is_active=True,
                        )
                    )
                    logger.info("Created explicitly requested development user: %s", email)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global engine, _engine_task, _api_observer_mode
    logger.info("Initializing Database...")
    await init_db()
    try:
        await _bootstrap_explicit_demo_users()
    except Exception as exc:
        logger.error("User bootstrap failed (%s)", exc.__class__.__name__)
        raise RuntimeError("API user bootstrap failed") from exc

    # ── Engine config from env (so the API server matches the live process) ──
    # When running `run_fenix.py --mode live` separately, that process uses
    # its own TradingEngine.  The API server creates its OWN engine here.
    # To make the dashboard reflect the live trading configuration, set:
    #   FENIX_API_SYMBOL, FENIX_API_TIMEFRAME, FENIX_API_PAPER,
    #   FENIX_API_ALLOW_LIVE, FENIX_USE_TESTNET
    api_symbol = os.getenv("FENIX_API_SYMBOL", os.getenv("FENIX_SYMBOL", "BTCUSDT"))
    api_timeframe = os.getenv("FENIX_API_TIMEFRAME", os.getenv("FENIX_TIMEFRAME", "15m"))
    api_paper_trading = os.getenv("FENIX_API_PAPER", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    api_allow_live = _api_live_capability_enabled()
    if not api_paper_trading and not api_allow_live:
        raise RuntimeError(
            "FENIX_API_PAPER=false requires the explicit FENIX_API_ALLOW_LIVE=true capability"
        )

    logger.info(
        f"Initializing Trading Engine: symbol={api_symbol} tf={api_timeframe} "
        f"paper={api_paper_trading} allow_live={api_allow_live}"
    )
    engine = TradingEngine(
        symbol=api_symbol,
        timeframe=api_timeframe,
        paper_trading=api_paper_trading,
        allow_live_trading=api_allow_live,
        use_testnet=os.getenv("FENIX_USE_TESTNET", "false").lower() == "true",
    )
    engine.on_agent_event = handle_engine_event

    # Observer mode: when the live engine runs in a separate process
    # (run_fenix.py --mode live + Redis bridge), the API should NOT run its
    # own analysis loop — it would duplicate LLM inference and pollute the
    # dashboard with a second (paper) session. Set FENIX_API_OBSERVER=1 to
    # keep the API as a pure data/metrics server. The local engine object only
    # serves read paths; control endpoints are rejected in observer mode.
    api_observer = os.getenv("FENIX_API_OBSERVER", "").lower() in ("1", "true", "yes", "on")
    _api_observer_mode = api_observer
    if api_observer:
        logger.info(
            "🔭 FENIX_API_OBSERVER=1 — API will not start its own engine "
            "(expecting a live process to publish events via Redis)"
        )
        _engine_task = None
    else:
        # Start engine in background task
        _engine_task = asyncio.create_task(engine.start())

    # The live CLI process owns evaluation in observer mode. Starting another
    # evaluator here duplicates labels and Binance polling.
    if not api_observer:
        try:
            from src.analysis.auto_evaluator import AutoEvaluator

            auto_evaluator = AutoEvaluator(symbol=api_symbol, timeframe=api_timeframe)
            _track_task(asyncio.create_task(auto_evaluator.start()))
            logger.info("✅ AutoEvaluator started")
        except Exception as e:
            logger.error(f"Failed to start AutoEvaluator: {e}")

    # Start metrics broadcaster
    _track_task(asyncio.create_task(broadcast_metrics()))

    # v2.5: start NanoFenix companion signal broadcaster.
    _track_task(asyncio.create_task(_broadcast_nano_signals()))

    yield

    # Shutdown
    if engine:
        await engine.stop()
    if _engine_task:
        with suppress(asyncio.CancelledError):
            _engine_task.cancel()
            await _engine_task


# FastAPI App with OpenAPI Metadata
_enable_api_docs = os.getenv("FENIX_ENABLE_API_DOCS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
app = FastAPI(
    title="FenixAI Trading Bot API",
    description="""
🦅 **FenixAI Trading Bot v2.0**

API for autonomous multi-agent cryptocurrency trading system.

## Features
- Real-time market data via WebSocket
- Multi-agent trading decisions with ReasoningBank memory
- Portfolio and risk management
- Agent performance analytics

## Authentication
Most endpoints require JWT authentication. Use `/api/auth/login` to obtain a token.
    """,
    version="2.0.0",
    docs_url="/docs" if _enable_api_docs else None,
    redoc_url="/redoc" if _enable_api_docs else None,
    openapi_url="/openapi.json" if _enable_api_docs else None,
    openapi_tags=[
        {"name": "auth", "description": "Authentication and user management"},
        {"name": "trading", "description": "Trading operations, orders, and positions"},
        {"name": "agents", "description": "Agent outputs and reasoning bank"},
        {"name": "market", "description": "Market data and price feeds"},
        {"name": "system", "description": "System status, health, and metrics"},
        {"name": "engine", "description": "Trading engine control"},
    ],
    lifespan=lifespan,
)
app.include_router(auth_router, tags=["auth"])  # Register Auth Routes
app.include_router(nano_router)  # v2.5 NanoFenix / MiniFenix / release-info
app_socketio = socketio.ASGIApp(sio, app)

# Reject spoofed Host headers unless the operator explicitly extends the list.
_allowed_hosts = _env_csv(
    "FENIX_ALLOWED_HOSTS",
    "localhost,127.0.0.1,testclient,testserver",
)
if not _allowed_hosts or len(_allowed_hosts) > 32:
    raise RuntimeError("FENIX_ALLOWED_HOSTS must contain 1 to 32 explicit hosts")
if any(
    "*" in host
    or len(host) > 253
    or not host
    or any(character.isspace() or character in "/\\@" for character in host)
    for host in _allowed_hosts
):
    raise RuntimeError("FENIX_ALLOWED_HOSTS contains an invalid or wildcard host")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# CORS is restricted to explicit bare origins and bearer headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _request_body_limit() -> int:
    return _MAX_REQUEST_BYTES


class RequestBodyLimitMiddleware:
    """Bound request bodies even when clients use chunked transfer encoding."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length"},
                )(scope, receive, send)
                return

        buffered: list[dict] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.disconnect":
                break
            if message.get("type") != "http.request":
                continue
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive():
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


app.add_middleware(RequestBodyLimitMiddleware, max_bytes=_request_body_limit())


@app.middleware("http")
async def security_baseline_middleware(request: Request, call_next):
    """Apply a small API body limit and response security headers."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _request_body_limit():
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    request_path = str(request.scope.get("path", ""))
    if request_path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if request.scope.get("scheme") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Prometheus Metrics Middleware
try:
    from src.monitoring.prometheus_metrics import PrometheusMiddleware, metrics_endpoint

    app.add_middleware(PrometheusMiddleware)
    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        dependencies=[Depends(require_metrics_access)],
        include_in_schema=False,
    )
    logger.info("✅ Prometheus metrics enabled at /metrics")
except ImportError as e:
    logger.warning(f"Prometheus metrics not available: {e}")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Request validation error on %s", request.url.path)
    safe_errors = [
        {
            "type": error.get("type", "validation_error"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg", "Invalid input"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": "Validation error", "detail": safe_errors},
    )


# --- Background Tasks ---


async def broadcast_metrics():
    """Broadcast system metrics to frontend via Socket.IO"""
    while True:
        try:
            metrics = build_system_metrics()
            metrics_summary = _summarize_metrics(metrics)
            await sio.emit("system:metrics", {"summary": metrics_summary, "detail": metrics})

            connection_payload = {"connections": _build_connection_status()}
            await sio.emit("system:connection", connection_payload)

        except Exception as e:
            logger.error(f"Broadcast error: {e}")

        await asyncio.sleep(1)


async def _broadcast_nano_signals():
    """v2.5: Emit the latest NanoFenix companion signal every few seconds.

    Reads the JSON file the companion subprocess writes (the same file the
    trading engine consumes) and emits ``nanofenix:signal`` events over
    Socket.IO so the dashboard can display the live signal without a poll.
    """
    last_emitted_ts: dict[str, str] = {}
    while True:
        try:
            symbols = {"SOLUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDC"}
            if engine is not None and getattr(engine, "symbol", None):
                symbols.add(str(engine.symbol).upper())
            for symbol in symbols:
                path = _nano_signal_path_for(symbol)
                if not path.exists():
                    continue
                raw = _nano_load_signal(symbol)
                if not raw:
                    continue
                ts = str(raw.get("timestamp_utc") or "")
                if ts and ts == last_emitted_ts.get(symbol):
                    continue
                last_emitted_ts[symbol] = ts
                await sio.emit(
                    "nanofenix:signal",
                    {
                        "symbol": symbol,
                        "signal": raw,
                        "release": _NANO_RELEASE_INFO,
                    },
                )
        except Exception as exc:  # broadcaster must never die
            logger.error(f"NanoFenix broadcast error: {exc}")
        await asyncio.sleep(5)


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
        "network": metrics.get("network", {}).get("bytes_in", 0)
        + metrics.get("network", {}).get("bytes_out", 0),
        "process": metrics.get("process", {}).get("uptime", 0),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _fetch_ticker(symbol: str) -> dict | None:
    """Get 24h ticker data from Binance; returns None on failure."""
    testnet = engine.paper_trading if engine else True
    symbol_upper = _market_symbol(symbol)

    async def _inner(client: BinanceClient):
        return await client.get_ticker(symbol_upper)

    return await _with_binance_client(testnet, _inner)


async def _fetch_klines(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """Get historical klines for charting."""
    testnet = engine.paper_trading if engine else True
    symbol_upper = _market_symbol(symbol)
    interval = _market_interval(interval)
    limit = max(1, min(int(limit), 500))

    async def _inner(client: BinanceClient):
        return await client.get_klines(symbol_upper, interval=interval, limit=limit)

    data = await _with_binance_client(testnet, _inner)
    return data or []


async def _with_binance_client(testnet: bool, fn):
    """Helper to ensure Binance client lifecycle is managed per request.

    When BINANCE_API_KEY and BINANCE_API_SECRET are set in the environment,
    the client is created with those credentials so it can access private
    endpoints (balance, positions, trades). Otherwise it falls back to a
    public-data-only client.
    """
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if api_key and api_secret:
        client = BinanceClient(
            api_key=api_key, api_secret=api_secret, testnet=testnet
        )
    else:
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


def _canonical_agent_id(raw_id: str | None) -> str:
    """Map stored agent identifiers ("Visual_Agent", "Technical Analyst") to
    the canonical frontend ids ("visual", "technical", ...)."""
    normalized = str(raw_id or "").lower()
    for key in ("technical", "visual", "sentiment", "qabba", "decision", "risk"):
        if key in normalized:
            return key
    return normalized or "unknown"


def _build_scorecards(outputs: list[AgentOutput]) -> list[dict]:
    """Aggregate recent agent outputs into lightweight scorecards."""
    grouped: dict[str, list[AgentOutput]] = {}
    for output in outputs:
        grouped.setdefault(_canonical_agent_id(output.agent_id), []).append(output)

    scorecards: list[dict] = []
    for agent_id, items in grouped.items():
        total = len(items)
        success = sum(o.confidence >= 0.6 for o in items)
        failed = total - success
        avg_conf = sum(o.confidence for o in items) / total if total else 0.0
        accuracy = success / total if total else 0.0

        scorecards.append(
            {
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
            }
        )

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
    symbol: str | None = None,
    timeframe: str | None = None,
    paper_trading: bool | None = None,
    allow_live_trading: bool | None = None,
    enable_visual_agent: bool | None = None,
    enable_sentiment_agent: bool | None = None,
):
    """Restart engine with new configuration requested by the UI."""
    global engine, _engine_task

    current_symbol = symbol or (engine.symbol if engine else "BTCUSDT")
    current_timeframe = timeframe or (engine.timeframe if engine else "15m")
    current_paper = (
        paper_trading if paper_trading is not None else (engine.paper_trading if engine else True)
    )
    current_live = (
        allow_live_trading
        if allow_live_trading is not None
        else (engine.allow_live_trading if engine else False)
    )
    current_visual = (
        enable_visual_agent
        if enable_visual_agent is not None
        else (getattr(engine, "enable_visual", True))
    )
    current_sentiment = (
        enable_sentiment_agent
        if enable_sentiment_agent is not None
        else (getattr(engine, "enable_sentiment", True))
    )
    deployment_allows_live = _api_live_capability_enabled()
    if not current_paper and (not current_live or not deployment_allows_live):
        raise HTTPException(
            status_code=403,
            detail=(
                "Live API engine mode requires both allow_live_trading=true and "
                "FENIX_API_ALLOW_LIVE=true in the deployment"
            ),
        )

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


@app.get("/api/system/status", dependencies=[Depends(get_current_active_user)])
async def get_system_status():
    if not engine:
        return {"error": "Engine not initialized"}

    status = engine.get_status()
    metrics = build_system_metrics()
    return {
        "metrics": _summarize_metrics(metrics),
        "raw_metrics": metrics,
        "engine": status,
        "instances": read_runtime_instances(),
        "observer_mode": _api_observer_mode,
    }


@app.get("/api/system/instances", dependencies=[Depends(get_current_active_user)])
async def get_runtime_instances():
    """Return discovered Fenix CLI instances; stale heartbeats are never live."""
    return {
        "instances": read_runtime_instances(),
        "api_engine": _engine_config_payload(engine),
    }


# ============ Persistent System Settings ============


@app.get("/api/system/settings", dependencies=[Depends(get_current_admin_user)])
async def get_system_settings():
    """Return persisted administrative settings with all secrets masked."""
    return public_system_settings()


@app.put("/api/system/settings/{section}", dependencies=[Depends(get_current_admin_user)])
async def update_system_settings(section: str, payload: dict):
    """Validate and atomically persist one settings section."""
    try:
        persisted = update_persisted_system_settings(section, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Settings section not found") from None
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    public_section = public_system_settings({**load_system_settings(), section: persisted})[section]
    return {"success": True, "section": section, "settings": public_section}


def _test_email_connection(settings: dict) -> None:
    email = settings["notifications"]
    host = str(email.get("email_host", "")).strip()
    port = int(email.get("email_port", 0) or 0)
    if not host or not port:
        raise ValueError("Email host and port must be configured before testing")

    test_smtp_connection(
        host=host,
        port=port,
        username=str(email.get("email_username", "")).strip(),
        password=str(email.get("email_password", "")),
    )


@app.post("/api/system/test-connection/{type}", dependencies=[Depends(get_current_admin_user)])
async def test_system_connection(type: str, db: AsyncSession = Depends(get_db)):
    """Perform a real, non-destructive connectivity check."""
    if type == "database":
        await db.execute(text("SELECT 1"))
        return {"success": True, "type": type, "message": "Database query succeeded"}
    if type == "email":
        try:
            await asyncio.to_thread(_test_email_connection, load_system_settings())
        except (OSError, smtplib.SMTPException, SMTPDestinationError, ValueError) as exc:
            logger.warning("SMTP connection test failed (%s)", exc.__class__.__name__)
            raise HTTPException(status_code=502, detail="SMTP connection failed") from exc
        return {"success": True, "type": type, "message": "SMTP connection succeeded"}
    if type == "sms":
        raise HTTPException(
            status_code=501,
            detail="SMS connectivity testing is not implemented for the selected provider",
        )
    raise HTTPException(status_code=404, detail="Connection type not found")


@app.post(
    "/api/system/settings/{section}/reset",
    dependencies=[Depends(get_current_admin_user)],
)
async def reset_system_settings(section: str):
    """Reset one section to defaults and persist the result."""
    try:
        persisted = reset_persisted_system_settings(section)
    except KeyError:
        raise HTTPException(status_code=404, detail="Settings section not found") from None
    public_section = public_system_settings({**load_system_settings(), section: persisted})[section]
    return {"success": True, "section": section, "settings": public_section}


@app.get("/api/system/alerts", dependencies=[Depends(get_current_active_user)])
async def get_alerts():
    alerts: list[dict] = []
    return {"alerts": alerts, "data": alerts}


@app.get("/health", include_in_schema=False)
async def health_alias():
    """Minimal unauthenticated liveness probe for process supervisors."""
    return {"status": "ok"}


@app.get("/api/system/health", dependencies=[Depends(get_current_active_user)])
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


@app.get("/api/system/connections", dependencies=[Depends(get_current_active_user)])
async def get_connections():
    connections = _build_connection_status()
    return {"connections": connections, "data": connections}


@app.get("/api/system/metrics/history", dependencies=[Depends(get_current_active_user)])
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


@app.post("/api/engine/start", dependencies=[Depends(require_control_access)])
async def start_engine():
    global _engine_task
    if _api_observer_mode:
        raise HTTPException(
            status_code=409,
            detail="API observer mode cannot start the separately managed trading engine",
        )
    async with _engine_start_lock:
        if engine and not engine.get_status().get("running"):
            if _engine_task is None or _engine_task.done():
                _engine_task = _track_task(asyncio.create_task(engine.start()))
    return {"status": "started"}


@app.post("/api/engine/stop", dependencies=[Depends(require_control_access)])
async def stop_engine():
    if _api_observer_mode:
        raise HTTPException(
            status_code=409,
            detail="API observer mode cannot stop the separately managed trading engine",
        )
    if engine and engine.get_status().get("running"):
        await engine.stop()
    return {"status": "stopped"}


@app.get("/api/engine/config", dependencies=[Depends(get_current_active_user)])
async def get_engine_config():
    return {"config": _engine_config_payload(engine)}


@app.post("/api/engine/config", dependencies=[Depends(get_current_admin_user)])
async def update_engine_config(payload: EngineConfigUpdate):
    if _api_observer_mode:
        raise HTTPException(
            status_code=409,
            detail="API observer mode cannot reconfigure the separately managed trading engine",
        )
    config = await _restart_engine_with_config(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        paper_trading=payload.paper_trading,
        allow_live_trading=payload.allow_live_trading,
        enable_visual_agent=payload.enable_visual_agent,
        enable_sentiment_agent=payload.enable_sentiment_agent,
    )
    return {"status": "restarted", "config": config}


@app.get("/api/engine/risk-flags", dependencies=[Depends(get_current_active_user)])
async def get_risk_flags():
    return {"macro_riskoff_enabled": _engine_env_flag("FENIX_MACRO_RISKOFF_ENABLE", True)}


@app.post("/api/engine/risk-flags", dependencies=[Depends(require_control_access)])
async def update_risk_flags(payload: RiskFlagsUpdate):
    if _api_observer_mode:
        raise HTTPException(
            status_code=409,
            detail="API observer mode cannot change risk flags on the separately managed trading engine",
        )
    os.environ["FENIX_MACRO_RISKOFF_ENABLE"] = "1" if payload.macro_riskoff_enabled else "0"
    return {"macro_riskoff_enabled": payload.macro_riskoff_enabled}


# ============ Trading Endpoints ============


@app.get("/api/trading/orders", dependencies=[Depends(get_current_active_user)])
async def get_orders(
    status: Literal["pending", "filled", "cancelled", "rejected"] | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get all orders, optionally filtered by status."""
    query = select(Order).order_by(desc(Order.created_at)).limit(limit)
    if status:
        query = query.where(Order.status == status)

    result = await db.execute(query)
    orders = result.scalars().all()
    return {"orders": orders}


@app.post(
    "/api/trading/orders",
    response_model=OrderResponse,
    dependencies=[Depends(require_control_access)],
)
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
            await sio.emit(
                "orderUpdate",
                {"id": new_order.id, "status": new_order.status, "symbol": new_order.symbol},
            )
            await sio.emit(
                "tradeExecuted", {"id": trade.id, "symbol": trade.symbol, "price": trade.price}
            )

        except Exception:
            new_order.status = "rejected"
            logger.error("Order execution failed")

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


@app.delete("/api/trading/orders/{order_id}", dependencies=[Depends(require_control_access)])
async def cancel_order(
    order_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$"),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an order by ID."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order and order.status == "pending":
        order.status = "cancelled"
        order.updated_at = datetime.utcnow()
        await db.commit()

        await sio.emit("orderUpdate", {"id": order.id, "status": "cancelled"})
        return {"message": "Order cancelled", "order": {"id": order.id, "status": "cancelled"}}

    raise HTTPException(status_code=404, detail="Order not found or cannot be cancelled")


@app.get("/api/trading/balance", dependencies=[Depends(get_current_active_user)])
async def get_account_balance(db: AsyncSession = Depends(get_db)):
    """Return paper ledger equity or the authenticated live Futures balance."""
    paper_mode = (
        bool(engine.paper_trading)
        if engine is not None
        else _engine_env_flag("ENABLE_PAPER_TRADING", True)
    )
    if paper_mode:
        try:
            initial_balance = float(os.getenv("FENIX_BALANCE_FALLBACK_USDT", "100") or 100)
        except ValueError:
            initial_balance = 100.0
        if (
            not math.isfinite(initial_balance)
            or initial_balance <= 0
            or initial_balance > 1_000_000_000
        ):
            initial_balance = 100.0
        realized_result = await db.execute(
            select(func.coalesce(func.sum(Position.realized_pnl), 0.0)).where(
                Position.is_open.is_(False)
            )
        )
        unrealized_result = await db.execute(
            select(func.coalesce(func.sum(Position.unrealized_pnl), 0.0)).where(
                Position.is_open.is_(True)
            )
        )
        realized_pnl = float(realized_result.scalar_one())
        unrealized_pnl = float(unrealized_result.scalar_one())
        equity = initial_balance + realized_pnl + unrealized_pnl
        return {
            "balances": [
                {
                    "asset": "USDT",
                    "balance": equity,
                    "available": equity,
                    "unrealized_pnl": unrealized_pnl,
                }
            ],
            "total_usdt": equity,
            "initial_balance": initial_balance,
            "realized_pnl": realized_pnl,
            "mode": "paper",
            "source": "paper_ledger",
        }

    testnet = engine.use_testnet if engine else False

    async def _inner(client: BinanceClient):
        # Get all asset balances
        data = await client._request("GET", "/fapi/v2/balance", signed=True)
        if not data:
            return {"balances": [], "total_usdt": 0.0}

        balances = []
        total_usdt = 0.0
        for item in data:
            asset = item.get("asset", "")
            balance = float(item.get("balance", 0))
            if balance != 0:
                cross_unpnl = float(item.get("crossWalletUnPnl", 0))
                available = float(item.get("availableBalance", 0))
                balances.append({
                    "asset": asset,
                    "balance": balance,
                    "available": available,
                    "unrealized_pnl": cross_unpnl,
                })
                # Approximate USDT equivalent (USDT and USDC are ~1:1)
                if asset in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD"):
                    total_usdt += balance

        return {"balances": balances, "total_usdt": total_usdt, "mode": "live"}

    result = await _with_binance_client(testnet, _inner)
    if result is None:
        return {"balances": [], "total_usdt": 0.0, "error": "Binance connection failed"}
    return result


@app.get("/api/trading/positions", dependencies=[Depends(get_current_active_user)])
async def get_positions(db: AsyncSession = Depends(get_db)):
    """Get real open positions from Binance Futures."""
    testnet = engine.use_testnet if engine else True

    async def _inner(client: BinanceClient):
        positions = await client.get_positions()
        if not positions:
            return {"positions": []}

        result = []
        for p in positions:
            symbol = p.get("symbol", "")
            qty = float(p.get("positionAmt", 0))
            entry = float(p.get("entryPrice", 0))
            pnl = float(p.get("unRealizedProfit", 0))
            side = "LONG" if qty > 0 else "SHORT"
            result.append({
                "id": f"binance:{symbol}",
                "symbol": symbol,
                "side": side,
                "quantity": abs(qty),
                "entry_price": entry,
                "current_price": float(p.get("markPrice", 0)),
                "unrealized_pnl": pnl,
                "realized_pnl": 0.0,
                "opened_at": datetime.utcnow().isoformat(),
                "leverage": int(p.get("leverage", 1)),
                "margin_type": p.get("marginType", ""),
            })
        return {"positions": result}

    # Try Binance first
    result = await _with_binance_client(testnet, _inner)
    if result is not None:
        return result

    # Fallback to DB positions
    result_db = await db.execute(select(Position).where(Position.is_open == True))
    positions = result_db.scalars().all()
    return {"positions": positions}


@app.get("/api/trading/history", dependencies=[Depends(get_current_active_user)])
async def get_trade_history(
    limit: int = Query(50, ge=1, le=500),
    symbol: str | None = Query(None, min_length=5, max_length=20),
    db: AsyncSession = Depends(get_db),
):
    """Get trade history — real Binance trades + DB trades merged."""
    testnet = engine.use_testnet if engine else True
    target_symbol = _market_symbol(symbol) if symbol else (engine.symbol if engine else None)

    # Try to fetch real trades from Binance
    async def _inner(client: BinanceClient):
        params = {"limit": min(limit, 100)}
        if target_symbol:
            params["symbol"] = target_symbol.upper()
        data = await client._request("GET", "/fapi/v1/userTrades", params=params, signed=True)
        if not data:
            return []
        trades = []
        for t in data:
            trades.append({
                "id": f"binance:{t.get('id', '')}",
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "quantity": float(t.get("qty", 0)),
                "price": float(t.get("price", 0)),
                "realized_pnl": float(t.get("realizedPnl", 0)),
                "executed_at": datetime.utcfromtimestamp(
                    t.get("time", 0) / 1000
                ).isoformat(),
                "commission": float(t.get("commission", 0)),
                "commission_asset": t.get("commissionAsset", ""),
                "maker": t.get("maker", False),
            })
        return trades

    binance_trades = await _with_binance_client(testnet, _inner)

    # Also get DB trades
    query = select(Trade).order_by(desc(Trade.executed_at)).limit(limit)
    if symbol:
        query = query.where(Trade.symbol == target_symbol)
    result = await db.execute(query)
    db_trades = result.scalars().all()

    # Merge: if Binance trades available, use those; otherwise fall back to DB
    if binance_trades:
        return {"trades": binance_trades, "source": "binance"}

    return {"trades": db_trades, "source": "database"}


@app.get("/api/trading/market", dependencies=[Depends(get_current_active_user)])
async def get_market_data(
    symbol: str | None = Query(None, min_length=5, max_length=20),
):
    """Return live market snapshot using engine stream with Binance fallback."""
    target_symbol = _market_symbol(symbol or (engine.symbol if engine else "BTCUSDT"))

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


@app.get("/api/agents", dependencies=[Depends(get_current_active_user)])
async def get_agents(db: AsyncSession = Depends(get_db)):
    """Get all registered agents enriched with live performance when available."""
    result = await db.execute(
        select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(10_000)
    )
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

        agents.append(
            {
                **agent,
                "status": "active" if running else "inactive",
                "last_run": datetime.utcnow().isoformat(),
                "performance": performance,
            }
        )

    return {"agents": agents, "data": agents}


@app.get("/api/agents/outputs", dependencies=[Depends(get_current_active_user)])
async def get_agent_outputs(
    timeframe: Literal["24h", "7d", "30d"] = Query("24h"),
    agent_id: str | None = Query(None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get recent agent outputs/reasoning."""
    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(limit)
    if agent_id:
        query = query.where(AgentOutput.agent_id == agent_id)

    result = await db.execute(query)
    outputs = result.scalars().all()
    return {"outputs": outputs, "data": outputs}


@app.get("/api/reasoning", dependencies=[Depends(get_current_active_user)])
async def get_reasoning_entries(
    agent_name: str | None = Query(
        None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
    limit: int = Query(20, ge=1, le=200),
):
    """Return recent entries from ReasoningBank for an agent or all agents."""
    try:
        reasoning_bank = get_reasoning_bank()
        agents = (
            [agent_name]
            if agent_name
            else [
                "technical_agent",
                "qabba_agent",
                "sentiment_agent",
                "visual_agent",
                "decision_agent",
                "risk_manager",
            ]
        )
        result = {}
        for ag in agents:
            try:
                entries = reasoning_bank.get_recent(ag, limit)
                result[ag] = [e.__dict__ for e in entries]
            except Exception:
                result[ag] = []
        return {"reasoning": result}
    except Exception:
        logger.warning("Error fetching reasoning entries")
        return {"reasoning": {}}


@app.get("/api/agents/{agent_id}", dependencies=[Depends(get_current_active_user)])
async def get_agent(
    agent_id: str = Path(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific agent by ID."""
    agents_response = await get_agents(db=db)
    for agent in agents_response["agents"]:
        if agent["id"] == agent_id:
            return {"agent": agent}
    raise HTTPException(status_code=404, detail="Agent not found")


@app.post("/api/agents/outputs", dependencies=[Depends(require_control_access)])
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
        input_summary=output.input_summary,
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
        "input_summary": new_output.input_summary,
    }

    # Emit via socket
    await sio.emit("agentOutput", output_dict)
    await sio.emit("agent:reasoning", output_dict)

    return output_dict


@app.get("/api/agents/scorecards", dependencies=[Depends(get_current_active_user)])
async def get_agent_scorecards(db: AsyncSession = Depends(get_db)):
    """Return aggregated performance metrics per agent."""
    result = await db.execute(
        select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(10_000)
    )
    outputs = result.scalars().all()
    scorecards = _build_scorecards(outputs)
    return {"data": scorecards, "scorecards": scorecards}


@app.get("/api/reasoning-bank/logs", dependencies=[Depends(get_current_active_user)])
async def get_reasoning_bank_logs(
    agent_id: str | None = Query(None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
    timeframe: Literal["24h", "7d", "30d"] = Query("24h"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
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


@app.get("/api/reasoning/analytics", dependencies=[Depends(get_current_active_user)])
async def get_reasoning_analytics(
    timeframe: Literal["24h", "7d", "30d"] = Query("24h"),
    db: AsyncSession = Depends(get_db),
):
    """Provide lightweight analytics for the reasoning dashboard."""
    cutoff_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff_delta = cutoff_map.get(timeframe)

    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(10_000)
    if cutoff_delta:
        query = query.where(AgentOutput.timestamp >= datetime.utcnow() - cutoff_delta)

    result = await db.execute(query)
    outputs = result.scalars().all()
    return _build_reasoning_analytics(outputs)


@app.get("/api/reasoning/consensus", dependencies=[Depends(get_current_active_user)])
async def get_reasoning_consensus(
    timeframe: Literal["24h", "7d", "30d"] = Query("24h"),
    db: AsyncSession = Depends(get_db),
):
    """Provide a simple consensus signal per agent for the UI."""
    cutoff_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff_delta = cutoff_map.get(timeframe)

    query = select(AgentOutput).order_by(desc(AgentOutput.timestamp)).limit(10_000)
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
        consensus_payload.append(
            {
                "agent_id": agent_id,
                "agent_name": items[0].agent_name if items else agent_id,
                "consensus_score": avg_confidence,
                "agreement_count": len(items),
                "total_agents": total_agents,
                "dominant_sentiment": "bullish" if avg_confidence >= 0.6 else "neutral",
                "confidence": avg_confidence,
            }
        )

    return consensus_payload


@app.get("/api/market/data/{symbol}", dependencies=[Depends(get_current_active_user)])
async def get_market_series(
    symbol: str = Path(..., min_length=5, max_length=20),
    interval: str | None = Query(None, min_length=2, max_length=3),
    limit: int = Query(120, ge=10, le=500),
):
    """Return real kline series for charting."""
    target_symbol = _market_symbol(symbol)
    target_interval = _market_interval(interval or (engine.timeframe if engine else "15m"))
    klines = await _fetch_klines(target_symbol, target_interval, limit)

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

    return {"symbol": target_symbol, "interval": target_interval, "data": points}


@app.get("/api/market/overview", dependencies=[Depends(get_current_active_user)])
async def get_market_overview(symbols: str | None = Query(None, max_length=256)):
    """Return 24h overview for a handful of symbols used by the dashboard."""
    default_symbols = [
        engine.symbol if engine else "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "ADAUSDT",
    ]

    requested = symbols.split(",") if symbols else default_symbols
    if len(requested) > 10:
        raise HTTPException(status_code=422, detail="At most 10 symbols are allowed")
    symbol_list = list(
        dict.fromkeys(_market_symbol(symbol) for symbol in requested if symbol.strip())
    )
    if not symbol_list:
        raise HTTPException(status_code=422, detail="At least one symbol is required")

    markets: list[dict] = []
    for sym in symbol_list:
        ticker = await _fetch_ticker(sym)
        if not ticker:
            continue

        markets.append(
            {
                "symbol": sym,
                "price": float(ticker.get("lastPrice", 0)),
                "change_percent": float(ticker.get("priceChangePercent", 0)),
                "price_change": float(ticker.get("priceChange", 0)),
                "volume": float(ticker.get("volume", 0)),
                "quote_volume": float(ticker.get("quoteVolume", 0)),
                "high_24h": float(ticker.get("highPrice", 0)),
                "low_24h": float(ticker.get("lowPrice", 0)),
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    if not markets:
        raise HTTPException(status_code=503, detail="No market overview data available")

    return {"markets": markets, "data": markets}


@sio.on("subscribe:agents")
async def subscribe_agents(sid):
    logger.info(f"Client {sid} subscribed to agents")


# ============ ReasoningBank Endpoints ============


@app.get("/api/reasoning/entries", dependencies=[Depends(get_current_active_user)])
async def get_reasoning_entries(
    agent_id: str | None = Query(None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
    decision: str | None = Query(None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
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


@app.get("/api/reasoning/search", dependencies=[Depends(get_current_active_user)])
async def search_reasoning(
    query: str = Query(..., min_length=3, max_length=512),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Semantic search in ReasoningBank."""
    # SECURITY: Escape SQL LIKE wildcards to prevent wildcard injection
    safe_query = _escape_sql_like(query)
    sql_query = (
        select(AgentOutput)
        .where(AgentOutput.reasoning.ilike(f"%{safe_query}%", escape="\\"))
        .limit(limit)
    )
    result = await db.execute(sql_query)
    results = result.scalars().all()
    return {"results": results, "query": query}


# --- Socket IO Events ---


@sio.event
async def connect(sid, environ, auth=None):
    """Authenticate every realtime connection before events can be received."""
    token = auth.get("token") if isinstance(auth, dict) else None
    scope = environ.get("asgi.scope", {}) if isinstance(environ, dict) else {}
    client = scope.get("client") or ("", 0)
    client_host = client[0] if isinstance(client, (tuple, list)) and client else ""

    allow_unsafe_loopback = (
        os.getenv("FENIX_ALLOW_UNAUTHENTICATED_LOOPBACK_CONTROL", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if (not SECRET_KEY or len(SECRET_KEY.encode("utf-8")) < 32) and (
        allow_unsafe_loopback and client_host in _LOOPBACK_HOSTS
    ):
        logger.warning("Development-only unauthenticated loopback Socket.IO connection")
        await sio.save_session(sid, {"development_loopback": True})
        return True

    if not isinstance(token, str) or not token:
        logger.warning("Rejected unauthenticated Socket.IO connection")
        return False

    try:
        async with SessionLocal() as db:
            user = await get_current_user(token=token, db=db)
        if not user.is_active:
            return False
        await sio.save_session(
            sid,
            {"user_id": str(user.id), "email": user.email, "role": user.role},
        )
    except HTTPException:
        logger.warning("Rejected invalid Socket.IO credentials")
        return False
    except Exception:
        logger.warning("Socket.IO authentication backend failed")
        return False

    logger.info("Authenticated Socket.IO connection for user_id=%s", user.id)
    return True


@sio.event
async def disconnect(sid):
    logger.info(f"Socket disconnected: {sid}")


@sio.on("subscribe:system")
async def subscribe_system(sid):
    logger.info(f"Client {sid} subscribed to system")


if __name__ == "__main__":
    import uvicorn

    allow_expose_api = os.getenv("ALLOW_EXPOSE_API", "false").lower() == "true"
    host = "0.0.0.0" if allow_expose_api else "127.0.0.1"  # nosec B104
    try:
        port = int(os.getenv("FENIX_API_PORT", "8000"))
    except ValueError:
        logger.warning("Invalid FENIX_API_PORT; falling back to 8000")
        port = 8000
    if not 1 <= port <= 65535:
        logger.warning("FENIX_API_PORT is out of range; falling back to 8000")
        port = 8000
    if allow_expose_api:
        logger.warning("ALLOW_EXPOSE_API is set: the API will bind to 0.0.0.0 (external exposure)")
    else:
        logger.info("Binding to 127.0.0.1 by default. Set ALLOW_EXPOSE_API=true to bind to 0.0.0.0")
    reload_enabled = os.getenv("FENIX_DEV_RELOAD", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    uvicorn.run("src.api.server:app_socketio", host=host, port=port, reload=reload_enabled)
