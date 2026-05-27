from scripts.run_hybrid_live_paper import _compatible_engine_kwargs
from scripts.run_hybrid_live_paper import _visual_enabled_for_timeframe


class _CurrentEngineShape:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        use_testnet: bool = False,
        paper_trading: bool = True,
        enable_visual_agent: bool = True,
        enable_sentiment_agent: bool = True,
        allow_live_trading: bool = False,
        llm_config=None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe


def test_compatible_engine_kwargs_drops_unsupported_constructor_args():
    kwargs = _compatible_engine_kwargs(
        _CurrentEngineShape,
        symbol="SOLUSDT",
        timeframe="5m",
        paper_trading=True,
        enable_trading=False,
        enable_visual_agent=False,
        enable_sentiment_agent=True,
        llm_config={"provider": "test"},
        market_data_force_new=True,
    )

    assert kwargs["symbol"] == "SOLUSDT"
    assert kwargs["timeframe"] == "5m"
    assert kwargs["paper_trading"] is True
    assert kwargs["enable_visual_agent"] is False
    assert kwargs["enable_sentiment_agent"] is True
    assert kwargs["llm_config"] == {"provider": "test"}
    assert "enable_trading" not in kwargs
    assert "market_data_force_new" not in kwargs


def test_visual_enabled_for_timeframe_supports_global_disable(monkeypatch):
    monkeypatch.setenv("FENIX_DISABLE_VISUAL_ALL_TF", "1")
    monkeypatch.delenv("FENIX_DISABLE_VISUAL_SHORT_TF", raising=False)

    assert _visual_enabled_for_timeframe("3m") is False
    assert _visual_enabled_for_timeframe("15m") is False
