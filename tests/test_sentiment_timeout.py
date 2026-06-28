import time
import types
from unittest.mock import AsyncMock

import pytest


def test_rss_fetch_uses_resilient_http_session_with_timeout(tmp_path, monkeypatch):
    import src.tools.enhanced_news_scraper as news_module

    scraper = news_module.EnhancedNewsScraper(cache_dir=str(tmp_path), cache_ttl=0)
    calls: dict[str, object] = {}

    class _Response:
        content = b"<rss><channel><item><title>BTC</title></item></channel></rss>"
        headers = {"content-type": "application/rss+xml"}

        def raise_for_status(self):
            calls["raise_for_status"] = True

    def fake_get(url: str, *, timeout: float, **kwargs):
        calls["url"] = url
        calls["timeout"] = timeout
        return _Response()

    class _Feed(dict):
        def __init__(self):
            super().__init__(bozo=False)
            self.entries = [
                {
                    "title": "BTC rallies",
                    "link": "https://example.test/btc",
                    "published": "2026-06-18T00:00:00Z",
                    "summary": "Market update",
                }
            ]

    def fake_parse(payload, **kwargs):
        calls["payload"] = payload
        assert payload == _Response.content
        return _Feed()

    monkeypatch.setattr(scraper.http, "get", fake_get)
    monkeypatch.setattr(news_module.feedparser, "parse", fake_parse)

    articles = scraper._fetch_from_rss("cointelegraph", "https://example.test/rss")

    assert articles[0]["title"] == "BTC rallies"
    assert calls["url"] == "https://example.test/rss"
    assert calls["timeout"] == 10.0
    assert calls["raise_for_status"] is True


def _build_timeout_test_engine(monkeypatch):
    import src.trading.engine as engine_module

    class _MarketData:
        current_price = 100.0
        current_volume = 0.0

        def get_microstructure_metrics(self):
            return types.SimpleNamespace()

    monkeypatch.setattr(engine_module, "get_market_data_manager", lambda **kwargs: _MarketData())
    monkeypatch.setattr(engine_module, "OrderExecutor", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "FenixChartGenerator", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(
        engine_module,
        "ProfessionalChartGenerator",
        lambda *args, **kwargs: types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        engine_module,
        "EnhancedNewsScraper",
        lambda *args, **kwargs: types.SimpleNamespace(fetch_crypto_news=lambda limit: []),
    )
    monkeypatch.setattr(
        engine_module,
        "TwitterScraper",
        lambda *args, **kwargs: types.SimpleNamespace(_run=lambda: {}),
    )
    monkeypatch.setattr(
        engine_module,
        "RedditScraper",
        lambda *args, **kwargs: types.SimpleNamespace(_run=lambda: {}),
    )
    monkeypatch.setattr(
        engine_module,
        "FearGreedTool",
        lambda *args, **kwargs: types.SimpleNamespace(_run=lambda limit: "N/A"),
    )
    monkeypatch.setattr(engine_module, "get_reasoning_bank", lambda: None)
    monkeypatch.setattr(engine_module, "get_trade_manager", lambda: None)
    monkeypatch.setattr(engine_module, "get_risk_manager", lambda: None)

    return engine_module.TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=True,
    )


@pytest.mark.asyncio
async def test_blocking_sentiment_call_returns_fallback_on_timeout(monkeypatch):
    monkeypatch.setenv("FENIX_SENTIMENT_FETCH_TIMEOUT_SEC", "0.01")
    engine = _build_timeout_test_engine(monkeypatch)

    started = time.perf_counter()
    result = await engine._run_blocking_sentiment_call(
        "news",
        lambda: (time.sleep(0.2), ["late"])[1],
        fallback=[],
    )
    elapsed = time.perf_counter() - started

    assert result == []
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_analysis_cycle_uses_timeout_wrapper_for_news(monkeypatch):
    import src.trading.engine as engine_module

    engine = _build_timeout_test_engine(monkeypatch)
    engine._trading_graph = None
    engine._execute_fallback_analysis = AsyncMock(
        return_value={
            "final_trade_decision": {
                "final_decision": "HOLD",
                "confidence_in_decision": "LOW",
                "combined_reasoning": "test",
            }
        }
    )
    engine._process_decision = AsyncMock()
    monkeypatch.setattr(engine_module, "get_current_indicators", lambda: {"rsi": 50.0})

    labels: list[str] = []

    async def fake_sentiment_call(label, func, *, fallback):
        labels.append(label)
        return fallback

    engine._run_blocking_sentiment_call = AsyncMock(side_effect=fake_sentiment_call)

    await engine._run_analysis_cycle()

    assert "news" in labels
    engine._process_decision.assert_awaited_once()
