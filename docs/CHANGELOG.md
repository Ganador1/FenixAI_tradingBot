# 📋 FenixAI - Changelog

All notable changes to this project will be documented in this file.

---

## [2.5.0] - 2026-05-27

### Added

- v2.5 release notes in `docs/releases/v2.5.md`.
- Long-form v2.0-to-v2.5 development history in `docs/releases/v2.5-development-history.md`.
- Public new-systems guide in `docs/releases/v2.5-new-systems.md`, covering NanoFenix, MiniFenix, Fenix Experimental, new agent paths, model-role teams, and live runners.
- Script evolution and release-surface guide in `docs/releases/v2.5-script-evolution.md`, covering the v25/v31 live-safe launcher lineage and historical script families.
- v2.5 release checklist in `RELEASE_CHECKLIST.md`.
- Script release-surface index in `scripts/README.md`.
- English NanoFenix HTF v2.5 documentation in `docs/NANOFENIX_HTF_V2_5_CHANGES.md`.
- Hybrid runner support for staged TP/SL tracking and partial closes.
- Binance Futures algorithmic-order checks when verifying live protective orders.

### Changed

- README and quick-start documentation updated for the official v2.5.0 release.
- NanoFenix changelog has been translated to English.
- MiniFenix README has been translated to English and reframed as a two-speed research prototype.
- NanoFenix adaptive-fusion comments and docstrings have been translated to English.
- Hybrid runner comments and docstrings around staged exits have been translated to English.
- Live engine restart behavior hydrates an existing exchange position before assuming the bot is flat.
- Live slot summaries now account for hydrated exchange positions when validating close events.
- Paper/hybrid execution guards reject invalid prices before recording position transitions.
- Live suite plans can now carry explicit lite/MTF guard settings into `run_fenix_live_slot.py`, avoiding hidden dependence on inherited shell variables.
- NanoFenix trailing stops account for estimated fees before treating an exit as acceptable.

### Fixed

- Failed execution attempts are no longer recorded as realized trading losses by runtime risk accounting.
- Protective-order verification no longer misses open Binance Futures algorithmic orders.
- A safe reduce-only close of a hydrated live position no longer causes a false `completed_with_accounting_gap` summary.
- Paper/hybrid logs are less likely to contain misleading zero or missing entry prices.
- `technical_mtf_qabba_guard` now reports missing strict-MTF configuration instead of silently recording `_mtf_bias: {}`.
- A Technical timeout/error fallback can no longer count as a clean Technical HOLD for the optional MTF+QABBA entry path.

---

## [2.0.0] - 2024-12-05 🚀

### 🎯 Major Architecture Overhaul

This release represents a **complete rewrite** of FenixAI, transitioning from CrewAI to **LangGraph** for a more robust and flexible agent orchestration system.

### ✨ New Features

#### Core Architecture

- **LangGraph Orchestrator**: Replaced CrewAI with LangGraph state machine for agent coordination
- **Multi-Provider LLM System**: Support for Ollama, MLX (Apple Silicon), Groq, and HuggingFace
- **ReasoningBank**: New memory system inspired by academic research (arXiv:2509.25140)
  - Semantic search for similar past decisions
  - Self-evaluation with LLM-as-Judge
  - Reward shaping based on trade outcomes
- **Dynamic Weighting**: Agents weights adapt based on historical performance and market conditions

#### Agents

- **Enhanced Technical Analyst**: Improved indicator validation and confluence scoring
- **Enhanced Visual Analyst**: Better chart pattern recognition with security validation
- **Enhanced Sentiment Analyst**: Multi-source sentiment aggregation
- **Enhanced QABBA Agent**: Bollinger Bands and volatility analysis
- **Enhanced Decision Agent**: Weighted consensus with LLM-as-Judge integration
- **Risk Manager**: Circuit breakers and dynamic position sizing

#### Frontend

- **New React Dashboard**: Built with Vite, TypeScript, and TailwindCSS
- **Real-time Updates**: Socket.IO for live data streaming
- **Agent Performance Charts**: Visualize agent accuracy and contributions
- **System Status Monitoring**: CPU, memory, and connection health

#### Trading

- **Async Binance Client**: Improved WebSocket handling
- **Paper Trading Mode**: Realistic simulation with slippage and fees
- **Multi-timeframe Analysis**: Context from 15m, 1h, and 4h timeframes

#### Security

- **SecureSecretsManager**: Encrypted storage for API keys
- **Chart Path Validation**: Prevents path traversal and stale data attacks
- **Live Trading Safeguard**: Requires explicit `--allow-live` flag

### 🔄 Changed

- **Project Structure**: Reorganized into `src/` with clear module separation
- **Configuration**: YAML-based configuration with multiple LLM provider profiles
- **Logging**: Structured logging with JSON trace files for debugging
- **Dependencies**: Updated to Python 3.10+, modern async patterns

### 🗑️ Removed

- CrewAI dependency (replaced by LangGraph)
- Old monolithic `live_trading.py` (split into modules)
- Deprecated agent implementations (moved to `legacy/`)

### 📚 Documentation

- New comprehensive documentation in `/docs`
- Legacy development notes preserved in `/legacy/docs/v2_development_notes`

---

## [1.0.0] - 2024-06-01

### Initial Release

- **CrewAI-based Architecture**: Multi-agent system using CrewAI
- **Basic Agents**: Technical, Visual, Sentiment, Consensus
- **Binance Integration**: Live and paper trading
- **Simple Dashboard**: Flask-based monitoring
- **Paper Trading System**: Basic simulation

---

## Migration Guide (v1.0 → v2.0)

### Breaking Changes

1. **Configuration Format**
   - Old: `config/config.yaml`
   - New: `config/fenix.yaml` + `config/llm_providers.yaml`

2. **Agent Imports**
   - Old: `from agents.sentiment import SentimentAgent`
   - New: `from src.agents.sentiment_enhanced import EnhancedSentimentAnalyst`

3. **Running the Bot**
   - Old: `python live_trading.py`
   - New: `python run_fenix.py --api`

4. **Environment Variables**
   - New required variables for LLM providers
   - See `.env.example` for complete list

### Migration Steps

1. Backup your configuration and trades
2. Install new dependencies: `pip install -e ".[dev]"`
3. Update configuration files
4. Pull required Ollama models
5. Test with paper trading before going live

---

## Versioning

We use [Semantic Versioning](https://semver.org/):

- **MAJOR**: Incompatible API changes
- **MINOR**: New features, backwards compatible
- **PATCH**: Bug fixes, backwards compatible

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on:

- Bug reports
- Feature requests
- Pull requests
- Code style
