import asyncio
import json
import logging
import time

from src.system.connections.ws_factory import ws_connect

_STREAM_REGISTRY = {}
_logger = logging.getLogger("StreamHealth")


class MarketDataStream:
    def __init__(
        self,
        name: str = "default",
        ping_interval: int = 30,
        ping_timeout: int = 20,
        base_delay: int = 3,
        max_delay: int = 300,
    ):
        self.name = name
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.base_delay = base_delay
        self.max_delay = max_delay
        _STREAM_REGISTRY.setdefault(
            self.name,
            {
                "connection_attempts": 0,
                "successful_connections": 0,
                "reconnects": 0,
                "errors": 0,
                "total_messages": 0,
                "avg_message_interval_ms": 0.0,
                "last_connect_ts": 0.0,
                "last_message_ts": 0.0,
                "stalls": 0,
            },
        )

    async def run(self, url: str, on_message):
        delay = self.base_delay
        prev_msg_ts = None
        while True:
            try:
                _STREAM_REGISTRY[self.name]["connection_attempts"] += 1
                _STREAM_REGISTRY[self.name]["last_connect_ts"] = time.time()
                async with ws_connect(
                    url, ping_interval=self.ping_interval, ping_timeout=self.ping_timeout
                ) as websocket:
                    _STREAM_REGISTRY[self.name]["successful_connections"] += 1
                    delay = self.base_delay
                    async for message in websocket:
                        try:
                            now = time.time()
                            _STREAM_REGISTRY[self.name]["total_messages"] += 1
                            if prev_msg_ts is not None:
                                interval = (now - prev_msg_ts) * 1000.0
                                count = _STREAM_REGISTRY[self.name]["total_messages"]
                                avg = _STREAM_REGISTRY[self.name]["avg_message_interval_ms"]
                                _STREAM_REGISTRY[self.name]["avg_message_interval_ms"] = (
                                    (avg * (count - 1)) + interval
                                ) / count
                                if interval > float(self.ping_timeout) * 1000.0:
                                    _STREAM_REGISTRY[self.name]["stalls"] += 1
                                    _logger.warning(
                                        f"Stream {self.name} stall detected: interval={interval:.0f}ms"
                                    )
                            prev_msg_ts = now
                            _STREAM_REGISTRY[self.name]["last_message_ts"] = now
                            data = json.loads(message)
                            if asyncio.iscoroutinefunction(on_message):
                                await on_message(data)
                            else:
                                on_message(data)
                        except Exception:
                            _STREAM_REGISTRY[self.name]["errors"] += 1
                            _logger.exception("Stream %s callback error", self.name)
            except asyncio.CancelledError:
                return
            except Exception:
                _STREAM_REGISTRY[self.name]["errors"] += 1
                _STREAM_REGISTRY[self.name]["reconnects"] += 1
                _logger.exception("Stream %s crashed; reconnecting", self.name)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_delay)


def get_global_stream_metrics():
    return {k: dict(v) for k, v in _STREAM_REGISTRY.items()}
