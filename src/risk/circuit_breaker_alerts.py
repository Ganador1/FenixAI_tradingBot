"""Sistema de alertas para circuit breakers.

Implementa integración con Telegram/Discord para alertas cuando
se activan modos SEVERE/CAUTION.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass

import logging

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    """Configuración de alertas."""
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    enable_alerts: bool = True
    min_alert_level: str = "SEVERE"  # NORMAL, CAUTION, SEVERE, HOT


try:
    from src.risk.runtime_feedback import RiskFeedbackStatus
except ImportError:
    RiskFeedbackStatus = None


class CircuitBreakerNotifier:
    """
    Notificador para alertas de circuit breaker.
    
    Envía alertas cuando se activan modos de riesgo para mantener
    al usuario informado del estado del sistema.
    """
    
    def __init__(self, config: Optional[AlertConfig] = None):
        self.config = config or self._load_config_from_env()
        self._last_alert_time: Optional[datetime] = None
        self._alert_cooldown_minutes: int = 5  # Evitar spam
    
    def _load_config_from_env(self) -> AlertConfig:
        """Carga configuración desde variables de entorno."""
        return AlertConfig(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            enable_alerts=os.getenv("ENABLE_CIRCUIT_BREAKER_ALERTS", "true").lower() == "true",
            min_alert_level=os.getenv("MIN_ALERT_LEVEL", "SEVERE")
        )
    
    def should_alert(self, status: RiskFeedbackStatus) -> bool:
        """Verifica si se debe enviar alerta."""
        if not self.config.enable_alerts:
            return False
        
        # Niveles de alerta
        levels = {
            "NORMAL": 0,
            "HOT": 1,
            "CAUTION": 2,
            "SEVERE": 3
        }
        
        min_level = levels.get(self.config.min_alert_level, 1)
        current_level = levels.get(status.mode, 0)
        
        if current_level < min_level:
            return False
        
        # Cooldown para evitar spam
        if self._last_alert_time:
            elapsed = (datetime.now(timezone.utc) - self._last_alert_time).total_seconds() / 60
            if elapsed < self._alert_cooldown_minutes:
                return False
        
        return True
    
    async def send_alert(self, status: RiskFeedbackStatus, metrics: Dict[str, Any]) -> bool:
        """Envía alerta de circuit breaker."""
        if not self.should_alert(status):
            return False
        
        success = False
        
        # Telegram
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            try:
                await self._send_telegram(status, metrics)
                success = True
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")
        
        # Discord
        if self.config.discord_webhook_url:
            try:
                await self._send_discord(status, metrics)
                success = True
            except Exception as e:
                logger.error(f"Failed to send Discord alert: {e}")
        
        if success:
            self._last_alert_time = datetime.now(timezone.utc)
        
        return success
    
    async def _send_telegram(self, status: RiskFeedbackStatus, metrics: Dict[str, Any]) -> None:
        """Envía alerta vía Telegram Bot API."""
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not installed, skipping Telegram alert")
            return
        
        emoji = {
            "NORMAL": "✅",
            "HOT": "🔥",
            "CAUTION": "⚠️",
            "SEVERE": "🚨"
        }.get(status.mode, "ℹ️")
        
        message = f"""
{emoji} *CIRCUIT BREAKER ALERT*

*Mode:* `{status.mode}`
*Reason:* {status.reason}
*Risk Bias:* {status.risk_bias:.2f}

*Metrics:*
• Win Rate: {metrics.get('win_rate', 0):.1%}
• PnL: ${metrics.get('daily_pnl', 0):.2f}
• Drawdown: {metrics.get('drawdown_pct', 0):.1f}%
• Loss Streak: {metrics.get('loss_streak', 0)}

{'' if not status.block_trading else '🚫 TRADING BLOCKED!'}

_Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC_
"""
        
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_notification": status.mode not in ("SEVERE",)
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent successfully")
                else:
                    response_text = await resp.text()
                    logger.error(f"Telegram API error: {resp.status} - {response_text}")
    
    async def _send_discord(self, status: RiskFeedbackStatus, metrics: Dict[str, Any]) -> None:
        """Envía alerta vía Discord Webhook."""
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not installed, skipping Discord alert")
            return
        
        colors = {
            "NORMAL": 0x00ff00,   # Green
            "HOT": 0xffa500,      # Orange
            "CAUTION": 0xffff00,  # Yellow
            "SEVERE": 0xff0000    # Red
        }.get(status.mode, 0x808080)
        
        embed = {
            "title": f"🚨 Circuit Breaker: {status.mode}",
            "color": colors,
            "description": status.reason,
            "fields": [
                {
                    "name": "Risk Bias",
                    "value": f"{status.risk_bias:.2f}",
                    "inline": True
                },
                {
                    "name": "Win Rate",
                    "value": f"{metrics.get('win_rate', 0):.1%}",
                    "inline": True
                },
                {
                    "name": "Daily PnL",
                    "value": f"${metrics.get('daily_pnl', 0):.2f}",
                    "inline": True
                },
                {
                    "name": "Drawdown",
                    "value": f"{metrics.get('drawdown_pct', 0):.1f}%",
                    "inline": True
                },
                {
                    "name": "Loss Streak",
                    "value": f"{metrics.get('loss_streak', 0)}",
                    "inline": True
                },
                {
                    "name": "Status",
                    "value": "🚫 TRADING BLOCKED" if status.block_trading else "⚠️ Trading reduced",
                    "inline": False
                }
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {
                "text": "FenixAI Circuit Breaker"
            }
        }
        
        payload = {
            "embeds": [embed],
            "content": "@everyone" if status.mode == "SEVERE" else None
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.discord_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status == 204:
                    logger.info("Discord alert sent successfully")
                else:
                    response_text = await resp.text()
                    logger.error(f"Discord API error: {resp.status} - {response_text}")


# Singleton
_notifier: Optional[CircuitBreakerNotifier] = None


def get_circuit_breaker_notifier() -> CircuitBreakerNotifier:
    """Obtiene o crea el notificador global."""
    global _notifier
    if _notifier is None:
        _notifier = CircuitBreakerNotifier()
    return _notifier
