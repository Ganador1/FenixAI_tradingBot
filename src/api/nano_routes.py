"""FastAPI router for the v2.5 NanoFenix / MiniFenix companion endpoints.

This module exposes REST endpoints over the live NanoFenix v3.5 companion
signal that the trading engine already consumes. It also offers a small
process supervisor so the dashboard can start / stop the companion without
touching the engine. MiniFenix exposes a read-only regime endpoint.

The router is mounted by ``src.api.server`` via ``app.include_router``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("FenixAPI.nano")

router = APIRouter(prefix="/api", tags=["v25"])

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NANOFENIX_LAUNCHER = REPO_ROOT / "run_nanofenixv3.py"
DEFAULT_SIGNAL_DIR = REPO_ROOT / "logs"

# Process registry: symbol_upper -> Popen.
_NANO_PROCESSES: dict[str, subprocess.Popen] = {}

# Recommended v2.5 release configuration (matches docs/releases/v2.5*.md).
RELEASE_INFO = {
    "version": "2.5.0",
    "status": "release-candidate",
    "recommended_symbol": "SOLUSDT",
    "recommended_timeframe": "15m",
    "recommended_mode": "paper",
    "recommended_team": {
        "technical": "ministral-3:14b-cloud",
        "qabba": "ministral-3:14b-cloud",
        "decision": "nemotron-3-nano:30b-cloud",
        "risk_manager": "devstral-small-2:24b-cloud",
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
        "nanofenix_v3_5": "Zero-LLM microstructure companion (LightGBM)",
        "minifenix": "Two-speed slow-brain/fast-trigger research prototype",
        "fenix_experimental": "Brain/trigger/agent bridge runner",
    },
}


# ---- Schemas -------------------------------------------------------------


class NanoSignal(BaseModel):
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
    age_seconds: float | None = Field(
        None, description="Seconds since the companion last wrote this signal."
    )
    raw: dict | None = None


class NanoStartRequest(BaseModel):
    symbol: str = Field("SOLUSDT", description="Trading pair")
    observer_only: bool = Field(True, description="Observer-only mode (recommended).")
    adaptive_fusion: bool = Field(True, description="Use AdaptiveDualHorizonFusion.")


class NanoStatus(BaseModel):
    symbol: str
    running: bool
    pid: int | None = None
    signal_path: str | None = None
    signal_age_seconds: float | None = None


# ---- Helpers -------------------------------------------------------------


def _signal_path_for(symbol: str) -> Path:
    return DEFAULT_SIGNAL_DIR / f"nanofenixv3_companion_{symbol.lower()}.json"


def _load_signal(symbol: str) -> dict | None:
    path = _signal_path_for(symbol)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read NanoFenix signal %s: %s", path, exc)
        return None


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
        age_seconds=age,
        raw=raw,
    )


# ---- REST endpoints ------------------------------------------------------


@router.get("/v25/release-info")
async def release_info() -> dict:
    """Return the v2.5 release info and recommended config the UI shows."""
    return RELEASE_INFO


@router.get("/nanofenix/signal", response_model=NanoSignal)
async def nano_signal(symbol: str = Query("SOLUSDT", description="Trading pair")):
    """Return the latest NanoFenix v3.5 companion signal for a symbol."""
    symbol_upper = symbol.upper()
    raw = _load_signal(symbol_upper)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"No NanoFenix signal file for {symbol_upper}. Start the companion first.",
        )
    return _to_signal_model(symbol_upper, raw, _signal_path_for(symbol_upper))


@router.get("/nanofenix/status", response_model=NanoStatus)
async def nano_status(symbol: str = Query("SOLUSDT")):
    symbol_upper = symbol.upper()
    proc = _NANO_PROCESSES.get(symbol_upper)
    path = _signal_path_for(symbol_upper)
    age = None
    if path.exists():
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            age = None
    return NanoStatus(
        symbol=symbol_upper,
        running=proc is not None and proc.poll() is None,
        pid=proc.pid if proc is not None and proc.poll() is None else None,
        signal_path=str(path) if path.exists() else None,
        signal_age_seconds=age,
    )


@router.post("/nanofenix/start", response_model=NanoStatus)
async def nano_start(req: NanoStartRequest):
    """Spawn a NanoFenix v3.5 companion subprocess for the given symbol."""
    if not NANOFENIX_LAUNCHER.exists():
        raise HTTPException(status_code=500, detail=f"{NANOFENIX_LAUNCHER} not found")

    symbol_upper = req.symbol.upper()
    existing = _NANO_PROCESSES.get(symbol_upper)
    if existing is not None and existing.poll() is None:
        return await nano_status(symbol=symbol_upper)  # type: ignore[arg-type]

    signal_path = _signal_path_for(symbol_upper)
    signal_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if req.observer_only:
        env["NANOFENIXV3_COMPANION_OBSERVER_ONLY"] = "1"
    env["NANOFENIX_SIGNAL_STATE_PATH"] = str(signal_path)

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

    logger.info("Spawning NanoFenix companion: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, env=env)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to launch NanoFenix: {exc}") from exc

    _NANO_PROCESSES[symbol_upper] = proc
    return NanoStatus(
        symbol=symbol_upper,
        running=True,
        pid=proc.pid,
        signal_path=str(signal_path),
        signal_age_seconds=None,
    )


@router.post("/nanofenix/stop")
async def nano_stop(symbol: str = Query("SOLUSDT")):
    """Terminate the NanoFenix companion subprocess for a symbol."""
    symbol_upper = symbol.upper()
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
        raise HTTPException(status_code=500, detail=f"Stop error: {exc}") from exc
    return {"symbol": symbol_upper, "stopped": True}


# ---- MiniFenix ------------------------------------------------------------


@router.get("/minifenix/regime")
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
    latest = candidates[-1]
    try:
        with latest.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read {latest}: {exc}") from exc
    return {
        "source": str(latest),
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
