# monitoring/dashboard.py
"""
Dashboard web básico para monitoreo del bot de trading
"""

from __future__ import annotations

import json
import threading
import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from pathlib import Path

try:
    from flask import Flask, jsonify, render_template, request

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

from .alerts import alert_manager
from .metrics_collector import metrics_collector

try:
    from src.system.connections.market_stream import get_global_stream_metrics
except Exception:

    def get_global_stream_metrics():
        return {}


def _tail_signal_trace(max_lines: int = 200):
    try:
        p = Path("logs/signal_trace.jsonl")
        if not p.exists():
            return []
        with p.open("r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _analyze_consistency():
    lines = _tail_signal_trace(200)
    flags = []
    ok = 0
    total = 0
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        total += 1
        sig = (d.get("technical_output") or {}).get("signal") or d.get("final_decision") or "HOLD"
        m = d.get("technical_input_summary") or {}
        cmf = m.get("cmf")
        pvm = (d.get("mtf_indicators") or {}).get("ITF") or {}
        above = pvm.get("price_vs_ma")
        incoherent = False
        if sig == "BUY" and (
            above == "BELOW_MA50_MA200" or (isinstance(cmf, (int, float)) and cmf < 0)
        ):
            incoherent = True
        if sig == "SELL" and (
            above and above.startswith("ABOVE") and (isinstance(cmf, (int, float)) and cmf > 0)
        ):
            incoherent = True
        if incoherent:
            flags.append(
                {
                    "signal": sig,
                    "price_vs_ma": above,
                    "cmf": cmf,
                    "ts": d.get("timestamp_utc"),
                    "tf": d.get("timeframe"),
                }
            )
        else:
            ok += 1
    ratio = ok / max(total or 1, 1)
    return {"total": total, "coherent": ok, "coherence_ratio": ratio, "flags": flags}


class TradingDashboard:
    """Dashboard web para monitoreo del bot"""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000):
        self.host = host
        self.port = port
        self.app: Flask | None = None
        self.server_thread: threading.Thread | None = None
        self._running = False

        if not FLASK_AVAILABLE:
            print("Flask no está disponible. Instala con: pip install flask")
            return

        self._setup_flask_app()

    def _setup_flask_app(self):
        """Configura la aplicación Flask"""
        if not FLASK_AVAILABLE:
            return

        self.app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

        # Ruta principal
        @self.app.route("/")
        def dashboard():
            return render_template("dashboard_modern.html")

        # API endpoints
        @self.app.route("/api/metrics")
        def get_metrics():
            return jsonify(metrics_collector.get_performance_summary())

        @self.app.route("/api/alerts")
        def get_alerts():
            alerts = alert_manager.get_active_alerts()
            return jsonify(
                {
                    "active_alerts": [
                        {
                            "id": alert.id,
                            "severity": alert.severity.value,
                            "title": alert.title,
                            "message": alert.message,
                            "timestamp": alert.timestamp.isoformat(),
                            "category": alert.category,
                        }
                        for alert in alerts
                    ],
                    "summary": alert_manager.get_alert_summary(),
                }
            )

        @self.app.route("/api/alerts/<alert_id>/resolve", methods=["POST"])
        def resolve_alert(alert_id):
            data = request.get_json() or {}
            resolution_note = data.get("note", "")
            alert_manager.resolve_alert(alert_id, resolution_note)
            return jsonify({"success": True})

        @self.app.route("/api/status")
        def get_status():
            return jsonify(
                {
                    "bot_status": "running",  # Esto debería venir del bot principal
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "uptime_seconds": 0,  # Calcular desde el inicio del bot
                }
            )

        @self.app.route("/api/stream_metrics")
        def stream_metrics():
            try:
                return jsonify(get_global_stream_metrics())
            except Exception as e:
                logger.error(f"Error getting stream metrics: {e}")
                return jsonify({})

        @self.app.route("/api/consistency")
        def consistency():
            try:
                return jsonify(_analyze_consistency())
            except Exception as e:
                logger.error(f"Error analyzing consistency: {e}")
                return jsonify({"coherence_ratio": 0, "flags": []})

    def start(self):
        """Inicia el servidor del dashboard"""
        if not FLASK_AVAILABLE or not self.app:
            print("Dashboard no disponible - Flask no instalado")
            return

        if self._running:
            return

        def run_server():
            self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self._running = True

        print(f"Dashboard iniciado en http://{self.host}:{self.port}")

    def stop(self):
        """Detiene el servidor del dashboard"""
        self._running = False
        # Flask no tiene un método clean shutdown en modo threading
        # En producción se usaría un servidor WSGI adecuado


def get_global_stream_metrics():
    """Obtiene métricas globales de streams de WebSocket"""
    try:
        from src.system.connections.market_stream import (
            get_global_stream_metrics as stream_metrics_func,
        )

        return stream_metrics_func()
    except ImportError:
        # Retornar datos simulados si no está disponible
        return {
            "BTCUSDT": {
                "total_messages": 1250,
                "successful_connections": 15,
                "reconnects": 2,
                "errors": 1,
                "avg_message_interval_ms": 850.5,
            },
            "ETHUSDT": {
                "total_messages": 980,
                "successful_connections": 12,
                "reconnects": 1,
                "errors": 0,
                "avg_message_interval_ms": 920.3,
            },
        }


def _analyze_consistency():
    """Analiza la consistencia de los agentes"""
    try:
        from src.analysis.analizar_coherencia_agentes import analyze_agent_consistency

        return analyze_agent_consistency()
    except ImportError:
        # Retornar datos simulados si no está disponible
        return {
            "coherence_ratio": 0.85,
            "flags": [
                {
                    "tf": "15m",
                    "ts": "2025-11-12 10:30:00",
                    "signal": "BUY",
                    "price_vs_ma": "above",
                    "cmf": 0.25,
                },
                {
                    "tf": "1h",
                    "ts": "2025-11-12 10:00:00",
                    "signal": "HOLD",
                    "price_vs_ma": "near",
                    "cmf": -0.05,
                },
            ],
        }


# Instancia global del dashboard
dashboard = TradingDashboard()
