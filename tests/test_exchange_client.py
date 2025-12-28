# tests/test_exchange_client.py
"""
Tests for the generic ExchangeClient.
"""
import pytest
from src.trading.exchange_client import ExchangeClient

@pytest.mark.asyncio
async def test_exchange_client_connection():
    """
    Tests that the ExchangeClient can connect to a mock exchange.
    """
    client = ExchangeClient(exchange_id='binance', testnet=True)
    connected = await client.connect()
    assert connected
    await client.close()

@pytest.mark.asyncio
async def test_get_price():
    """
    Tests that the ExchangeClient can fetch a price.
    """
    client = ExchangeClient(exchange_id='binance', testnet=True)
    await client.connect()
    price = await client.get_price('BTC/USDT')
    assert isinstance(price, float)
    assert price > 0
    await client.close()
