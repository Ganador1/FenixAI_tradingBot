import logging
from typing import List, Dict, Optional
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException, BinanceRequestException

logger = logging.getLogger(__name__)

class FuturesService:
    """
    Service for interacting with Binance Futures (USDT-M) using python-binance.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        """
        Initialize the Binance Futures client.

        Args:
            api_key: Binance API Key
            api_secret: Binance API Secret
            testnet: Whether to use the Testnet (True) or Real (False) environment.
        """
        self.client = Client(api_key, api_secret, testnet=testnet)
        self.testnet = testnet
        logger.info(f"FuturesService initialized (Testnet: {testnet})")

    def get_usdt_balance(self) -> float:
        """
        Get the current USDT balance in the Futures wallet.
        """
        try:
            # Method: futures_account_balance()
            balances = self.client.futures_account_balance()
            for asset in balances:
                if asset['asset'] == 'USDT':
                    # 'balance' usually refers to total wallet balance
                    # 'withdrawAvailable' or 'availableBalance' checks might be needed depending on use case
                    return float(asset['balance'])
            return 0.0
        except BinanceAPIException as e:
            logger.error(f"Error getting USDT balance: {e}")
            raise

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """
        Get open positions.
        
        Args:
            symbol: Optional symbol to filter (e.g. 'BTCUSDT'). 
                    If None, returns all positions with non-zero size.
        """
        try:
            # Method: futures_position_information()
            # If symbol is provided, it returns a list with one item or detailed info
            positions = self.client.futures_position_information(symbol=symbol)
            
            # If no specific symbol filtered by API, we filter manually for open positions only
            # because the API returns ALL symbols even with 0 position size if symbol param allows.
            active_positions = []
            for pos in positions:
                amt = float(pos['positionAmt'])
                if amt != 0:
                    active_positions.append(pos)
            
            return active_positions
        except BinanceAPIException as e:
            logger.error(f"Error getting positions: {e}")
            raise

    def place_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False) -> Dict:
        """
        Place a MARKET order.

        Args:
            symbol: Trading pair, e.g., 'BTCUSDT'
            side: 'BUY' or 'SELL'
            quantity: Amount of base asset to buy/sell
            reduce_only: If True, this order will only reduce position.
        """
        try:
            # Method: futures_create_order()
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=reduce_only
            )
            logger.info(f"Market order placed: {side} {quantity} {symbol}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Error placing market order: {e}")
            raise

    def change_leverage(self, symbol: str, leverage: int):
        """
        Change leverage for a symbol.
        """
        try:
            return self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            logger.error(f"Error changing leverage: {e}")
            raise

if __name__ == "__main__":
    # Example Usage (Do not run without valid keys)
    # import os
    # api_key = os.getenv("BINANCE_API_KEY")
    # api_secret = os.getenv("BINANCE_API_SECRET")
    # service = FuturesService(api_key, api_secret, testnet=True)
    pass
