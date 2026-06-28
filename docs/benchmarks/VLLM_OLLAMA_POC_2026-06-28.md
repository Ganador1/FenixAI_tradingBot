# vLLM vs Ollama PoC - 2026-06-28

## Context

GitHub issue #9 asks whether `vllm` could be a viable alternative to `ollama` for
FenixAI LLM inference. The current FenixAI runtime already uses a multi-provider
LLM architecture, so the safest first step is an endpoint-level benchmark rather
than replacing the production provider path.

Relevant vLLM docs:

- vLLM installation docs list Apple Silicon support through `vLLM-Metal`, and CPU
  support separately: <https://docs.vllm.ai/en/stable/getting_started/installation/>
- vLLM online serving exposes OpenAI-compatible endpoints such as
  `/v1/models` and `/v1/chat/completions`: <https://docs.vllm.ai/en/stable/serving/online_serving/>
- The vLLM-Metal plugin targets Apple Silicon with MLX/Metal:
  <https://docs.vllm.ai/projects/vllm-metal/en/latest/>

## Local Environment

- Host: macOS 26.3.1, arm64
- Python: 3.12.12 inside `.venv`
- Ollama: available at `http://localhost:11434`
- Docker: installed, daemon not running
- vLLM: no local `vllm` CLI/server detected

This means a CUDA/Docker vLLM test is not directly runnable on this machine right
now. A future local Apple Silicon test should use `vllm-metal`; a production-like
throughput test should run on a Linux GPU host with vLLM serving an
OpenAI-compatible endpoint.

## Benchmark Tool

Added:

```bash
scripts/benchmark_vllm_ollama.py
```

The script:

- sends the same dashboard-summary chat prompt to Ollama and vLLM;
- uses Ollama `/api/chat`;
- uses vLLM `/v1/models` and `/v1/chat/completions`;
- records latency, success count, output size, completion tokens, and token/s;
- writes a JSON report under `logs/` or a caller-provided output path;
- exits with code `2` only when no provider completes a benchmark request.

Example:

```bash
.venv/bin/python scripts/benchmark_vllm_ollama.py \
  --providers ollama,vllm \
  --repeat 2 \
  --max-tokens 64 \
  --timeout 45 \
  --output logs/vllm_ollama_benchmark_latest.json
```

To test a running vLLM server:

```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct

.venv/bin/python scripts/benchmark_vllm_ollama.py \
  --providers ollama,vllm \
  --vllm-base-url http://localhost:8000/v1 \
  --vllm-model Qwen/Qwen2.5-1.5B-Instruct
```

## Result From This Machine

Command:

```bash
.venv/bin/python scripts/benchmark_vllm_ollama.py \
  --providers ollama,vllm \
  --repeat 2 \
  --max-tokens 64 \
  --timeout 45 \
  --output logs/vllm_ollama_benchmark_latest.json
```

Observed result:

| Provider | Model | Status | Success | Avg latency | p95 latency | Tokens/s |
|---|---:|---:|---:|---:|---:|---:|
| Ollama | `glm-5.2:cloud` | ok | 2/2 | 0.834s | 0.907s | 76.76 |
| vLLM | n/a | unavailable | 0/0 | n/a | n/a | n/a |

vLLM was unavailable because no server was listening at `localhost:8000/v1`.

## Recommendation

Do not replace Ollama with vLLM in production yet.

The viable next step is to keep this benchmark as the acceptance harness and run
it in one of these environments:

1. Apple Silicon local test with `vllm-metal`.
2. Linux GPU host with `vllm serve` and a small instruct model.
3. Containerized vLLM test only when Docker daemon and compatible accelerator
   access are available.

Promote vLLM to a FenixAI provider only if it beats the current Ollama path on
latency or throughput for Fenix-style prompts and can be operated reliably in the
target deployment environment.
