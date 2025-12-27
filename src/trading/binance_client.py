# src/trading/binance_client.py
"""
Binance Futures client for Fenix.

Handles communication with Binance:
- REST and WebSocket connection
- Market data retrieval
- Order execution
- Account management

Testnet compatible for paper trading.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


@dataclass
class BinanceConfig:
    """Binance client configuration."""
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    recv_window: int = 5000
    
    # URLs
    base_url: str = ""
    ws_url: str = ""
    
    def __post_init__(self):
        if self.testnet:
            self.base_url = "https://testnet.binancefuture.com"
            self.ws_url = "wss://stream.binancefuture.com"
        else:
            self.base_url = "https://fapi.binance.com"
            self.ws_url = "wss://fstream.binance.com"


class BinanceClient:
    """
    Asynchronous client for Binance Futures.
    
    Usage:
        client = BinanceClient(testnet=True)
        await client.connect()
        ticker = await client.get_ticker("BTCUSDT")
        await client.close()
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool = True,
    ):
        self.config = BinanceConfig(
            api_key=api_key or os.getenv("BINANCE_API_KEY", ""),
            api_secret=api_secret or os.getenv("BINANCE_API_SECRET", ""),
            testnet=testnet,
        )
        
        self._session = None
        self._ws = None
        self._connected = False
    
    async def connect(self) -> bool:
        """Establishes a connection to Binance."""
        try:
            import httpx
            
            self._session = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=30.0,
            )
            
            # Verify connection
            response = await self._session.get("/fapi/v1/ping")
            if response.status_code == 200:
                self._connected = True
                mode = "TESTNET" if self.config.testnet else "LIVE"
                logger.info(f"Connected to Binance Futures ({mode})")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error connecting to Binance: {e}")
            return False
    
    async def close(self) -> None:
        """Closes the connections."""
        if self._session:
            await self._session.aclose()
            self._session = None
        self._connected = False
        logger.info("Connection to Binance closed")
    
    def _sign_request(self, params: dict) -> dict:
        """Signs a request with HMAC SHA256."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.config.recv_window
        
        query_string = urlencode(params)
        signature = hmac.new(
            self.config.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        params["signature"] = signature
        return params
    
    def _get_headers(self) -> dict:
        """Returns headers with API key."""
        return {"X-MBX-APIKEY": self.config.api_key}
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> dict | list | None:
        """Makes a request to the API."""
        if not self._session:
            raise RuntimeError("Client not connected")
        
        params = params or {}
        headers = {}
        
        if signed:
            params = self._sign_request(params)
            headers = self._get_headers()
        
        try:
            if method == "GET":
                response = await self._session.get(
                    endpoint,
                    params=params,
                    headers=headers,
                )
            elif method == "POST":
                response = await self._session.post(
                    endpoint,
                    params=params,
                    headers=headers,
                )
            elif method == "DELETE":
                response = await self._session.delete(
                    endpoint,
                    params=params,
                    headers=headers,
                )
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            if response.status_code == 200:
                return response.json()
            else:
                error_msg = response.text
                logger.error(f"API Error: {response.status_code} - {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    
    # === Public methods ===
    
    async def get_ticker(self, symbol: str) -> dict | None:
        """Gets 24h ticker for a symbol."""
        return await self._request(
            "GET",
            "/fapi/v1/ticker/24hr",
            {"symbol": symbol},
        )
    
    async def get_price(self, symbol: str) -> float | None:
        """Gets the last price of a symbol."""
        data = await self._request(
            "GET",
            "/fapi/v1/ticker/price",
            {"symbol": symbol},
        )
        return float(data["price"]) if data else None
    
    async def get_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 100,
        start_time: int | float | None = None,
        end_time: int | float | None = None,
    ) -> list[dict]:
        """Gets historical candles."""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        # Convert datetimes in ms if provided
        if start_time is not None:
            if isinstance(start_time, (int, float)):
                params["startTime"] = int(start_time)
            else:
                # Allow datetime-like strings or objects by attempting conversion
                try:
                    from datetime import datetime
                    if isinstance(start_time, datetime):
                        params["startTime"] = int(start_time.timestamp() * 1000)
                except Exception:
                    pass
        if end_time is not None:
            if isinstance(end_time, (int, float)):
                params["endTime"] = int(end_time)
            else:
                try:
                    from datetime import datetime
                    if isinstance(end_time, datetime):
                        params["endTime"] = int(end_time.timestamp() * 1000)
                except Exception:
                    pass

        data = await self._request(
            "GET",
            "/fapi/v1/klines",
            params,
        )
        
        if not data:
            return []
        
        return [
            {
                "timestamp": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": k[8],
            }
            for k in data
        ]
    
    async def get_orderbook(
        self,
        symbol: str,
        limit: int = 20,
    ) -> dict | None:
        """Gets the order book."""
        return await self._request(
            "GET",
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": limit},
        )
    
    async def get_balance(self, asset: str = "USDT") -> float:
        """Gets the balance of an asset."""
        data = await self._request(
            "GET",
            "/fapi/v2/balance",
            signed=True,
        )
        
        if not data:
            return 0.0
        
        for item in data:
            if item.get("asset") == asset:
                return float(item.get("balance", 0))
        
        return 0.0
    
    async def get_positions(self, symbol: str | None = None) -> list[dict]:
        """Gets open positions."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        data = await self._request(
            "GET",
            "/fapi/v2/positionRisk",
            params,
            signed=True,
        )
        
        if not data:
            return []
        
        # Filter positions with size > 0
        return [
            p for p in data
            if float(p.get("positionAmt", 0)) != 0
        ]
    
    async def place_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> dict | None:
        """Places an order."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": f"{quantity:.6f}",
        }
        
        if order_type == "LIMIT" and price:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"
        
        if reduce_only:
            params["reduceOnly"] = "true"
        
        order = await self._request(
            "POST",
            "/fapi/v1/order",
            params,
            signed=True,
        )
        
        if order:
            logger.info(f"Order placed: {order.get('orderId')}")
            
            # Place SL/TP if specified
            if stop_loss:
                await self._place_stop_loss(symbol, side, quantity, stop_loss)
            if take_profit:
                await self._place_take_profit(symbol, side, quantity, take_profit)
        
        return order
    
    async def _place_stop_loss(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> dict | None:
        """Places a stop loss order."""
        sl_side = "SELL" if side == "BUY" else "BUY"
        params = {
            "symbol": symbol,
            "side": sl_side,
            "type": "STOP_MARKET",
            "stopPrice": f"{price:.2f}",
            "quantity": f"{quantity:.6f}",
            "reduceOnly": "true",
        }
        return await self._request("POST", "/fapi/v1/order", params, signed=True)
    
    async def _place_take_profit(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> dict | None:
        """Places a take profit order."""
        tp_side = "SELL" if side == "BUY" else "BUY"
        params = {
            "symbol": symbol,
            "side": tp_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": f"{price:.2f}",
            "quantity": f"{quantity:.6f}",
            "reduceOnly": "true",
        }
        return await self._request("POST", "/fapi/v1/order", params, signed=True)
    
    async def cancel_order(
        self,
        symbol: str,
        order_id: int | None = None,
    ) -> dict | None:
        """Cancels an order."""
        params = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        
        return await self._request(
            "DELETE",
            "/fapi/v1/order",
            params,
            signed=True,
        )
    
    async def cancel_all_orders(self, symbol: str) -> dict | None:
        """Cancels all orders for a symbol."""
        return await self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )


# === Utility function ===

async def test_connection(testnet: bool = True) -> bool:
    """Tests the connection to Binance."""
    client = BinanceClient(testnet=testnet)
    connected = await client.connect()
    
    if connected:
        price = await client.get_price("BTCUSDT")
        print(f"BTC Price: ${price:,.2f}")
    
    await client.close()
    return connected


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_connection())
