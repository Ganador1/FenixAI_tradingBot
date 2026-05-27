import pytest

from src.trading.trade_manager import TradeManager


def test_trailing_update_marks_protection_refresh_pending():
    manager = TradeManager(
        trailing_stop_pct=0.02,
        trailing_tp_enabled=True,
        trailing_tp_pct=0.004,
        trailing_tp_arm_pct=0.0015,
    )
    position = manager.open_position(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=100.0,
        quantity=1.0,
        signal_timestamp="test",
        stop_loss=98.0,
        take_profit=110.0,
        protection_position_id="pos-1",
        sl_order_id="sl-1",
        tp_order_id="tp-1",
    )

    assert position.protection_refresh_pending is False
    assert position.last_synced_stop_loss == 98.0
    assert position.last_synced_take_profit == 110.0

    exit_result = manager.check_exit_conditions("ETHUSDT", 103.0)

    assert exit_result is None
    assert position.trailing_activated is True
    assert position.stop_loss > 98.0
    assert position.protection_refresh_pending is True


def test_open_position_accumulates_same_side_adds_with_weighted_entry():
    manager = TradeManager()

    first = manager.open_position(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2000.0,
        quantity=0.10,
        signal_timestamp="t1",
        stop_loss=2020.0,
        take_profit=1970.0,
        trade_id="trade-1",
    )
    second = manager.open_position(
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=2020.0,
        quantity=0.20,
        signal_timestamp="t2",
        stop_loss=2030.0,
        take_profit=1960.0,
        trade_id="trade-2",
    )

    assert first is second
    assert second.quantity == pytest.approx(0.30)
    assert second.entry_price == pytest.approx(2013.3333333333333)
    assert second.stop_loss == 2030.0
    assert second.take_profit == 1960.0
    assert second.trade_id == "trade-2"
    assert second.entry_count == 2
