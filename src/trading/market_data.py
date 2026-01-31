# src/trading/market_data.py
"""
Gestión de datos de mercado en tiempo real para Fenix Trading Bot.

Este módulo centraliza:
- Conexión WebSocket a Binance
- Procesamiento de klines (velas)
- Order book y microestructura (OBI, CVD)
- Cache de indicadores técnicos
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import websockets

from src.trading.binance_client import BinanceClient

logger = logging.getLogger("FenixMarketData")


@dataclass
class OrderBookSnapshot:
    """Snapshot del order book con bids y asks."""
    bids: list[list[float]] = field(default_factory=list)
    asks: list[list[float]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def get_best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0
    
    def get_best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0
    
    def get_spread(self) -> float:
        if self.bids and self.asks:
            return self.asks[0][0] - self.bids[0][0]
        return 0.0
    
    def get_mid_price(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return 0.0


@dataclass
class MicrostructureMetrics:
    """Métricas de microestructura del mercado."""
    obi: float = 1.0  # Order Book Imbalance
    cvd: float = 0.0  # Cumulative Volume Delta
    spread: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    liquidity: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "obi": self.obi,
            "cvd": self.cvd,
            "spread": self.spread,
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "liquidity": self.liquidity,
        }


class MarketDataManager:
    """
    Gestiona datos de mercado en tiempo real.
    
    Responsabilidades:
    - WebSocket connections a Binance
    - Procesamiento de klines
    - Cálculo de métricas de microestructura
    - Buffer de trades para CVD
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        use_testnet: bool = False,
    ):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.use_testnet = use_testnet
        
        # WebSocket URLs
        base_url = "stream.binancefuture" if use_testnet else "fstream.binance"
        self.kline_ws_url = f"wss://{base_url}.com/ws/{self.symbol.lower()}@kline_{timeframe}"
        self.depth_ws_url = f"wss://{base_url}.com/ws/{self.symbol.lower()}@depth20@100ms"
        self.trade_ws_url = f"wss://{base_url}.com/ws/{self.symbol.lower()}@aggTrade"
        
        # State
        self.orderbook = OrderBookSnapshot()
        self.trade_buffer: deque = deque(maxlen=500)
        self.cvd_value: float = 0.0
        self.current_price: float = 0.0
        self.current_volume: float = 0.0
        
        # Callbacks
        self._kline_callbacks: list[Callable] = []
        self._microstructure_callbacks: list[Callable] = []
        
        # Tasks
        self._tasks: list[asyncio.Task] = []
        self._running = False
        
        logger.info(f"MarketDataManager initialized for {symbol}@{timeframe}")
    
    def on_kline(self, callback: Callable[[dict], None]) -> None:
        """Registra callback para nuevas klines."""
        self._kline_callbacks.append(callback)
    
    def on_microstructure_update(self, callback: Callable[[MicrostructureMetrics], None]) -> None:
        """Registra callback para actualizaciones de microestructura."""
        self._microstructure_callbacks.append(callback)
    
    async def start(self) -> None:
        """Inicia todas las conexiones WebSocket."""
        if self._running:
            logger.warning("MarketDataManager already running")
            return
        
        self._running = True
        logger.info(f"Starting MarketDataManager for {self.symbol}")

        # Prefill de velas históricas para evitar gráficos vacíos en timeframes cortos
        await self._prefill_klines()
        
        # Iniciar tareas de WebSocket
        self._tasks = [
            asyncio.create_task(self._run_kline_ws()),
            asyncio.create_task(self._run_depth_ws()),
            asyncio.create_task(self._run_trade_ws()),
        ]
        
        logger.info("All WebSocket connections started")

    async def _prefill_klines(self, limit: int = 200) -> None:
        """Carga velas históricas al iniciar para llenar buffers y gráficos."""
        try:
            client = BinanceClient(testnet=self.use_testnet)
            if not await client.connect():
                logger.warning("Could not connect to Binance for prefill")
                return

            klines = await client.get_klines(self.symbol, self.timeframe, limit=limit)
            if not klines:
                logger.warning("No historical klines for prefill")
                await client.close()
                return

            for k in klines:
                kline_data = {
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "open_time": k.get("timestamp"),
                    "close_time": k.get("close_time"),
                    "open": float(k.get("open", 0)),
                    "high": float(k.get("high", 0)),
                    "low": float(k.get("low", 0)),
                    "close": float(k.get("close", 0)),
                    "volume": float(k.get("volume", 0)),
                    "is_closed": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                for callback in self._kline_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(kline_data)
                        else:
                            callback(kline_data)
                    except Exception as e:
                        logger.error(f"Error in kline prefill callback: {e}")

            await client.close()
            logger.info(f"Prefilled {len(klines)} historical klines")

        except Exception as e:
            logger.warning(f"Prefill klines failed: {e}")
    
    async def stop(self) -> None:
        """Detiene todas las conexiones."""
        self._running = False
        
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        self._tasks.clear()
        logger.info("MarketDataManager stopped")
    
    async def _run_kline_ws(self) -> None:
        """WebSocket para klines."""
        while self._running:
            try:
                async with websockets.connect(self.kline_ws_url) as ws:
                    logger.info(f"Connected to kline stream: {self.kline_ws_url}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        try:
                            data = json.loads(message)
                            await self._process_kline(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error in kline: {e}")
                            
            except websockets.ConnectionClosed:
                logger.warning("Kline WS connection closed, reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Kline WS error: {e}")
                await asyncio.sleep(5)
    
    async def _run_depth_ws(self) -> None:
        """WebSocket para order book depth."""
        while self._running:
            try:
                async with websockets.connect(self.depth_ws_url) as ws:
                    logger.info(f"Connected to depth stream: {self.depth_ws_url}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        try:
                            data = json.loads(message)
                            self._update_orderbook(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error in depth: {e}")
                            
            except websockets.ConnectionClosed:
                logger.warning("Depth WS connection closed, reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Depth WS error: {e}")
                await asyncio.sleep(5)
    
    async def _run_trade_ws(self) -> None:
        """WebSocket para trades (CVD calculation)."""
        while self._running:
            try:
                async with websockets.connect(self.trade_ws_url) as ws:
                    logger.info(f"Connected to trade stream: {self.trade_ws_url}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        try:
                            data = json.loads(message)
                            self._update_cvd(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error in trades: {e}")
                            
            except websockets.ConnectionClosed:
                logger.warning("Trade WS connection closed, reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Trade WS error: {e}")
                await asyncio.sleep(5)
    
    async def _process_kline(self, data: dict) -> None:
        """Procesa datos de kline recibidos."""
        if "k" not in data:
            return
        
        kline = data["k"]
        
        # Actualizar precio actual
        self.current_price = float(kline.get("c", 0))
        self.current_volume = float(kline.get("v", 0))
        
        # Solo notificar cuando la vela cierra
        is_closed = kline.get("x", False)
        
        kline_data = {
            "symbol": kline.get("s"),
            "timeframe": kline.get("i"),
            "open_time": kline.get("t"),
            "close_time": kline.get("T"),
            "open": float(kline.get("o", 0)),
            "high": float(kline.get("h", 0)),
            "low": float(kline.get("l", 0)),
            "close": float(kline.get("c", 0)),
            "volume": float(kline.get("v", 0)),
            "is_closed": is_closed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Notificar callbacks
        for callback in self._kline_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(kline_data)
                else:
                    callback(kline_data)
            except Exception as e:
                logger.error(f"Error in kline callback: {e}")
    
    def _update_orderbook(self, data: dict) -> None:
        """Actualiza snapshot del order book."""
        bids = [[float(p), float(q)] for p, q in data.get("bids", data.get("b", []))]
        asks = [[float(p), float(q)] for p, q in data.get("asks", data.get("a", []))]
        
        self.orderbook = OrderBookSnapshot(
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )
    
    def _update_cvd(self, data: dict) -> None:
        """Actualiza CVD con nuevo trade."""
        qty = float(data.get("q", 0))
        is_buyer_maker = data.get("m", False)
        
        # Si buyer es maker, el trade es una venta agresiva
        if is_buyer_maker:
            self.cvd_value -= qty
            side = "sell"
        else:
            self.cvd_value += qty
            side = "buy"
        
        self.trade_buffer.append({
            "qty": qty,
            "side": side,
            "price": float(data.get("p", 0)),
            "timestamp": datetime.now(timezone.utc),
        })
    
    def get_microstructure_metrics(self) -> MicrostructureMetrics:
        """Calcula y retorna métricas de microestructura actuales."""
        # Tomar top 5 niveles para cálculos
        bids = self.orderbook.bids[:5]
        asks = self.orderbook.asks[:5]
        
        bid_volume = sum(q for _, q in bids) if bids else 0
        ask_volume = sum(q for _, q in asks) if asks else 0
        
        # Order Book Imbalance
        obi = bid_volume / ask_volume if ask_volume > 0 else 1.0
        
        return MicrostructureMetrics(
            obi=obi,
            cvd=self.cvd_value,
            spread=self.orderbook.get_spread(),
            bid_depth=bid_volume,
            ask_depth=ask_volume,
            liquidity=bid_volume + ask_volume,
        )
    
    def get_current_state(self) -> dict[str, Any]:
        """Retorna estado actual completo."""
        metrics = self.get_microstructure_metrics()
        
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_price": self.current_price,
            "current_volume": self.current_volume,
            "best_bid": self.orderbook.get_best_bid(),
            "best_ask": self.orderbook.get_best_ask(),
            "mid_price": self.orderbook.get_mid_price(),
            "microstructure": metrics.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ============================================================================
# Factory para crear instancias
# ============================================================================

_market_data_instance: MarketDataManager | None = None


def get_market_data_manager(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    use_testnet: bool = False,
    force_new: bool = False,
) -> MarketDataManager:
    """Singleton factory para MarketDataManager."""
    global _market_data_instance
    
    if _market_data_instance is None or force_new:
        _market_data_instance = MarketDataManager(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )
    
    return _market_data_instance
