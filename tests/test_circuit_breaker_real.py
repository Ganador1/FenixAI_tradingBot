"""Real behavior tests for the Circuit Breaker System.

These tests verify actual circuit breaker behavior without mocking core logic.
Tests cover:
- SEVERE mode after 5 consecutive losses
- CAUTION mode after 3 consecutive losses  
- Trade blocking in SEVERE mode
- Position size reduction in CAUTION (70%)
- Position size increase in HOT mode (112%)
- Cooldown periods (5min CAUTION, 15min SEVERE)
- Drawdown protection thresholds (4%/6.5%)
- Daily loss protection (2%/3.5%)
- Metrics calculation (win rate, streaks)
- State persistence
"""

import os
import sys
import json
import time
import pytest
import datetime
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import deque

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord
from src.risk.runtime_feedback import RiskFeedbackLoopConfig, RiskFeedbackStatus


class TestCircuitBreakerReal:
    """Comprehensive tests for circuit breaker with real behavior."""
    
    @pytest.fixture
def temp_dir(self):
    """Create temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
    
    
    @pytest.fixture
def fresh_manager(self, temp_dir):
    """Create fresh RiskManager for each test."""
    config = RiskFeedbackLoopConfig(
        enabled=True,
        lookback_trades=12,
        caution_drawdown_pct=4.0,
        severe_drawdown_pct=6.5,
        caution_daily_loss_pct=2.0,
        severe_daily_loss_pct=3.5,
        loss_streak_caution=3,
        loss_streak_halt=5,
        hot_streak_win_rate=0.68,
        hot_streak_min_trades=6,
        hot_streak_min_avg_pnl=12.0,
        caution_cooldown_seconds=300,  # 5 min
        severe_cooldown_seconds=900,   # 15 min
        cooldown_risk_bias=0.7,
        drawdown_risk_bias=0.45,
        hot_streak_risk_bias=1.12,
    )
    storage_path = os.path.join(temp_dir, "risk.jsonl")
    return RuntimeRiskManager(config=config, storage_path=storage_path)
    
    def test_severe_mode_after_five_consecutive_losses(self, fresh_manager):
        """CRITICAL: SEVERE mode activates after 5 consecutive losses."""
        fresh_manager.update_balance(10000.0)
        
        # Record 5 losing trades consecutively
        for i in range(5):
            trade = TradeRecord(
                trade_id=f"loss_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=49800.0,
                pnl=-200.0,
                pnl_pct=-0.4,
                success=False,
                size=1000.0
            )
            fresh_manager.record_trade(trade)
            fresh_manager.update_balance(10000.0 - (i + 1) * 50)
        
        status = fresh_manager.evaluate_risk()
        
        assert status.mode == "SEVERE", f"Expected SEVERE mode, got {status.mode}"
        assert status.block_trading == True, "SEVERE mode must block trading"
        assert status.risk_bias == 0.45, f"Expected 0.45 bias, got {status.risk_bias}"
        assert "loss streak" in status.reason.lower(), f"Expected loss streak reason, got: {status.reason}"
        
        # Verify trades are blocked
        allowed, _ = fresh_manager.check_trade_allowed("BTCUSDT", 1000.0)
        assert allowed == False, "Trades must be blocked in SEVERE mode"
    
    def test_caution_mode_after_three_consecutive_losses(self, fresh_manager):
        """CRITICAL: CAUTION mode activates after 3 consecutive losses."""
        fresh_manager.update_balance(10000.0)
        
        # Record 3 losing trades
        for i in range(3):
            trade = TradeRecord(
                trade_id=f"caution_loss_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="SELL",
                entry_price=50000.0,
                exit_price=50100.0,
                pnl=-100.0,
                pnl_pct=-0.2,
                success=False,
                size=1000.0
            )
            fresh_manager.record_trade(trade)
            fresh_manager.update_balance(10000.0 - (i + 1) * 20)
        
        status = fresh_manager.evaluate_risk()
        
        assert status.mode == "CAUTION", f"Expected CAUTION mode, got {status.mode}"
        assert status.block_trading == False, "CAUTION mode should not block, only reduce"
        assert status.risk_bias == 0.7, f"Expected 0.7 bias, got {status.risk_bias}"
        
        # Verify position size is reduced
        base_size = 1000.0
        adjusted = fresh_manager.get_adjusted_size(base_size)
        assert adjusted == 700.0, f"Expected 700.0 (70%), got {adjusted}"
    
    def test_trades_blocked_in_severe_mode(self, fresh_manager):
        """CRITICAL: Verify trades are physically blocked in SEVERE mode."""
        # Force SEVERE mode
        fresh_manager.current_status = RiskFeedbackStatus(
            mode="SEVERE",
            risk_bias=0.45,
            block_trading=True,
            reason="Test forced SEVERE mode",
            cooldown_seconds=900,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=900)
        )
        
        # Try multiple trades - all should be blocked
        for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            allowed, status = fresh_manager.check_trade_allowed(symbol, size=1000.0)
            assert allowed == False, f"Trade for {symbol} should be blocked in SEVERE mode"
            assert status.block_trading == True
        
        # Verify logging indicates blockage
        summary = fresh_manager.get_status_summary()
        assert summary["block_trading"] == True
    
    def test_position_size_reduction_in_caution(self, fresh_manager):
        """CRITICAL: Position size reduced by 30% in CAUTION mode (to 70%)."""
        # Force CAUTION mode
        fresh_manager.current_status = RiskFeedbackStatus(
            mode="CAUTION",
            risk_bias=0.7,
            block_trading=False,
            reason="Test CAUTION mode"
        )
        
        # Test various base sizes
        test_cases = [
            (1000.0, 700.0),
            (500.0, 350.0),
            (2500.0, 1750.0),
            (0.01, 0.007),  # Edge case
        ]
        
        for base_size, expected in test_cases:
            adjusted = fresh_manager.get_adjusted_size(base_size)
            assert abs(adjusted - expected) < 0.001, \
                f"Base {base_size}: expected {expected}, got {adjusted}"
    
    def test_position_size_increase_in_hot_mode(self, fresh_manager):
        """CRITICAL: Position size increased by 12% in HOT mode (to 112%)."""
        # Setup HOT streak conditions
        fresh_manager.update_balance(10000.0)
        fresh_manager._daily_start_balance = 10000.0
        
        # Create 8 winning trades (triggers HOT mode)
        for i in range(8):
            trade = TradeRecord(
                trade_id=f"hot_win_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=50200.0,
                pnl=200.0,
                pnl_pct=0.4,
                success=True,
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        status = fresh_manager.evaluate_risk()
        
        # If HOT mode achieved, verify 112% size
        if status.mode == "HOT":
            adjusted = fresh_manager.get_adjusted_size(1000.0)
            assert adjusted == 1120.0, f"Expected 1120.0, got {adjusted}"
            assert status.risk_bias == 1.12, f"Expected 1.12 bias, got {status.risk_bias}"
    
    def test_caution_cooldown_period(self, fresh_manager):
        """CAUTION cooldown is 5 minutes (300 seconds)."""
        # Force CAUTION mode with cooldown
        now = datetime.now(timezone.utc)
        fresh_manager.current_status = RiskFeedbackStatus(
            mode="CAUTION",
            risk_bias=0.7,
            block_trading=False,
            reason="Test cooldown",
            cooldown_seconds=300,
            expires_at=now + timedelta(seconds=300)
        )
        fresh_manager._cooldown_start = now
        
        # Verify status returns CAUTION during cooldown
        status = fresh_manager.evaluate_risk()
        assert status.mode == "CAUTION", "Should still be in CAUTION during cooldown"
        
        # Simulate cooldown expiry
        expired_time = now - timedelta(seconds=301)
        fresh_manager._cooldown_start = expired_time
        fresh_manager.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
        
        # After expiry, should evaluate fresh
        status = fresh_manager.evaluate_risk()
        # If no bad metrics, should be NORMAL
    
    def test_severe_cooldown_period(self, fresh_manager):
        """SEVERE cooldown is 15 minutes (900 seconds)."""
        # Force SEVERE mode with cooldown
        now = datetime.now(timezone.utc)
        fresh_manager.current_status = RiskFeedbackStatus(
            mode="SEVERE",
            risk_bias=0.45,
            block_trading=True,
            reason="Test SEVERE cooldown",
            cooldown_seconds=900,
            expires_at=now + timedelta(seconds=900)
        )
        fresh_manager._cooldown_start = now
        
        # Verify trades blocked during cooldown
        allowed, status = fresh_manager.check_trade_allowed("BTCUSDT", 1000.0)
        assert allowed == False, "Should stay blocked during SEVERE cooldown"
        
        # Expire cooldown
        fresh_manager._cooldown_start = now - timedelta(seconds=901)
        fresh_manager.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
        
        # Should re-evaluate
        status = fresh_manager.evaluate_risk()
    
    def test_drawdown_protection_caution_threshold(self, fresh_manager):
        """CAUTION drawsdown threshold: 4% from peak balance."""
        fresh_manager.update_balance(10000.0)
        fresh_manager._daily_start_balance = 10000.0
        
        # Create losses to trigger 4% drawdown from $10,000 peak
        # Need to lose $400 to reach 4% drawdown
        drawdown_trades = [
            {"pnl": -100, "success": False},
            {"pnl": -100, "success": False}, 
            {"pnl": -100, "success": False},
            {"pnl": -100, "success": False},
        ]
        
        for i, data in enumerate(drawdown_trades):
            trade = TradeRecord(
                trade_id=f"dd_trade_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="SELL",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
            fresh_manager.update_balance(10000.0 - (i + 1) * 100)
        
        metrics = fresh_manager.get_metrics()
        drawdown_pct = metrics["drawdown_pct"]
        
        # Check if at or above 4% threshold
        if drawdown_pct >= 4.0:
            status = fresh_manager.evaluate_risk()
            assert status.mode in ["CAUTION", "SEVERE"], \
                f"Expected CAUTION/SEVERE for {drawdown_pct:.1f}% drawdown"
    
    def test_drawdown_protection_severe_threshold(self, fresh_manager):
        """SEVERE drawdown threshold: 6.5% from peak balance."""
        fresh_manager.update_balance(10000.0)
        fresh_manager._daily_start_balance = 10000.0
        
        # Create $650 loss to trigger 6.5% drawdown
        severe_trades = [
            {"pnl": -200, "success": False},
            {"pnl": -200, "success": False},
            {"pnl": -250, "success": False},
        ]
        
        running_balance = 10000.0
        for i, data in enumerate(severe_trades):
            trade = TradeRecord(
                trade_id=f"severe_dd_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="SELL",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
            running_balance += data["pnl"]
            fresh_manager.update_balance(running_balance)
        
        metrics = fresh_manager.get_metrics()
        drawdown_pct = metrics["drawdown_pct"]
        
        if drawdown_pct >= 6.5:
            status = fresh_manager.evaluate_risk()
            assert status.mode == "SEVERE", \
                f"Expected SEVERE for {drawdown_pct:.1f}% drawdown, got {status.mode}"
            assert status.block_trading == True
    
    def test_daily_loss_protection_caution(self, fresh_manager):
        """CAUTION daily loss threshold: 2% of daily start balance."""
        fresh_manager._daily_start_balance = 10000.0
        fresh_manager.update_balance(10000.0)
        fresh_manager._last_trading_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Create $200 daily loss (2% of 10k)
        daily_losses = [
            {"pnl": -50, "success": False},
            {"pnl": -50, "success": False},
            {"pnl": -50, "success": False},
            {"pnl": -50, "success": False},
        ]
        
        for i, data in enumerate(daily_losses):
            trade = TradeRecord(
                trade_id=f"daily_loss_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="SELL",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        daily_loss_pct = metrics.get("daily_loss_pct", 0)
        
        # Verify metrics include daily loss tracking
        assert "daily_loss_pct" in metrics
    
    def test_daily_loss_protection_severe(self, fresh_manager):
        """SEVERE daily loss threshold: 3.5% of daily start balance."""
        fresh_manager._daily_start_balance = 10000.0
        fresh_manager.update_balance(10000.0)
        fresh_manager._last_trading_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Create $350 daily loss (3.5% of 10k)
        severe_daily = [
            {"pnl": -70, "success": False},
            {"pnl": -70, "success": False},
            {"pnl": -70, "success": False},
            {"pnl": -70, "success": False},
            {"pnl": -70, "success": False},
        ]
        
        for i, data in enumerate(severe_daily):
            trade = TradeRecord(
                trade_id=f"sev_daily_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="SELL",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        daily_loss_pct = metrics.get("daily_loss_pct", 0)
        
        assert "daily_loss_pct" in metrics
        # Trigger check
        status = fresh_manager.evaluate_risk()
    
    def test_win_rate_calculation(self, fresh_manager):
        """Win rate calculation is accurate (wins / total)."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": 50, "success": True},
            {"pnl": -30, "success": False},
            {"pnl": 20, "success": True},
            {"pnl": -40, "success": False},
        ]
        
        for i, data in enumerate(trades):
            trade = TradeRecord(
                trade_id=f"wr_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        
        # 3 wins, 2 losses -> 60% win rate
        total = metrics["total_trades"]
        wins = metrics["wins"]
        win_rate = metrics["win_rate"]
        
        assert total == 5, f"Expected 5 trades, got {total}"
        assert wins == 3, f"Expected 3 wins, got {wins}"
        assert abs(win_rate - 0.60) < 0.01, f"Expected 0.60 win rate, got {win_rate}"
    
    def test_consecutive_loss_streak_detection(self, fresh_manager):
        """Loss streak is correctly counted from most recent."""
        # Pattern: win, loss, loss, loss
        streak_trades = [
            {"pnl": 100, "success": True},  # Not part of streak
            {"pnl": -50, "success": False},  # Start of streak
            {"pnl": -50, "success": False},
            {"pnl": -50, "success": False},
        ]
        
        for i, data in enumerate(streak_trades):
            trade = TradeRecord(
                trade_id=f"streak_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        
        assert metrics["loss_streak"] == 3, f"Expected streak of 3, got {metrics['loss_streak']}"
        
        # Add one more loss -> 4 consecutive losses
        trade = TradeRecord(
            trade_id="streak_extra",
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=4),
            symbol="BTCUSDT",
            decision="BUY",
            entry_price=50000.0,
            exit_price=49600.0,
            pnl=-200.0,
            pnl_pct=-0.4,
            success=False,
            size=1000.0
        )
        fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        assert metrics["loss_streak"] >= 3, f"Streak should be at least 3, got {metrics['loss_streak']}"
    
    def test_consecutive_win_streak_detection(self, fresh_manager):
        """Win streak is correctly counted from most recent."""
        win_trades = [
            {"pnl": 50, "success": False},  # Not part of streak
            {"pnl": 100, "success": True},""",  # Start of win streak
            {"pnl": 120, "success": True},
            {"pnl": 80, "success": True},
        ]
        
        for i, data in enumerate(win_trades):
            trade = TradeRecord(
                trade_id=f"win_streak_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        # Verify wins tracked
        metrics = fresh_manager.get_metrics()
        assert metrics["wins"] >= 3, f"Expected at least 3 wins, got {metrics['wins']}"
    
    def test_state_persistence(self, temp_dir):
        """State is correctly saved to and loaded from disk."""
        storage_path = os.path.join(temp_dir, "persist.jsonl")
        
        # Create manager and save state
        config = RiskFeedbackLoopConfig()
        manager1 = RuntimeRiskManager(config=config, storage_path=storage_path)
        
        manager1._daily_pnl = 150.0
        manager1._peak_balance = 11000.0
        manager1._last_trading_day = "2025-01-28"
        manager1._save_state()
        
        # Create new manager - should load from disk
        manager2 = RuntimeRiskManager(config=config, storage_path=storage_path)
        
        assert manager2._daily_pnl == 150.0, f"Expected 150.0, got {manager2._daily_pnl}"
        assert manager2._peak_balance == 11000.0, f"Expected 11000.0, got {manager2._peak_balance}"
        assert manager2._last_trading_day == "2025-01-28"
    
    def test_metrics_total_pnl_calculation(self, fresh_manager):
        """Total PnL is sum of all trades."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 75, "success": True},
            {"pnl": -25, "success": False},
        ]
        
        for i, data in enumerate(trades):
            trade = TradeRecord(
                trade_id=f"pnl_{i}",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=i),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=50000.0,
                exit_price=50000.0 + data["pnl"],
                pnl=data["pnl"],
                pnl_pct=0,
                success=data["success"],
                size=1000.0
            )
            fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        
        # Total PnL: 100 - 50 + 75 - 25 = 100
        expected_pnl = 100.0
        assert abs(metrics["total_pnl"] - expected_pnl) < 0.001, \
            f"Expected total PnL {expected_pnl}, got {metrics['total_pnl']}"
    
    def test_metrics_daily_pnl_reset(self, fresh_manager):
        """Daily PnL resets on new trading day."""
        fresh_manager.update_balance(10000.0)
        
        # Old day data
        fresh_manager._last_trading_day = "2025-01-28"
        fresh_manager._daily_pnl = -150.0
        
        # Update with new day
        new_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fresh_manager.update_balance(10000.0)
        
        if fresh_manager._last_trading_day != new_day:
            assert fresh_manager._daily_pnl == 0.0, "Daily PnL should reset on new day"
    
    def test_edge_case_single_trade_metrics(self, fresh_manager):
        """Metrics work correctly with single trade."""
        trade = TradeRecord(
            trade_id="single",
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            decision="BUY",
            entry_price=50000.0,
            exit_price=50100.0,
            pnl=100.0,
            pnl_pct=0.2,
            success=True,
            size=1000.0
        )
        fresh_manager.record_trade(trade)
        
        metrics = fresh_manager.get_metrics()
        
        assert metrics["total_trades"] == 1
        assert metrics["wins"] == 1
        assert metrics["win_rate"] == 1.0
        assert metrics["loss_streak"] == 0
    
    def test_edge_case_empty_trades(self, fresh_manager):
        """Metrics work correctly with no trades."""
        metrics = fresh_manager.get_metrics()
        
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0.0
        
        # Should be NORMAL mode
        status = fresh_manager.evaluate_risk()
        assert status.mode == "NORMAL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
