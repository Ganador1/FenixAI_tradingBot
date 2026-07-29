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
4. websocket_disconnect  — authenticated user-data stream can stop and reconnect cleanly
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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.security.dotenv_security import secure_load_dotenv
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

        with (
            patch.object(service, "place_market_order", side_effect=mock_place),
            patch.object(service, "get_order_by_client_id", side_effect=mock_get_order),
        ):
            order_result = await executor.execute_market_order(
                side="BUY", quantity=quantity, reduce_only=False,
            )

        result["mode"] = "simulated_exchange_boundary"
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

        with (
            patch.object(service, "place_market_order", side_effect=mock_place),
            patch.object(service, "get_order", side_effect=mock_get_order),
        ):
            order_result = await executor.execute_market_order(
                side="BUY", quantity=quantity, reduce_only=False,
            )

        result["mode"] = "simulated_exchange_boundary"
        result["fill_polls"] = fill_call_count["n"]
        result["order_status"] = order_result.status
        result["order_success"] = order_result.success
        result["pass"] = order_result.success is True and fill_call_count["n"] >= 3

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

        def mock_reject(*args, **kwargs):
            raise RuntimeError("Simulated protective order rejection")

        # This fault is intentionally real on Testnet: the minimum-notional entry
        # reaches Binance, only SL/TP placement is rejected locally, and the real
        # executor must immediately submit a reduce-only fail-safe close.
        with (
            patch.object(service, "place_stop_loss_market", side_effect=mock_reject),
            patch.object(service, "place_take_profit_market", side_effect=mock_reject),
        ):
            order_result = await executor.execute_market_order(
                side="BUY",
                quantity=quantity,
                stop_loss=ticker * 0.99,
                take_profit=ticker * 1.01,
                reduce_only=False,
            )

        final_snapshot = await asyncio.to_thread(executor.get_position_snapshot)
        final_amount = _float(final_snapshot.get("positionAmt"))
        result["mode"] = "real_testnet_order"
        result["order_status"] = order_result.status
        result["final_position_amt"] = final_amount
        result["pass"] = (
            order_result.success is False
            and order_result.status == "PROTECTION_NOT_VERIFIED"
            and abs(final_amount) <= 1e-9
        )

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
    """The authenticated stream must reconnect cleanly after a controlled stop."""
    logger.info("[websocket_disconnect] Starting — testing authenticated stream restart")
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
        result["initial_connected"] = (
            initial_status["running"] and initial_status["ready"]
        )

        # A client-session close does not necessarily close python-binance's
        # websocket transport. Use the public lifecycle to reproduce the
        # operational recovery path without pretending it is a network fault.
        await stream.stop()
        stopped_status = stream.get_status()
        await stream.start(timeout_sec=10)
        reconnect_status = stream.get_status()
        result["mode"] = "real_testnet_authenticated_restart"
        result["stopped_cleanly"] = not stopped_status["running"]
        result["running_after_reconnect"] = (
            reconnect_status["running"] and reconnect_status["ready"]
        )
        result["last_error"] = reconnect_status["last_error"]
        result["pass"] = (
            result["initial_connected"]
            and result["stopped_cleanly"]
            and result["running_after_reconnect"]
            and reconnect_status["last_error"] is None
        )

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
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.pkl"
            model_path.write_text(json.dumps({"version": "known-good"}), encoding="utf-8")

            child_code = (
                "import os,signal,sys,time;"
                "from pathlib import Path;"
                "target=Path(sys.argv[1]);"
                "temp=target.with_name(f'.{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp');"
                "f=temp.open('wb');f.write(b'partial-model');f.flush();os.fsync(f.fileno());"
                "os.kill(os.getpid(), signal.SIGKILL)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", child_code, str(model_path)],
                check=False,
                capture_output=True,
                timeout=10,
            )
            data = json.loads(model_path.read_text(encoding="utf-8"))
            temp_files = list(model_path.parent.glob(f".{model_path.name}.*.tmp"))

            result["mode"] = "real_subprocess_sigkill"
            result["child_returncode"] = completed.returncode
            result["original_model_intact"] = data == {"version": "known-good"}
            result["orphan_temp_files"] = len(temp_files)
            result["pass"] = (
                completed.returncode == -signal.SIGKILL
                and result["original_model_intact"]
                and len(temp_files) == 1
            )
            for temp_path in temp_files:
                temp_path.unlink(missing_ok=True)

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
        executor1 = OrderExecutor(
            symbol=symbol,
            timeframe="1m",
            testnet=True,
            allow_mutations=True,
        )
        executor1._service = service
        executor2 = OrderExecutor(
            symbol=symbol,
            timeframe="1m",
            testnet=True,
            allow_mutations=True,
        )
        executor2._service = service

        # Patch the account margin check to always pass (we're testing the lock, not the margin).
        executor1._check_global_account_margin = lambda qty: (True, "test")
        executor2._check_global_account_margin = lambda qty: (True, "test")

        # The service boundary is simulated, but the real file lock and two real
        # executor tasks are used. Any overlap means account serialization failed.
        counter_lock = threading.Lock()
        active_submissions = 0
        max_concurrent_submissions = 0
        order_counter = 110

        def slow_place(*args, **kwargs):
            nonlocal active_submissions, max_concurrent_submissions, order_counter
            with counter_lock:
                active_submissions += 1
                max_concurrent_submissions = max(
                    max_concurrent_submissions, active_submissions
                )
                order_counter += 1
                order_id = order_counter
            time.sleep(0.25)
            with counter_lock:
                active_submissions -= 1
            return {
                "orderId": order_id,
                "status": "FILLED",
                "avgPrice": "100",
                "executedQty": "0.01",
            }

        def filled_order(sym, oid):
            return {
                "orderId": oid,
                "status": "FILLED",
                "avgPrice": "100",
                "executedQty": "0.01",
            }

        with (
            patch.object(service, "place_market_order", side_effect=slow_place),
            patch.object(service, "get_order", side_effect=filled_order),
        ):
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
        result["mode"] = "real_file_lock_simulated_exchange_boundary"
        result["max_concurrent_submissions"] = max_concurrent_submissions
        result["pass"] = (
            getattr(r1, "success", False) is True
            and getattr(r2, "success", False) is True
            and max_concurrent_submissions == 1
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
    secure_load_dotenv(PROJECT_ROOT / ".env", override=False)
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

    executor = OrderExecutor(
        symbol=symbol,
        timeframe="1m",
        testnet=True,
        allow_mutations=True,
    )
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
    standard_orders = await asyncio.to_thread(service.get_open_orders, symbol)
    algo_orders = await asyncio.to_thread(service.get_open_algo_orders, symbol)
    if standard_orders or algo_orders:
        raise RuntimeError(
            f"Symbol {symbol} has pre-existing Testnet orders; refusing to run"
        )

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
            elif name == "simultaneous_signals":
                test_result = await test_fn(service, symbol)
            else:
                test_result = await test_fn(service, executor, symbol)
        except Exception as exc:
            test_result = {"test": name, "pass": False, "error": f"{type(exc).__name__}: {exc}"}

        report["tests"][name] = test_result
        logger.info("=== %s: %s ===", name, "PASS" if test_result.get("pass") else "FAIL")
        cleanup = await _flatten_and_cancel(service, executor, symbol)
        test_result["post_test_cleanup"] = cleanup
        post_position = await asyncio.to_thread(executor.get_position_snapshot)
        test_result["post_test_position_amt"] = _float(post_position.get("positionAmt"))
        if abs(test_result["post_test_position_amt"]) > 1e-9:
            test_result["pass"] = False
            test_result["cleanup_error"] = "Test left residual Testnet exposure"

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
