"""Fail-closed validation for chart images used in trading decisions."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

logger = logging.getLogger(__name__)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MIN_CHART_BYTES = 5_000
_MAX_CHART_BYTES = 25 * 1024 * 1024
_SYMBOL = re.compile(r"^[A-Z0-9]{5,20}$")
_TIMEFRAMES = {
    "1",
    "3",
    "5",
    "15",
    "30",
    "60",
    "240",
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "4h",
    "1d",
    "1w",
}


def _validated_market_inputs(symbol: str, timeframe: str) -> tuple[str, str]:
    normalized_symbol = str(symbol).strip().upper()
    normalized_timeframe = str(timeframe).strip()
    if not _SYMBOL.fullmatch(normalized_symbol):
        raise ValueError("invalid chart symbol")
    if normalized_timeframe not in _TIMEFRAMES:
        raise ValueError("unsupported chart timeframe")
    return normalized_symbol, normalized_timeframe


def _safe_chart_metadata(path: str | Path) -> tuple[Path, os.stat_result]:
    chart_path = Path(path)
    if not str(chart_path) or len(str(chart_path)) > 4_096 or chart_path.suffix.lower() != ".png":
        raise ValueError("chart path must identify a bounded PNG filename")
    if chart_path.is_symlink():
        raise ValueError("chart symlinks are not allowed")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(chart_path, flags)
    except OSError as exc:
        raise ValueError("chart cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("chart must be a regular file")
        if not _MIN_CHART_BYTES <= metadata.st_size <= _MAX_CHART_BYTES:
            raise ValueError("chart size is outside the allowed range")
        if os.read(descriptor, len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
            raise ValueError("chart does not contain a valid PNG signature")
    finally:
        os.close(descriptor)
    return chart_path, metadata


class ChartPathSecurityManager:
    """Validate chart identity, type, size, freshness, and temporal ordering."""

    def __init__(
        self,
        max_age_minutes: int = 5,
        disable_age_check: bool | None = None,
    ):
        configured = os.getenv("FENIX_CHART_MAX_AGE_MINUTES")
        if configured:
            try:
                max_age_minutes = int(configured)
            except ValueError:
                logger.warning("Ignoring invalid FENIX_CHART_MAX_AGE_MINUTES")
        if not 1 <= int(max_age_minutes) <= 1_440:
            raise ValueError("chart maximum age must be between 1 and 1440 minutes")

        self.max_age_seconds = int(max_age_minutes) * 60
        self.used_paths_history: list[dict[str, Any]] = []
        self.last_validated_path: str | None = None
        self.last_validation_time: float | None = None
        # The environment can no longer disable this trading-safety control.
        # Tests may opt out only by constructing an isolated manager explicitly.
        self._age_check_disabled = bool(disable_age_check)
        if self._age_check_disabled:
            logger.warning("Chart age validation disabled on an explicitly isolated manager")

    def validate_chart_path_freshness(
        self,
        chart_path: str,
        symbol: str,
        timeframe: str,
    ) -> tuple[bool, str]:
        """Validate a chart without following symlinks or trusting its extension."""
        try:
            normalized_symbol, normalized_timeframe = _validated_market_inputs(
                symbol,
                timeframe,
            )
            path, metadata = _safe_chart_metadata(chart_path)
        except ValueError as exc:
            message = f"Chart rejected: {exc}"
            logger.error(message)
            return False, message

        name = path.name.lower()
        if normalized_symbol.lower() not in name or normalized_timeframe.lower() not in name:
            message = "Chart filename does not match the requested symbol and timeframe"
            logger.error(message)
            return False, message

        current_time = time.time()
        age_seconds = current_time - metadata.st_mtime
        if age_seconds < -5:
            message = "Chart timestamp is unexpectedly in the future"
            logger.error(message)
            return False, message
        if not self._age_check_disabled and age_seconds > self.max_age_seconds:
            message = f"Chart is stale ({age_seconds / 60:.1f} minutes old)"
            logger.error(message)
            return False, message

        normalized_path = str(path.resolve())
        if (
            self.last_validated_path == normalized_path
            and self.last_validation_time is not None
            and current_time - self.last_validation_time < 30
        ):
            logger.warning("The same chart was validated again within 30 seconds")

        self.last_validated_path = normalized_path
        self.last_validation_time = current_time
        self.used_paths_history.append(
            {
                "path": normalized_path,
                "timestamp": current_time,
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "file_age_seconds": max(0.0, age_seconds),
            }
        )
        self.used_paths_history = self.used_paths_history[-10:]
        return True, f"Chart is valid and fresh ({max(0.0, age_seconds):.1f}s old)"

    def get_most_recent_chart_path(
        self,
        symbol: str,
        timeframe: str,
        screenshots_dir: str = "screenshots",
    ) -> str | None:
        """Return the newest safe direct child of a real screenshots directory."""
        try:
            normalized_symbol, normalized_timeframe = _validated_market_inputs(
                symbol,
                timeframe,
            )
        except ValueError as exc:
            logger.error("Chart lookup rejected: %s", exc)
            return None

        screenshots_path = Path(screenshots_dir)
        if screenshots_path.is_symlink() or not screenshots_path.is_dir():
            logger.error("Screenshots directory is unavailable or unsafe")
            return None

        pattern = f"*{normalized_symbol}*{normalized_timeframe}*.png"
        candidates = list(screenshots_path.glob(pattern))
        if len(candidates) > 1_000:
            logger.error("Chart candidate count exceeds the safety limit")
            return None

        safe_candidates: list[tuple[float, Path]] = []
        for candidate in candidates:
            try:
                candidate_path, metadata = _safe_chart_metadata(candidate)
                safe_candidates.append((metadata.st_mtime, candidate_path))
            except ValueError:
                continue
        safe_candidates.sort(key=lambda item: item[0], reverse=True)

        for _, candidate in safe_candidates:
            valid, _ = self.validate_chart_path_freshness(
                str(candidate),
                normalized_symbol,
                normalized_timeframe,
            )
            if valid:
                return str(candidate.resolve())
        return None

    def validate_temporal_analysis_paths(
        self,
        current_path: str,
        previous_path: str,
        symbol: str,
        timeframe: str,
    ) -> tuple[bool, str]:
        """Require two distinct valid charts in strictly increasing time order."""
        current_valid, current_message = self.validate_chart_path_freshness(
            current_path,
            symbol,
            timeframe,
        )
        if not current_valid:
            return False, f"Invalid current chart: {current_message}"
        previous_valid, previous_message = self.validate_chart_path_freshness(
            previous_path,
            symbol,
            timeframe,
        )
        if not previous_valid:
            return False, f"Invalid previous chart: {previous_message}"

        try:
            current_resolved, current_metadata = _safe_chart_metadata(current_path)
            previous_resolved, previous_metadata = _safe_chart_metadata(previous_path)
        except ValueError as exc:
            return False, f"Chart changed during temporal validation: {exc}"

        if current_resolved.resolve() == previous_resolved.resolve():
            return False, "Temporal chart paths must be different"
        if current_metadata.st_mtime <= previous_metadata.st_mtime:
            return False, "Temporal charts are not in strictly increasing order"
        return True, "Temporal chart paths are valid"

    def get_security_report(self) -> dict[str, Any]:
        """Return bounded, serializable validation metadata."""
        recent: list[dict[str, Any]] = []
        for validation in self.used_paths_history[-5:]:
            item = dict(validation)
            item["timestamp_human"] = datetime.fromtimestamp(
                float(validation["timestamp"])
            ).isoformat()
            item["age_at_validation_minutes"] = (
                float(validation["file_age_seconds"]) / 60
            )
            recent.append(item)
        return {
            "max_age_seconds": self.max_age_seconds,
            "last_validated_path": self.last_validated_path,
            "last_validation_time": self.last_validation_time,
            "validations_count": len(self.used_paths_history),
            "recent_validations": recent,
        }


chart_security_manager = ChartPathSecurityManager()


def validate_chart_path_safe(chart_path: str, symbol: str, timeframe: str) -> bool:
    valid, _ = chart_security_manager.validate_chart_path_freshness(
        chart_path,
        symbol,
        timeframe,
    )
    return valid


def get_safe_chart_path(symbol: str, timeframe: str) -> str | None:
    return chart_security_manager.get_most_recent_chart_path(symbol, timeframe)


def validate_temporal_paths_safe(
    current_path: str,
    previous_path: str,
    symbol: str,
    timeframe: str,
) -> bool:
    valid, _ = chart_security_manager.validate_temporal_analysis_paths(
        current_path,
        previous_path,
        symbol,
        timeframe,
    )
    return valid
