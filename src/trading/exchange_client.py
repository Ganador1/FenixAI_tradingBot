# src/trading/exchange_client.py
"""
Unified Exchange Client for Fenix using ccxt.

This module provides a generic interface for interacting with multiple cryptocurrency
exchanges, focusing on those available in the USA.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, cast

import ccxt.async_support as ccxt
from ccxt.base.errors import AuthenticationError, ExchangeError

logger = logging.getLogger(__name__)


class ExchangeClient:
    """
    A unified client for interacting with multiple exchanges via ccxt.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool = False,
    ):
        """
        Initializes the exchange client.

        Args:
            exchange_id: The ID of the exchange (e.g., 'coinbasepro', 'kraken').
            api_key: The API key for the exchange.
            api_secret: The API secret for the exchange.
            testnet: Whether to use the testnet/sandbox environment.
        """
        self.exchange_id = exchange_id
        self.testnet = testnet
        self._exchange: ccxt.Exchange | None = None

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Exchange '{exchange_id}' is not supported by ccxt.")

        exchange_class = getattr(ccxt, exchange_id)
        self._exchange = exchange_class({
            'apiKey': api_key,
            'secret': api_secret,
        })

        if testnet:
            if self._exchange.has['sandbox']:
                self._exchange.set_sandbox_mode(True)
            else:
                logger.warning(f"Exchange '{exchange_id}' does not support a sandbox/testnet environment.")

    async def connect(self) -> bool:
        """
        Connects to the exchange and verifies the connection.
        """
        if not self._exchange:
            return False
        try:
            await self._exchange.load_markets()
            # Try a lightweight, non-authenticated endpoint to verify connection
            await self._exchange.fetch_time()
            logger.info(f"Successfully connected to {self.exchange_id}.")
            return True
        except (AuthenticationError, ExchangeError) as e:
            logger.error(f"Failed to connect to {self.exchange_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during connection: {e}")
            return False

    async def close(self) -> None:
        """
        Closes the connection to the exchange.
        """
        if self._exchange:
            await self._exchange.close()
            logger.info(f"Connection to {self.exchange_id} closed.")

    async def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        """
        Gets the 24h ticker for a symbol.
        """
        if not self._exchange:
            return None
        try:
            return await self._exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            return None

    async def get_price(self, symbol: str) -> float | None:
        """
        Gets the last price of a symbol.
        """
        ticker = await self.get_ticker(symbol)
        return cast(float, ticker['last']) if ticker and 'last' in ticker else None

    async def get_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Gets historical OHLCV candles.
        """
        if not self._exchange or not self._exchange.has['fetchOHLCV']:
            logger.warning(f"Exchange '{self.exchange_id}' does not support fetching OHLCV data.")
            return []
        try:
            klines = await self._exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
            # Convert to the same format as BinanceClient
            return [
                {
                    "timestamp": k[0],
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "volume": k[5],
                }
                for k in klines
            ]
        except Exception as e:
            logger.error(f"Failed to fetch klines for {symbol}: {e}")
            return []

    async def get_balance(self, asset: str = "USDT") -> float:
        """
        Gets the balance of an asset.
        """
        if not self._exchange or not self._exchange.has['fetchBalance']:
            return 0.0
        try:
            balance = await self._exchange.fetch_balance()
            return cast(float, balance.get(asset, {}).get('free', 0.0))
        except Exception as e:
            logger.error(f"Failed to fetch balance for {asset}: {e}")
            return 0.0

    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Places an order with optional stop-loss and take-profit.
        """
        if not self._exchange:
            return None

        params = {}
        if stop_loss:
            params['stopLoss'] = {
                'type': 'stopMarket',
                'triggerPrice': stop_loss,
            }
        if take_profit:
            params['takeProfit'] = {
                'type': 'takeProfitMarket',
                'triggerPrice': take_profit,
            }

        try:
            if order_type == "market":
                return await self._exchange.create_market_order(symbol, side, quantity, params=params)
            elif order_type == "limit" and price:
                return await self._exchange.create_limit_order(symbol, side, quantity, price, params=params)
            else:
                logger.error(f"Unsupported order type '{order_type}' or missing price for limit order.")
                return None
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any] | None:
        """
        Cancels an order.
        """
        if not self._exchange or not self._exchange.has['cancelOrder']:
            return None
        try:
            return await self._exchange.cancel_order(order_id, symbol)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return None

    async def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """
        Fetches open orders for a symbol.
        """
        if not self._exchange or not self._exchange.has['fetchOpenOrders']:
            return []
        try:
            return await self._exchange.fetch_open_orders(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch open orders for {symbol}: {e}")
            return []
