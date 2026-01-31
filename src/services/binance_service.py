"""
Binance Service - Encapsulated Binance client and symbol management
Replaces global binance_client and symbol filter management
"""

import threading
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

try:
    from binance.client import Client
    from binance import enums as binance_enums
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

from src.core.trading_constants import get_trading_constants, SymbolConfig

logger = logging.getLogger(__name__)


class BinanceService:
    """
    Encapsulated Binance service that manages client connections
    and symbol configurations without global state
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self._client: Optional[Client] = None
        self._symbol_filters: Dict[str, List[Dict[str, Any]]] = {}
        self._symbol_configs: Dict[str, SymbolConfig] = {}
        self._exchange_info: Optional[Dict[str, Any]] = None
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
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    testnet=self.testnet
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
        
        for symbol_info in self._exchange_info.get('symbols', []):
            symbol = symbol_info['symbol']
            filters = symbol_info.get('filters', [])
            
            self._symbol_filters[symbol] = filters
            
            # Create or update symbol configuration
            # Note: Futures filters structure might differ slightly from Spot, ensuring compatibility
            config = SymbolConfig.from_filters(symbol, filters)
            self._symbol_configs[symbol] = config
            
            logger.debug(f"Processed filters for {symbol}")

    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information (Futures)"""
        if not self._client:
            return None
        
        try:
            return self._client.futures_account()
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return None

    def get_balance_usdt(self) -> float:
        """Get USDT balance (Futures)"""
        if not self._client:
            logger.warning("Binance client not initialized when requesting balance")
            return 0.0
            
        try:
            # futures_account_balance returns list of assets
            balances = self._client.futures_account_balance()
            for balance in balances:
                if balance['asset'] == 'USDT':
                    wallet = float(balance.get('balance', 0.0))
                    available = float(balance.get('availableBalance', 0.0))
                    return max(wallet, available)
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get USDT balance: {e}")
            return 0.0

    def get_ticker_price(self, symbol: str) -> float:
        """Get current ticker price (Futures)"""
        if not self._client:
            return 0.0
            
        try:
            ticker = self._client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"Failed to get ticker price for {symbol}: {e}")
            return 0.0

    def place_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False) -> Dict[str, Any]:
        """Place a market order (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")
            
        try:
            return self._client.futures_create_order(
                symbol=symbol,
                side=side,
                type=binance_enums.ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=reduce_only
            )
        except Exception as e:
            logger.error(f"Failed to place market order for {symbol}: {e}")
            raise e

    def place_algo_order(self, symbol: str, side: str, order_type: str, trigger_price: float, quantity: float | None = None, close_position: bool = False, working_type: str = 'MARK_PRICE') -> Dict[str, Any]:
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
            "workingType": working_type
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

    def place_stop_loss_market(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict[str, Any]:
        """Place a stop loss market order (Futures) using Algos"""
        return self.place_algo_order(
            symbol=symbol,
            side=side,
            order_type='STOP_MARKET',
            trigger_price=stop_price,
            quantity=quantity,
            close_position=True # Default to close position for safety
        )

    def place_take_profit_market(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict[str, Any]:
        """Place a take profit market order (Futures) using Algos"""
        return self.place_algo_order(
            symbol=symbol,
            side=side,
            order_type='TAKE_PROFIT_MARKET',
            trigger_price=stop_price,
            quantity=quantity,
            close_position=True
        )

    def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Get order details (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")
            
        try:
            return self._client.futures_get_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            raise e

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an order (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")
            
        try:
            return self._client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise e

    def cancel_all_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Cancel all open orders (Futures)"""
        if not self._client:
            raise Exception("Binance client not initialized")
            
        try:
            return self._client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception as e:
             logger.error(f"Failed to cancel all orders for {symbol}: {e}")
             return []

    def get_position(self, symbol: str) -> Dict[str, Any]:
        """Get current position (Futures)"""
        if not self._client:
            return {}
            
        try:
            positions = self._client.futures_position_information(symbol=symbol)
            # futures_position_information normally returns a list (one for each margin mode if not specific? or just list of one)
            # When symbol is provided, it returns a list of positions for that symbol (usually 1 unless hedge mode)
            
            for pos in positions:
                # Return the detailed position info
                # Logic: If hedge mode, might need to filter by positionSide. Assuming One-Way Mode for simplicity or taking the one with amount
                if float(pos.get('positionAmt', 0)) != 0:
                    return {
                        'symbol': symbol,
                        'positionAmt': float(pos['positionAmt']),
                        'entryPrice': float(pos['entryPrice']),
                        'unRealizedProfit': float(pos['unRealizedProfit']),
                        'leverage': int(pos.get('leverage', 1))
                    }
            
            # If no open position, return first with 0
            if positions:
                 return {
                        'symbol': symbol,
                        'positionAmt': 0.0,
                        'entryPrice': 0.0,
                        'unRealizedProfit': 0.0
                    }
            return {}
        except Exception as e:
            logger.error(f"Failed to get position for {symbol}: {e}")
            return {}

    
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
_binance_services: Dict[bool, Optional[BinanceService]] = {True: None, False: None}
_service_lock = threading.Lock()


def get_binance_service(api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False) -> BinanceService:
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