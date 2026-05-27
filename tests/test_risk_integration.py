# tests/test_risk_integration.py
"""
Integration tests for RuntimeRiskManager.
Tests the interaction between risk modes and trading decisions.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord, RiskFeedbackLoopConfig, RiskFeedbackStatus

class TestRiskManagerIntegration(unittest.TestCase):
    """Integration tests for RuntimeRiskManager."""

    def setUp(self):
        """Create a fresh RuntimeRiskManager for testing."""
        # Configurar limites estrictos para testing
        self.config = RiskFeedbackLoopConfig(
            caution_daily_loss_pct=5.0,
            loss_streak_caution=3,
            loss_streak_halt=5,
            severe_drawdown_pct=6.5,
            enabled=True
        )
        self.risk_manager = RuntimeRiskManager(config=self.config)
        
        # Resetear estado interno
        self.risk_manager._trades.clear()
        self.risk_manager._daily_pnl = 0.0
        self.risk_manager._daily_start_balance = 10000.0
        self.risk_manager._current_balance = 10000.0
        self.risk_manager._peak_balance = 10000.0
        self.risk_manager.current_status.mode = "NORMAL"
        self.risk_manager.current_status.risk_bias = 1.0
        self.risk_manager.set_max_exposure_pct(0.5)

    def create_trade_record(self, trade_id, pnl, success, size=1.0):
        return TradeRecord(
            trade_id=trade_id,
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            decision="BUY",
            entry_price=50000.0,
            exit_price=50000.0 + (pnl if success else -abs(pnl)),
            pnl=pnl,
            pnl_pct=pnl/50000.0,
            success=success,
            size=size
        )

    def test_severe_mode_blocks_trading_via_drawdown(self):
        """Verify SEVERE mode triggered by drawdown blocks trading."""
        # Drawdown 7% = 700 USD
        trade = self.create_trade_record("drawdown-1", -700.0, False)
        self.risk_manager.record_trade(trade)
        
        # Verificar estado
        status = self.risk_manager.evaluate_risk()
        
        self.assertEqual(status.mode, "SEVERE", 
            f"Expected SEVERE mode after 7% drawdown, got {status.mode}")
            
        allowed, status_check = self.risk_manager.check_trade_allowed("BTCUSDT", 1.0)
        self.assertFalse(allowed, "Trade should be blocked in SEVERE mode")

    def test_daily_reset_clears_metrics(self):
        """Verify new trading day resets daily PnL logic."""
        # Simular trade para poblar métricas (evitar KeyError en get_metrics)
        trade = self.create_trade_record("yesterday-1", -10.0, False)
        self.risk_manager.record_trade(trade)
        
        # Manipular día anterior
        self.risk_manager._last_trading_day = "2000-01-01"
        self.risk_manager._daily_pnl = -500.0
        
        # Actualizar balance hoy triggers reset
        self.risk_manager.update_balance(9500.0)
        
        # get_metrics debería mostrar 0.0 daily_pnl
        metrics = self.risk_manager.get_metrics()
        self.assertEqual(metrics.get("daily_pnl", 0.0), 0.0,
            f"Daily PnL should be 0 after day change, got {metrics.get('daily_pnl')}")
            
        self.assertEqual(self.risk_manager._daily_pnl, 0.0)

    def test_get_adjusted_size(self):
        """Verify position size adjustment based on mode."""
        # 1. Test NORMAL mode
        # Mock evaluate_risk to return NORMAL
        with patch.object(self.risk_manager, 'evaluate_risk') as mock_eval:
            mock_eval.return_value = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
            
            size = self.risk_manager.get_adjusted_size(1000.0)
            self.assertAlmostEqual(size, 1000.0)
        
        # 2. Test CAUTION/Bias mode
        # Mock evaluate_risk to return CAUTION with bias 0.5
        with patch.object(self.risk_manager, 'evaluate_risk') as mock_eval:
            mock_eval.return_value = RiskFeedbackStatus(mode="CAUTION", risk_bias=0.5)
            
            size_caution = self.risk_manager.get_adjusted_size(1000.0)
            self.assertAlmostEqual(size_caution, 500.0)

if __name__ == '__main__':
    unittest.main()
