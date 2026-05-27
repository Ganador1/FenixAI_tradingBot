# 🚀 FenixAI v2.5 Release Candidate - Quick Start Guide

This guide starts FenixAI in local paper mode by default. v2.5 is a release candidate, so validate behavior in paper mode before enabling live trading.

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for frontend)
- **Ollama** running locally
- **Binance API keys** (optional, for live trading)

---

## 1. Clone and Setup

```bash
# Clone the repository after v2.5 is published
git clone https://github.com/Ganador1/FenixAI_tradingBot.git
cd FenixAI_tradingBot

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or .venv\Scripts\activate  # Windows

# Install dependencies
pip install -e ".[dev,vision,monitoring]"
```

---

## 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings
nano .env
```

**Required variables:**

```env
# Binance (optional for paper trading)
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret

# Optional: Cloud LLM providers
GROQ_API_KEY=your_groq_key
HF_TOKEN=your_huggingface_token
```

---

## 3. Install Ollama Models

```bash
# Start Ollama
ollama serve

# In another terminal, pull required models
ollama pull qwen3:8b
ollama pull qwen3-vl:8b  # For visual agent (optional)
```

---

## 4. Configure FenixAI

Edit `config/fenix.yaml`:

```yaml
trading:
  symbol: BTCUSDT
  timeframe: 15m
  
agents:
  enable_technical: true
  enable_qabba: true
  enable_visual: false  # Enable if you have vision model
  enable_sentiment: false

llm:
  default_provider: ollama_local
  default_model: qwen3:8b
```

---

## 5. Run FenixAI

### Paper Trading (Recommended for Testing)

```bash
# Terminal 1: Start backend
python run_fenix.py --api

# Terminal 2: Start frontend
cd frontend
npm install
npm run client:dev
```

Access the dashboard at: **<http://localhost:5173>**

### CLI Options

```bash
python run_fenix.py --help

# Examples:
python run_fenix.py                      # Paper trading, BTCUSDT, 15m
python run_fenix.py --symbol ETHUSDT     # Different symbol
python run_fenix.py --timeframe 5m       # Different timeframe
python run_fenix.py --no-visual          # Disable visual agent
python run_fenix.py --dry-run            # Simulate without executing
```

### Live Trading (⚠️ Real Money)

```bash
# Requires explicit confirmation flag
python run_fenix.py --mode live --allow-live
```

---

## 6. Verify Setup

Check that everything is working:

```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Check API
curl http://localhost:8000/health
```

Expected output:

```json
{"status": "healthy", "version": "2.x"}
```

---

## Quick Configuration Reference

### Enable/Disable Agents

```yaml
# config/fenix.yaml
agents:
  enable_technical: true   # RSI, MACD, ADX analysis
  enable_qabba: true       # Bollinger Bands, volatility
  enable_visual: false     # Chart pattern recognition
  enable_sentiment: false  # News/social sentiment
```

### Change LLM Provider

```yaml
# config/llm_providers.yaml
active_profile: "all_local"  # Options: all_local, mixed_providers, mlx_optimized, all_cloud
```

### Risk Parameters

```yaml
# config/fenix.yaml
trading:
  max_risk_per_trade: 0.02    # 2% max per trade
  max_total_exposure: 0.05    # 5% total exposure
  min_risk_reward_ratio: 1.5  # Minimum R:R
```

---

## Common Issues

### Ollama Not Available

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Start Ollama
ollama serve
```

### Model Not Found

```bash
# List available models
ollama list

# Pull missing model
ollama pull qwen3:8b
```

### Binance Connection Failed

1. Check `.env` has correct API keys
2. For testnet, ensure `testnet: true` in config
3. Verify network connectivity

### Port Already in Use

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>
```

---

## Next Steps

1. 📖 Read [ARCHITECTURE.md](ARCHITECTURE.md) to understand the system
2. 🤖 Learn about [AGENTS.md](AGENTS.md) and how they work
3. 📡 Explore the [API.md](API.md) for integration options
4. 🔧 Customize `config/fenix.yaml` for your needs

---

## Support

- **GitHub Issues**: [github.com/Ganador1/FenixAI_tradingBot/issues](https://github.com/Ganador1/FenixAI_tradingBot/issues)
- **Documentation**: See `/docs` folder
- **Legacy Notes**: See `/legacy/docs` for development history

---

**Happy Trading! 🦅**
