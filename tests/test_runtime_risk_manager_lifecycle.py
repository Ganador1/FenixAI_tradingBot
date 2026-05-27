from __future__ import annotations

from datetime import datetime, timezone

from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord


def test_open_trade_does_not_count_metrics_until_close():
    manager = RuntimeRiskManager()
    manager.update_balance(1000.0)

    manager.open_trade(
        TradeRecord(
            trade_id="trade-1",
            timestamp=datetime.now(timezone.utc),
            symbol="ETHUSDT",
            decision="BUY",
            entry_price=100.0,
            exit_price=None,
            pnl=0.0,
            pnl_pct=0.0,
            success=False,
            size=125.0,
        )
    )

    metrics = manager.get_metrics()
    exposure = manager.get_total_exposure()
    assert metrics["total_trades"] == 0
    assert exposure["positions_count"] == 1
    assert exposure["total_exposure"] == 125.0


def test_close_trade_counts_finalized_metrics_and_clears_exposure():
    manager = RuntimeRiskManager()
    manager.update_balance(1000.0)

    manager.open_trade(
        TradeRecord(
            trade_id="trade-2",
            timestamp=datetime.now(timezone.utc),
            symbol="ETHUSDT",
            decision="SELL",
            entry_price=110.0,
            exit_price=None,
            pnl=0.0,
            pnl_pct=0.0,
            success=False,
            size=140.0,
        )
    )

    closed = manager.close_trade(
        trade_id="trade-2",
        exit_price=108.0,
        pnl=12.5,
        pnl_pct=1.25,
        success=True,
        symbol="ETHUSDT",
    )

    metrics = manager.get_metrics()
    exposure = manager.get_total_exposure()
    assert closed is True
    assert metrics["total_trades"] == 1
    assert metrics["wins"] == 1
    assert metrics["losses"] == 0
    assert metrics["total_pnl"] == 12.5
    assert exposure["positions_count"] == 0


def test_close_trade_by_symbol_fallback_clears_active_trade():
    manager = RuntimeRiskManager()
    manager.update_balance(1000.0)

    manager.open_trade(
        TradeRecord(
            trade_id="trade-3",
            timestamp=datetime.now(timezone.utc),
            symbol="ETHUSDT",
            decision="BUY",
            entry_price=2000.0,
            exit_price=None,
            pnl=0.0,
            pnl_pct=0.0,
            success=False,
            size=150.0,
        )
    )

    closed = manager.close_trade_by_symbol(
        symbol="ETHUSDT",
        exit_price=2010.0,
        pnl=10.0,
        pnl_pct=0.5,
        success=True,
    )

    metrics = manager.get_metrics()
    exposure = manager.get_total_exposure()
    assert closed is True
    assert metrics["total_trades"] == 1
    assert metrics["wins"] == 1
    assert exposure["positions_count"] == 0


def test_total_exposure_uses_configured_leverage_multiplier():
    manager = RuntimeRiskManager()
    manager.update_balance(1000.0)
    manager.set_max_exposure_pct(0.5)
    manager.set_exposure_leverage_multiplier(6.0)

    exposure = manager.get_total_exposure()

    assert exposure["max_margin_exposure"] == 500.0
    assert exposure["max_exposure"] == 3000.0
    assert exposure["exposure_leverage_multiplier"] == 6.0


def test_open_trade_accumulates_same_symbol_exposure_across_adds():
    manager = RuntimeRiskManager()
    manager.update_balance(1000.0)

    manager.open_trade(
        TradeRecord(
            trade_id="trade-add-1",
            timestamp=datetime.now(timezone.utc),
            symbol="ETHUSDT",
            decision="SELL",
            entry_price=100.0,
            exit_price=None,
            pnl=0.0,
            pnl_pct=0.0,
            success=False,
            size=125.0,
        )
    )
    manager.open_trade(
        TradeRecord(
            trade_id="trade-add-2",
            timestamp=datetime.now(timezone.utc),
            symbol="ETHUSDT",
            decision="SELL",
            entry_price=101.0,
            exit_price=None,
            pnl=0.0,
            pnl_pct=0.0,
            success=False,
            size=75.0,
        )
    )

    exposure = manager.get_total_exposure()

    assert exposure["positions_count"] == 1
    assert exposure["total_exposure"] == 200.0
    assert exposure["positions"]["ETHUSDT"]["notional"] == 200.0


def test_sell_trade_intent_still_counts_as_new_exposure():
    manager = RuntimeRiskManager()
    manager.update_balance(100.0)
    manager.set_max_exposure_pct(0.5)
    manager.set_exposure_leverage_multiplier(6.0)
    manager.update_open_position("ETHUSDT", size=280.0, notional=280.0, side="short")

    allowed, message = manager._check_total_exposure(40.0, "sell")

    assert allowed is False
    assert "Total exposure would exceed limit" in message


def test_update_balance_reanchors_stale_persisted_baseline_without_trades():
    manager = RuntimeRiskManager()
    manager._peak_balance = 5000.0
    manager._current_balance = 5000.0
    manager._daily_start_balance = 5000.0
    manager._daily_pnl = 22.5
    manager._last_trading_day = "2099-01-01"

    manager.update_balance(76.42)

    metrics = manager.get_metrics()
    assert manager._peak_balance == 76.42
    assert manager._current_balance == 76.42
    assert manager._daily_start_balance == 76.42
    assert manager._daily_pnl == 0.0
    assert manager._last_trading_day == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert metrics["total_trades"] == 0
