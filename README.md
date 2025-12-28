<div align="center">

# 🦅 FenixAI Trading Bot v2.0

### Autonomous Multi-Agent Cryptocurrency Trading System with Self-Evolving Memory

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-green.svg)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/Frontend-React_18-61DAFB.svg)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0-3178C6.svg)](https://www.typescriptlang.org/)
[![CCXT](https://img.shields.io/badge/Exchange-CCXT-green.svg)](https://ccxt.trade/)
[![arXiv](https://img.shields.io/badge/arXiv-2509.25140-b31b1b.svg)](https://arxiv.org/abs/2509.25140)
[![TailwindCSS](https://img.shields.io/badge/Styling-TailwindCSS-38B2AC.svg)](https://tailwindcss.com/)
[![Socket.IO](https://img.shields.io/badge/Realtime-Socket.IO-010101.svg)](https://socket.io/)

*An advanced trading system powered by multiple specialized AI agents that collaborate to analyze markets, manage risk, and execute trades on multiple cryptocurrency exchanges. Features ReasoningBank memory system for self-evolving agent capabilities.*

![Fenix Dashboard Preview](./Dashboard%20Fenix.png)

[📖 Documentation](./docs/) · [🚀 Quick Start](#-quick-start) · [🏗️ Architecture](#-architecture) · [📝 Changelog](./docs/CHANGELOG.md) · [📄 Paper](https://arxiv.org/abs/2509.25140)

</div>

---

> **⚠️ WARNING: This is a BETA project.** Fenix is under active development, is not yet profitable, and may not work as expected. Use at your own risk!

### 🦅 A Message from the Creator (v2.0)

Hello, it has been 6 months since I launched the first version of Fenix. I have been on an incredible journey for six months, learning a ton about programming, LLMs, AI papers, and above all, experimenting a lot with Fenix.

I have been doing hundreds of tests with both paper trading and live trading, testing different LLM configurations, different cryptocurrencies, different timeframes, adding and removing more agents, and I have learned a lot. I believe the main advantage of Fenix is that it evolves over time along with LLMs; they are getting smarter and it shows in their trading decisions.

So far, the best performance I have obtained is with large models of over 50b, but the main problem is that they are expensive and difficult to maintain privacy. I think the best option currently between price/privacy and large models is the Ollama cloud models which are expanding more and more, but without a doubt, I believe the best option will be to fine-tune several small models. Right now I am experimenting with that configuration.

But I didn't want to leave you any longer without updates, so this is **Version 2.0**. It now includes a nice and intuitive local page to make it more accessible to use. Regarding performance, what has improved the most is undoubtedly the **Reasoning Bank**; it helps agents not to make the same mistakes repeatedly and also to be right more often thanks to remembering. I am also experimenting with the new HOPE model that learns as it is used, but I still don't have a clear result to share.

While I continue investigating and improving Fenix, I hope this new version can be useful to at least one person as inspiration or to test the new limits of trading.

Thank you for taking the time to read my words. I would appreciate it if you leave me a star, a comment in discussion, a contribution, advice, or some change on my BuyMeACoffee page.

Thank you very much,
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

## ✨ What's New in v2.0

> **Complete architectural overhaul** - Migrated from CrewAI to **LangGraph** for more robust and flexible agent orchestration.

| Feature | v1.0 (June 2025) | v2.0 (December 2025) |
|---------|------------------|---------------------|
| **Orchestration** | CrewAI | LangGraph (State Machine) |
| **Memory System** | Basic TradeMemory | [ReasoningBank](https://arxiv.org/abs/2509.25140) + LLM-as-Judge |
| **Visual Analysis** | Static screenshots | Chart Generator + Playwright TradingView Capture |
| **LLM Providers** | Ollama only | Ollama, MLX, Groq, HuggingFace |
| **Exchange Support**| Binance only | **Multi-Exchange (CCXT)** |
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
│                              FENIX AI v2.0                                  │
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
│  │  │ Exchange Client │  │  Order Executor │  │   Market Data       │   │   │
│  │  │ (CCXT)          │  │  (Paper/Live)   │  │   (Real-time)       │   │   │
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

### Multi-Exchange Support (CCXT)

- 📈 **CCXT Integration** for trading on multiple exchanges
- 🇺🇸 **USA-Compliant Exchanges** supported (Coinbase, Kraken, Gemini)
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

- **Exchange Account** - For live/testnet trading (Binance, Coinbase, Kraken, etc.)
- **Groq API Key** - For cloud LLM inference
- **HuggingFace Token** - For HF Inference API
- **Playwright** - For TradingView chart capture

### Installation

```bash
# Clone the repository
git clone https://github.com/DragonBtc93/FenixAI_tradingBot_mod.git
cd FenixAI_tradingBot_mod

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # For Linux/Mac, including Debian-based systems like Pop!_OS

# Install dependencies
pip install -e ".[dev,vision,monitoring]"

# Note for TA-Lib installation on Debian-based systems (like Pop!_OS)
# You may need to install the TA-Lib C library first:
# sudo apt-get install -y ta-lib-dev

# Default Login Credentials for Local Development
# The application will create two demo users by default in development.
# - Admin: admin@trading.com
# - Trader: trader@trading.com
# The default password for both is "password". You can change this by setting the DEFAULT_DEMO_PASSWORD environment variable.

# Configure environment
cp .env.example .env
# Edit .env with your exchange and API keys

# Pull required Ollama models
ollama pull qwen3:8b
```

### Running FenixAI

**1. Start the Backend Server**

In your first terminal, run the following command to start the FastAPI backend:

```bash
python run_fenix.py --api
```

**2. Start the Frontend Development Server**

In a second terminal, navigate to the `frontend` directory, install the dependencies, and start the development server:

```bash
cd frontend
npm install
npm run client:dev
```

Access the dashboard at: **http://localhost:5173**

Note: For safety, the API will bind to 127.0.0.1 by default. To allow external binding, set `ALLOW_EXPOSE_API=true`.
If you want to enable demo accounts for local development, set `CREATE_DEMO_USERS=true` and (optionally) `DEFAULT_DEMO_PASSWORD` to control the demo password. Avoid enabling demo users in production.

---

## 🔐 Release v2.0 & Security Highlights

- This release improves security defaults: API binds to `127.0.0.1` by default, demo users are gated, and secrets scanning is included in the developer workflow.
- Please follow `RELEASE_CHECKLIST.md` before final releases. Dev-focused run instructions are in `DEVELOPMENT.md`.
- Archived development reports can be found in `docs/archives/reports/`.
- Demo credentials information moved to: `docs/security/docs/security/DEMO_CREDENTIALS.md`.

### CLI Options

```bash
python run_fenix.py --help

python run_fenix.py --exchange kraken     # Use Kraken exchange
python run_fenix.py --symbol ETH/USD      # Different symbol
python run_fenix.py --timeframe 5m        # Different timeframe
python run_fenix.py --no-visual           # Disable visual agent
python run_fenix.py --mode live --allow-live  # Live trading (⚠️ real money)
```

---

## 🏗️ Architecture

### Project Structure

```
FenixAI/
├── run_fenix.py              # Main entry point
├── pyproject.toml            # Python project configuration
│
├── src/
│   ├── analysis/             # Technical analysis modules
│   ├── api/                  # FastAPI server & WebSocket
│   ├── cache/                # Caching utilities
│   ├── core/                 # LangGraph orchestrator
│   │   └── langgraph_orchestrator.py
│   ├── dashboard/            # Trading dashboard backend
│   ├── inference/            # Multi-provider LLM clients
│   │   ├── providers/        # Ollama, MLX, Groq, HuggingFace
│   │   ├── reasoning_judge.py
│   │   └── unified_inference_client.py
│   ├── memory/               # Memory systems
│   │   ├── reasoning_bank.py # ReasoningBank implementation
│   │   └── trade_memory.py   # Trade history storage
│   ├── models/               # Data models & schemas
│   ├── monitoring/           # System monitoring
│   ├── pipeline/             # Data processing pipelines
│   ├── prompts/              # Agent prompt templates
│   ├── risk/                 # Risk management module
│   ├── services/             # External service integrations
│   ├── tools/                # Agent tools
│   │   ├── chart_generator.py           # Chart generation with mplfinance
│   │   ├── chart_generator_playwright.py
│   │   ├── tradingview_playwright_capture.py
│   │   ├── fear_greed.py                # Fear & Greed Index
│   │   ├── twitter_scraper.py
│   │   └── reddit_scraper.py
│   ├── trading/              # Trading engine
│   │   ├── engine.py         # Main trading engine
│   │   ├── exchange_client.py # Generic CCXT client
│   │   └── executor.py       # Order execution
│   └── utils/                # Utility functions
│
├── config/
│   ├── fenix.yaml            # Main configuration
│   ├── llm_providers.yaml    # LLM provider profiles
│   └── settings.py           # Environment settings
│
├── frontend/                 # React + Vite dashboard
│   ├── components/           # React components
│   ├── pages/                # Page components
│   ├── hooks/                # Custom React hooks
│   ├── stores/               # State management
│   ├── providers/            # Context providers
│   └── api/                  # API client for frontend
│
├── docs/                     # Documentation
├── tests/                    # Test suite
├── scripts/                  # Utility scripts
└── logs/                     # Application logs
```

### Technology Stack

| Layer | Technology | Details |
|-------|------------|---------|
| **Orchestration** | LangGraph, LangChain | State machine-based agent workflow |
| **LLM Inference** | Ollama, MLX, Groq, HuggingFace | Multi-provider with automatic fallback |
| **Backend** | Python 3.10+, FastAPI, Socket.IO | Async REST API + WebSocket |
| **Frontend** | React 18, Vite, TypeScript, TailwindCSS | Modern SPA with real-time updates |
| **Exchange** | CCXT | Multi-exchange support (Binance, Coinbase, Kraken, etc.) |
| **Memory** | ReasoningBank | Semantic search + embeddings + LLM-as-Judge |
| **Visual Tools** | mplfinance, Playwright | Chart generation + TradingView capture |
| **Database** | SQLite | Trade history & reasoning persistence |
| **Monitoring** | Custom dashboard | System metrics, agent performance |

---

## 📊 Configuration

### Main Configuration (`config/fenix.yaml`)

```yaml
trading:
  exchange: binance
  symbol: BTC/USDT
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
| `EXCHANGE_ID` | The exchange to use | `binance` |
| `[EXCHANGE]_API_KEY`| API key for the selected exchange | - |
| `[EXCHANGE]_SECRET` | API secret for the selected exchange | - |
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
| **Rate Limiting** | Respects exchange API limits |
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

- [LangGraph](https://langchain-ai.github.io/langgraph/) - Agent orchestration framework
- [Ollama](https://ollama.ai/) - Local LLM inference
- [MLX](https://github.com/ml-explore/mlx) - Apple Silicon optimized ML framework
- [Groq](https://groq.com/) - Ultra-fast LLM inference
- [HuggingFace](https://huggingface.co/) - Model hub and inference
- [CCXT](https://ccxt.trade/) - Multi-exchange trading library
- [Playwright](https://playwright.dev/) - Browser automation for TradingView capture
- [FastAPI](https://fastapi.tiangolo.com/) - Modern Python web framework
- [React](https://reactjs.org/) - Frontend framework
- [TailwindCSS](https://tailwindcss.com/) - Utility-first CSS
- [mplfinance](https://github.com/matplotlib/mplfinance) - Financial chart generation

### 📚 Research Papers

- **ReasoningBank**: ["ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory"](https://arxiv.org/abs/2509.25140) - Ouyang et al., 2025
  - Core memory architecture enabling agents to learn from past decisions
  - Implements semantic retrieval, LLM-as-Judge, and memory-aware test-time scaling

---

## 📬 Contact & Support

- **Issues**: [GitHub Issues](https://github.com/Ganador1/FenixAI_tradingBot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Ganador1/FenixAI_tradingBot/discussions)

---

<div align="center">

**Made with ❤️ by [Ganador1](https://github.com/Ganador1)**

*If you find this project useful, please consider giving it a ⭐!*

[⬆ Back to Top](#-fenixai-trading-bot-v20)

</div>
