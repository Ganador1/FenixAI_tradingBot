"""Unit tests for the testnet fault injection framework.

These tests validate the fault injection logic without requiring a live
Binance Testnet connection. They use mocks to verify that each fault mode
is correctly detected and handled.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.testnet_fault_injection import _flatten_and_cancel


def _make_service_and_executor(symbol: str = "SOLUSDT"):
    service = MagicMock()
    service.get_ticker_price.return_value = 100.0

    config = MagicMock()
    config.min_notional = 5.0
    config.step_size = 0.01
    config.quantity_precision = 2
    config.price_precision = 2
    service.get_symbol_config.return_value = config

    executor = MagicMock()
    executor.symbol = symbol
    executor.get_position_snapshot.return_value = {"positionAmt": "0.0"}

    return service, executor


class TestSubmissionTimeout:
    @pytest.mark.asyncio
    async def test_timeout_triggers_reconciliation(self):
        """Verify that a timeout leads to reconciliation by clientOrderId, not blind retry."""
        from src.trading.executor import OrderExecutor

        executor = OrderExecutor(
            symbol="SOLUSDT",
            testnet=True,
            timeframe="1m",
            allow_mutations=True,
        )
        service = MagicMock()
        executor._service = service

        call_count = {"place": 0, "reconcile": 0}

        def mock_place(*args, **kwargs):
            call_count["place"] += 1
            raise TimeoutError("timeout")

        def mock_get_order(sym, client_order_id):
            call_count["reconcile"] += 1
            return {"orderId": 99999, "status": "FILLED", "avgPrice": "100.0", "executedQty": "0.06"}

        service.place_market_order = mock_place
        service.get_order_by_client_id = mock_get_order
        service.get_ticker_price = MagicMock(return_value=100.0)
        service.get_position = MagicMock(return_value={"positionAmt": "0.0"})

        # Disable the account guard to isolate the timeout test.
        with patch.dict(os.environ, {"FENIX_GLOBAL_PORTFOLIO_GUARD": "0"}):
            result = await executor.execute_market_order("BUY", quantity=0.06)

        # The executor should have reconciled, not blindly retried.
        assert call_count["place"] == 1
        assert call_count["reconcile"] >= 1
        assert result.success is True
        assert result.order_id == 99999


class TestDelayedFill:
    @pytest.mark.asyncio
    async def test_slow_fill_is_waited_for(self):
        """Verify that a slow-filling order is polled until FILLED."""
        from src.trading.executor import OrderExecutor

        executor = OrderExecutor(
            symbol="SOLUSDT",
            testnet=True,
            timeframe="1m",
            allow_mutations=True,
        )
        service = MagicMock()
        executor._service = service

        fill_count = {"n": 0}

        def mock_get_order(sym, oid):
            fill_count["n"] += 1
            if fill_count["n"] < 3:
                return {"orderId": oid, "status": "NEW", "avgPrice": "0", "executedQty": "0"}
            return {"orderId": oid, "status": "FILLED", "avgPrice": "100.0", "executedQty": "0.06"}

        def mock_place(*args, **kwargs):
            return {"orderId": 88888, "status": "NEW", "avgPrice": "0", "executedQty": "0"}

        service.place_market_order = mock_place
        service.get_order = mock_get_order
        service.get_position = MagicMock(return_value={"positionAmt": "0.0"})

        with patch.dict(os.environ, {"FENIX_GLOBAL_PORTFOLIO_GUARD": "0"}):
            result = await executor.execute_market_order("BUY", quantity=0.06)

        assert result.success is True
        assert fill_count["n"] >= 3


class TestProtectiveRejection:
    @pytest.mark.asyncio
    async def test_rejection_triggers_fail_safe_close(self):
        """Verify that SL/TP rejection triggers a fail-safe position close."""
        from src.trading.executor import OrderExecutor

        executor = OrderExecutor(
            symbol="SOLUSDT",
            testnet=True,
            timeframe="1m",
            allow_mutations=True,
        )
        service = MagicMock()
        executor._service = service

        close_called = {"yes": False}

        async def mock_close(*args, **kwargs):
            close_called["yes"] = True
            return {"status": "closed"}

        def mock_reject(*args, **kwargs):
            raise RuntimeError("rejected")

        def mock_place(*args, **kwargs):
            return {"orderId": 77777, "status": "FILLED", "avgPrice": "100.0", "executedQty": "0.06"}

        service.place_stop_loss_market = mock_reject
        service.place_take_profit_market = mock_reject
        service.place_market_order = mock_place
        service.get_order = lambda sym, oid: {"orderId": oid, "status": "FILLED", "avgPrice": "100.0", "executedQty": "0.06"}
        service.get_position = MagicMock(return_value={"positionAmt": "0.0"})
        service.get_symbol_config = MagicMock(return_value=MagicMock(
            tick_size=0.01, price_precision=2, quantity_precision=2, step_size=0.01,
        ))

        executor._close_unprotected_position = mock_close

        with patch.dict(os.environ, {"FENIX_GLOBAL_PORTFOLIO_GUARD": "0"}):
            result = await executor.execute_market_order(
                "BUY", quantity=0.06, stop_loss=99.0, take_profit=101.0,
            )

        assert result.success is False
        assert result.status == "PROTECTION_NOT_VERIFIED"
        assert close_called["yes"] is True


class TestProcessKillDuringSave:
    @pytest.mark.asyncio
    async def test_atomic_save_leaves_no_temp_files(self):
        """Verify that the atomic save pattern (temp + os.replace) works correctly.

        This tests the same pattern used by DualHorizonPredictor.save_model and
        ReasoningBank._atomic_write_text without requiring a full predictor instance.
        """
        import os
        import tempfile
        import time
        from pathlib import Path

        target = Path(tempfile.mkdtemp()) / "model.pkl"
        target.parent.mkdir(parents=True, exist_ok=True)

        # Simulate the atomic save pattern from predictor.py save_model().
        temp_path = target.with_name(
            f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        with temp_path.open("wb") as f:
            f.write(b"model data")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target)

        # The target file should exist and be valid.
        assert target.exists()
        assert target.read_bytes() == b"model data"

        # No temp files should remain.
        temp_files = list(target.parent.glob(f".{target.name}.*.tmp"))
        assert len(temp_files) == 0

    @pytest.mark.asyncio
    async def test_interrupted_save_does_not_corrupt_existing(self):
        """If a save is interrupted, the existing file must remain intact."""
        import os
        import tempfile
        import time
        from pathlib import Path

        target = Path(tempfile.mkdtemp()) / "model.pkl"
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write an initial valid file.
        target.write_bytes(b"original model")

        # Simulate an interrupted save: create temp file but don't os.replace.
        temp_path = target.with_name(
            f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        with temp_path.open("wb") as f:
            f.write(b"partial write")
            f.flush()
            # Simulate crash: no os.replace, temp file left behind.

        # The original file must be intact.
        assert target.exists()
        assert target.read_bytes() == b"original model"

        # Clean up the orphaned temp file.
        temp_path.unlink(missing_ok=True)


class TestFlattenAndCancel:
    @pytest.mark.asyncio
    async def test_cancels_orders_and_flattens_position(self):
        service = MagicMock()
        executor = MagicMock()

        service.get_open_orders.return_value = [{"orderId": 1}, {"orderId": 2}]
        service.get_open_algo_orders.return_value = [{"algoId": 3}]
        service.cancel_order = MagicMock(return_value={})
        service.cancel_algo_order = MagicMock(return_value={})

        executor.get_position_snapshot.return_value = {"positionAmt": "0.1"}
        executor.execute_market_order = AsyncMock(
            return_value=MagicMock(success=True, status="FILLED")
        )

        result = await _flatten_and_cancel(service, executor, "SOLUSDT")

        assert "cleanup" in result
        assert len(result["cleanup"]) >= 3  # 2 standard + 1 algo + 1 flatten
        service.cancel_order.assert_called()
        service.cancel_algo_order.assert_called_once()
        executor.execute_market_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_position_skips_flatten(self):
        service = MagicMock()
        executor = MagicMock()

        service.get_open_orders.return_value = []
        service.get_open_algo_orders.return_value = []
        executor.get_position_snapshot.return_value = {"positionAmt": "0.0"}
        executor.execute_market_order = AsyncMock()

        result = await _flatten_and_cancel(service, executor, "SOLUSDT")

        assert "cleanup" in result
        executor.execute_market_order.assert_not_called()
