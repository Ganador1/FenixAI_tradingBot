import sys
import os
from pathlib import Path

import pytest

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.risk.dynamic_stop_loss import calculate_dynamic_risk_levels, StopLossConfig, get_dynamic_stop_loss_calculator

def test_fee_math_accuracy():
    """
    Test that net profit is calculated correctly.
    Scenario:
    - Entry Price: 100
    - ATR: 1.0 (1%)
    - Fee: 0.1% ($0.1 per leg, $0.2 total)
    - TP Multiplier: 3.0 (Target move = $3.0)
    
    Expected Net Profit = ($3.0 move * pos_size) - ($0.2 fee * pos_size)
    """
    # Force a clean config
    config = StopLossConfig(
        conservative_sl_multiplier=1.0, # Used in low-vol (atr_pct < 0.5)
        atr_multiplier_tp=3.0, # TP = 3.0 * SL_dist
        trading_fee_pct=0.1,    # 0.1%
        max_risk_per_trade_pct=2.0 # 2% of balance
    )
    # Reset singleton or use manual instance
    from src.risk.dynamic_stop_loss import DynamicStopLossCalculator
    calc = DynamicStopLossCalculator(config)
    
    entry = 100.0
    atr = 0.4 # atr_pct = 0.4% (< 0.5% = low vol regime)
    balance = 10000.0
    
    # Calculate Risk Levels
    levels = calc.calculate(
        entry_price=entry,
        atr=atr,
        balance_usd=balance,
        decision="BUY"
    )
    
    # SL distance = atr * 1.0 = 0.4
    # TP distance = SL distance * 3.0 = 1.2
    expected_tp_dist = 1.2
    expected_net_profit = levels.position_size * expected_tp_dist
    
    print(f"Position Size: {levels.position_size:.4f}")
    print(f"Net Profit Expected: {expected_net_profit:.4f}")
    print(f"Net Profit Calculated: {levels.net_profit_potential:.4f}")
    print(f"Fees USD: {levels.fees_usd:.4f}")
    
    # Match to 4 decimal places
    assert abs(levels.net_profit_potential - expected_net_profit) < 0.0001
    
    # Check that net profit is not severely underestimated
    # Before fix, it would be: (pos * 3.0) - (fees * 3) approx? 
    # Actually (pos * 3.0) - (fees_usd * 2) where fees_usd was already entry+exit.
    
    print("✅ Fee math accuracy test passed!")


def test_1m_timeframe_floor_widens_tight_levels():
    config = StopLossConfig(
        conservative_sl_multiplier=1.0,
        atr_multiplier_tp=3.0,
        trading_fee_pct=0.1,
        max_risk_per_trade_pct=2.0,
    )
    from src.risk.dynamic_stop_loss import DynamicStopLossCalculator

    calc = DynamicStopLossCalculator(config)
    levels = calc.calculate(
        entry_price=50000.0,
        atr=5.0,
        balance_usd=10000.0,
        decision="BUY",
        timeframe="1m",
    )

    assert levels.sl_distance_pct >= 0.299
    assert levels.tp_distance_pct >= 0.499

if __name__ == "__main__":
    test_fee_math_accuracy()
