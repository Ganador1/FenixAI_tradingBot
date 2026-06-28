import json

import pytest


def test_builds_provider_payloads_for_same_chat_prompt():
    from scripts.benchmark_vllm_ollama import build_ollama_payload, build_openai_payload

    messages = [{"role": "user", "content": "Summarize BTC momentum."}]

    ollama_payload = build_ollama_payload(
        model="qwen3:8b",
        messages=messages,
        temperature=0.1,
        max_tokens=64,
    )
    openai_payload = build_openai_payload(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=messages,
        temperature=0.1,
        max_tokens=64,
    )

    assert ollama_payload == {
        "model": "qwen3:8b",
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 64},
    }
    assert openai_payload == {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": messages,
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 64,
    }


def test_summarize_results_reports_latency_and_token_throughput():
    from scripts.benchmark_vllm_ollama import RequestResult, summarize_results

    summary = summarize_results(
        provider="vllm",
        model="Qwen/Qwen2.5-7B-Instruct",
        results=[
            RequestResult(ok=True, latency_s=1.0, output_chars=100, output_tokens=20),
            RequestResult(ok=True, latency_s=2.0, output_chars=140, output_tokens=30),
            RequestResult(ok=False, latency_s=0.2, output_chars=0, error="timeout"),
        ],
    )

    assert summary["provider"] == "vllm"
    assert summary["status"] == "ok"
    assert summary["requests"] == 3
    assert summary["successes"] == 2
    assert summary["failures"] == 1
    assert summary["latency_avg_s"] == pytest.approx(1.5)
    assert summary["latency_p50_s"] == pytest.approx(1.5)
    assert summary["latency_p95_s"] == pytest.approx(1.95)
    assert summary["output_tokens_total"] == 50
    assert summary["tokens_per_second"] == pytest.approx(50 / 3)


def test_benchmark_provider_posts_to_vllm_openai_compatible_endpoint():
    from scripts.benchmark_vllm_ollama import benchmark_provider

    calls = []

    def fake_post_json(url, payload, timeout_s):
        calls.append((url, payload, timeout_s))
        return {
            "choices": [{"message": {"content": "BUY bias is weak; wait for confirmation."}}],
            "usage": {"completion_tokens": 9},
        }

    result = benchmark_provider(
        provider="vllm",
        base_url="http://localhost:8000/v1",
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[{"role": "user", "content": "Summarize ETH."}],
        repeat=2,
        timeout_s=5,
        temperature=0.0,
        max_tokens=48,
        post_json=fake_post_json,
    )

    assert len(result) == 2
    assert all(item.ok for item in result)
    assert calls[0][0] == "http://localhost:8000/v1/chat/completions"
    assert calls[0][1]["model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert calls[0][1]["max_tokens"] == 48
    assert calls[0][2] == 5


def test_extracts_ollama_thinking_when_content_is_empty():
    from scripts.benchmark_vllm_ollama import _extract_text_and_tokens

    text, tokens = _extract_text_and_tokens(
        "ollama",
        {
            "message": {
                "role": "assistant",
                "content": "",
                "thinking": "BTC momentum is mixed; wait for stronger confirmation.",
            },
            "eval_count": 11,
        },
    )

    assert text == "BTC momentum is mixed; wait for stronger confirmation."
    assert tokens == 11


def test_main_writes_json_report_with_unavailable_provider(tmp_path, capsys):
    from scripts import benchmark_vllm_ollama

    def fake_get_json(url, timeout_s):
        raise OSError("connection refused")

    exit_code = benchmark_vllm_ollama.main(
        [
            "--providers",
            "vllm",
            "--output",
            str(tmp_path / "report.json"),
            "--repeat",
            "1",
        ],
        get_json=fake_get_json,
    )

    report = json.loads((tmp_path / "report.json").read_text())
    assert exit_code == 2
    assert report["providers"][0]["provider"] == "vllm"
    assert report["providers"][0]["status"] == "unavailable"
    assert "No provider completed a benchmark request" in capsys.readouterr().out
