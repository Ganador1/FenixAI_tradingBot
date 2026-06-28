#!/usr/bin/env python3
"""Benchmark Ollama against a vLLM OpenAI-compatible endpoint.

This script is intentionally endpoint-based. It can run today against local Ollama
and can benchmark vLLM when a server is available at --vllm-base-url.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_PROMPT = (
    "You are the FenixAI dashboard summarizer. Given this market snapshot: "
    "BTCUSDT 15m, price 61750, RSI 48, MACD slightly bullish, funding neutral, "
    "volume below average. Produce a concise trading summary with bias, risks, "
    "and whether to trade now."
)


@dataclass
class RequestResult:
    ok: bool
    latency_s: float
    output_chars: int
    output_tokens: int | None = None
    error: str | None = None


JsonGetter = Callable[[str, float], dict[str, Any]]
JsonPoster = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _join_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def http_get_json(url: str, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload or "{}")


def http_post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw or "{}")


def build_ollama_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }


def build_openai_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def _extract_text_and_tokens(provider: str, response: dict[str, Any]) -> tuple[str, int | None]:
    if provider == "ollama":
        message = response.get("message") if isinstance(response.get("message"), dict) else {}
        text = str(message.get("content") or message.get("thinking") or response.get("response") or "")
        tokens = response.get("eval_count")
        return text, int(tokens) if isinstance(tokens, int) else None

    choices = response.get("choices") if isinstance(response.get("choices"), list) else []
    text = ""
    if choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else {}
        if isinstance(message, dict):
            text = str(message.get("content") or "")
        if not text and isinstance(first, dict):
            text = str(first.get("text") or "")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    tokens = usage.get("completion_tokens")
    return text, int(tokens) if isinstance(tokens, int) else None


def benchmark_provider(
    *,
    provider: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    repeat: int,
    timeout_s: float,
    temperature: float,
    max_tokens: int,
    post_json: JsonPoster = http_post_json,
) -> list[RequestResult]:
    endpoint = (
        _join_url(base_url, "/api/chat")
        if provider == "ollama"
        else _join_url(base_url, "/chat/completions")
    )
    payload_builder = build_ollama_payload if provider == "ollama" else build_openai_payload
    results: list[RequestResult] = []

    for _ in range(max(1, repeat)):
        payload = payload_builder(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        started = time.perf_counter()
        try:
            response = post_json(endpoint, payload, timeout_s)
            latency = time.perf_counter() - started
            text, tokens = _extract_text_and_tokens(provider, response)
            results.append(
                RequestResult(
                    ok=True,
                    latency_s=latency,
                    output_chars=len(text),
                    output_tokens=tokens,
                )
            )
        except Exception as exc:
            latency = time.perf_counter() - started
            results.append(
                RequestResult(
                    ok=False,
                    latency_s=latency,
                    output_chars=0,
                    error=str(exc),
                )
            )
    return results


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def summarize_results(
    *,
    provider: str,
    model: str,
    results: list[RequestResult],
) -> dict[str, Any]:
    successes = [result for result in results if result.ok]
    failures = [result for result in results if not result.ok]
    latencies = [result.latency_s for result in successes]
    total_tokens = sum(result.output_tokens or 0 for result in successes)
    total_latency = sum(latencies)

    return {
        "provider": provider,
        "model": model,
        "status": "ok" if successes else "failed",
        "requests": len(results),
        "successes": len(successes),
        "failures": len(failures),
        "latency_avg_s": (sum(latencies) / len(latencies)) if latencies else None,
        "latency_p50_s": _percentile(latencies, 0.50) if latencies else None,
        "latency_p95_s": _percentile(latencies, 0.95) if latencies else None,
        "output_chars_avg": (
            sum(result.output_chars for result in successes) / len(successes) if successes else None
        ),
        "output_tokens_total": total_tokens if total_tokens else None,
        "tokens_per_second": (total_tokens / total_latency) if total_tokens and total_latency else None,
        "errors": [result.error for result in failures if result.error][:3],
    }


def unavailable_summary(provider: str, model: str, reason: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "status": "unavailable",
        "requests": 0,
        "successes": 0,
        "failures": 0,
        "reason": reason,
    }


def _select_ollama_model(tags: dict[str, Any], requested: str | None) -> str | None:
    if requested:
        return requested
    models = tags.get("models") if isinstance(tags.get("models"), list) else []
    if not models:
        return None
    first = models[0]
    if isinstance(first, dict):
        return str(first.get("model") or first.get("name") or "") or None
    return None


def _select_vllm_model(models_payload: dict[str, Any], requested: str | None) -> str | None:
    if requested:
        return requested
    data = models_payload.get("data") if isinstance(models_payload.get("data"), list) else []
    if not data:
        return None
    first = data[0]
    if isinstance(first, dict):
        return str(first.get("id") or "") or None
    return None


def _parse_providers(raw: str) -> list[str]:
    providers = [item.strip().lower() for item in raw.replace(",", " ").split() if item.strip()]
    valid = {"ollama", "vllm"}
    invalid = sorted(set(providers) - valid)
    if invalid:
        raise SystemExit(f"Unsupported provider(s): {', '.join(invalid)}")
    return providers or ["ollama", "vllm"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama vs vLLM for a FenixAI dashboard-summary prompt."
    )
    parser.add_argument("--providers", default="ollama,vllm", help="Providers: ollama,vllm")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--vllm-base-url", default="http://localhost:8000/v1")
    parser.add_argument("--ollama-model", default=None)
    parser.add_argument("--vllm-model", default=None)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Defaults to logs/vllm_ollama_benchmark_<timestamp>.json",
    )
    return parser


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path("logs") / f"vllm_ollama_benchmark_{stamp}.json"


def main(
    argv: list[str] | None = None,
    *,
    get_json: JsonGetter = http_get_json,
    post_json: JsonPoster = http_post_json,
) -> int:
    args = build_parser().parse_args(argv)
    providers = _parse_providers(args.providers)
    messages = [{"role": "user", "content": args.prompt}]
    summaries: list[dict[str, Any]] = []

    for provider in providers:
        if provider == "ollama":
            model = args.ollama_model
            try:
                tags = get_json(_join_url(args.ollama_base_url, "/api/tags"), min(args.timeout, 5.0))
                model = _select_ollama_model(tags, model)
                if not model:
                    summaries.append(unavailable_summary(provider, "", "no Ollama models returned"))
                    continue
            except Exception as exc:
                summaries.append(unavailable_summary(provider, model or "", str(exc)))
                continue
            base_url = args.ollama_base_url
        else:
            model = args.vllm_model
            try:
                models_payload = get_json(_join_url(args.vllm_base_url, "/models"), min(args.timeout, 5.0))
                model = _select_vllm_model(models_payload, model)
                if not model:
                    summaries.append(unavailable_summary(provider, "", "no vLLM models returned"))
                    continue
            except Exception as exc:
                summaries.append(unavailable_summary(provider, model or "", str(exc)))
                continue
            base_url = args.vllm_base_url

        results = benchmark_provider(
            provider=provider,
            base_url=base_url,
            model=model,
            messages=messages,
            repeat=args.repeat,
            timeout_s=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            post_json=post_json,
        )
        summaries.append(summarize_results(provider=provider, model=model, results=results))

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "python": sys.version.split()[0],
            "system": platform.system(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "prompt": args.prompt,
        "repeat": args.repeat,
        "providers": summaries,
    }

    output = Path(args.output) if args.output else _default_output_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    any_success = any(summary.get("successes", 0) for summary in summaries)
    for summary in summaries:
        status = summary["status"]
        if status == "ok":
            print(
                f"{summary['provider']} {summary['model']}: "
                f"avg={summary['latency_avg_s']:.3f}s "
                f"p95={summary['latency_p95_s']:.3f}s "
                f"success={summary['successes']}/{summary['requests']}"
            )
        else:
            print(f"{summary['provider']} {summary.get('model') or ''}: {status} - {summary.get('reason') or summary.get('errors')}")
    print(f"Report written to {output}")
    if not any_success:
        print("No provider completed a benchmark request")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
