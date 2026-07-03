import sys
import types

from src.core.orchestrator.llm_factory import LLMFactory
from src.config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig
from src.prompts.agent_prompts import get_system_prompt, format_prompt
from src.core.orchestrator.llm_factory import _MLXChatAdapter


class _FakeChatOllama:
    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.kwargs = kwargs
        self.model = kwargs.get("model")

    async def ainvoke(self, messages):
        raise NotImplementedError

    def invoke(self, messages):
        raise NotImplementedError


def _install_fake_langchain_ollama(monkeypatch):
    fake_module = types.SimpleNamespace(ChatOllama=_FakeChatOllama)
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)


def test_llm_factory_disables_reasoning_for_qabba_deepseek_strict_json(monkeypatch):
    _install_fake_langchain_ollama(monkeypatch)

    cfg = LLMProvidersConfig(
        qabba=AgentProviderConfig(
            provider_type="ollama_cloud",
            model_name="deepseek-v3.2:cloud",
            api_base="https://api.ollama.com",
            max_tokens=800,
            timeout=120,
            extra_config={"disable_rotation": True},
        )
    )

    factory = LLMFactory(cfg)
    llm = factory.get_llm_for_agent("qabba")

    assert isinstance(llm, _FakeChatOllama)
    assert _FakeChatOllama.last_kwargs["reasoning"] is False
    assert isinstance(_FakeChatOllama.last_kwargs["format"], dict)


def test_llm_factory_preserves_explicit_qabba_reasoning_override(monkeypatch):
    _install_fake_langchain_ollama(monkeypatch)

    cfg = LLMProvidersConfig(
        qabba=AgentProviderConfig(
            provider_type="ollama_cloud",
            model_name="deepseek-v3.2:cloud",
            api_base="https://api.ollama.com",
            max_tokens=800,
            timeout=120,
            extra_config={"disable_rotation": True, "reasoning": True},
        )
    )

    factory = LLMFactory(cfg)
    llm = factory.get_llm_for_agent("qabba")

    assert isinstance(llm, _FakeChatOllama)
    assert _FakeChatOllama.last_kwargs["reasoning"] is True


def test_llm_factory_applies_agent_token_override(monkeypatch):
    _install_fake_langchain_ollama(monkeypatch)
    monkeypatch.setenv("FENIX_QABBA_MAX_TOKENS", "512")

    cfg = LLMProvidersConfig(
        qabba=AgentProviderConfig(
            provider_type="ollama_cloud",
            model_name="deepseek-v3.2:cloud",
            api_base="https://api.ollama.com",
            max_tokens=800,
            timeout=120,
            extra_config={"disable_rotation": True},
        )
    )

    factory = LLMFactory(cfg)
    llm = factory.get_llm_for_agent("qabba")

    assert isinstance(llm, _FakeChatOllama)
    assert _FakeChatOllama.last_kwargs["num_predict"] == 512


def test_llm_factory_prefers_selected_canonical_cloud_key_over_dual_pool(monkeypatch):
    _install_fake_langchain_ollama(monkeypatch)
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "selected-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "selected-key")
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY_1", "pool-key-1")
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY_2", "pool-key-2")

    cfg = LLMProvidersConfig(
        risk_manager=AgentProviderConfig(
            provider_type="ollama_cloud",
            model_name="devstral-small-2:24b-cloud",
            api_base="https://api.ollama.com",
            max_tokens=700,
            timeout=120,
            extra_config={"disable_rotation": True},
        )
    )

    factory = LLMFactory(cfg)
    llm = factory.get_llm_for_agent("risk_manager")

    assert isinstance(llm, _FakeChatOllama)
    headers = _FakeChatOllama.last_kwargs["client_kwargs"]["headers"]
    assert headers["Authorization"] == "Bearer selected-key"


def test_risk_manager_prompt_drops_btc_anchored_example_levels():
    system_prompt = get_system_prompt("risk_manager")
    messages = format_prompt(
        "risk_manager",
        decision="BUY",
        symbol="ETHUSDT",
        confidence="HIGH",
        entry_price="2263.10",
        balance="76.48",
        open_positions="0",
        daily_pnl="0",
        current_drawdown="0%",
        atr="4.2",
        volatility="MEDIUM",
        liquidity="HIGH",
        max_risk_per_trade="1",
        max_total_exposure="20",
    )

    assert "84000.00" not in system_prompt
    assert "86000.00" not in system_prompt
    assert "Reference Entry Price: 2263.10" in messages[1]["content"]
    assert "Do not copy example values from prior prompts or other assets" in messages[1]["content"]


def test_visual_prompt_formats_literal_json_example():
    messages = format_prompt(
        "visual_analyst",
        symbol="ETHUSDC",
        timeframe="15m",
        candle_count=50,
        visible_indicators="EMA 9/21, Bollinger Bands, SuperTrend",
        current_price="2500.00",
        price_range="2450.00-2550.00",
    )

    assert messages is not None
    assert messages[1]["content"].startswith("Analyze the chart for ETHUSDC on 15m timeframe.")
    assert '{"action":"BUY|SELL|HOLD"' in messages[1]["content"]


def test_mlx_vision_adapter_honors_ollama_host_when_base_url_absent(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")

    assert _MLXChatAdapter._resolve_ollama_base_url() == "http://host.docker.internal:11434"
