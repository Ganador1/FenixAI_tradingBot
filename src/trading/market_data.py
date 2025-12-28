# src/trading/market_data.py
"""
Real-time market data management for Fenix Trading Bot.

This module centralizes:
- WebSocket connection to exchanges via CCXT
- Processing of klines (candles)
- Order book and microstructure (OBI, CVD)
- Technical indicator cache
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.trading.exchange_client import ExchangeClient

logger = logging.getLogger("FenixMarketData")


@dataclass
class OrderBookSnapshot:
    """Snapshot of the order book with bids and asks."""
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
    """Metrics of market microstructure."""
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
    Manages real-time market data.
    
    Responsibilities:
    - WebSocket connections via CCXT
    - Kline processing
    - Calculation of microstructure metrics
    - Trade buffer for CVD
    """
    
    def __init__(
        self,
        exchange_id: str = "binance",
        symbol: str = "BTC/USDT",
        timeframe: str = "15m",
        use_testnet: bool = False,
    ):
        self.exchange_id = exchange_id
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.use_testnet = use_testnet
        
        # Exchange client
        self.exchange_client = ExchangeClient(exchange_id=exchange_id, testnet=use_testnet)
        
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
        
        logger.info(f"MarketDataManager initialized for {exchange_id} - {symbol}@{timeframe}")
    
    def on_kline(self, callback: Callable[[dict], None]) -> None:
        """Registers a callback for new klines."""
        self._kline_callbacks.append(callback)
    
    def on_microstructure_update(self, callback: Callable[[MicrostructureMetrics], None]) -> None:
        """Registers a callback for microstructure updates."""
        self._microstructure_callbacks.append(callback)
    
    async def start(self) -> None:
        """Starts all WebSocket connections."""
        if self._running:
            logger.warning("MarketDataManager already running")
            return
        
        if not await self.exchange_client.connect():
            logger.error(f"Failed to connect to {self.exchange_id}")
            return

        self._running = True
        logger.info(f"Starting MarketDataManager for {self.symbol}")
        
        # Start WebSocket tasks
        self._tasks = [
            asyncio.create_task(self._run_kline_ws()),
            asyncio.create_task(self._run_depth_ws()),
            asyncio.create_task(self._run_trade_ws()),
        ]
        
        logger.info("All WebSocket connections started")
    
    async def stop(self) -> None:
        """Stops all connections."""
        self._running = False
        
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        await self.exchange_client.close()

        self._tasks.clear()
        logger.info("MarketDataManager stopped")
    
    async def _run_kline_ws(self) -> None:
        """WebSocket for klines."""
        while self._running:
            try:
                klines = await self.exchange_client._exchange.watch_ohlcv(self.symbol, self.timeframe)
                for kline in klines:
                    await self._process_kline(kline)
            except Exception as e:
                logger.error(f"Kline WS error: {e}")
                await asyncio.sleep(5)
    
    async def _run_depth_ws(self) -> None:
        """WebSocket for order book depth."""
        while self._running:
            try:
                orderbook = await self.exchange_client._exchange.watch_order_book(self.symbol)
                self._update_orderbook(orderbook)
            except Exception as e:
                logger.error(f"Depth WS error: {e}")
                await asyncio.sleep(5)
    
    async def _run_trade_ws(self) -> None:
        """WebSocket for trades (CVD calculation)."""
        while self._running:
            try:
                trades = await self.exchange_client._exchange.watch_trades(self.symbol)
                for trade in trades:
                    self._update_cvd(trade)
            except Exception as e:
                logger.error(f"Trade WS error: {e}")
                await asyncio.sleep(5)
    
    async def _process_kline(self, kline: list) -> None:
        """Processes kline data received."""
        self.current_price = float(kline[4])
        self.current_volume = float(kline[5])
        
        kline_data = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": kline[0],
            "open": float(kline[1]),
            "high": float(kline[2]),
            "low": float(kline[3]),
            "close": float(kline[4]),
            "volume": float(kline[5]),
            "is_closed": True,  # ccxt watch_ohlcv returns closed candles
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        for callback in self._kline_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(kline_data)
                else:
                    callback(kline_data)
            except Exception as e:
                logger.error(f"Error in kline callback: {e}")
    
    def _update_orderbook(self, data: dict) -> None:
        """Updates the order book snapshot."""
        bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
        asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
        
        self.orderbook = OrderBookSnapshot(
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )
    
    def _update_cvd(self, data: dict) -> None:
        """Updates CVD with a new trade."""
        qty = float(data.get("amount", 0))
        side = data.get("side", "buy")
        
        if side == "sell":
            self.cvd_value -= qty
        else:
            self.cvd_value += qty
        
        self.trade_buffer.append({
            "qty": qty,
            "side": side,
            "price": float(data.get("price", 0)),
            "timestamp": datetime.now(timezone.utc),
        })
    
    def get_microstructure_metrics(self) -> MicrostructureMetrics:
        """Calculates and returns current microstructure metrics."""
        bids = self.orderbook.bids[:5]
        asks = self.orderbook.asks[:5]
        
        bid_volume = sum(q for _, q in bids) if bids else 0
        ask_volume = sum(q for _, q in asks) if asks else 0
        
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
        """Returns the complete current state."""
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
# Factory to create instances
# ============================================================================

_market_data_instance: MarketDataManager | None = None


def get_market_data_manager(
    exchange_id: str = "binance",
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    use_testnet: bool = False,
    force_new: bool = False,
) -> MarketDataManager:
    """Singleton factory for MarketDataManager."""
    global _market_data_instance
    
    if _market_data_instance is None or force_new:
        _market_data_instance = MarketDataManager(
            exchange_id=exchange_id,
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
        )
    
    return _market_data_instance
