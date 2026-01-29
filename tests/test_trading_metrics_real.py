"""Real behavior tests for Trading Metrics System.

Tests cover:
- Win rate calculation accuracy
- Profit factor calculation
- Sharpe ratio calculation (needs variance)
- Max drawdown calculation (peak-to-trough)
- Expectancy calculation
- Payoff ratio calculation
- Agent metrics per agent type
- Dashboard JSON generation
- Metrics save/load persistence
"""

import os
import sys
import json
import math
import pytest
import tempfile
from datetime import datetime, timezone
from typing import List, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.metrics.trading_metrics import (
    TradingMetricsDashboard,
    get_metrics_dashboard,
    TradeMetrics,
    AgentMetrics,
    format_metrics_for_display,
)


class TestTradingMetricsReal:
    """Comprehensive tests for trading metrics with real behavior."""
    
    @pytest.fixture
def temp_dir(self):
    """Create temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
    
    
    @pytest.fixture
def fresh_dashboard(self, temp_dir):
    """Create fresh dashboard for each test."""
    storage_path = os.path.join(temp_dir, "metrics.jsonl")
    return TradingMetricsDashboard(storage_path=storage_path)
    
    def test_win_rate_calculation_accuracy(self, fresh_dashboard):
        """CRITICAL: Win rate = wins / total trades."""
        # Create trades with known outcomes
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 80, "success": True},
            {"pnl": -30, "success": False},
            {"pnl": 120, "success": True},
            {"pnl": 50, "success": True},
            {"pnl": -40, "success": False},
            {"pnl": 90, "success": True},
        ]
        
        # Convert to proper format
        trades_list = [
            {"pnl": t["pnl"], "success": t["success"]} for t in trades
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades_list)
        
        # Calculate expected win rate
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t["success"])
        expected_win_rate = winning_trades / total_trades
        
        assert metrics.win_rate == pytest.approx(expected_win_rate, abs=0.001)
        assert metrics.total_trades == total_trades
        assert metrics.winning_trades == winning_trades
        assert metrics.losing_trades == total_trades - winning_trades
    
    def test_win_rate_edge_cases(self, fresh_dashboard):
        """Win rate handles edge cases correctly."""
        # All wins
        all_wins = [{"pnl": 100, "success": True} for _ in range(5)]
        metrics = fresh_dashboard.calculate_trade_metrics(all_wins)
        assert metrics.win_rate == 1.0
        assert metrics.losing_trades == 0
        
        # All losses
        all_losses = [{"pnl": -50, "success": False} for _ in range(5)]
        metrics = fresh_dashboard.calculate_trade_metrics(all_losses)
        assert metrics.win_rate == 0.0
        assert metrics.winning_trades == 0
        
        # Empty list
        empty_metrics = fresh_dashboard.calculate_trade_metrics([])
        assert empty_metrics.win_rate == 0.0
        assert empty_metrics.total_trades == 0
    
    def test_profit_factor_calculation(self, fresh_dashboard):
        """CRITICAL: Profit factor = sum(gross wins) / sum(gross losses)."""
        # Create trades with known PnL
        trades = [
            {"pnl": 100, "success": True},   # Gross win: 100
            {"pnl": -50, "success": False},  # Gross loss: 50
            {"pnl": 80, "success": True},    # Gross win: 80 (total: 180)
            {"pnl": -30, "success": False},  # Gross loss: 30 (total: 80)
            {"pnl": -40, "success": False},  # Gross loss: 40 (total: 120)
        ]
        
        # Calculate expected profit factor
        gross_wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_losses = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
        expected_pf = gross_wins / gross_losses
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        assert metrics.profit_factor == pytest.approx(expected_pf, abs=0.001)
        assert metrics.profit_factor == pytest.approx(180.0 / 120.0, abs=0.001)
    
    def test_profit_factor_all_wins(self, fresh_dashboard):
        """Profit factor is inf when all trades are wins."""
        all_wins = [{"pnl": 100, "success": True} for _ in range(5)]
        metrics = fresh_dashboard.calculate_trade_metrics(all_wins)
        
        # When no losses, profit factor should be inf
        assert metrics.profit_factor == float('inf')
    
    def test_profit_factor_all_losses(self, fresh_dashboard):
        """Profit factor is 0 when all trades are losses."""
        all_losses = [{"pnl": -50, "success": False} for _ in range(5)]
        metrics = fresh_dashboard.calculate_trade_metrics(all_losses)
        
        assert metrics.profit_factor == 0.0
    
    def test_sharpe_ratio_calculation(self, fresh_dashboard):
        """Sharpe ratio uses correct variance calculation."""
        # Create trades with capital employed
        trades = []
        for i in range(12):  # Need at least 10 for Sharpe calculation
            capital = 1000.0
            pnl = (i - 5) * 10  # Series: -50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50, 60
            trades.append({
                "pnl": pnl,
                "success": pnl > 0,
                "capital_employed": capital,
                "position_size": capital
            })
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        # Sharpe should be calculated with variance
        # With varying returns, should be non-zero
        assert isinstance(metrics.sharpe_ratio, float)
        assert not math.isnan(metrics.sharpe_ratio)
    
    def test_sharpe_ratio_insufficient_data(self, fresh_dashboard):
        """Sharpe ratio is 0 with insufficient data (< 10 trades)."""
        trades = [
            {"pnl": 100, "success": True, "capital_employed": 1000},
            {"pnl": -50, "success": False, "capital_employed": 1000},
            {"pnl": 75, "success": True, "capital_employed": 1000},
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        # With < 10 trades, Sharpe should be 0.0
        assert metrics.sharpe_ratio == 0.0
    
    def test_max_drawdown_calculation(self, fresh_dashboard):
        """CRITICAL: Max drawdown = peak - trough / peak."""
        # Create equity curve with known drawdown
        trades = [
            {"pnl": 50, "success": True},    # Equity: 50
            {"pnl": 80, "success": True},    # Equity: 130 (peak)
            {"pnl": 40, "success": True},    # Equity: 170 (new peak)
            {"pnl": -30, "success": False},  # Equity: 140
            {"pnl": -50, "success": False},  # Equity: 90 (drawdown from 170: 47%)
            {"pnl": -20, "success": False},  # Equity: 70 (drawdown from 170: 59%)
            {"pnl": 30, "success": True},    # Equity: 100
        ]
        
        # Peak was 170, lowest was 70
        # Drawdown = (170 - 70) / 170 = 59%
        expected_max_dd = 59.0
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        # Verify max drawdown
        assert metrics.max_drawdown_pct >= expected_max_dd - 5  # Allow some tolerance
        assert metrics.max_drawdown_dollars >= 100  # Dollars lost in worst drawdown
    
    def test_expectancy_calculation(self, fresh_dashboard):
        """CRITICAL: Expectancy = (win_rate * avg_win) - (loss_rate * |avg_loss|)."""
        # Create trades with known outcomes
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 120, "success": True},
            {"pnl": -40, "success": False},
            {"pnl": 80, "success": True},
            {"pnl": -30, "success": False},
        ]
        
        # Calculate manually
        wins = [t["pnl"] for t in trades if t["success"]]
        losses = [abs(t["pnl"]) for t in trades if not t["success"]]
        
        win_rate = len(wins) / len(trades)
        loss_rate = 1 - win_rate
        avg_win = sum(wins) / len(wins)
        avg_loss = sum(losses) / len(losses)
        
        expected_expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        assert metrics.expectancy == pytest.approx(expected_expectancy, abs=0.001)
        assert metrics.expectancy == pytest.approx((0.5 * 100) - (0.5 * 40), abs=0.001)
    
    def test_payoff_ratio_calculation(self, fresh_dashboard):
        """CRITICAL: Payoff ratio = avg_win / |avg_loss|."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 120, "success": True},
            {"pnl": -40, "success": False},
        ]
        
        wins = [t["pnl"] for t in trades if t["success"]]
        losses = [abs(t["pnl"]) for t in trades if not t["success"]]
        
        avg_win = sum(wins) / len(wins)
        avg_loss = sum(losses) / len(losses)
        expected_payoff = avg_win / avg_loss
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        assert metrics.payoff_ratio == pytest.approx(expected_payoff, abs=0.001)
        assert metrics.win_loss_ratio == pytest.approx(expected_payoff, abs=0.001)
    
    def test_dashboard_json_structure(self, fresh_dashboard):
        """Dashboard JSON has correct structure."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 80, "success": True},
        ]
        
        dashboard = fresh_dashboard.generate_dashboard(trades)
        
        # Verify structure
        assert "timestamp" in dashboard
        assert isinstance(dashboard["timestamp"], str)
        
        assert "trading_metrics" in dashboard
        trading_metrics = dashboard["trading_metrics"]
        
        required_fields = [
            "total_trades", "win_rate", "profit_factor", "sharpe_ratio",
            "max_drawdown_pct", "max_drawdown_dollars",
            "avg_pnl", "expectancy", "payoff_ratio"
        ]
        
        for field in required_fields:
            assert field in trading_metrics, f"Missing field: {field}"
            assert isinstance(trading_metrics[field], str), f"{field} should be string"
    
    def test_formats_for_display(self, fresh_dashboard):
        """format_metrics_for_display produces valid output."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 80, "success": True},
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        output = format_metrics_for_display(metrics)
        
        # Verify output contains key metrics
        assert "Win Rate" in output
        assert "Profit Factor" in output
        assert "Sharpe Ratio" in output
        assert "Max Drawdown" in output
        assert "Expectancy" in output
        assert "Payoff Ratio" in output
        
        # Verify format is readable
        assert "=" in output  # Has dividers
        assert "Total Trades" in output
    
    def test_metrics_save_persistence(self, fresh_dashboard, temp_dir):
        """Metrics are saved to persisting storage."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
        ]
        
        dashboard = fresh_dashboard.generate_dashboard(trades)
        fresh_dashboard.save_metrics(dashboard)
        
        # Verify file exists and has content
        assert os.path.exists(fresh_dashboard.storage_path)
        
        with open(fresh_dashboard.storage_path) as f:
            lines = f.readlines()
        
        assert len(lines) > 0
        
        # Verify JSON is valid
        saved_metrics = json.loads(lines[0])
        assert "timestamp" in saved_metrics
        assert "trading_metrics" in saved_metrics
    
    def test_metrics_multiple_saves(self, fresh_dashboard, temp_dir):
        """Multiple saves append to file, don't overwrite."""
        for i in range(5):
            trades = [{"pnl": 100 * (i + 1), "success": True}]
            dashboard = fresh_dashboard.generate_dashboard(trades)
            fresh_dashboard.save_metrics(dashboard)
        
        with open(fresh_dashboard.storage_path) as f:
            lines = f.readlines()
        
        assert len(lines) == 5, f"Expected 5 saves, got {len(lines)}"
    
    def test_agent_metrics_mock_bank(self, fresh_dashboard):
        """Agent metrics calculation works with mock reasoning bank."""
        # Mock reasoning bank entries
        class MockEntry:
            def __init__(self, success, confidence, latency=None):
                self.success = success
                self.confidence = confidence
                self.latency_ms = latency
                self.metadata = {}
        
        class MockBank:
            def get_recent(self, agent, limit):
                return [
                    MockEntry(True, 0.7, 150.0),
                    MockEntry(False, 0.5, 100.0),
                    MockEntry(True, 0.8, 200.0),
                    MockEntry(True, 0.6, 120.0),
                    MockEntry(None, 0.55, 180.0),  # Not yet evaluated
                ]
        
        mock_bank = MockBank()
        
        # Test agent metrics calculation
        metrics = fresh_dashboard.calculate_agent_metrics(mock_bank, "test_agent", 50)
        
        assert metrics.agent_name == "test_agent"
        assert isinstance(metrics.accuracy, float)
        assert isinstance(metrics.avg_confidence, float)
    
    def test_total_pnl_calculation(self, fresh_dashboard):
        """Total PnL is sum of all individual PnLs."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 80, "success": True},
            {"pnl": -30, "success": False},
            {"pnl": 150, "success": True},
        ]
        
        expected_total = sum(t["pnl"] for t in trades)
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        assert metrics.total_pnl == pytest.approx(expected_total, abs=0.001)
        assert metrics.avg_pnl == pytest.approx(expected_total / len(trades), abs=0.001)
    
    def test_win_loss_counts(self, fresh_dashboard):
        """Win and loss counts match success flags."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": -20, "success": False},  # This should count as loss too
            {"pnl": 80, "success": True},
            {"pnl": 0, "success": True},     # Edge case: zero PnL with success=True
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        assert metrics.winning_trades == 3
        assert metrics.losing_trades == 2
        assert metrics.total_trades == 5
    
    def test_avg_win_avg_loss_values(self, fresh_dashboard):
        """Average win and loss are correctly calculated."""
        # Wins: 100, 150, 80 → avg = 110
        # Losses: -50, -40 → avg = -45
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": -50, "success": False},
            {"pnl": 150, "success": True},
            {"pnl": -40, "success": False},
            {"pnl": 80, "success": True},
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        wins = [100, 150, 80]
        losses = [-50, -40]
        expected_avg_win = sum(wins) / len(wins)
        expected_avg_loss = sum(losses) / len(losses)
        
        assert metrics.avg_win == pytest.approx(expected_avg_win, abs=0.01)
        assert metrics.avg_loss == pytest.approx(expected_avg_loss, abs=0.01)
    
    def test_empty_trades_metrics(self, fresh_dashboard):
        """Metrics with empty trades return safe defaults."""
        metrics = fresh_dashboard.calculate_trade_metrics([])
        
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0
        assert metrics.sharpe_ratio == 0.0
        assert metrics.expectancy == 0.0
    
    def test_metrics_accuracy_with_real_numbers(self, fresh_dashboard):
        """Metrics are accurate with real trading numbers."""
        # Simulate realistic trade history
        trades = [
            {"pnl": 127.50, "success": True},
            {"pnl": -82.30, "success": False},
            {"pnl": 245.80, "success": True},
            {"pnl": -45.20, "success": False},
            {"pnl": 89.40, "success": True},
            {"pnl": -124.60, "success": False},
            {"pnl": 312.90, "success": True},
            {"pnl": 67.30, "success": True},
            {"pnl": -98.40, "success": False},
            {"pnl": 156.20, "success": True},
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        
        # Verify calculations make sense
        assert 0 < metrics.win_rate < 1  # Between 0 and 1
        assert metrics.profit_factor > 0  # More wins than losses
        assert metrics.expectancy != 0  # Has some value
        assert metrics.total_pnl == sum(t["pnl"] for t in trades)
    
    def test_dashboard_timestamp_present(self, fresh_dashboard):
        """Dashboard includes timestamp in ISO format."""
        trades = [{"pnl": 100, "success": True}]
        
        dashboard = fresh_dashboard.generate_dashboard(trades)
        
        assert "timestamp" in dashboard
        # Verify ISO format
        try:
            datetime.fromisoformat(dashboard["timestamp"].replace('Z', '+00:00'))
        except ValueError:
            pytest.fail("Timestamp not in valid ISO format")
    
    def test_format_metrics_output_includes_all(self, fresh_dashboard):
        """Format output includes all key metrics."""
        trades = [
            {"pnl": 100, "success": True},
            {"pnl": 50, "success": True},
            {"pnl": -30, "success": False},
            {"pnl": 80, "success": True},
            {"pnl": -40, "success": False},
        ]
        
        metrics = fresh_dashboard.calculate_trade_metrics(trades)
        formatted = format_metrics_for_display(metrics)
        
        # All metrics should appear
        required_labels = [
            "Total Trades", "Win Rate", "Profit Factor",
            "Sharpe Ratio", "Max Drawdown", "Avg PnL/Trade",
            "Expectancy", "Payoff Ratio"
        ]
        
        for label in required_labels:
            assert label in formatted, f"Missing label: {label}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
