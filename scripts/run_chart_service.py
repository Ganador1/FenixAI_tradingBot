#!/usr/bin/env python3
"""
Chart Capture Service - Servicio autónomo para captura de charts.

Este script se puede ejecutar como un daemon en background para mantener
charts frescos disponibles para el Visual Agent.

Características:
- Se auto-reinicia en caso de errores fatales
- Escribe logs a archivo
- Puede correr en foreground o background
- Health check endpoint (opcional)
- Señales UNIX para control (SIGTERM, SIGHUP)

Uso:
    # Foreground
    python scripts/run_chart_service.py

    # Background (daemon)
    python scripts/run_chart_service.py --daemon

    # Con símbolos específicos
    python scripts/run_chart_service.py --symbols BTCUSDT ETHUSDT SOLUSDT

    # Con timeframes específicos
    python scripts/run_chart_service.py --timeframes 1m 5m 15m 1h
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Añadir path del proyecto
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.chart_capture_scheduler import ChartCaptureScheduler
from src.security.private_files import (
    ensure_private_directory,
    read_private_text,
    write_private_text,
)

# Configuración por defecto
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = LOG_DIR / "runtime_locks" / "chart_service.pid"


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{5,20}", normalized):
        raise argparse.ArgumentTypeError("symbol must be a 5-20 character Binance pair")
    return normalized


def _timeframe(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[1-9][0-9]{0,4}(?:m|h|d|w|M)", normalized):
        raise argparse.ArgumentTypeError("timeframe has an invalid format")
    return normalized


def setup_logging(log_to_file: bool = True, verbose: bool = False) -> logging.Logger:
    """Configura logging con rotación de archivos."""
    ensure_private_directory(LOG_DIR)

    level = logging.DEBUG if verbose else logging.INFO

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)

    handlers = [console]

    # File handler con rotación diaria
    if log_to_file:
        log_file = LOG_DIR / f"chart_service_{datetime.now().strftime('%Y%m%d')}.log"
        if log_file.is_symlink():
            raise ValueError("Chart service log cannot be a symbolic link")
        file_handler = logging.FileHandler(log_file)
        os.fchmod(file_handler.stream.fileno(), 0o600)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    # Silenciar logs ruidosos de librerías
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return logging.getLogger("chart_service")


def _process_identity(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or "run_chart_service.py" not in line:
        return None
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _read_pid_record() -> dict[str, object] | None:
    if not PID_FILE.exists():
        return None
    try:
        payload = json.loads(read_private_text(PID_FILE, max_bytes=4096))
        pid = payload.get("pid") if isinstance(payload, dict) else None
        identity = payload.get("identity") if isinstance(payload, dict) else None
        if (
            type(pid) is not int
            or pid <= 1
            or not isinstance(identity, str)
            or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or _process_identity(pid) != identity
        ):
            return None
        return {"pid": pid, "identity": identity}
    except (ValueError, json.JSONDecodeError):
        return None


def _remove_own_pidfile() -> None:
    record = _read_pid_record()
    if record and record["pid"] == os.getpid() and not PID_FILE.is_symlink():
        PID_FILE.unlink(missing_ok=True)


def write_pid():
    """Escribe el PID actual al archivo."""
    identity = _process_identity(os.getpid())
    if identity is None:
        raise RuntimeError("Could not establish chart service process identity")
    ensure_private_directory(PID_FILE.parent)
    write_private_text(
        PID_FILE,
        json.dumps({"pid": os.getpid(), "identity": identity}),
    )
    atexit.register(_remove_own_pidfile)


def check_running() -> bool:
    """Verifica si ya hay un servicio corriendo."""
    return _read_pid_record() is not None


def daemonize():
    """Convierte el proceso en un daemon."""
    # Primera fork
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.exit(f"Fork #1 failed: {e}")

    # Decouple del entorno padre
    os.chdir("/")
    os.setsid()
    os.umask(0o077)

    # Segunda fork
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.exit(f"Fork #2 failed: {e}")

    # Redirigir file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    with open("/dev/null") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())


class ChartService:
    """
    Servicio principal de captura de charts.

    Maneja el ciclo de vida del scheduler con:
    - Auto-recovery en errores
    - Señales de control
    - Health monitoring
    """

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        logger: logging.Logger,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.logger = logger
        self.scheduler: ChartCaptureScheduler | None = None
        self._running = False
        self._restart_count = 0
        self._max_restarts = 5
        self._restart_window = 300  # 5 minutos
        self._last_restart = 0

    def start(self):
        """Inicia el servicio."""
        self.logger.info("=" * 60)
        self.logger.info("🚀 CHART CAPTURE SERVICE - Starting")
        self.logger.info("=" * 60)
        self.logger.info("PID: %d", os.getpid())
        self.logger.info("Symbols: %s", self.symbols)
        self.logger.info("Timeframes: %s", self.timeframes)

        self._running = True
        self._setup_signals()
        write_pid()

        while self._running:
            try:
                self._run_scheduler()
            except Exception as e:
                self.logger.error("💥 Scheduler crashed: %s", e, exc_info=True)
                if self._should_restart():
                    self.logger.info("🔄 Restarting in 10 seconds...")
                    time.sleep(10)
                else:
                    self.logger.critical("❌ Max restarts exceeded, giving up")
                    break

        self.logger.info("👋 Service stopped")

    def _run_scheduler(self):
        """Ejecuta el scheduler principal."""
        self.scheduler = ChartCaptureScheduler(
            symbols=self.symbols,
            timeframes=self.timeframes,
        )
        self.scheduler.start()

        # Loop principal - imprime status cada minuto
        while self._running:
            time.sleep(60)
            if self._running:
                self._print_health()

    def _should_restart(self) -> bool:
        """Determina si debemos reintentar."""
        now = time.time()

        # Resetear contador si pasó la ventana
        if now - self._last_restart > self._restart_window:
            self._restart_count = 0

        self._restart_count += 1
        self._last_restart = now

        return self._restart_count <= self._max_restarts

    def _print_health(self):
        """Imprime estado de salud."""
        if not self.scheduler:
            return

        status = self.scheduler.get_status()
        cache = status.get("cache", {})

        self.logger.info(
            "💚 Health: uptime=%s | cache=%d valid | jobs=%d ok / %d fail",
            status.get("uptime_human", "?"),
            cache.get("valid_entries", 0),
            status.get("jobs_executed", 0),
            status.get("jobs_failed", 0),
        )

    def _setup_signals(self):
        """Configura handlers de señales."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGHUP, self._handle_reload)

    def _handle_shutdown(self, signum, frame):
        """Handler para shutdown graceful."""
        sig_name = signal.Signals(signum).name
        self.logger.info("📥 Received %s, shutting down...", sig_name)
        self._running = False
        if self.scheduler:
            self.scheduler.stop()

    def _handle_reload(self, signum, frame):
        """Handler para reload de configuración."""
        self.logger.info("📥 Received SIGHUP, reloading...")
        # En el futuro: recargar configuración de archivo


def main():
    """Punto de entrada principal."""
    parser = argparse.ArgumentParser(
        description="Chart Capture Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_chart_service.py
  python scripts/run_chart_service.py --daemon
  python scripts/run_chart_service.py --symbols BTCUSDT ETHUSDT --timeframes 15m 1h
        """,
    )

    parser.add_argument(
        "--symbols",
        "-s",
        nargs="+",
        type=_symbol,
        default=DEFAULT_SYMBOLS,
        help=f"Símbolos a capturar (default: {DEFAULT_SYMBOLS})",
    )

    parser.add_argument(
        "--timeframes",
        "-t",
        nargs="+",
        type=_timeframe,
        default=DEFAULT_TIMEFRAMES,
        help=f"Timeframes (default: {DEFAULT_TIMEFRAMES})",
    )

    parser.add_argument(
        "--daemon", "-d", action="store_true", help="Correr como daemon en background"
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Logging verbose (debug)")

    parser.add_argument("--no-log-file", action="store_true", help="No escribir a archivo de log")

    parser.add_argument("--stop", action="store_true", help="Detener servicio corriendo")

    parser.add_argument("--status", action="store_true", help="Mostrar estado del servicio")

    args = parser.parse_args()

    # Comando: --status
    if args.status:
        record = _read_pid_record()
        if record:
            print(f"✅ Chart service is running (PID: {record['pid']})")
        else:
            print("❌ Chart service is not running")
        return

    # Comando: --stop
    if args.stop:
        record = _read_pid_record()
        if not record:
            print("❌ No service running")
            return

        pid = int(record["pid"])
        expected_identity = str(record["identity"])
        print(f"Stopping service (PID: {pid})...")
        os.kill(pid, signal.SIGTERM)

        # Esperar que termine
        for _ in range(10):
            time.sleep(0.5)
            current = _read_pid_record()
            if current is None:
                print("✅ Service stopped")
                return

        current = _read_pid_record()
        if current and current["pid"] == pid and current["identity"] == expected_identity:
            print("⚠️ Service did not stop gracefully, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
        else:
            print("Refusing SIGKILL because the process identity changed")
        return

    # Verificar si ya hay uno corriendo
    running_record = _read_pid_record()
    if running_record:
        print(f"❌ Service already running (PID: {running_record['pid']})")
        print("Use --stop to stop it first")
        sys.exit(1)

    # Daemon mode
    if args.daemon:
        print("Starting chart service in background...")
        daemonize()

    # Configurar logging
    logger = setup_logging(log_to_file=not args.no_log_file, verbose=args.verbose)

    # Iniciar servicio
    service = ChartService(
        symbols=args.symbols,
        timeframes=args.timeframes,
        logger=logger,
    )

    service.start()


if __name__ == "__main__":
    main()
