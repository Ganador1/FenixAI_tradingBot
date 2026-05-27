import asyncio

import pytest

from src.core.orchestrator.retry_system import invoke_with_retry_and_validation, reset_retry_stats


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        # Tiny await to ensure timing fields are exercised without slowing the suite.
        await asyncio.sleep(0.001)
        return _Resp(self._content)


@pytest.mark.asyncio
async def test_retry_system_attaches_perf_metadata():
    reset_retry_stats()
    llm = _FakeLLM('{"signal":"BUY","confidence":0.8,"rationale":"ok","indicator_validations":{}}')
    parsed, attempts, errors = await invoke_with_retry_and_validation(
        llm=llm,
        messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
        agent_type="technical_analyst",
        max_retries=0,
        required_keys=["signal"],
    )

    assert errors == []
    assert attempts == 1
    assert isinstance(parsed, dict)
    assert parsed["signal"] == "BUY"
    assert "_perf" in parsed

    perf = parsed["_perf"]
    assert perf["agent_type"] == "technical_analyst"
    assert isinstance(perf.get("llm_attempt_ms"), list)
    assert len(perf["llm_attempt_ms"]) == 1
    assert isinstance(perf.get("llm_total_ms"), float)
    assert isinstance(perf.get("json_extract_ms"), float)
    assert isinstance(perf.get("validation_ms"), float)
    assert isinstance(perf.get("total_ms"), float)
