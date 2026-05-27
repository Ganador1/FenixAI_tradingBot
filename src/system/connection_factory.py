from typing import Any


def create_binance_client(
    api_key: str, api_secret: str, is_paper: bool, client_ctor: Any = None, wrap: bool = True
):
    if client_ctor is None:
        try:
            from binance.client import Client as _Client
        except Exception:
            try:
                from binance import Spot as _Client
            except Exception:
                from binance import Binance as _Client
        client_ctor = _Client

    client = client_ctor(api_key, api_secret)
    if is_paper and hasattr(client, "FUTURES_URL"):
        client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

    try:
        client.futures_ping()
    except Exception:
        pass

    if wrap:
        try:
            from src.system.batch_processor import OptimizedBinanceClient

            return OptimizedBinanceClient(client)
        except Exception:
            return client
    return client
