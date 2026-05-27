import asyncio
import websockets
import json

async def test():
    uri = "wss://stream.binance.com:9443/ws/btcusdt@depth5@100ms"
    async with websockets.connect(uri) as ws:
        msg = await ws.recv()
        print(json.loads(msg))

asyncio.run(test())
