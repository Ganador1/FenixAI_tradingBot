import json
import os

import pytest
import websockets


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FENIX_RUN_NETWORK_TESTS") != "1",
    reason="Live Binance network tests are opt-in",
)
@pytest.mark.asyncio
async def test_binance_depth_websocket():
    """Smoke-test Binance only when external network tests are explicitly enabled."""
    uri = "wss://stream.binance.com:9443/ws/btcusdt@depth5@100ms"
    async with websockets.connect(uri, open_timeout=10) as ws:
        msg = await ws.recv()
        payload = json.loads(msg)
        assert "bids" in payload
        assert "asks" in payload
