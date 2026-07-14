from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from run_fenix import InstanceLock
from src.risk.runtime_risk_manager import RuntimeRiskManager, TradeRecord
from src.trading.executor import OrderExecutor


def test_optional_dual_key_module_is_importable():
    from src.inference import dual_key_patch

    assert isinstance(dual_key_patch.get_key_distribution(), dict)


def test_dual_key_selection_never_logs_secret_material(monkeypatch, caplog):
    from src.inference import dual_key_patch

    fake_key = "fake-secret-key-that-must-not-appear"
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY_QABBA", fake_key)
    caplog.set_level("DEBUG", logger=dual_key_patch.__name__)

    assert dual_key_patch.get_api_key_for_agent("qabba") == fake_key
    assert fake_key not in caplog.text
    assert "fake-secret-key" not in caplog.text


def test_instance_lock_rejects_duplicate_symbol_process(tmp_path, monkeypatch):
    monkeypatch.setenv("FENIX_INSTANCE_LOCK_DIR", str(tmp_path))
    first = InstanceLock("SOLUSDT")
    second = InstanceLock("SOLUSDT")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another Fenix process"):
            second.acquire()
    finally:
        first.release()


def test_authoritative_risk_balance_does_not_apply_realized_pnl_twice(tmp_path):
    manager = RuntimeRiskManager(storage_path=str(tmp_path / "risk.jsonl"))
    manager.set_authoritative_balance_mode(True)
    manager.update_balance(95.0)

    manager.record_trade(TradeRecord(
        trade_id="closed-1",
        timestamp=datetime.now(timezone.utc),
        symbol="SOLUSDT",
        decision="SELL",
        entry_price=100.0,
        exit_price=95.0,
        pnl=5.0,
        pnl_pct=5.0,
        success=True,
        size=10.0,
    ))

    assert manager._current_balance == pytest.approx(95.0)
    assert manager._daily_pnl == pytest.approx(5.0)


def test_global_portfolio_guard_caps_projected_account_margin(monkeypatch):
    class AccountService:
        def get_account_info(self):
            return {
                "totalMarginBalance": "100.0",
                "totalInitialMargin": "48.0",
            }

        def get_ticker_price(self, _symbol):
            return 100.0

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    monkeypatch.setenv("FENIX_MAX_ACCOUNT_MARGIN_PCT", "0.50")
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    executor._service = AccountService()

    allowed, reason = executor._check_global_account_margin(quantity=0.30)

    assert allowed is False
    assert "exceeds cap" in reason


def test_margin_guard_uses_exchange_leverage_over_stale_env_value(monkeypatch):
    """A real leverage lower than FENIX_LEVERAGE must not be silently ignored.

    Regression: the guard divided notional by the env-configured leverage
    only, so a real exchange leverage of 3x while FENIX_LEVERAGE=10 made the
    projected margin 3.3x smaller than reality and could wave through an
    entry that actually breaches the cap.
    """

    class AccountService:
        def get_account_info(self):
            return {
                "totalMarginBalance": "100.0",
                "totalInitialMargin": "0.0",
                "positions": [{"symbol": "SOLUSDT", "leverage": "3"}],
            }

        def get_ticker_price(self, _symbol):
            return 100.0

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    monkeypatch.setenv("FENIX_MAX_ACCOUNT_MARGIN_PCT", "0.50")
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    executor._service = AccountService()

    # 0.30 * 100 / 3x = 10 margin -> within the 50 cap at 3x, so this proves
    # the real leverage (not the stale 10x env value) drove the computation:
    # at 10x the same order would project 3 margin, comfortably under cap
    # too, so use a quantity that only breaches the cap at the REAL leverage.
    allowed, reason = executor._check_global_account_margin(quantity=1.70)

    # 1.70 * 100 / 3x = 56.67 margin > 50 cap -> must block using real 3x.
    assert allowed is False
    assert "exceeds cap" in reason

    # At the stale FENIX_LEVERAGE=10 the same order would project only 17
    # margin (well under cap) -- confirms the fix, not a coincidence of math.
    stale_projected = 1.70 * 100.0 / 10.0
    assert stale_projected < 50.0


def test_margin_guard_falls_back_to_env_leverage_when_symbol_missing(monkeypatch):
    class AccountService:
        def get_account_info(self):
            return {
                "totalMarginBalance": "100.0",
                "totalInitialMargin": "0.0",
                "positions": [{"symbol": "ETHUSDC", "leverage": "5"}],
            }

        def get_ticker_price(self, _symbol):
            return 100.0

    monkeypatch.setenv("FENIX_LEVERAGE", "10")
    monkeypatch.setenv("FENIX_MAX_ACCOUNT_MARGIN_PCT", "0.50")
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    executor._service = AccountService()

    allowed, _ = executor._check_global_account_margin(quantity=0.30)

    assert allowed is True


def test_get_exchange_leverage_reads_symbol_from_account():
    class AccountService:
        def get_account_info(self):
            return {"positions": [{"symbol": "SOLUSDT", "leverage": "3"}]}

    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    executor._service = AccountService()

    assert executor.get_exchange_leverage() == pytest.approx(3.0)


def test_get_exchange_leverage_returns_none_when_symbol_missing_or_failed():
    class MissingSymbolService:
        def get_account_info(self):
            return {"positions": [{"symbol": "ETHUSDC", "leverage": "5"}]}

    class FailingService:
        def get_account_info(self):
            raise TimeoutError("network blip")

    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    executor._service = MissingSymbolService()
    assert executor.get_exchange_leverage() is None

    executor._service = FailingService()
    assert executor.get_exchange_leverage() is None


@pytest.mark.asyncio
async def test_ambiguous_market_submission_is_reconciled_by_client_id(monkeypatch):
    monkeypatch.setenv("FENIX_GLOBAL_PORTFOLIO_GUARD", "0")
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False)
    service = MagicMock()
    service.place_market_order.side_effect = TimeoutError("response timed out")
    service.get_order_by_client_id.return_value = {
        "orderId": 901,
        "status": "FILLED",
        "avgPrice": "100.0",
        "executedQty": "0.10",
    }
    executor._service = service

    result = await executor.execute_market_order("BUY", quantity=0.10)

    assert result.success is True
    assert result.order_id == 901
    service.get_order_by_client_id.assert_called_once()
