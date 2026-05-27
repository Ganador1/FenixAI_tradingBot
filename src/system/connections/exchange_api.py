from typing import Any


class ExchangeAPI:
    def futures_account(self) -> dict:
        raise NotImplementedError

    def futures_change_leverage(self, symbol: str, leverage: int) -> Any:
        raise NotImplementedError


class BinanceExchangeAPI(ExchangeAPI):
    def __init__(self, client: Any):
        self._client = client

    def futures_account(self) -> dict:
        return self._client.futures_account()

    def futures_change_leverage(self, symbol: str, leverage: int) -> Any:
        return self._client.futures_change_leverage(symbol=symbol, leverage=leverage)


def create_exchange_api(api_key: str, api_secret: str, is_paper: bool):
    from src.system.connection_factory import create_binance_client

    client = create_binance_client(api_key, api_secret, is_paper)
    return BinanceExchangeAPI(client)
