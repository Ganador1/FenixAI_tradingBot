"""FastAPI router for the v2.5 NanoFenix / MiniFenix companion endpoints.

This module exposes REST endpoints over the live NanoFenix v3.5 companion
signal that the trading engine already consumes. It also offers a small
process supervisor so the dashboard can start / stop the companion without
touching the engine. MiniFenix exposes a read-only regime endpoint.

The router is mounted by ``src.api.server`` via ``app.include_router``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_active_user, require_control_access
logger = logging.getLogger("FenixAPI.nano")

router = APIRouter(prefix="/api", tags=["v25"])

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NANOFENIX_LAUNCHER = REPO_ROOT / "run_nanofenixv3.py"
DEFAULT_SIGNAL_DIR = REPO_ROOT / "logs"

# Process registry: symbol_upper -> Popen.
_NANO_PROCESSES: dict[str, subprocess.Popen] = {}
_PROCESS_LOCK = asyncio.Lock()
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")

# Recommended release configuration. Team updated 2026-07-01 after the Ollama
# Cloud Max benchmark: deepseek-v4-flash analysts (21-27s cycles, 0 timeouts) +
# deepseek-v4-pro on decision/risk (highest quality), gemini-3-flash visual.
RELEASE_INFO = {
    "version": "2.5.1",
    "status": "release-candidate",
    "recommended_symbol": "ETHUSDC",
    "recommended_timeframe": "15m",
    "recommended_mode": "paper",
    "recommended_team": {
        "technical": "deepseek-v4-flash:cloud",
        "qabba": "deepseek-v4-flash:cloud",
        "sentiment": "deepseek-v4-flash:cloud",
        "decision": "deepseek-v4-pro:cloud",
        "risk_manager": "deepseek-v4-pro:cloud",
        "visual": "gemini-3-flash-preview:cloud",
    },
    "nanofenix": {
        "default_observer_only": True,
        "hard_veto_reasons": [
            "direction_mismatch",
            "high_uncertainty",
            "stale_signal",
            "symbol_mismatch",
            "signal_file_missing",
            "signal_file_empty",
            "signal_parse_error",
            "missing_or_invalid_timestamp",
        ],
    },
    "subsystems": {
        "fenix_core": "Main LangGraph multi-agent engine",
        "nanofenix_v3_5": "Zero-LLM microstructure companion (LightGBM, dual-horizon)",
        "drift_retrain": "Page-Hinkley concept-drift detection -> forced retrain",
        "regime_meta": "Per-regime meta-labeling gate on companion signals",
        "minifenix": "Two-speed slow-brain/fast-trigger research prototype",
        "fenix_experimental": "Brain/trigger/agent bridge runner",
    },
}


# ---- Schemas -------------------------------------------------------------


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NanoSignal(_StrictModel):
    symbol: str
    timestamp_utc: str | None = None
    signal: str | None = None
    action: str | None = None
    confidence: float | None = None
    pred_bps: float | None = None
    direction_accuracy: float | None = None
    regime: str | None = None
    trend: str | None = None
    allow_execute: bool | None = None
    allow_add_to_position: bool | None = None
    size_multiplier_hint: float | None = None
    calibration_health: float | None = None
    uncertainty_bps: float | None = None
    actionable_edge_bps: float | None = None
    has_position: bool | None = None
    # v3.5+: readiness, precisión por horizonte y estadísticas paper.
    companion_ready: bool | None = None
    companion_block_reasons: list[str] | None = None
    short_direction_accuracy: float | None = None
    long_direction_accuracy: float | None = None
    direction_samples: int | None = None
    val_accuracy: float | None = None
    volatility_state: str | None = None
    paper_trades: int | None = None
    paper_win_rate: float | None = None
    paper_pnl: float | None = None
    # Mejoras 2026-07: drift-retrain (Page-Hinkley) y meta-labeling por régimen.
    drift_score: float | None = None
    drift_retrain_count: int | None = None
    regime_meta_prob: float | None = None
    regime_meta_samples: float | None = None
    age_seconds: float | None = Field(
        None, description="Seconds since the companion last wrote this signal."
    )


class NanoStartRequest(_StrictModel):
    symbol: str = Field("SOLUSDT", pattern=r"^[A-Z0-9]{5,20}$", description="Trading pair")
    observer_only: bool = Field(True, description="Observer-only mode (recommended).")
    adaptive_fusion: bool = Field(True, description="Use AdaptiveDualHorizonFusion.")


class NanoStatus(_StrictModel):
    symbol: str
    running: bool
    pid: int | None = None
    signal_path: str | None = None
    signal_age_seconds: float | None = None


# ---- Helpers -------------------------------------------------------------


def _signal_path_for(symbol: str) -> Path:
    symbol_upper = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(symbol_upper):
        raise ValueError("invalid Binance symbol")
    base = DEFAULT_SIGNAL_DIR.resolve()
    path = (base / f"nanofenixv3_companion_{symbol_upper.lower()}.json").resolve()
    if path.parent != base:
        raise ValueError("signal path escaped its storage directory")
    return path


def _allowed_symbols() -> set[str]:
    raw = os.getenv(
        "FENIX_NANOFENIX_ALLOWED_SYMBOLS",
        "ETHUSDC,SOLUSDT,BTCUSDT,ETHUSDT",
    )
    return {
        value.strip().upper()
        for value in raw.split(",")
        if _SYMBOL_PATTERN.fullmatch(value.strip().upper())
    }


def _validate_symbol(symbol: str) -> str:
    symbol_upper = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(symbol_upper):
        raise HTTPException(status_code=422, detail="Invalid Binance symbol")
    if symbol_upper not in _allowed_symbols():
        raise HTTPException(status_code=403, detail="Symbol is not allowed for API supervision")
    return symbol_upper


def _nanofenix_subprocess_environment(signal_path: Path, observer_only: bool) -> dict[str, str]:
    """Build a minimal environment; companion processes never inherit secrets."""
    allowed_names = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    }
    env = {name: os.environ[name] for name in allowed_names if os.getenv(name)}
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["FENIX_SKIP_DOTENV"] = "1"
    env["NANOFENIX_SIGNAL_STATE_PATH"] = str(signal_path)
    env["NANOFENIXV3_COMPANION_OBSERVER_ONLY"] = "1" if observer_only else "0"
    runtime_stem = f"nanofenixv3_runtime_{signal_path.stem}"
    env["NANOFENIXV3_RUNTIME_STATE_PATH"] = str(signal_path.parent / f"{runtime_stem}.json")
    env["NANOFENIXV3_RUNTIME_MODEL_PATH"] = str(signal_path.parent / f"{runtime_stem}_model.pkl")
    env["FENIX_MODEL_SIGNING_KEY_FILE"] = str(
        signal_path.parent / ".security" / "model-signing.key"
    )
    return env


def _load_signal(symbol: str) -> dict | None:
    path = _signal_path_for(symbol)
    if not path.exists():
        return None
    try:
        return _read_bounded_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Could not read NanoFenix signal %s (%s)",
            path.name,
            exc.__class__.__name__,
        )
        return None


def _read_bounded_json_object(path: Path, *, max_bytes: int = 1_048_576) -> dict:
    """Read a small regular JSON file without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("state path is not a regular file")
        if file_stat.st_size > max_bytes:
            raise ValueError("state file is too large")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            value = json.load(fh)
        if not isinstance(value, dict):
            raise ValueError("state file must contain a JSON object")
        return value
    finally:
        if fd >= 0:
            os.close(fd)


def _to_signal_model(symbol: str, raw: dict, path: Path) -> NanoSignal:
    age = None
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        age = None

    return NanoSignal(
        symbol=symbol,
        timestamp_utc=raw.get("timestamp_utc"),
        signal=raw.get("signal"),
        action=raw.get("action"),
        confidence=raw.get("confidence"),
        pred_bps=raw.get("pred_bps"),
        direction_accuracy=raw.get("direction_accuracy"),
        regime=raw.get("regime"),
        trend=raw.get("trend"),
        allow_execute=raw.get("allow_execute"),
        allow_add_to_position=raw.get("allow_add_to_position"),
        size_multiplier_hint=raw.get("size_multiplier_hint"),
        calibration_health=raw.get("calibration_health"),
        uncertainty_bps=raw.get("uncertainty_bps"),
        actionable_edge_bps=raw.get("actionable_edge_bps"),
        has_position=raw.get("has_position"),
        companion_ready=raw.get("companion_ready"),
        companion_block_reasons=raw.get("companion_block_reasons"),
        short_direction_accuracy=raw.get("short_direction_accuracy"),
        long_direction_accuracy=raw.get("long_direction_accuracy"),
        direction_samples=raw.get("direction_samples"),
        val_accuracy=raw.get("val_accuracy"),
        volatility_state=raw.get("volatility_state"),
        paper_trades=raw.get("paper_trades"),
        paper_win_rate=raw.get("paper_win_rate"),
        paper_pnl=raw.get("paper_pnl"),
        drift_score=raw.get("drift_score"),
        drift_retrain_count=raw.get("drift_retrain_count"),
        regime_meta_prob=raw.get("regime_meta_prob"),
        regime_meta_samples=raw.get("regime_meta_samples"),
        age_seconds=age,
    )


# ---- REST endpoints ------------------------------------------------------


@router.get("/v25/release-info", dependencies=[Depends(get_current_active_user)])
async def release_info() -> dict:
    """Return the v2.5 release info and recommended config the UI shows."""
    return RELEASE_INFO


@router.get(
    "/nanofenix/signal",
    response_model=NanoSignal,
    dependencies=[Depends(get_current_active_user)],
)
async def nano_signal(
    symbol: str = Query("SOLUSDT", pattern=r"^[A-Z0-9]{5,20}$", description="Trading pair"),
):
    """Return the latest NanoFenix v3.5 companion signal for a symbol."""
    symbol_upper = _validate_symbol(symbol)
    raw = _load_signal(symbol_upper)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"No NanoFenix signal file for {symbol_upper}. Start the companion first.",
        )
    return _to_signal_model(symbol_upper, raw, _signal_path_for(symbol_upper))


@router.get(
    "/nanofenix/status",
    response_model=NanoStatus,
    dependencies=[Depends(get_current_active_user)],
)
async def nano_status(symbol: str = Query("SOLUSDT", pattern=r"^[A-Z0-9]{5,20}$")):
    symbol_upper = _validate_symbol(symbol)
    proc = _NANO_PROCESSES.get(symbol_upper)
    path = _signal_path_for(symbol_upper)
    age = None
    if path.exists():
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            age = None
    owned_running = proc is not None and proc.poll() is None
    # Un companion lanzado FUERA de la API (p.ej. la sesión live con
    # --with-nanofenix-companion) también cuenta como running si su señal
    # está fresca: evita que el dashboard ofrezca "Start" y se lance un
    # duplicado que pelee por el mismo archivo de señal.
    external_running = not owned_running and age is not None and age < 30.0
    return NanoStatus(
        symbol=symbol_upper,
        running=owned_running or external_running,
        pid=proc.pid if owned_running else None,
        signal_path=path.name if path.exists() else None,
        signal_age_seconds=age,
    )


@router.post(
    "/nanofenix/start",
    response_model=NanoStatus,
    dependencies=[Depends(require_control_access)],
)
async def nano_start(req: NanoStartRequest):
    """Spawn a NanoFenix v3.5 companion subprocess for the given symbol."""
    if not NANOFENIX_LAUNCHER.exists():
        raise HTTPException(status_code=500, detail="NanoFenix launcher is unavailable")

    symbol_upper = _validate_symbol(req.symbol)
    async with _PROCESS_LOCK:
        existing = _NANO_PROCESSES.get(symbol_upper)
        if existing is not None and existing.poll() is None:
            return await nano_status(symbol=symbol_upper)  # type: ignore[arg-type]

        active_count = sum(proc.poll() is None for proc in _NANO_PROCESSES.values())
        max_processes = max(1, min(16, int(os.getenv("FENIX_MAX_API_NANO_PROCESSES", "4"))))
        if active_count >= max_processes:
            raise HTTPException(status_code=409, detail="NanoFenix process limit reached")

        # Refuse duplicate writers when another process already publishes the
        # same symbol's signal.
        external_path = _signal_path_for(symbol_upper)
        if external_path.exists():
            try:
                external_age = max(0.0, time.time() - external_path.stat().st_mtime)
            except OSError:
                external_age = None
            if external_age is not None and external_age < 30.0:
                raise HTTPException(
                    status_code=409,
                    detail=f"A companion for {symbol_upper} is already publishing a fresh signal",
                )

        signal_path = _signal_path_for(symbol_upper)
        signal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        env = _nanofenix_subprocess_environment(signal_path, req.observer_only)

        cmd = [
            sys.executable,
            str(NANOFENIX_LAUNCHER),
            "--symbol",
            symbol_upper,
            "--companion",
            "--output-path",
            str(signal_path),
        ]
        if req.adaptive_fusion:
            cmd.append("--adaptive-fusion")

        logger.info("Spawning NanoFenix companion for %s", symbol_upper)
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            logger.error("NanoFenix launch failed", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to launch NanoFenix") from exc

        _NANO_PROCESSES[symbol_upper] = proc
    return NanoStatus(
        symbol=symbol_upper,
        running=True,
        pid=proc.pid,
        signal_path=signal_path.name,
        signal_age_seconds=None,
    )


@router.post("/nanofenix/stop", dependencies=[Depends(require_control_access)])
async def nano_stop(symbol: str = Query("SOLUSDT")):
    """Terminate the NanoFenix companion subprocess for a symbol."""
    symbol_upper = _validate_symbol(symbol)
    async with _PROCESS_LOCK:
        proc = _NANO_PROCESSES.pop(symbol_upper, None)
        if proc is None or proc.poll() is not None:
            return {"symbol": symbol_upper, "stopped": False, "reason": "not running"}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except (OSError, ValueError) as exc:
            logger.error("NanoFenix stop failed", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to stop NanoFenix") from exc
    return {"symbol": symbol_upper, "stopped": True}


# ---- MiniFenix ------------------------------------------------------------


@router.get("/minifenix/regime", dependencies=[Depends(get_current_active_user)])
async def minifenix_regime():
    """Read the latest MiniFenix Brain regime if a state file exists.

    MiniFenix does not currently persist its regime to disk by default. This
    endpoint scans ``logs/minifenix_regime*.json`` if the operator opts in,
    and returns 404 otherwise. The schema is intentionally minimal.
    """
    candidates = sorted(DEFAULT_SIGNAL_DIR.glob("minifenix_regime*.json"))
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="No MiniFenix regime file found. MiniFenix is a research prototype; expose it via logs/minifenix_regime*.json if needed.",
        )
    latest = candidates[-1].resolve()
    if latest.parent != DEFAULT_SIGNAL_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid MiniFenix state path")
    try:
        data = _read_bounded_json_object(latest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Could not read MiniFenix regime", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read MiniFenix regime") from exc
    return {
        "source": latest.name,
        "regime": data,
        "age_seconds": max(0.0, time.time() - latest.stat().st_mtime),
    }


# ---- Lifecycle hook (used by server.py shutdown) -------------------------


def shutdown_companions() -> None:
    """Terminate any NanoFenix subprocesses owned by the API."""
    for symbol, proc in list(_NANO_PROCESSES.items()):
        if proc.poll() is None:
            logger.info("Stopping NanoFenix subprocess for %s (pid=%s)", symbol, proc.pid)
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
    _NANO_PROCESSES.clear()
