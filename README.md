<div align="center">

# 🦅 FenixAI Trading Bot v2.5.0

### Autonomous Multi-Agent Cryptocurrency Trading System with Self-Evolving Memory

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-green.svg)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/Frontend-React_18-61DAFB.svg)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0-3178C6.svg)](https://www.typescriptlang.org/)
[![Binance](https://img.shields.io/badge/Exchange-Binance_Futures-F0B90B.svg)](https://www.binance.com/)
[![arXiv](https://img.shields.io/badge/arXiv-2509.25140-b31b1b.svg)](https://arxiv.org/abs/2509.25140)
[![TailwindCSS](https://img.shields.io/badge/Styling-TailwindCSS-38B2AC.svg)](https://tailwindcss.com/)
[![Socket.IO](https://img.shields.io/badge/Realtime-Socket.IO-010101.svg)](https://socket.io/)

*An advanced trading system powered by multiple specialized AI agents that collaborate to analyze markets, manage risk, and execute trades on Binance Futures. Features ReasoningBank memory system for self-evolving agent capabilities.*

![Fenix Dashboard Preview](./Dashboard%20Fenix.png)

[📖 Documentation](./docs/) · [🚀 Quick Start](#-quick-start) · [🏗️ Architecture](#-architecture) · [📝 Changelog](./docs/CHANGELOG.md) · [📄 Paper](https://arxiv.org/abs/2509.25140)

</div>

---

> **⚠️ WARNING:** Fenix is under active development, is not yet proven profitable, and may not work as expected. Use at your own risk. Paper trading is strongly recommended before any live deployment.

### 🦅 A Message from the Creator (v2.5)

It has been a few months since v2.0. I've been testing the project 24/7 and brainstorming ways to make Fenix more reliable and capable of making better trades. After extensive testing, I am including in this v2.5 release the changes that have made a real, quantifiable impact on performance. 

Among these improvements are the removal of the Sentiment agent, as well as refining the entry and exit logic with new rules, better indicators, and improved timing for decisions. Another upgrade that has provided a massive boost is **Nanofenix**, which introduces a classical ML model with live training. It acts as a strict filter for trades—preventing us from entering too early or too late—and improves the overall win rate by analyzing more input layers. Fenix now executes fewer trades, but the system is much safer and more confident when deciding on an entry or exit.

I will keep testing and improving Fenix. I know I don't push commits very often, but I strongly prefer to test everything exhaustively in a local environment before sharing my findings with you all, just to ensure everything is perfect. 

This version will be available as a new Release. If you prefer to revert to v2.0 (which is highly stable), you will still be able to do so.

Thank you all so much for your massive support!

**Ganador**

---

## ⭐ Star History

<a href="https://star-history.com/#Ganador1/FenixAI_tradingBot&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Ganador1/FenixAI_tradingBot&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Ganador1/FenixAI_tradingBot&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Ganador1/FenixAI_tradingBot&type=Date" />
 </picture>
</a>

---

## ✨ What's New in v2.5

> **Reliability-focused release** — v2.5 brings short-timeframe latency work, a complete performance optimisation pass, NanoFenix v3.5 as a first-class companion signal, DeepSeek v4 cloud experiments, and a full suite of live/paper reliability fixes.

### Core Engine & Latency

| Improvement | Details |
|-------------|---------|
| **Hot-path nonblocking (1m/3m/5m)** | Critical path cycle dropped from ~140 ms → **~10–13 ms**. Technical and QABBA agents resolve cache/fallback before building prompts; LLM refreshes run in background. |
| **Paper mode no-REST** | Paper trades no longer initialise `BinanceService` for balance; uses `FENIX_BALANCE_FALLBACK_USDT` to avoid ~1 s spikes per simulated trade. |
| **Parallel agent graph** | Technical, QABBA, Sentiment, and Visual run in true parallel via LangGraph; background caches for charts, news, and balance. |
| **Deterministic risk mode** | `FENIX_RISK_DETERMINISTIC=1` skips the Risk LLM entirely and computes ATR-based SL/TP/size — 15m full pipeline now runs in ~15 s (was ~57 s). |

### Execution Reliability

| Fix | Details |
|-----|---------|
| **Live position hydration** | On restart, if Binance already has an open position, the engine hydrates local state before assuming the account is flat — prevents duplicate entries. |
| **Invalid-price guard** | Paper `trade:simulated` events and hybrid runner reject signals with `price = 0.0` before logging position transitions. |
| **Algo protective order verification** | Order monitor now also checks `openAlgoOrders` (Binance 3xxxxxxx IDs), fixing false `PROTECTION_NOT_VERIFIED` alerts. |
| **Failed execution risk isolation** | Failed live execution attempts no longer count as realized losing trades in the `RuntimeRiskManager` loss-streak counter. |
| **Direction-aware SL/TP validation** | Risk agent validates SL is on the correct side of entry; example-copied BTC-like levels for SOL are replaced with deterministic ATR levels before execution. |
| **Same-side entry prevention** | Engine skips same-side entries after hydration; `FENIX_ALLOW_ADD_TO_POSITION=1` enables intentional pyramiding. |

### NanoFenix v3.5 — Companion Signal

| Feature | Details |
|---------|---------|
| **Adaptive fusion** | `ENABLE_ADAPTIVE_FUSION=1` — multi-horizon blending adapts weights based on per-horizon calibration instead of fixed 0.4/0.6 split. |
| **Fee-aware trailing** | `MIN_TRAILING_NET_PCT` gates trailing exits: position is only closed when estimated net PnL after round-trip fees exceeds the threshold — no more "wins" that lose money to fees. |
| **Configurable hard-veto** | `FENIX_NANOFENIX_HARD_VETO_REASONS` — only critical reasons (direction mismatch, companion not ready, stale signal) unconditionally block execution; soft reasons (`low_pred_bps`) reduce size without blocking. |
| **Companion readiness** | `COMPANION_MIN_DIR_SAMPLES` lowered from 80 → 10, allowing companion activation in the first few hundred bars. |

### Agent Improvements (v2.1)

| Improvement | Details |
|-------------|---------|
| **Tiered trailing stop** | Four profit tiers: 0–1% → 2.0%, 1–2% → 1.0%, 2–3% → 0.5%, >3% → 0.3% trailing. Trailing history tracked per trade. |
| **Risk Manager soft-cap** | Instead of vetoing, the Risk Manager now caps position size to available exposure and approves the trade. |
| **Agent weight rebalance** | Technical/QABBA raised to 0.35 each; Sentiment reduced to 0.05 (was 0.15) — reflects real-world reliability. |
| **Decision Agent JSON fix** | Prompt payload trimmed to essential fields; timeout reduced 15 s → 12 s; fallback consensus improved. |
| **Sentiment Agent cache** | 15-minute news cache (`_NEWS_CACHE_TTL_SEC=900`); payload and retries reduced for faster fallback. |

### Timeframe-Aware Indicator System

| Feature | Details |
|---------|---------|
| **Per-TF indicator profiles** | Database of 20+ indicators scored by timeframe, market regime, lag, and reliability. |
| **CHOP / Donchian / Keltner** | Choppiness Index drives execution gating; Donchian breakout detection; Keltner Channels for TTM Squeeze. |
| **Advanced indicators** | HMA, Fisher Transform, VWAP bands, Funding Rate extremes, Open Interest trend confirmation, CVD divergences. |
| **Timeframe-aware SL/TP** | Long TF (15m/1h/4h): 4% default SL, 2.0 RR, 2× ATR. Short TF (1m/5m): 2% SL, 1.5 RR, 1.5× ATR. |

### New LLM Integrations

| Model | Role | Notes |
|-------|------|-------|
| **DeepSeek v4 Flash** (`deepseek-v4-flash:cloud`) | Technical / Decision | Fast, cost-efficient cloud inference |
| **DeepSeek v4 Pro** (`deepseek-v4-pro:cloud`) | Full pipeline | Highest-accuracy cloud option tested |
| **cogito-2.1:671b-cloud** | QABBA | Benchmark winner: 75–80% directional accuracy |
| **nemotron-3-nano:30b-cloud** | Technical + Decision | 66.7% accuracy; most active decision model |
| **glm-5:cloud** | Risk Manager | 77.8% activity rate, score 0.504 in benchmark |

### v2.5 Benchmark Results (32 models tested)

See [docs/benchmarks/BENCHMARK_FINAL_SUMMARY.md](./docs/benchmarks/BENCHMARK_FINAL_SUMMARY.md) for the full winner table.

| Agent | Recommended Model | Accuracy |
|-------|-------------------|---------|
| QABBA | cogito-2.1:671b-cloud | 75–80% |
| Technical | nemotron-3-nano:30b-cloud | 66.7% |
| Visual | gemini-3-flash-preview:cloud | 55–75% |
| Decision | nemotron-3-nano:30b-cloud | Score 0.450 |
| Risk | glm-5:cloud | Score 0.504 |

See [v2.5 release notes](./docs/releases/v2.5.md), [v2.5 new systems guide](./docs/releases/v2.5-new-systems.md), [NanoFenix HTF v2.5 changes](./docs/NANOFENIX_HTF_V2_5_CHANGES.md), and [release checklist](./RELEASE_CHECKLIST.md).

---

## ✨ What's New in v2.0

> **Complete architectural overhaul** - Migrated from CrewAI to **LangGraph** for more robust and flexible agent orchestration.

| Feature | v1.0 (June 2025) | v2.0 (December 2025) |
|---------|------------------|---------------------|
| **Orchestration** | CrewAI | LangGraph (State Machine) |
| **Memory System** | Basic TradeMemory | [ReasoningBank](https://arxiv.org/abs/2509.25140) + LLM-as-Judge |
| **Visual Analysis** | Static screenshots | Chart Generator + Playwright TradingView Capture |
| **LLM Providers** | Ollama only | Ollama, MLX, Groq, HuggingFace |
| **Frontend** | Flask Dashboard | React + Vite + TypeScript |
| **Agent Weighting** | Static | Dynamic (performance-based) |
| **Security** | Basic | SecureSecretsManager + Path Validation |
| **Real-time** | Polling | WebSocket + Socket.IO |

### Notable security and developer workflow improvements
- API binds to `127.0.0.1` by default to avoid accidental public exposure. To bind to all interfaces intentionally, set `ALLOW_EXPOSE_API=true`.
- Demo accounts are not seeded by default; set `CREATE_DEMO_USERS=true` for local development.
- `DEFAULT_DEMO_PASSWORD` and `DEFAULT_ADMIN_PASSWORD` may be used for local testing; avoid using them in production.
- We added `DEVELOPMENT.md` and `RELEASE_CHECKLIST.md` to help developers follow the release process and avoid secrets leaks.
- Archived internal reports are now in `docs/archives/reports/` to reduce root clutter.

---

## 🧠 How It Works

FenixAI employs a **multi-agent architecture** where specialized AI agents collaborate to make trading decisions. The system is built on three core pillars:

1. **Multi-Agent Collaboration**: Specialized agents analyze different aspects of the market
2. **Self-Evolving Memory**: ReasoningBank enables agents to learn from past decisions
3. **Dynamic Risk Management**: Real-time circuit breakers and position sizing

### 🧪 ReasoningBank: Self-Evolving Agent Memory

FenixAI implements the **ReasoningBank** architecture based on the research paper ["ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory"](https://arxiv.org/abs/2509.25140). This novel memory framework:

- **Distills reasoning strategies** from successful and failed trading decisions
- **Semantic retrieval** of relevant historical context during analysis
- **LLM-as-Judge** evaluates decision quality and provides feedback
- **Continuous learning** enables agents to improve over time
- **Embeddings-based search** finds similar market conditions from history

```python
# Example: Agent retrieves relevant context from ReasoningBank
context = reasoning_bank.get_relevant_context(
    agent_name="technical_analyst",
    current_prompt=market_analysis_prompt,
    limit=3
)
# Agent uses historical insights to make better decisions
```

### 📊 Visual Analysis System

The Visual Agent supports two modes for chart analysis:

| Mode | Description | Use Case |
|------|-------------|----------|
| **Chart Generator** | Generates charts with indicators using `mplfinance` | Fast, offline, customizable |
| **Playwright Capture** | Captures TradingView screenshots via browser automation | Real TradingView charts, advanced indicators |

Both modes produce base64-encoded images that are analyzed by vision-capable LLMs (LLaVA, GPT-4V, etc.).

![Fenix Agent Architecture](./docs/images/architecture_v2.png)

### 🤖 The Agent Team

| Agent | Responsibility | Inputs | Output |
|-------|---------------|--------|--------|
| **Technical Analyst** | RSI, MACD, ADX, SuperTrend, EMA crossovers | OHLCV data, indicators | Signal + confidence |
| **Visual Analyst** | Chart pattern recognition, support/resistance | Generated charts / TradingView screenshots | Pattern analysis |
| **Sentiment Analyst** | News, Twitter, Reddit, Fear & Greed Index | Social feeds, news APIs | Market sentiment |
| **QABBA Agent** | Bollinger Bands, volatility, squeeze detection, OBI, CVD | Microstructure data | Volatility signal |
| **Decision Agent** | Weighted consensus from all agents | All agent reports | Final trade decision |
| **Risk Manager** | Circuit breakers, position sizing, drawdown limits | Portfolio state, decision | Approved/vetoed trade |

### 🔄 Agent Workflow

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FENIX AI v2.5 RC                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌──────────────────────────────────────────────────┐    │
│  │   Frontend  │◄──►│              FastAPI + Socket.IO                 │    │
│  │  React/Vite │    │                  (Real-time)                     │    │
│  └─────────────┘    └────────────────────┬─────────────────────────────┘    │
│                                          │                                  │
│  ┌───────────────────────────────────────▼──────────────────────────────┐   │
│  │                      TRADING ENGINE                                  │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │   │
│  │  │                 LangGraph Orchestrator                          │ │   │
│  │  │                   (State Machine)                               │ │   │
│  │  └─────────────────────────────────────────────────────────────────┘ │   │
│  │           │              │              │              │             │   │
│  │     ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐       │   │
│  │     │ Technical │  │  Visual   │  │ Sentiment │  │   QABBA   │       │   │
│  │     │  Agent    │  │  Agent    │  │  Agent    │  │  Agent    │       │   │
│  │     └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘       │   │
│  │           │              │              │              │             │   │
│  │     ┌─────▼──────────────▼──────────────▼──────────────▼─────┐       │   │
│  │     │              Decision Agent + Risk Manager             │       │   │
│  │     │           (Dynamic Weighting + LLM-as-Judge)           │       │   │
│  │     └────────────────────────┬───────────────────────────────┘       │   │
│  └──────────────────────────────┼───────────────────────────────────────┘   │
│                                 │                                           │
│  ┌──────────────────────────────▼───────────────────────────────────────┐   │
│  │                         MEMORY LAYER                                 │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐   │   │
│  │  │  ReasoningBank  │  │  Trade Memory   │  │   LLM-as-Judge      │   │   │
│  │  │ (Semantic Search)│ │   (History)     │  │  (Self-Evaluation)  │   │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        EXECUTION LAYER                               │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐   │   │
│  │  │  Binance Client │  │  Order Executor │  │   Market Data       │   │   │
│  │  │ (REST + WS)     │  │  (Paper/Live)   │  │   (Real-time)       │   │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 🌟 Key Features

### Multi-Agent Collaboration

- 🤖 **6 Specialized Agents** working in parallel and sequence
- 🔄 **Dynamic Weighting** based on agent performance history
- 🎯 **Consensus-Based Decisions** with configurable thresholds

### Self-Evolving Memory (ReasoningBank)

- 🧠 **Semantic Memory Search** using embeddings
- 📝 **Experience Distillation** from successes and failures
- ⚖️ **LLM-as-Judge** for decision quality evaluation
- 📈 **Continuous Improvement** over time

### Visual Analysis

- 📊 **Chart Generator** with mplfinance (RSI, MACD, Bollinger, etc.)
- 🖼️ **TradingView Capture** via Playwright browser automation
- 👁️ **Vision LLM Integration** (LLaVA, GPT-4V compatible)

### Multi-Provider LLM Support

- 🦙 **Ollama** - Local inference with any GGUF model
- 🍎 **MLX** - Apple Silicon optimized (M1/M2/M3)
- ⚡ **Groq** - Ultra-fast cloud inference
- 🤗 **HuggingFace** - Serverless inference API

### Trading Features

- 📈 **Binance Futures** integration (testnet & live)
- 🛡️ **Paper Trading** mode by default
- ⚠️ **Circuit Breakers** for risk management
- 📊 **Multi-Timeframe Analysis** support

### Real-Time Dashboard

- 🌐 **React + TypeScript** modern frontend
- 🔌 **WebSocket** real-time updates
- 📱 **Responsive Design** with TailwindCSS
- 📊 **Live Charts** and agent performance metrics

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | 3.11 recommended |
| Node.js | 18+ | For frontend |
| Ollama | Latest | Local LLM inference |
| RAM | 16GB+ | 32GB for larger models |
| GPU | Optional | CUDA for faster inference |
| Apple Silicon | M1/M2/M3 | MLX support for optimized inference |

### Optional Services

- **Binance Account** - For live/testnet trading
- **Groq API Key** - For cloud LLM inference
- **HuggingFace Token** - For HF Inference API
- **Playwright** - For TradingView chart capture

### Installation

```bash
# Clone the repository
git clone https://github.com/Ganador1/FenixAI_tradingBot.git
cd FenixAI_tradingBot

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -e ".[dev,vision,monitoring]"

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Pull required Ollama models
ollama pull qwen3:8b
```

### Running FenixAI

```bash
# Terminal 1: Start the backend with API
python run_fenix.py --api

# Terminal 2: Start the frontend
cd frontend && npm install && npm run client:dev
```

Access the dashboard at: **http://localhost:5173**

Note: For safety, the API will bind to 127.0.0.1 by default. To allow external binding, set `ALLOW_EXPOSE_API=true`.
If you want to enable demo accounts for local development, set `CREATE_DEMO_USERS=true` and (optionally) `DEFAULT_DEMO_PASSWORD` to control the demo password. Avoid enabling demo users in production.

---

## 🔐 Release v2.5 & Security Highlights

- This release-candidate cleanup keeps the security defaults from v2.0: API binds to `127.0.0.1` by default, demo users are gated, and secrets scanning is part of the developer workflow.
- Please follow `RELEASE_CHECKLIST.md` before publishing. Dev-focused run instructions are in `DEVELOPMENT.md`.
- Archived development reports can be found in `docs/archives/reports/`.
- Demo credentials information moved to: `docs/security/docs/security/DEMO_CREDENTIALS.md`.

### CLI Options

```bash
python run_fenix.py --help

python run_fenix.py                      # Paper trading (default)
python run_fenix.py --symbol ETHUSDT     # Different symbol
python run_fenix.py --timeframe 5m       # Different timeframe
python run_fenix.py --no-visual          # Disable visual agent
python run_fenix.py --mode live --allow-live  # Live trading (⚠️ real money)
```

---

## 🏗️ Architecture

### Project Structure

```
FenixAI/
├── run_fenix.py              # Main entry point (paper / live / testnet)
├── run_nanofenix*.py         # NanoFenix entry points (v1/v2/v3/live)
├── run_hybrid_live_paper.py  # Hybrid live+paper runner
├── run_minifenix*.py         # MiniFenix entry points
├── pyproject.toml            # Python project configuration
│
├── src/
│   ├── analysis/             # Technical analysis modules
│   ├── api/                  # FastAPI server & WebSocket
│   ├── cache/                # Caching utilities (AgentReportCache)
│   ├── core/                 # LangGraph orchestrator
│   │   ├── langgraph_orchestrator.py
│   │   └── orchestrator/
│   │       ├── agents/       # Individual agent logic
│   │       ├── agent_cache.py
│   │       ├── state.py      # FenixAgentState TypedDict
│   │       ├── validation.py
│   │       └── retry_system.py
│   ├── indicators/           # Timeframe-aware indicator system
│   │   ├── timeframe_aware_indicators.py
│   │   └── advanced_indicators.py
│   ├── inference/            # Multi-provider LLM clients
│   │   ├── providers/        # Ollama, MLX, Groq, HuggingFace
│   │   └── unified_inference_client.py
│   ├── memory/               # ReasoningBank + trade memory
│   │   ├── reasoning_bank.py
│   │   └── trade_memory.py
│   ├── models/               # Pydantic models & DB schemas
│   ├── prompts/              # Agent prompt templates
│   ├── risk/                 # Runtime risk manager + circuit breakers
│   ├── security/             # SecureSecretsManager, path validation
│   ├── services/             # Binance REST/WS service layer
│   ├── tools/                # Chart generators, scrapers
│   └── trading/              # Trading engine, executor, order monitor
│       ├── engine.py         # Main trading engine
│       ├── executor.py       # Order execution (timeframe-aware SL/TP)
│       ├── trade_manager.py  # Tiered trailing stop, position tracking
│       └── market_data.py    # Microstructure metrics + normalisation
│
├── nanofenixv3/              # NanoFenix v3.5 — ML companion signal
│   ├── predictor.py          # Online LightGBM, adaptive fusion
│   ├── executor.py           # Fee-aware trailing stop
│   ├── feature_engine.py     # LOB microstructure features
│   └── adaptive_fusion.py    # Multi-horizon blending
│
├── config/
│   ├── fenix.yaml            # Main trading configuration
│   ├── llm_providers.yaml    # LLM provider profiles
│   └── settings.py           # Environment settings
│
├── frontend/                 # React + Vite + TypeScript dashboard
├── docs/                     # Documentation
│   ├── analysis/             # Run analysis reports
│   ├── benchmarks/           # Model benchmark results
│   ├── research/             # Research papers and notes
│   └── releases/             # Release notes per version
├── tests/                    # Test suite (pytest, 300+ tests)
├── scripts/                  # Utility scripts
│   ├── analysis/             # Run analysis scripts
│   └── fixes/                # One-off patch scripts
├── plans/                    # Experiment and improvement plans
└── logs/                     # Application logs
```

### Technology Stack

| Layer | Technology | Details |
|-------|------------|---------|
| **Orchestration** | LangGraph, LangChain | State machine-based agent workflow |
| **LLM Inference** | Ollama, MLX, Groq, HuggingFace | Multi-provider with automatic fallback |
| **Backend** | Python 3.10+, FastAPI, Socket.IO | Async REST API + WebSocket |
| **Frontend** | React 18, Vite, TypeScript, TailwindCSS | Modern SPA with real-time updates |
| **Exchange** | Binance Futures (ccxt, python-binance) | Testnet & production support |
| **Memory** | ReasoningBank | Semantic search + embeddings + LLM-as-Judge |
| **Visual Tools** | mplfinance, Playwright | Chart generation + TradingView capture |
| **Database** | SQLite | Trade history & reasoning persistence |
| **Monitoring** | Custom dashboard | System metrics, agent performance |

---

## 📊 Configuration

### Main Configuration (`config/fenix.yaml`)

```yaml
trading:
  symbol: BTCUSDT
  timeframe: 15m
  max_risk_per_trade: 0.02
  
agents:
  enable_technical: true
  enable_qabba: true
  enable_visual: true  # Requires vision model
  enable_sentiment: true  # Requires news APIs
  technical_weight: 0.30
  qabba_weight: 0.30
  consensus_threshold: 0.65
```

### LLM Provider Profile

You can choose a provider profile in `config/llm_providers.yaml` or by setting the environment variable `LLM_PROFILE`. For example, to use the Groq Free profile:

```bash
export GROQ_API_KEY=gsk_...
export LLM_PROFILE=groq_free
export LLM_ALLOW_NOOP_STUB=1  # optional -- fallback to noop in dev
```

If Groq packages (`langchain_groq`) or local providers (e.g., `langchain_ollama`) are not installed, Fenix will try the configured fallback provider. If none are available and `LLM_ALLOW_NOOP_STUB` is `1`, the system will initialize a Noop stub so the graph can still run for local testing.

### LLM Providers (`config/llm_providers.yaml`)

```yaml
active_profile: "all_local"  # Options: all_local, mixed_providers, mlx_optimized, all_cloud

all_local:
  technical:
    provider_type: "ollama_local"
    model_name: "qwen3:8b"
    temperature: 0.1
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BINANCE_API_KEY` | Binance API key | - |
| `BINANCE_SECRET_KEY` | Binance secret key | - |
| `LLM_PROFILE` | LLM provider profile to use | `all_local` |
| `GROQ_API_KEY` | Groq API key (for cloud inference) | - |
| `HF_TOKEN` | HuggingFace token | - |
| `ALLOW_EXPOSE_API` | Allow API to bind to all interfaces | `false` |
| `CREATE_DEMO_USERS` | Enable demo user creation | `false` |
| `LLM_ALLOW_NOOP_STUB` | Fallback to noop LLM for testing | `0` |
| `ENABLE_VISUAL_AGENT` | Enable chart analysis agent | `true` |
| `ENABLE_SENTIMENT_AGENT` | Enable news/social analysis | `true` |

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agents.py -v

# Run with coverage
pytest --cov=src --cov-report=html

# Run integration tests
pytest tests/test_integration.py -v

# Run LangGraph orchestrator tests
pytest tests/test_langgraph_orchestrator.py -v
```

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [QUICKSTART.md](./docs/QUICKSTART.md) | Getting started guide |
| [ARCHITECTURE.md](./docs/ARCHITECTURE.md) | System architecture |
| [AGENTS.md](./docs/AGENTS.md) | Agent system documentation |
| [API.md](./docs/API.md) | REST API reference |
| [CHANGELOG.md](./docs/CHANGELOG.md) | Version history |
| [DEVELOPMENT.md](./DEVELOPMENT.md) | Developer guide |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | Contribution guidelines |


---

## 🛡️ Security Considerations

### Trading Safety

| Feature | Description |
|---------|-------------|
| **Paper Trading Default** | Always starts in paper mode - no real money at risk |
| **Live Trading Safeguard** | Requires explicit `--allow-live` flag |
| **Circuit Breakers** | Automatic trading halt on excessive losses |
| **Position Limits** | Configurable maximum position sizes |
| **Daily Loss Limits** | Stop trading when daily loss threshold reached |

### Application Security

| Feature | Description |
|---------|-------------|
| **API Key Encryption** | SecureSecretsManager for encrypted storage |
| **Local API Binding** | API binds to `127.0.0.1` by default |
| **Path Validation** | Prevents path traversal attacks |
| **Rate Limiting** | Respects Binance API limits |
| **Demo User Gating** | Demo accounts disabled by default |
| **Secrets Scanning** | Pre-commit hooks for secret detection |

---

## 🤝 Contributing

Contributions are welcome! Please read our [contributing guidelines](./CONTRIBUTING.md) before submitting PRs.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Development Setup

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run linting
ruff check src/

# Run type checking
mypy src/
```

---

## ⚠️ Disclaimer

**This software is for educational and research purposes only.**

- ⚠️ Cryptocurrency trading involves substantial risk of loss
- 📉 Past performance is not indicative of future results
- 💸 Never trade with money you cannot afford to lose
- 🚫 The authors are not responsible for any financial losses
- 🧪 Always test thoroughly on paper trading before considering live trading

---

## 📄 License

This project is licensed under the **Apache License 2.0** - see the [LICENSE](LICENSE) file for details.

```
Copyright 2025 Ganador1

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

---

## 🙏 Acknowledgments

### Technologies

- [LangGraph](https://langchain-ai.github.io/langgraph/) — Agent orchestration framework (state-machine-based multi-agent graph)
- [Ollama](https://ollama.ai/) — Local LLM inference with any GGUF model
- [MLX](https://github.com/ml-explore/mlx) — Apple Silicon optimised ML framework (M1/M2/M3)
- [Groq](https://groq.com/) — Ultra-fast cloud LLM inference
- [HuggingFace](https://huggingface.co/) — Model hub and serverless inference API
- [Binance](https://www.binance.com/) — Futures exchange API (testnet + production)
- [Playwright](https://playwright.dev/) — Browser automation for TradingView chart capture
- [FastAPI](https://fastapi.tiangolo.com/) — Async Python web framework
- [React](https://reactjs.org/) — Frontend SPA framework
- [TailwindCSS](https://tailwindcss.com/) — Utility-first CSS
- [mplfinance](https://github.com/matplotlib/mplfinance) — Financial chart generation
- [sentence-transformers](https://www.sbert.net/) — Semantic embeddings for ReasoningBank memory search
- [LightGBM](https://lightgbm.readthedocs.io/) — Gradient boosting for NanoFenix return prediction
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) — Async ORM and database migrations

---

## 📚 Research & Inspiration

FenixAI v2.5 draws on two distinct bodies of research: the **multi-agent LLM system** (Fenix core) and the **NanoFenix ML companion** (high-frequency microstructure predictor). Each component has its own set of inspirations.

---

### Fenix Core — Multi-Agent LLM System

**[ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory](https://arxiv.org/abs/2509.25140)**
Ouyang et al., arXiv:2509.25140, 2025

> The core memory architecture of FenixAI. ReasoningBank enables agents to distil reasoning
> strategies from successful and failed decisions, retrieve semantically similar historical context
> at inference time, and use LLM-as-Judge feedback to continuously improve decision quality.
> Fenix implements: semantic retrieval via sentence-transformers, experience distillation,
> LLM-as-Judge evaluation, and memory-aware test-time scaling.

**[Large Language Model-based Multi-Agent Systems for Trading Firms](https://arxiv.org/abs/2402.03755)**
(Multi-agent role specialisation in financial LLM systems, 2024)

> Inspires the specialised agent roles in Fenix: Technical, Sentiment, QABBA, Visual, Decision, and
> Risk Manager mirror a professional trading desk structure. Empirical benchmarks in FenixAI show
> multi-agent outperforms monolithic by +15.8 pp win rate and +$1.54 per trade.

---

### NanoFenix — High-Frequency ML Companion

NanoFenix is a **zero-LLM, ultra-low-latency prediction engine** (~0.2 ms per prediction) that runs
alongside Fenix as a microstructure companion signal. It uses online LightGBM with a 28-feature
LOB-derived feature set and a dual-horizon consensus architecture.

**[Learning Fast and Slow for Online Time Series Forecasting](https://arxiv.org/abs/2209.11278)**
Pham et al., 2022 — *directly cited in `nanofenixv3/adaptive_fusion.py`*

> The adaptive dual-horizon fusion in NanoFenix v3.5 is directly based on this paper.
> NanoFenix maintains a "fast" short-horizon model (30 bars ≈ 30s) and a "slow" long-horizon
> model (120 bars ≈ 2 min). Weights adapt dynamically by market regime (Trending / Chop /
> Volatile / Dead) instead of using a fixed 0.4/0.6 blend.

**[Deep Learning for Limit Order Books](https://arxiv.org/abs/1901.04555)**
Wallbridge, 2020 — *DeepLOB architecture*

> Informs the "V0 Deep LOB features" in the NanoFenix feature engine: WAP (Weighted Average Price)
> distance, depth OBI (Order Book Imbalance across levels), and price pressure from the top-of-book.
> NanoFenix uses a simplified subset of these features (no deep neural net) while keeping the same
> LOB-derived signal logic.

**[Order Flow Imbalance and Market Impact](https://arxiv.org/abs/1402.2011)**
Cont, Kukanov & Stoikov, 2014

> Theoretical foundation for the OBI and multi-level OFI features used across both the QABBA agent
> and the NanoFenix feature engine. NanoFenix computes OBI at each 1s bar from bookTicker streams
> and accumulates it as a multi-scale signal (5s, 15s, 30s, 60s, 120s, 300s).

**[The Microstructure of Financial Markets](https://www.cambridge.org/core/books/microstructure-of-financial-markets/B2C81DC24B69A4CFEC91A0413E1BDC53)**
De Jong & Rindi, 2009

> Conceptual foundation for the regime detection logic in NanoFenix v1–v3: the system classifies
> each bar into LONG / SHORT / NEUTRAL based on fast/slow OBI EMA crossover and price trend in bps
> — a direct application of market microstructure theory (order flow driving short-term price
> formation).

**[Temporal Kolmogorov-Arnold Networks (T-KAN)](https://arxiv.org/abs/2405.07344)**
Liu et al., 2024 — *targeted for NanoFenix v4 (planned)*

> T-KAN replaces standard LSTM/RNN architectures with learnable B-Spline activation functions,
> reducing alpha decay in LOB forecasting. NanoFenix v4 plans a hybrid LightGBM + T-KAN module
> accelerated on Apple Neural Engine (MLX) consuming `@depth10` / `@depth20` data.

**[Multi-Level Order Flow Imbalance with Siamese Networks](https://arxiv.org/abs/2110.06827)**
(Deep OFI, 2021) — *targeted for NanoFenix v4 (planned)*

> Motivates the "Vía 3" NanoFenix v4 architecture: processing bid and ask sides in parallel via
> Siamese networks over full depth-10/20 tensor data to expose institutional walls invisible in the
> top-of-book OBI.

---

### MiniFenix — Two-Speed Slow-Brain / Fast-Trigger Prototype

MiniFenix is the research prototype that proved LLM reasoning should not sit on the hot path. It
runs a slow loop (Ollama LLM, ~15 s cadence) that publishes a `TradingRegime` object and a fast
loop (Binance WebSocket + LightGBM) that reads the regime without blocking. The lessons from
MiniFenix directly shaped NanoFenix v3.5 and the live slot runners in v2.5.

**[DeepLOB: Deep Convolutional Neural Networks for Limit Order Books](https://arxiv.org/abs/1808.03668)**
Zhang, Zohren & Roberts, 2018 — *cited in `minifenix/feature_engine.py`*

> Multi-level LOB feature design that informs the MiniFenix feature engine: depth-aware order
> book features, normalised LOB tensors, and the multi-scale momentum / imbalance signals that
> MiniFenix produces for its fast trigger.

**[LOBCAST: A Benchmark Framework for Stock Price Forecasting from Limit Order Book Data](https://arxiv.org/abs/2308.01915)**
Sangiorgio et al., 2023 — *cited in `minifenix/feature_engine.py`*

> Comparative benchmark of 15 state-of-the-art LOB forecasting models. MiniFenix borrows the
> standardised feature definitions and the train/test methodology, while keeping the actual
> predictor lightweight (online LightGBM) so it can run on a single laptop alongside Fenix Core.

---

### LLM Providers & Model-Role Specialisation

**[Ollama Cloud](https://ollama.com/) — Multi-model cloud inference**

> v2.5 routes specialised model-role assignment through Ollama Cloud: Technical and QABBA use
> Ministral-3 14B, Decision uses Nemotron-3-Nano 30B, Risk Manager uses DeepSeek's Devstral-Small-2
> 24B. The recommended team is exposed by the `/api/v25/release-info` endpoint and forwarded
> through `FENIX_TEAM_MODELS`.

**[DeepSeek V4](https://www.deepseek.com/) — Frontier reasoning model**

> Tested as an experimental Decision/Risk option via Ollama Cloud. The v2.5 release ships the
> stable Nemotron + Devstral team by default and keeps DeepSeek V4 as a configurable opt-in until
> more long-run benchmarks are available.

---

### Technical Analysis & Regime Detection

**[The Choppiness Index](https://www.investopedia.com/terms/c/choppinessindex.asp)**
E.W. Dreiss, 1993

> The CHOP indicator (38.2–61.8 transition band) drives execution gating in the trading engine.
> In transition, position size is reduced by `FENIX_FILTER_CHOP_SIZE_MULT`; in CHOP (≥61.8),
> low-confidence signals are blocked entirely.

**[TTM Squeeze](https://www.investopedia.com/terms/t/ttm-squeeze.asp)** — John Carter

> TTM Squeeze detection (`bb_inside_kc`) is used by the QABBA agent as a momentum context cue.
> A squeeze fires when Bollinger Bands collapse inside Keltner Channels; the release is treated
> as a high-momentum breakout signal.

---

### Risk Management

**[The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market](https://www.eecs.harvard.edu/cs286r/courses/fall12/papers/Thorpe_KellyCriterion2007.pdf)**
Thorp, 2007

> Informs ATR-based position sizing and the Risk Manager soft-cap: size is bounded so a string
> of losses cannot breach the configured daily drawdown limit, consistent with fractional Kelly
> sizing principles.

---

## 📬 Contact & Support

- **Issues**: [GitHub Issues](https://github.com/Ganador1/FenixAI_tradingBot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Ganador1/FenixAI_tradingBot/discussions)

---

<div align="center">

**Made with ❤️ by [Ganador1](https://github.com/Ganador1)**

*If you find this project useful, please consider giving it a ⭐!*

[⬆ Back to Top](#-fenixai-trading-bot-v25-release-candidate)

</div>
