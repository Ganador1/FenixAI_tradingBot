import types
import asyncio


def _stub_news_scraper(monkeypatch):
    import src.trading.engine as engine_module

    monkeypatch.setattr(
        engine_module,
        "EnhancedNewsScraper",
        lambda *args, **kwargs: types.SimpleNamespace(),
    )


def test_engine_constructor_keeps_agent_flags(monkeypatch):
    from src.trading.engine import TradingEngine

    _stub_news_scraper(monkeypatch)

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="1m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )

    assert engine.enable_visual is False
    assert engine.enable_sentiment is False


def test_engine_constructor_keeps_llm_config_reference(monkeypatch):
    from src.trading.engine import TradingEngine

    _stub_news_scraper(monkeypatch)
    llm_config = object()

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="1m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
        llm_config=llm_config,
    )

    assert engine._llm_config is llm_config


def test_engine_initialize_passes_graph_flags(monkeypatch):
    import src.trading.engine as engine_module
    from src.trading.engine import TradingEngine

    captured: dict[str, object] = {}

    class _StubMarketData:
        def on_kline(self, callback):
            self.callback = callback

    monkeypatch.setattr(engine_module, "get_market_data_manager", lambda **kwargs: _StubMarketData())
    monkeypatch.setattr(engine_module, "OrderExecutor", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "FenixChartGenerator", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(
        engine_module,
        "ProfessionalChartGenerator",
        lambda *args, **kwargs: types.SimpleNamespace(),
    )
    monkeypatch.setattr(engine_module, "EnhancedNewsScraper", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "TwitterScraper", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "RedditScraper", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "FearGreedTool", lambda *args, **kwargs: types.SimpleNamespace())
    monkeypatch.setattr(engine_module, "get_reasoning_bank", lambda: None)
    monkeypatch.setattr(engine_module, "get_risk_manager", lambda: None)
    monkeypatch.setattr(engine_module, "LANGGRAPH_AVAILABLE", True)

    def _fake_get_trading_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(engine_module, "get_trading_graph", _fake_get_trading_graph)
    monkeypatch.setenv("FENIX_DISABLE_RISK_MANAGER", "1")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="1m",
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        llm_config="sentinel-config",
    )

    assert asyncio.run(engine.initialize()) is True
    assert captured["llm_config"] == "sentinel-config"
    assert captured["force_new"] is True
    assert captured["enable_visual"] is False
    assert captured["enable_sentiment"] is False
    assert captured["enable_risk"] is False
