#!/usr/bin/env python3
"""Run one minimal, protected Binance Futures Testnet order lifecycle.

The script is intentionally testnet-only. It refuses to run when testnet
credentials are absent or when the selected symbol already has exposure or
open orders. Every exit path attempts to cancel protection and flatten the
testnet symbol before writing a JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.binance_service import BinanceService  # noqa: E402
from src.trading.executor import OrderExecutor  # noqa: E402
from src.trading.user_data_stream import FuturesUserDataStream  # noqa: E402


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _select_testnet_credentials(index: int) -> tuple[str, str]:
    key = os.getenv(f"BINANCE_TESTNET_API_KEY_{index}") or os.getenv(
        "BINANCE_TESTNET_API_KEY"
    )
    secret = os.getenv(f"BINANCE_TESTNET_API_SECRET_{index}") or os.getenv(
        "BINANCE_TESTNET_API_SECRET"
    )
    if not key or not secret:
        raise RuntimeError(
            "Missing Binance Testnet credentials. Configure "
            f"BINANCE_TESTNET_API_KEY_{index} and BINANCE_TESTNET_API_SECRET_{index}."
        )
    return key, secret


def _extract_order_id(order: dict[str, Any]) -> int | str | None:
    return order.get("algoId") or order.get("orderId") or order.get("clientAlgoId")


async def _cancel_every_open_order(service: BinanceService, symbol: str) -> list[str]:
    outcomes: list[str] = []
    standard_orders = await asyncio.to_thread(service.get_open_orders, symbol)
    for order in standard_orders or []:
        order_id = _extract_order_id(order)
        if order_id is None:
            continue
        try:
            await asyncio.to_thread(service.cancel_order, symbol, order_id)
            outcomes.append(f"standard:{order_id}:cancelled")
        except Exception as exc:
            outcomes.append(f"standard:{order_id}:error:{type(exc).__name__}")

    algo_orders = await asyncio.to_thread(service.get_open_algo_orders, symbol)
    for order in algo_orders or []:
        order_id = _extract_order_id(order)
        if order_id is None:
            continue
        try:
            await asyncio.to_thread(service.cancel_algo_order, symbol, order_id)
            outcomes.append(f"algo:{order_id}:cancelled")
        except Exception as exc:
            outcomes.append(f"algo:{order_id}:error:{type(exc).__name__}")
    return outcomes


async def _flatten_symbol(executor: OrderExecutor) -> dict[str, Any]:
    snapshot = await asyncio.to_thread(executor.get_position_snapshot)
    amount = _float(snapshot.get("positionAmt"))
    if abs(amount) <= 1e-12:
        return {"attempted": False, "flat": True}
    close_result = await executor.execute_market_order(
        side="SELL" if amount > 0 else "BUY",
        quantity=abs(amount),
        reduce_only=True,
    )
    final_snapshot = await asyncio.to_thread(executor.get_position_snapshot)
    final_amount = _float(final_snapshot.get("positionAmt"))
    return {
        "attempted": True,
        "result": close_result.to_dict(),
        "final_position_amt": final_amount,
        "flat": abs(final_amount) <= 1e-12,
    }


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key, secret = _select_testnet_credentials(args.api_key_index)
    os.environ["BINANCE_TESTNET_API_KEY"] = key
    os.environ["BINANCE_TESTNET_API_SECRET"] = secret
    os.environ["FENIX_GLOBAL_PORTFOLIO_GUARD"] = "1"
    os.environ["FENIX_REQUIRE_LIVE_STOP_LOSS"] = "1"
    os.environ["FENIX_PYRAMID_ENABLE"] = "0"
    os.environ["FENIX_ALLOW_ADD_TO_POSITION"] = "0"
    os.environ.setdefault("FENIX_MAX_ACCOUNT_MARGIN_PCT", "0.50")
    os.environ.setdefault("FENIX_LEVERAGE", "10")

    symbol = args.symbol.upper()
    service = BinanceService(key, secret, testnet=True)
    if not service.initialize():
        raise RuntimeError("Could not initialize Binance Futures Testnet")
    executor = OrderExecutor(symbol=symbol, timeframe=args.timeframe, testnet=True)
    executor._service = service
    user_events: list[dict[str, Any]] = []

    async def _capture_user_event(event: dict[str, Any]) -> None:
        order = event.get("o") if isinstance(event.get("o"), dict) else {}
        user_events.append(
            {
                "event_type": event.get("e") or event.get("eventType"),
                "symbol": order.get("s") or event.get("s"),
                "order_status": order.get("X"),
                "execution_type": order.get("x"),
                "order_type": order.get("o"),
            }
        )

    user_stream = FuturesUserDataStream(
        api_key=key,
        api_secret=secret,
        testnet=True,
        on_event=_capture_user_event,
    )

    report: dict[str, Any] = {
        "network": "binance_futures_testnet",
        "symbol": symbol,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_key_index": args.api_key_index,
    }
    try:
        await user_stream.start()
        permissions_ok, permission_errors = await asyncio.to_thread(service.validate_permissions)
        if not permissions_ok:
            raise RuntimeError(f"Testnet key cannot trade: {permission_errors}")

        initial_position = await asyncio.to_thread(executor.get_position_snapshot)
        initial_amount = _float(initial_position.get("positionAmt"))
        initial_standard = await asyncio.to_thread(service.get_open_orders, symbol)
        initial_algo = await asyncio.to_thread(service.get_open_algo_orders, symbol)
        if abs(initial_amount) > 1e-12 or initial_standard or initial_algo:
            raise RuntimeError(
                "Smoke test refuses to touch a symbol with existing testnet exposure or orders"
            )

        ticker = await asyncio.to_thread(service.get_ticker_price, symbol)
        config = await asyncio.to_thread(service.get_symbol_config, symbol)
        if not config or ticker <= 0:
            raise RuntimeError(f"Missing symbol configuration or ticker for {symbol}")
        raw_quantity = (config.min_notional * args.min_notional_multiplier) / ticker
        quantity = math.ceil(raw_quantity / config.step_size) * config.step_size
        quantity = round(quantity, config.quantity_precision)
        stop_loss = round(ticker * (1.0 - args.stop_pct), config.price_precision)
        take_profit = round(ticker * (1.0 + args.take_profit_pct), config.price_precision)

        report["preflight"] = {
            "permissions_ok": permissions_ok,
            "equity": await asyncio.to_thread(service.get_balance_usdt),
            "available": await asyncio.to_thread(service.get_available_balance_usdt),
            "ticker": ticker,
            "quantity": quantity,
            "notional": quantity * ticker,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

        result = await executor.execute_market_order(
            side="BUY",
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        report["entry"] = result.to_dict()
        report["monitor"] = executor.get_protection_status()
        report["visible_standard_orders"] = len(
            await asyncio.to_thread(service.get_open_orders, symbol)
        )
        report["visible_algo_orders"] = len(
            await asyncio.to_thread(service.get_open_algo_orders, symbol)
        )
        if not result.success:
            raise RuntimeError(f"Protected entry failed: {result.status}: {result.message}")
        if result.position_id is None:
            raise RuntimeError("Protected entry was not registered in OrderMonitor")
        report["success"] = True
        return report
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        try:
            report["cleanup_orders"] = await _cancel_every_open_order(service, symbol)
            report["cleanup_position"] = await _flatten_symbol(executor)
            report["remaining_standard_orders"] = len(
                await asyncio.to_thread(service.get_open_orders, symbol)
            )
            report["remaining_algo_orders"] = len(
                await asyncio.to_thread(service.get_open_algo_orders, symbol)
            )
        except Exception as exc:
            report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.5)
        report["user_data_stream"] = user_stream.get_status()
        report["user_data_events"] = user_events
        await user_stream.stop()
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        service.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--api-key-index", type=int, choices=(1, 2), default=1)
    parser.add_argument("--min-notional-multiplier", type=float, default=1.25)
    parser.add_argument("--stop-pct", type=float, default=0.01)
    parser.add_argument("--take-profit-pct", type=float, default=0.01)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "logs" / "testnet_order_lifecycle_smoke.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {}
    exit_code = 0
    try:
        report = asyncio.run(run_smoke(args))
        cleanup = report.get("cleanup_position") or {}
        if not report.get("success"):
            exit_code = 1
        if not cleanup.get("flat"):
            exit_code = 2
    except Exception as exc:
        report.setdefault("error", f"{type(exc).__name__}: {exc}")
        exit_code = 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"report_path={args.report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
