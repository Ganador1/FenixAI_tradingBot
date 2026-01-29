# src/trading/binance_client.py
"""
Cliente de Binance Futures para Fenix.

Maneja la comunicación con Binance:
- Conexión REST y WebSocket
- Obtención de datos de mercado
- Ejecución de órdenes
- Gestión de cuenta

Compatible con Testnet para paper trading.
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
    """Configuración del cliente Binance."""
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
            self.ws_url = "wss://stream.binancefuture.com/ws"  # Testnet WebSocket
        else:
            self.base_url = "https://fapi.binance.com"
            self.ws_url = "wss://fstream.binance.com/ws"


class BinanceClient:
    """
    Cliente asíncrono para Binance Futures.
    
    Uso:
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
        # Usar credenciales de testnet si está habilitado
        if testnet:
            default_key = os.getenv("BINANCE_TESTNET_API_KEY", os.getenv("BINANCE_API_KEY", ""))
            default_secret = os.getenv("BINANCE_TESTNET_API_SECRET", os.getenv("BINANCE_API_SECRET", ""))
        else:
            default_key = os.getenv("BINANCE_API_KEY", "")
            default_secret = os.getenv("BINANCE_API_SECRET", "")
        
        self.config = BinanceConfig(
            api_key=api_key or default_key,
            api_secret=api_secret or default_secret,
            testnet=testnet,
        )
        
        self._session = None
        self._ws = None
        self._connected = False
    
    async def connect(self) -> bool:
        """Establece conexión con Binance."""
        try:
            import httpx
            
            self._session = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=30.0,
            )
            
            # Verificar conexión
            response = await self._session.get("/fapi/v1/ping")
            if response.status_code == 200:
                self._connected = True
                mode = "TESTNET" if self.config.testnet else "LIVE"
                logger.info(f"Conectado a Binance Futures ({mode})")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error conectando a Binance: {e}")
            return False
    
    async def close(self) -> None:
        """Cierra las conexiones."""
        if self._session:
            await self._session.aclose()
            self._session = None
        self._connected = False
        logger.info("Conexión a Binance cerrada")
    
    def _sign_request(self, params: dict) -> dict:
        """Firma una petición con HMAC SHA256."""
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
        """Retorna headers con API key."""
        return {"X-MBX-APIKEY": self.config.api_key}
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> dict | list | None:
        """Realiza una petición a la API."""
        if not self._session:
            raise RuntimeError("Cliente no conectado")
        
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
                raise ValueError(f"Método no soportado: {method}")
            
            if response.status_code == 200:
                return response.json()
            else:
                error_msg = response.text
                logger.error(f"Error API: {response.status_code} - {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"Error en petición: {e}")
            return None
    
    # === Métodos públicos ===
    
    async def get_ticker(self, symbol: str) -> dict | None:
        """Obtiene ticker de 24h de un símbolo."""
        return await self._request(
            "GET",
            "/fapi/v1/ticker/24hr",
            {"symbol": symbol},
        )
    
    async def get_price(self, symbol: str) -> float | None:
        """Obtiene el último precio de un símbolo."""
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
        """Obtiene velas históricas."""
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
        """Obtiene el order book."""
        return await self._request(
            "GET",
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": limit},
        )
    
    async def get_balance(self, asset: str = "USDT") -> float:
        """Obtiene el balance de un asset."""
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
        """Obtiene posiciones abiertas."""
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
        
        # Filtrar posiciones con tamaño > 0
        return [
            p for p in data
            if float(p.get("positionAmt", 0)) != 0
        ]
    
    async def place_order(
        self,
        symbol: str,
        side: str,  # "BUY" o "SELL"
        quantity: float,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> dict | None:
        """Coloca una orden."""
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
            logger.info(f"Orden colocada: {order.get('orderId')}")
            
            # Colocar SL/TP si se especificaron
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
        """Coloca orden de stop loss."""
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
        """Coloca orden de take profit."""
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
        """Cancela una orden."""
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
        """Cancela todas las órdenes de un símbolo."""
        return await self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )


# === Función de utilidad ===

async def test_connection(testnet: bool = True) -> bool:
    """Prueba la conexión a Binance."""
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
