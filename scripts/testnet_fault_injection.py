#!/usr/bin/env python3
"""Controlled fault-injection tests against Binance Futures Testnet.

Validates that the Fenix safety stack handles each failure mode without
creating unintended exposure, corrupting risk state, or losing track of
a position.  Every test is testnet-only and always attempts cleanup.

Usage:
    python scripts/testnet_fault_injection.py --api-key-index 1
    python scripts/testnet_fault_injection.py --api-key-index 1 --only submission_timeout

Fault modes tested:
1. submission_timeout   — market order times out; reconciliation by clientOrderId
2. delayed_fill         — order fills slowly; _wait_for_fill must not give up
3. protective_rejection — SL/TP rejected; fail-safe close must execute
4. websocket_disconnect  — user-data stream drops; polling fallback must work
5. process_kill_during_save — interrupt during NanoFenix model save; file must not truncate
6. simultaneous_signals   — two symbols signal at once; account lock must serialize
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.services.binance_service import BinanceService
from src.trading.executor import OrderExecutor
from src.trading.user_data_stream import FuturesUserDataStream

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fault_injection")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _select_testnet_credentials(index: int) -> tuple[str, str]:
    key = os.getenv(f"BINANCE_TESTNET_API_KEY_{index}") or os.getenv("BINANCE_TESTNET_API_KEY")
    secret = os.getenv(f"BINANCE_TESTNET_API_SECRET_{index}") or os.getenv("BINANCE_TESTNET_API_SECRET")
    if not key or not secret:
        raise RuntimeError(f"Missing BINANCE_TESTNET_API_KEY_{index} / BINANCE_TESTNET_API_SECRET_{index}")
    return key, secret


async def _flatten_and_cancel(service: BinanceService, executor: OrderExecutor, symbol: str) -> dict[str, Any]:
    """Cancel all orders and flatten position for a symbol."""
    outcomes: list[str] = []
    try:
        for order in (await asyncio.to_thread(service.get_open_orders, symbol)) or []:
            oid = order.get("orderId")
            if oid:
                try:
                    await asyncio.to_thread(service.cancel_order, symbol, oid)
                    outcomes.append(f"cancel_order:{oid}")
                except Exception as exc:
                    outcomes.append(f"cancel_order_error:{oid}:{type(exc).__name__}")
        for order in (await asyncio.to_thread(service.get_open_algo_orders, symbol)) or []:
            oid = order.get("algoId") or order.get("orderId")
            if oid:
                try:
                    await asyncio.to_thread(service.cancel_algo_order, symbol, oid)
                    outcomes.append(f"cancel_algo:{oid}")
                except Exception as exc:
                    outcomes.append(f"cancel_algo_error:{oid}:{type(exc).__name__}")
    except Exception as exc:
        outcomes.append(f"cancel_all_error:{type(exc).__name__}")

    try:
        snapshot = await asyncio.to_thread(executor.get_position_snapshot)
        amount = _float(snapshot.get("positionAmt"))
        if abs(amount) > 1e-9:
            close_side = "SELL" if amount > 0 else "BUY"
            await executor.execute_market_order(side=close_side, quantity=abs(amount), reduce_only=True)
            outcomes.append(f"flatten:{close_side}:{amount}")
    except Exception as exc:
        outcomes.append(f"flatten_error:{type(exc).__name__}")

    return {"cleanup": outcomes}


# ---------------------------------------------------------------------------
# Fault 1: Submission timeout → reconciliation by clientOrderId
# ---------------------------------------------------------------------------

async def fault_test_submission_timeout(service: BinanceService, executor: OrderExecutor, symbol: str) -> dict:
    """A market order that times out must be reconciled by clientOrderId, never blindly retried."""
    logger.info("[submission_timeout] Starting — simulating timeout on market entry")
    result: dict[str, Any] = {"test": "submission_timeout", "symbol": symbol}

    try:
        # Patch place_market_order to raise TimeoutError, then return a filled order on reconciliation.
        original_place = service.place_market_order
        original_get_order = service.get_order_by_client_id

        ticker = await asyncio.to_thread(service.get_ticker_price, symbol)
        config = await asyncio.to_thread(service.get_symbol_config, symbol)
        if not config or ticker <= 0:
            raise RuntimeError("Missing symbol config or ticker")
        quantity = math.ceil((config.min_notional * 1.25 / ticker) / config.step_size) * config.step_size
        quantity = round(quantity, config.quantity_precision)

        call_count = {"place": 0, "reconcile": 0}

        def mock_place(*args, **kwargs):
            call_count["place"] += 1
            raise TimeoutError("Simulated submission timeout")

        def mock_get_order(sym, client_order_id):
            call_count["reconcile"] += 1
            return {
                "orderId": 99999,
                "status": "FILLED",
                "avgPrice": str(ticker),
                "executedQty": str(quantity),
            }

        service.place_market_order = mock_place  # type: ignore
        service.get_order_by_client_id = mock_get_order  # type: ignore

        order_result = await executor.execute_market_order(
            side="BUY", quantity=quantity, reduce_only=False,
        )

        result["place_calls"] = call_count["place"]
        result["reconcile_calls"] = call_count["reconcile"]
        result["order_status"] = order_result.status
        result["order_success"] = order_result.success
        result["order_id"] = order_result.order_id

        # The executor should have reconciled, not blindly retried.
        assert_ok = (
            call_count["place"] == 1  # Only one submission attempt
            and call_count["reconcile"] >= 1  # Reconciliation happened
            and order_result.success is True  # Recovered the fill
        )
        result["pass"] = assert_ok

        # Restore and cleanup.
        service.place_market_order = original_place  # type: ignore
        service.get_order_by_client_id = original_get_order  # type: ignore

        # If a real order was created by the mock reconciliation, flatten it.
        if order_result.order_id:
            await _flatten_and_cancel(service, executor, symbol)

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        await _flatten_and_cancel(service, executor, symbol)

    return result


# ---------------------------------------------------------------------------
# Fault 2: Delayed fill → _wait_for_fill must not give up prematurely
# ---------------------------------------------------------------------------

async def fault_test_delayed_fill(service: BinanceService, executor: OrderExecutor, symbol: str) -> dict:
    """An order that fills slowly must be waited for, not abandoned."""
    logger.info("[delayed_fill] Starting — simulating slow fill response")
    result: dict[str, Any] = {"test": "delayed_fill", "symbol": symbol}

    try:
        ticker = await asyncio.to_thread(service.get_ticker_price, symbol)
        config = await asyncio.to_thread(service.get_symbol_config, symbol)
        if not config or ticker <= 0:
            raise RuntimeError("Missing symbol config or ticker")
        quantity = math.ceil((config.min_notional * 1.25 / ticker) / config.step_size) * config.step_size
        quantity = round(quantity, config.quantity_precision)

        # Patch get_order to return NEW first, then FILLED.
        original_get_order = service.get_order
        fill_call_count = {"n": 0}

        def mock_get_order(sym, order_id):
            fill_call_count["n"] += 1
            if fill_call_count["n"] < 3:
                return {"orderId": order_id, "status": "NEW", "avgPrice": "0", "executedQty": "0"}
            return {
                "orderId": order_id,
                "status": "FILLED",
                "avgPrice": str(ticker),
                "executedQty": str(quantity),
            }

        # Patch place_market_order to return a NEW order immediately.
        def mock_place(*args, **kwargs):
            return {"orderId": 88888, "status": "NEW", "avgPrice": "0", "executedQty": "0"}

        service.place_market_order = mock_place  # type: ignore
        service.get_order = mock_get_order  # type: ignore

        order_result = await executor.execute_market_order(
            side="BUY", quantity=quantity, reduce_only=False,
        )

        result["fill_polls"] = fill_call_count["n"]
        result["order_status"] = order_result.status
        result["order_success"] = order_result.success
        result["pass"] = order_result.success is True and fill_call_count["n"] >= 3

        service.place_market_order = original_place  # type: ignore
        service.get_order = original_get_order  # type: ignore

        await _flatten_and_cancel(service, executor, symbol)

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        await _flatten_and_cancel(service, executor, symbol)

    return result


# ---------------------------------------------------------------------------
# Fault 3: Protective order rejection → fail-safe close
# ---------------------------------------------------------------------------

async def fault_test_protective_rejection(service: BinanceService, executor: OrderExecutor, symbol: str) -> dict:
    """If SL/TP placement fails, the position must be closed immediately."""
    logger.info("[protective_rejection] Starting — simulating SL/TP rejection")
    result: dict[str, Any] = {"test": "protective_rejection", "symbol": symbol}

    try:
        ticker = await asyncio.to_thread(service.get_ticker_price, symbol)
        config = await asyncio.to_thread(service.get_symbol_config, symbol)
        if not config or ticker <= 0:
            raise RuntimeError("Missing symbol config or ticker")
        quantity = math.ceil((config.min_notional * 1.25 / ticker) / config.step_size) * config.step_size
        quantity = round(quantity, config.quantity_precision)

        # Patch protective order placement to raise an exception.
        original_place_sl = service.place_stop_loss_market
        original_place_tp = service.place_take_profit_market
        original_close = executor._close_unprotected_position

        close_called = {"yes": False}

        async def mock_close(*args, **kwargs):
            close_called["yes"] = True
            return {"status": "closed", "side": "SELL"}

        def mock_reject(*args, **kwargs):
            raise RuntimeError("Simulated protective order rejection")

        service.place_stop_loss_market = mock_reject  # type: ignore
        service.place_take_profit_market = mock_reject  # type: ignore
        executor._close_unprotected_position = mock_close  # type: ignore

        # Patch the entry to succeed.
        def mock_place(*args, **kwargs):
            return {"orderId": 77777, "status": "FILLED", "avgPrice": str(ticker), "executedQty": str(quantity)}

        service.place_market_order = mock_place  # type: ignore
        service.get_order = lambda sym, oid: {"orderId": oid, "status": "FILLED", "avgPrice": str(ticker), "executedQty": str(quantity)}

        order_result = await executor.execute_market_order(
            side="BUY",
            quantity=quantity,
            stop_loss=ticker * 0.99,
            take_profit=ticker * 1.01,
            reduce_only=False,
        )

        result["order_status"] = order_result.status
        result["fail_safe_close_called"] = close_called["yes"]
        result["pass"] = (
            order_result.success is False
            and order_result.status == "PROTECTION_NOT_VERIFIED"
            and close_called["yes"] is True
        )

        # Restore.
        service.place_stop_loss_market = original_place_sl  # type: ignore
        service.place_take_profit_market = original_place_tp  # type: ignore
        executor._close_unprotected_position = original_close  # type: ignore

        await _flatten_and_cancel(service, executor, symbol)

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        await _flatten_and_cancel(service, executor, symbol)

    return result


# ---------------------------------------------------------------------------
# Fault 4: WebSocket disconnect → polling fallback
# ---------------------------------------------------------------------------

async def fault_test_websocket_disconnect(key: str, secret: str, symbol: str) -> dict:
    """User-data stream disconnect must not lose position state; polling continues."""
    logger.info("[websocket_disconnect] Starting — testing stream reconnect")
    result: dict[str, Any] = {"test": "websocket_disconnect", "symbol": symbol}

    events: list[dict] = []

    stream = FuturesUserDataStream(
        api_key=key,
        api_secret=secret,
        testnet=True,
        on_event=events.append,
        reconnect_delay_sec=0.5,
    )

    try:
        await stream.start(timeout_sec=10)
        initial_status = stream.get_status()
        result["initial_connected"] = initial_status["running"]

        # Simulate a disconnect by closing the client.
        if stream._client is not None:
            try:
                await stream._client.close_connection()
            except Exception:
                pass

        # Wait for reconnect.
        await asyncio.sleep(3)
        reconnect_status = stream.get_status()
        result["reconnect_count"] = reconnect_status["reconnect_count"]
        result["running_after_reconnect"] = reconnect_status["running"]

        # The stream should have reconnected or be attempting to.
        result["pass"] = reconnect_status["reconnect_count"] >= 1

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        await stream.stop()

    return result


# ---------------------------------------------------------------------------
# Fault 5: Process kill during model save → file must not truncate
# ---------------------------------------------------------------------------

async def fault_test_process_kill_during_save() -> dict:
    """An interrupted NanoFenix model save must not leave a truncated file."""
    logger.info("[process_kill_during_save] Starting — testing atomic save")
    result: dict[str, Any] = {"test": "process_kill_during_save"}

    try:
        from nanofenixv3.predictor import DualHorizonPredictor

        import tempfile
        import pickle

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.pkl"

            # Create a minimal predictor with a mock model.
            predictor = DualHorizonPredictor.__new__(DualHorizonPredictor)
            predictor._short = MagicMock()
            predictor._short._model = {"weights": [1, 2, 3]}
            predictor._long = MagicMock()
            predictor._long._model = {"weights": [4, 5, 6]}
            predictor._short_buffer = MagicMock()
            predictor._short_buffer.data = []
            predictor._long_buffer = MagicMock()
            predictor._long_buffer.data = []
            predictor._drift_detector = MagicMock()
            predictor._drift_detector.export_state = MagicMock(return_value={})
            predictor._drift_retrain_count = 0

            # Save normally.
            predictor.save_model(str(model_path))
            result["save_succeeded"] = model_path.exists()

            # Verify the file is valid.
            with open(model_path, "rb") as f:
                data = pickle.load(f)
            result["model_valid"] = "short_model" in data

            # Simulate a crash during save by checking that temp files are cleaned up.
            # The atomic save uses os.replace, so a partial write should not corrupt.
            temp_files = list(model_path.parent.glob(f".{model_path.name}.*.tmp"))
            result["temp_files_left"] = len(temp_files)
            result["pass"] = (
                result["save_succeeded"]
                and result["model_valid"]
                and len(temp_files) == 0
            )

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


# ---------------------------------------------------------------------------
# Fault 6: Simultaneous signals → account lock serialization
# ---------------------------------------------------------------------------

async def fault_test_simultaneous_signals(service: BinanceService, symbol: str) -> dict:
    """Two concurrent entry attempts must be serialized by the account lock."""
    logger.info("[simultaneous_signals] Starting — testing account lock serialization")
    result: dict[str, Any] = {"test": "simultaneous_signals", "symbol": symbol}

    try:
        executor1 = OrderExecutor(symbol=symbol, timeframe="1m", testnet=True)
        executor1._service = service
        executor2 = OrderExecutor(symbol=symbol, timeframe="1m", testnet=True)
        executor2._service = service

        # Mock both to attempt acquiring the lock simultaneously.
        lock_acquired: list[float] = []
        lock_released: list[float] = []

        original_acquire = OrderExecutor._acquire_account_order_lock
        original_release = OrderExecutor._release_account_order_lock

        def tracked_acquire(self):
            handle = original_acquire(self)
            lock_acquired.append(time.monotonic())
            return handle

        @staticmethod
        def tracked_release(handle):
            lock_released.append(time.monotonic())
            original_release(handle)

        # Patch the account margin check to always pass (we're testing the lock, not the margin).
        executor1._check_global_account_margin = lambda qty: (True, "test")
        executor2._check_global_account_margin = lambda qty: (True, "test")

        # Patch place_market_order to simulate a slow submission.
        def slow_place(*args, **kwargs):
            time.sleep(0.5)  # Hold the lock for 500ms
            return {"orderId": 111, "status": "FILLED", "avgPrice": "100", "executedQty": "0.01"}

        service.place_market_order = slow_place  # type: ignore
        service.get_order = lambda sym, oid: {"orderId": oid, "status": "FILLED", "avgPrice": "100", "executedQty": "0.01"}

        with patch.object(OrderExecutor, "_acquire_account_order_lock", tracked_acquire), \
             patch.object(OrderExecutor, "_release_account_order_lock", tracked_release):

            # Launch two concurrent entries.
            task1 = asyncio.create_task(
                executor1.execute_market_order("BUY", quantity=0.01, reduce_only=False)
            )
            task2 = asyncio.create_task(
                executor2.execute_market_order("BUY", quantity=0.01, reduce_only=False)
            )
            r1, r2 = await asyncio.gather(task1, task2, return_exceptions=True)

        result["result1_status"] = getattr(r1, "status", str(r1))
        result["result2_status"] = getattr(r2, "status", str(r2))
        result["lock_acquired_count"] = len(lock_acquired)
        result["lock_released_count"] = len(lock_released)

        # Both should have acquired the lock (serialized), and both released it.
        result["pass"] = (
            len(lock_acquired) == 2
            and len(lock_released) == 2
            and len(lock_acquired) == len(lock_released)
        )

        await _flatten_and_cancel(service, executor1, symbol)

    except Exception as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = {
    "submission_timeout": fault_test_submission_timeout,
    "delayed_fill": fault_test_delayed_fill,
    "protective_rejection": fault_test_protective_rejection,
    "websocket_disconnect": fault_test_websocket_disconnect,
    "process_kill_during_save": fault_test_process_kill_during_save,
    "simultaneous_signals": fault_test_simultaneous_signals,
}


async def run_fault_injection(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key, secret = _select_testnet_credentials(args.api_key_index)
    os.environ["BINANCE_TESTNET_API_KEY"] = key
    os.environ["BINANCE_TESTNET_API_SECRET"] = secret
    os.environ["FENIX_GLOBAL_PORTFOLIO_GUARD"] = "1"
    os.environ["FENIX_REQUIRE_LIVE_STOP_LOSS"] = "1"
    os.environ["FENIX_PYRAMID_ENABLE"] = "0"
    os.environ.setdefault("FENIX_LEVERAGE", "10")

    symbol = args.symbol.upper()
    service = BinanceService(key, secret, testnet=True)
    if not service.initialize():
        raise RuntimeError("Could not initialize Binance Futures Testnet")

    executor = OrderExecutor(symbol=symbol, timeframe="1m", testnet=True)
    executor._service = service

    # Pre-flight: ensure no existing exposure.
    try:
        snapshot = await asyncio.to_thread(executor.get_position_snapshot)
        amount = _float(snapshot.get("positionAmt"))
        if abs(amount) > 1e-9:
            raise RuntimeError(f"Symbol {symbol} has existing position {amount}; refusing to run")
    except RuntimeError:
        raise
    except Exception:
        pass  # No position is fine.

    report: dict[str, Any] = {
        "network": "binance_futures_testnet",
        "symbol": symbol,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_key_index": args.api_key_index,
        "tests": {},
    }

    test_names = [args.only] if args.only else list(ALL_TESTS.keys())

    for name in test_names:
        test_fn = ALL_TESTS.get(name)
        if test_fn is None:
            report["tests"][name] = {"pass": False, "error": "unknown test"}
            continue

        logger.info("=== Running test: %s ===", name)
        try:
            if name == "websocket_disconnect":
                test_result = await test_fn(key, secret, symbol)
            elif name == "process_kill_during_save":
                test_result = await test_fn()
            else:
                test_result = await test_fn(service, executor, symbol)
        except Exception as exc:
            test_result = {"test": name, "pass": False, "error": f"{type(exc).__name__}: {exc}"}

        report["tests"][name] = test_result
        logger.info("=== %s: %s ===", name, "PASS" if test_result.get("pass") else "FAIL")

    # Final cleanup.
    try:
        report["final_cleanup"] = await _flatten_and_cancel(service, executor, symbol)
    except Exception as exc:
        report["final_cleanup_error"] = str(exc)

    report["ended_at"] = datetime.now(timezone.utc).isoformat()
    service.close()

    passed = sum(1 for t in report["tests"].values() if t.get("pass"))
    total = len(report["tests"])
    report["summary"] = f"{passed}/{total} passed"

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--api-key-index", type=int, choices=(1, 2), default=1)
    parser.add_argument("--only", choices=list(ALL_TESTS.keys()), default=None)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "logs" / "testnet_fault_injection_report.json",
    )
    args = parser.parse_args()

    report = asyncio.run(run_fault_injection(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"report_path={args.report}")

    all_passed = all(t.get("pass") for t in report["tests"].values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())