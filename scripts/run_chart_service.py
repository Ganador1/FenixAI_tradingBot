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
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Añadir path del proyecto
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.chart_capture_scheduler import ChartCaptureScheduler

# Configuración por defecto
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = PROJECT_ROOT / ".chart_service.pid"


def setup_logging(log_to_file: bool = True, verbose: bool = False) -> logging.Logger:
    """Configura logging con rotación de archivos."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    level = logging.DEBUG if verbose else logging.INFO
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    
    handlers = [console]
    
    # File handler con rotación diaria
    if log_to_file:
        log_file = LOG_DIR / f"chart_service_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        handlers.append(file_handler)
    
    logging.basicConfig(level=level, handlers=handlers)
    
    # Silenciar logs ruidosos de librerías
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    return logging.getLogger("chart_service")


def write_pid():
    """Escribe el PID actual al archivo."""
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))


def check_running() -> bool:
    """Verifica si ya hay un servicio corriendo."""
    if not PID_FILE.exists():
        return False
    
    try:
        pid = int(PID_FILE.read_text().strip())
        # Verificar si el proceso existe
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        # PID inválido o proceso no existe
        PID_FILE.unlink(missing_ok=True)
        return False


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
    os.umask(0)
    
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
    
    with open('/dev/null', 'r') as devnull:
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
        """
    )
    
    parser.add_argument(
        "--symbols", "-s",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help=f"Símbolos a capturar (default: {DEFAULT_SYMBOLS})"
    )
    
    parser.add_argument(
        "--timeframes", "-t",
        nargs="+", 
        default=DEFAULT_TIMEFRAMES,
        help=f"Timeframes (default: {DEFAULT_TIMEFRAMES})"
    )
    
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Correr como daemon en background"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logging verbose (debug)"
    )
    
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="No escribir a archivo de log"
    )
    
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Detener servicio corriendo"
    )
    
    parser.add_argument(
        "--status",
        action="store_true",
        help="Mostrar estado del servicio"
    )
    
    args = parser.parse_args()
    
    # Comando: --status
    if args.status:
        if check_running():
            pid = int(PID_FILE.read_text().strip())
            print(f"✅ Chart service is running (PID: {pid})")
        else:
            print("❌ Chart service is not running")
        return
    
    # Comando: --stop
    if args.stop:
        if not check_running():
            print("❌ No service running")
            return
        
        pid = int(PID_FILE.read_text().strip())
        print(f"Stopping service (PID: {pid})...")
        os.kill(pid, signal.SIGTERM)
        
        # Esperar que termine
        for _ in range(10):
            time.sleep(0.5)
            if not check_running():
                print("✅ Service stopped")
                return
        
        print("⚠️ Service did not stop gracefully, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        return
    
    # Verificar si ya hay uno corriendo
    if check_running():
        pid = int(PID_FILE.read_text().strip())
        print(f"❌ Service already running (PID: {pid})")
        print("Use --stop to stop it first")
        sys.exit(1)
    
    # Daemon mode
    if args.daemon:
        print("Starting chart service in background...")
        daemonize()
    
    # Configurar logging
    logger = setup_logging(
        log_to_file=not args.no_log_file,
        verbose=args.verbose
    )
    
    # Iniciar servicio
    service = ChartService(
        symbols=args.symbols,
        timeframes=args.timeframes,
        logger=logger,
    )
    
    service.start()


if __name__ == "__main__":
    main()
