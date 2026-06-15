"""
Binance Service - Encapsulated Binance client and symbol management
Replaces global binance_client and symbol filter management
"""

import logging
import threading
import time
from typing import Any

try:
    from binance import enums as binance_enums
    from binance.client import Client
    from binance.exceptions import BinanceAPIException, BinanceOrderException

    BINANCE_AVAILABLE = True
except ImportError:
    try:
        from binance import Spot as Client
        from binance import enums as binance_enums
        from binance.exceptions import BinanceAPIException, BinanceOrderException

        BINANCE_AVAILABLE = True
    except ImportError:
        BINANCE_AVAILABLE = False
        Client = None
        binance_enums = None

        class BinanceAPIException(Exception):
            pass

        class BinanceOrderException(Exception):
            pass


from src.core.trading_constants import SymbolConfig

logger = logging.getLogger(__name__)


_TRANSIENT_ERROR_MARKERS = (
    "closed connection",
    "connection aborted",
    "remote end",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "502",
    "503",
    "504",
)


class BinanceService:
    """
    Encapsulated Binance service that manages client connections
    and symbol configurations without global state
    """

    def __init__(
        self, api_key: str | None = None, api_secret: str | None = None, testnet: bool = False
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self._client: Client | None = None
        self._symbol_filters: dict[str, list[dict[str, Any]]] = {}
        self._symbol_configs: dict[str, SymbolConfig] = {}
        self._exchange_info: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._initialized = False

        if not BINANCE_AVAILABLE:
            logger.warning("Binance client not available. Service will operate in mock mode.")

    def initialize(self) -> bool:
        """Initialize Binance client and load exchange info (Futures)"""
        with self._lock:
            if self._initialized:
                return True

            if not BINANCE_AVAILABLE:
                logger.error("Cannot initialize BinanceService: Binance client not available")
                return False

            try:
                # Initialize client for FUTURES
                self._client = Client(
                    api_key=self.api_key, api_secret=self.api_secret, testnet=self.testnet
                )

                # Load exchange info for FUTURES
                self._exchange_info = self._client.futures_exchange_info()

                # Process symbol filters
                self._process_symbol_filters()

                self._initialized = True
                logger.info(f"BinanceService initialized successfully (Testnet: {self.testnet})")
                return True

            except Exception as e:
                logger.error(f"Failed to initialize BinanceService: {e}")
                return False

    def _process_symbol_filters(self) -> None:
        """Process symbol filters from exchange info"""
        if not self._exchange_info:
            return

        for symbol_info in self._exchange_info.get("symbols", []):
            symbol = symbol_info["symbol"]
            filters = symbol_info.get("filters", [])

            self._symbol_filters[symbol] = filters

            # Create or update symbol configuration
            # Note: Futures filters structure might differ slightly from Spot, ensuring compatibility
            config = SymbolConfig.from_filters(symbol, filters)
            self._symbol_configs[symbol] = config

            logger.debug(f"Processed filters for {symbol}")

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)

    def _call_with_retries(self, fn, *args, retries: int = 1, delay: float = 0.35, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= retries or not self._is_transient_error(exc):
                    raise
                time.sleep(delay * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unexpected retry helper state")

    def get_account_info(self) -> dict[str, Any] | None:
        """Get account information (Futures)"""
        if not self._client:
            return None

        try:
            return self._client.futures_account()
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return None

    def get_symbol_config(self, symbol: str) -> SymbolConfig | None:
        """Return cached futures symbol config when available."""
        return self._symbol_configs.get(symbol)

    def get_balance_usdt(self) -> float:
        """Get USDT balance (Futures)"""
        if not self._client:
            logger.warning("Binance client not initialized when requesting balance")
            return 0.0

        try:
            account = self._call_with_retries(self._client.futures_account, retries=1)
            if isinstance(account, dict):
                # In single-asset mode totalMarginBalance only reflects the
                # USDT bucket, so a USDC-funded account would read near-zero.
                # Always compute the stablecoin sum and take the max.
                total_margin = float(account.get("totalMarginBalance", 0.0) or 0.0)

                stable_total = 0.0
                for asset in account.get("assets", []) or []:
                    if asset.get("asset") not in {"USDT", "USDC"}:
                        continue
                    margin_balance = float(asset.get("marginBalance", 0.0) or 0.0)
                    wallet_balance = float(asset.get("walletBalance", 0.0) or 0.0)
                    available = float(asset.get("availableBalance", 0.0) or 0.0)
                    stable_total += max(margin_balance, wallet_balance, available)

                best = max(total_margin, stable_total)
                if best > 0:
                    return best
        except Exception as e:
            logger.error(f"Failed to get futures account balance: {e}")

        try:
            balances = self._call_with_retries(self._client.futures_account_balance, retries=1)
            stable_total = 0.0
            for balance in balances:
                if balance.get("asset") in {"USDT", "USDC"}:
                    wallet = float(balance.get("balance", 0.0) or 0.0)
                    available = float(balance.get("availableBalance", 0.0) or 0.0)
                    stable_total += max(wallet, available)
            if stable_total > 0:
                return stable_total
        except Exception as e:
            logger.error(f"Failed to get USDT balance: {e}")
        return 0.0

    def get_ticker_price(self, symbol: str) -> float:
        """Get current ticker price (Futures)"""
        if not self._client:
            return 0.0

        try:
            ticker = self._client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"Failed to get ticker price for {symbol}: {e}")
            return 0.0

    def place_market_order(
        self, symbol: str, side: str, quantity: float, reduce_only: bool = False
    ) -> dict[str, Any]:
        """Place a market order (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._client.futures_create_order(
                symbol=symbol,
                side=side,
                type=binance_enums.ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=reduce_only,
            )
        except Exception as e:
            logger.error(f"Failed to place market order for {symbol}: {e}")
            raise e

    def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float, reduce_only: bool = False
    ) -> dict[str, Any]:
        """Place a limit order (Futures, post-only for maker fee)."""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._client.futures_create_order(
                symbol=symbol,
                side=side,
                type="LIMIT",
                timeInForce="GTX",  # Post-only: rejected if would fill immediately
                quantity=quantity,
                price=price,
                reduceOnly=reduce_only,
            )
        except Exception as e:
            logger.error(f"Failed to place limit order for {symbol}: {e}")
            raise e

    def place_algo_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        trigger_price: float,
        quantity: float | None = None,
        close_position: bool = False,
        working_type: str = "MARK_PRICE",
    ) -> dict[str, Any]:
        """
        Place an Algo Order (Conditional TP/SL) using /fapi/v1/algoOrder
        Supported since Binance migration in Dec 2025 (error -4120 otherwise)
        """
        if not self._client:
            raise Exception("Binance client not initialized")

        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": str(trigger_price),
            "workingType": working_type,
        }

        if close_position:
            params["closePosition"] = "true"
        elif quantity:
            params["quantity"] = str(quantity)
            params["reduceOnly"] = "true"

        try:
            # Use raw request since python-binance might not have a high-level wrapper for algoOrder yet
            return self._client._request_futures_api("post", "algoOrder", True, data=params)
        except Exception as e:
            logger.error(f"Failed to place algo order ({order_type}) for {symbol}: {e}")
            raise e

    def _place_trigger_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        stop_price: float,
        quantity: float | None = None,
        close_position: bool = True,
        working_type: str = "MARK_PRICE",
    ) -> dict[str, Any]:
        """Place STOP_MARKET / TAKE_PROFIT_MARKET using the standard futures order API."""
        if not self._client:
            raise Exception("Binance client not initialized")

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "stopPrice": str(stop_price),
            "workingType": working_type,
        }
        if close_position:
            params["closePosition"] = "true"
        elif quantity is not None:
            params["quantity"] = str(quantity)
            params["reduceOnly"] = "true"

        try:
            return self._client.futures_create_order(**params)
        except Exception as e:
            logger.error(f"Failed to place trigger order ({order_type}) for {symbol}: {e}")
            raise e

    def place_stop_loss_market(
        self,
        symbol: str,
        side: str,
        quantity: float | None,
        stop_price: float,
        *,
        close_position: bool = True,
    ) -> dict[str, Any]:
        """Place a stop loss market order (Futures)."""
        return self._place_trigger_order(
            symbol=symbol,
            side=side,
            order_type="STOP_MARKET",
            stop_price=stop_price,
            quantity=quantity,
            close_position=close_position,
        )

    def place_take_profit_market(
        self,
        symbol: str,
        side: str,
        quantity: float | None,
        stop_price: float,
        *,
        close_position: bool = True,
    ) -> dict[str, Any]:
        """Place a take profit market order (Futures)."""
        return self._place_trigger_order(
            symbol=symbol,
            side=side,
            order_type="TAKE_PROFIT_MARKET",
            stop_price=stop_price,
            quantity=quantity,
            close_position=close_position,
        )

    def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Get order details (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._client.futures_get_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            raise e

    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an order (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise e

    def cancel_all_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Cancel all open orders (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"Failed to cancel all orders for {symbol}: {e}")
            return []

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Get currently open futures orders for a symbol."""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            return self._call_with_retries(
                self._client.futures_get_open_orders,
                symbol=symbol,
                retries=1,
            )
        except Exception as e:
            logger.error(f"Failed to get open orders for {symbol}: {e}")
            return []

    def get_open_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Get currently open conditional/algo futures orders for a symbol."""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            result = self._call_with_retries(
                self._client._request_futures_api,
                "get",
                "openAlgoOrders",
                True,
                data={"symbol": symbol},
                retries=1,
            )
            if isinstance(result, dict):
                orders = result.get("orders") or result.get("data") or result.get("rows") or []
                return orders if isinstance(orders, list) else []
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.debug(f"Failed to get open algo orders for {symbol}: {e}")
            return []

    def get_position(self, symbol: str) -> dict[str, Any]:
        """Get current position (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")

        try:
            positions = self._call_with_retries(
                self._client.futures_position_information,
                symbol=symbol,
                retries=1,
            )
            for pos in positions:
                if float(pos.get("positionAmt", 0) or 0) != 0:
                    return pos
            return positions[0] if positions else {}
        except Exception as e:
            logger.error(f"Failed to get position for {symbol}: {e}")
            raise

    def get_account_trades(
        self,
        symbol: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent futures account trades for a symbol."""
        if not self._client:
            raise Exception("Binance client not initialized")

        params: dict[str, Any] = {"symbol": symbol, "limit": max(1, min(int(limit), 1000))}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)

        try:
            return self._call_with_retries(self._client.futures_account_trades, retries=1, **params)
        except Exception as e:
            logger.error(f"Failed to get account trades for {symbol}: {e}")
            return []

    def get_income_history(
        self,
        symbol: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent futures income history for a symbol."""
        if not self._client:
            raise Exception("Binance client not initialized")

        params: dict[str, Any] = {"symbol": symbol, "limit": max(1, min(int(limit), 1000))}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)

        try:
            return self._call_with_retries(self._client.futures_income_history, retries=1, **params)
        except Exception as e:
            logger.error(f"Failed to get income history for {symbol}: {e}")
            return []

    def validate_permissions(self) -> tuple[bool, list[str]]:
        """Validate the current API key can trade on futures."""
        if not self._client:
            return False, ["Binance client not initialized"]

        account = self.get_account_info()
        if not account:
            return False, ["Could not retrieve Binance account information"]

        if account.get("canTrade") is False:
            return False, ["API key does not have trading permission"]

        return True, []

    def close(self) -> None:
        """Close Binance client connections"""
        if self._client:
            try:
                self._client.close_connection()
                logger.info("Binance client connection closed")
            except Exception as e:
                logger.error(f"Error closing Binance client: {e}")
            finally:
                self._client = None
                self._initialized = False


# Global service instances (separate for testnet and production)
_binance_services: dict[bool, BinanceService | None] = {True: None, False: None}
_service_lock = threading.Lock()


def get_binance_service(
    api_key: str | None = None, api_secret: str | None = None, testnet: bool = False
) -> BinanceService:
    """Get or create a Binance service instance (separate for testnet and production)"""
    global _binance_services

    if _binance_services[testnet] is None:
        with _service_lock:
            if _binance_services[testnet] is None:
                import os

                # Use provided keys or fallback to env vars
                if testnet:
                    key = api_key or os.getenv("BINANCE_TESTNET_API_KEY")
                    secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET")
                else:
                    key = api_key or os.getenv("BINANCE_API_KEY")
                    secret = api_secret or os.getenv("BINANCE_API_SECRET")

                service = BinanceService(key, secret, testnet)
                # Auto-initialize the service
                if service.initialize():
                    logger.info(f"✅ BinanceService initialized (testnet={testnet})")
                else:
                    logger.error(f"❌ Failed to initialize BinanceService (testnet={testnet})")

                _binance_services[testnet] = service

    return _binance_services[testnet]


def reset_binance_service(testnet: bool = None) -> None:
    """Reset the global Binance service instance (for testing)"""
    global _binance_services
    with _service_lock:
        if testnet is None:
            # Reset both
            for key in [True, False]:
                if _binance_services[key]:
                    _binance_services[key].close()
                _binance_services[key] = None
        else:
            if _binance_services[testnet]:
                _binance_services[testnet].close()
            _binance_services[testnet] = None
