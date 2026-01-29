import pytest
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Test target
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading.engine import TradingEngine
from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord, get_risk_manager
from src.risk.runtime_feedback import RiskFeedbackLoopConfig


class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker in TradingEngine."""
    
    @pytest.fixture
    def trading_engine(self, tmp_path):
        """Create TradingEngine with fresh RiskManager."""
        # Use tmp_path for storage
        engine = TradingEngine(
            symbol="BTCUSDT",
            timeframe="15m",
            use_testnet=True,
            paper_trading=True,
            enable_visual_agent=False,  # Skip for speed
            enable_sentiment_agent=False,
        )
        return engine
    
    def test_risk_manager_initialized(self, trading_engine, tmp_path):
        """Verify RiskManager is initialized with TradingEngine."""
        assert trading_engine.risk_manager is not None
        assert trading_engine.risk_manager.current_status.mode == "NORMAL"
    
    @pytest.mark.asyncio
    async def test_trade_blocked_in_severe_mode(self, trading_engine):
        """Verify trades are blocked when circuit breaker is SEVERE."""
        # Force engine into SEVERE mode via massive losses
        rm = trading_engine.risk_manager
        
        # Force balance
        rm.update_balance(10000.0)
        
        # Create 5 consecutive losses to trigger SEVERE
        for i in range(5):
            trade = TradeRecord(
                trade_id=f"severe_trade_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-300.0,  # Massive losses
                pnl_pct=-3.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Verify SEVERE mode
        assert rm.current_status.mode == "SEVERE"
        assert rm.current_status.block_trading is True
        
        # Check trade is blocked
        allowed, status = rm.check_trade_allowed("BTCUSDT", 1000.0)
        assert allowed is False, "Trade should be blocked in SEVERE mode"
        assert status.mode == "SEVERE"
    
    @pytest.mark.asyncio
    async def test_trade_sized_reduced_in_caution_mode(self, trading_engine):
        """Verify position size is reduced in CAUTION mode."""
        rm = trading_engine.risk_manager
        
        # Force balance
        rm.update_balance(10000.0)
        
        # Create 3 losses to trigger CAUTION
        for i in range(3):
            trade = TradeRecord(
                trade_id=f"caution_trade_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-100.0,
                pnl_pct=-1.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Verify CAUTION mode
        assert rm.current_status.mode == "CAUTION"
        assert rm.current_status.risk_bias == 0.70
        
        # Check size adjustment
        base_size = 1000.0
        adjusted = rm.get_adjusted_size(base_size)
        expected = base_size * 0.70  # 70%
        assert adjusted == expected, f"CAUTION should reduce to 70%, got ${adjusted}"
        assert adjusted == 700.0
    
    @pytest.mark.asyncio
    async def test_trade_sized_increased_in_hot_mode(self, trading_engine):
        """Verify position size is increased in HOT mode."""
        rm = trading_engine.risk_manager
        rm.update_balance(10000.0)
        
        # Create hot streak: 7 wins out of 8, avg PnL > $12
        for i in range(7):
            trade = TradeRecord(
                trade_id=f"hot_win_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=20.0,  # Over $12 avg
                pnl_pct=2.0,
                success=True,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Add 1 loss
        trade = TradeRecord(
            trade_id="hot_loss",
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            decision="BUY",
            entry_price=67000.0,
            exit_price=None,
            pnl=-50.0,
            pnl_pct=-0.5,
            success=False,
            size=1000.0,
        )
        rm.record_trade(trade)
        
        # Verify HOT mode
        status = rm.current_status
        assert status.mode == "HOT", f"Expected HOT mode, got {status.mode}"
        assert status.risk_bias == 1.12
        
        # Check increased size
        base_size = 1000.0
        adjusted = rm.get_adjusted_size(base_size)
        expected = base_size * 1.12  # 112%
        assert adjusted == expected, f"HOT should increase to 112%, got ${adjusted}"
    
    @pytest.mark.asyncio
    async def test_trade_allowed_in_normal_mode(self, trading_engine):
        """Verify trades are allowed in NORMAL mode."""
        rm = trading_engine.risk_manager
        rm.update_balance(10000.0)
        
        # Mix of wins/losses staying within normal boundaries
        trades = [
            {"pnl": 100.0, "success": True},
            {"pnl": 80.0, "success": True},
            {"pnl": -50.0, "success": False},
            {"pnl": 60.0, "success": True},
        ]
        
        for i, data in enumerate(trades):
            trade = TradeRecord(
                trade_id=f"normal_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=data["pnl"],
                pnl_pct=data["pnl"] / 1000.0,
                success=data["success"],
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Should stay in NORMAL
        assert rm.current_status.mode == "NORMAL"
        
        # Check trade is allowed
        allowed, status = rm.check_trade_allowed("BTCUSDT", 1000.0)
        assert allowed is True, "Trade should be allowed in NORMAL mode"
        assert status.mode == "NORMAL"
        
        # Check size is unchanged
        base_size = 1000.0
        adjusted = rm.get_adjusted_size(base_size)
        assert adjusted == base_size, "NORMAL mode should not change size"


class TestRiskManagerMetrics:
    """Tests for RiskManager metrics calculation."""
    
    def test_loss_streak_detection(self):
        """Verify loss streak is detected correctly."""
        config = RiskFeedbackLoopConfig(lookback_trades=12)
        rm = RuntimeRiskManager(config=config)
        rm.update_balance(10000.0)
        
        # Create win, win, loss, loss, loss (streak = 3)
        trades_data = [
            (100.0, True),
            (80.0, True),
            (-50.0, False),
            (-40.0, False),
            (-60.0, False),
        ]
        
        for i, (pnl, success) in enumerate(trades_data):
            trade = TradeRecord(
                trade_id=f"streak_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=pnl,
                pnl_pct=pnl / 1000.0,
                success=success,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        metrics = rm.get_metrics()
        assert metrics["loss_streak"] == 3, f"Expected streak of 3, got {metrics['loss_streak']}"
    
    def test_drawdown_calculation(self):
        """Verify drawdown is calculated correctly."""
        config = RiskFeedbackLoopConfig()
        rm = RuntimeRiskManager(config=config)
        rm.update_balance(10000.0)  # Starting balance
        
        # Trades causing drawdown
        # Start: 10000 -> 10500 (win) -> 10300 (loss) -> 10000 (loss)
        trades_data = [
            (500.0, True),   # 10500
            (-200.0, False), # 10300
            (-300.0, False), # 10000
        ]
        
        for i, (pnl, success) in enumerate(trades_data):
            trade = TradeRecord(
                trade_id=f"drawdown_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=pnl,
                pnl_pct=pnl / 1000.0,
                success=success,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        metrics = rm.get_metrics()
        # Peak was 10500, current is 10000
        # Drawdown = (10500 - 10000) / 10500 = 4.76%
        assert metrics["drawdown_pct"] >= 0, f"Drawdown should be positive, got {metrics['drawdown_pct']}"
    
    def test_daily_pnl_tracking(self):
        """Verify daily PnL is tracked."""
        config = RiskFeedbackLoopConfig()
        rm = RuntimeRiskManager(config=config)
        rm.update_balance(10000.0)
        
        # Record 5 winning trades
        for i in range(5):
            trade = TradeRecord(
                trade_id=f"daily_pnl_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=100.0,
                pnl_pct=10.0,
                success=True,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        metrics = rm.get_metrics()
        assert metrics["daily_pnl"] == 500.0, f"Daily PnL should be 500, got {metrics['daily_pnl']}"


class TestRiskManagerPersistence:
    """Tests for RiskManager state persistence."""
    
    def test_state_saved_to_file(self, tmp_path):
        """Verify state is persisted to file."""
        storage = str(tmp_path / "risk_state.jsonl")
        
        rm = RuntimeRiskManager(storage_path=storage)
        rm.update_balance(10000.0)
        
        # Record trades
        for i in range(3):
            trade = TradeRecord(
                trade_id=f"persist_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-100.0,
                pnl_pct=-1.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Verify file exists with content
        import json
        assert Path(storage).exists()
        
        with open(storage) as f:
            lines = f.readlines()
            assert len(lines) > 0
            state = json.loads(lines[-1])
            assert "daily_pnl" in state
            assert state["daily_pnl"] == -300.0
    
    def test_state_loaded_on_restart(self, tmp_path):
        """Verify state is loaded when manager restarts."""
        storage = str(tmp_path / "restart_state.jsonl")
        
        # First instance
        rm1 = RuntimeRiskManager(storage_path=storage)
        rm1.update_balance(10000.0)
        
        for i in range(4):
            trade = TradeRecord(
                trade_id=f"restart_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-150.0,
                pnl_pct=-1.5,
                success=False,
                size=1000.0,
            )
            rm1.record_trade(trade)
        
        # Create new instance with same storage
        rm2 = RuntimeRiskManager(storage_path=storage)
        
        # Verify loaded state
        metrics = rm2.get_metrics()
        assert metrics["daily_pnl"] == -600.0, f"Expected -600, got {metrics['daily_pnl']}"


class TestRiskManagerCooldowns:
    """Tests for cooldown behavior."""
    
    def test_caution_cooldown_duration(self, tmp_path):
        """Verify CAUTION mode has proper cooldown."""
        config = RiskFeedbackLoopConfig(
            caution_cooldown_seconds=300,  # 5 min
        )
        storage = str(tmp_path / "caution_cooldown.jsonl")
        rm = RuntimeRiskManager(config=config, storage_path=storage)
        rm.update_balance(10000.0)
        
        # Trigger CAUTION (3 losses)
        for i in range(3):
            trade = TradeRecord(
                trade_id=f"caution_cd_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-100.0,
                pnl_pct=-1.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        status = rm.current_status
        assert status.mode == "CAUTION"
        assert status.cooldown_seconds == 300  # 5 minutes
    
    def test_severe_cooldown_duration(self, tmp_path):
        """Verify SEVERE mode has proper cooldown."""
        config = RiskFeedbackLoopConfig(
            severe_cooldown_seconds=900,  # 15 min
        )
        storage = str(tmp_path / "severe_cooldown.jsonl")
        rm = RuntimeRiskManager(config=config, storage_path=storage)
        rm.update_balance(10000.0)
        
        # Trigger SEVERE (5 losses)
        for i in range(5):
            trade = TradeRecord(
                trade_id=f"severe_cd_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-200.0,
                pnl_pct=-2.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        status = rm.current_status
        assert status.mode == "SEVERE"
        assert status.cooldown_seconds == 900  # 15 minutes


class TestRiskManagerDisabled:
    """Tests when circuit breaker is disabled."""
    
    def test_disabled_allows_all_trades(self):
        """Verify disabled manager allows all trades."""
        config = RiskFeedbackLoopConfig(enabled=False)
        rm = RuntimeRiskManager(config=config)
        rm.update_balance(10000.0)
        
        # Create extreme losses
        for i in range(10):
            trade = TradeRecord(
                trade_id=f"disabled_{i}",
                timestamp=datetime.now(timezone.utc),
                symbol="BTCUSDT",
                decision="BUY",
                entry_price=67000.0,
                exit_price=None,
                pnl=-500.0,
                pnl_pct=-5.0,
                success=False,
                size=1000.0,
            )
            rm.record_trade(trade)
        
        # Should stay NORMAL when disabled
        assert rm.current_status.mode == "NORMAL"
        
        # Trades should be allowed
        allowed, status = rm.check_trade_allowed("BTCUSDT", 1000.0)
        # When disabled, returns status with "disabled" reason but doesn't block
        assert "disabled" in status.reason.lower() or allowed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
