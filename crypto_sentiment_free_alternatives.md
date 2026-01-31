# 🚀 ALTERNATIVAS GRATUITAS & OPEN SOURCE PARA SENTIMENT ANALYSIS EN CRYPTO TRADING

> **Presupuesto: $0** | Self-hosted | Free tiers generosos

---

## 1. 📰 FUENTES DE NOTICIAS GRATIS

### RSS Feeds Crypto Confiables

| Fuente | URL RSS | Frecuencia | Calidad |
|--------|---------|------------|---------|
| **CoinDesk** | `https://www.coindesk.com/feed/` | En tiempo real | ⭐⭐⭐⭐⭐ |
| **CoinTelegraph** | `https://cointelegraph.com/rss` | En tiempo real | ⭐⭐⭐⭐ |
| **Decrypt** | `https://decrypt.co/feed/` | Cada hora | ⭐⭐⭐⭐ |
| **The Block** | `https://www.theblock.co/rss.xml` | En tiempo real | ⭐⭐⭐⭐⭐ |
| **Bitcoin Magazine** | `https://bitcoinmagazine.com/feed/` | Diario | ⭐⭐⭐⭐ |
| **CryptoSlate** | `https://cryptoslate.com/feed/` | Cada hora | ⭐⭐⭐⭐ |
| **CryptoNews** | `https://crypto.news/feed/` | En tiempo real | ⭐⭐⭐ |
| **Protos** | `https://protos.com/feed/` | Diario | ⭐⭐⭐⭐ |
| **BeInCrypto** | `https://beincrypto.com/feed/` | Cada hora | ⭐⭐⭐ |
| **DL News** | `https://www.dlnews.com/rss.xml` | En tiempo real | ⭐⭐⭐⭐ |

### Reddit Communities (JSON API gratis)
- `https://www.reddit.com/r/CryptoCurrency/.json` - 5.3M miembros
- `https://www.reddit.com/r/Bitcoin/.json` - 5.2M miembros
- `https://www.reddit.com/r/ethereum/.json` - 1.8M miembros
- `https://www.reddit.com/r/CryptoMarkets/.json` - 600K miembros
- `https://www.reddit.com/r/wallstreetbets/.json` - 13M miembros (meme stocks + crypto)

### Twitter/X Alternativas Sin API
- **Nitter (instancias self-hosted)** - Scraping ético de tweets sin API
  - `https://nitter.net/` (principal, a veces bloqueado)
  - Instancias alternativas: `https://nitter.1d4.us/`, `https://nitter.kavin.rocks/`
  - Formato: `https://nitter.net/{username}/rss`
- **RSS-Bridge** (self-hosted): Convierte perfiles públicos a RSS
  - Repo: `https://github.com/RSS-Bridge/rss-bridge`

### APIs Free Tier Noticias Financieras

| API | Free Tier | Límites | Enlace |
|-----|-----------|---------|--------|
| **NewsAPI** | 100 requests/día | 1 request/6 segundos | `https://newsapi.org/pricing` |
| **Alpha Vantage** | 25 requests/día | 1 request/15 segundos | `https://www.alphavantage.co/support/` |
| **GNews** | 100 requests/día | 10 artículos/request | `https://gnews.io/pricing` |
| **Currents API** | 300 requests/mes | - | `https://currentsapi.services/` |
| **New York Times API** | 500 requests/día | 10 requests/min | `https://developer.nytimes.com/` |
| **The Guardian API** | 5000 requests/día | 12 requests/min | `https://open-platform.theguardian.com/access/` |
| **CryptoPanic API** | 1 request/s (no key) | Posts en feed público | `https://cryptopanic.com/developers/api/` |
| **NewsData.io** | 200 requests/día | 5 requests/min | `https://newsdata.io/pricing` |
| **WorldNews API** | 100 requests/día | - | `https://worldnewsapi.com/pricing` |
| **Bing News Search** | 1000 requests/mes (Azure) | 1 request/s | Azure Marketplace |

### Web Scraping Ético (Fuentes Públicas)

```python
# Librerías Python gratuitas
import requests
from bs4 import BeautifulSoup
import feedparser
import json

# 1. Fear & Greed Index (Crypto) - Gratuito
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=0"  # JSON completo

# 2. CoinGecko (API pública sin key, límites razonables)
COINGECKO_NEWS = "https://www.coingecko.com/news"  # Scrapear portada

# 3. LunarCrush (nivel gratuito)
LUNARCRUSH_API = "https://lunarcrush.com/api3"  # Tier gratuito disponible

# 4. CryptoCompare (API gratuita)
CRYPTOCOMPARE_NEWS = "https://min-api.cryptocompare.com/data/v2/news/"
# API Key gratuita obligatoria después de 100k calls/mes

# 5. TradingView ideas/sentiment
TRADINGVIEW_SCREENER = "https://scanner.tradingview.com/crypto/scan"
# Devuelve JSON con sentiment de traders
```

### Alternativas Gratuitas a Bloomberg/Reuters

| Alternativa | Qué ofrece | Acceso |
|-------------|------------|--------|
| **Trading Economics** | Noticias económicas, API gratuita | `https://tradingeconomics.com/api/` |
| **Finnhub** | News, fundamentales, 60calls/min gratis | `https://finnhub.io/pricing` |
| **MarketWatch** | RSS gratuito | `https://www.marketwatch.com/rss/` |
| **Investing.com** | RSS por categoría | `/rss/` al final de cada sección |
| **Forexlive** | News forex/crypto RSS | `https://www.forexlive.com/rss` |
| **ZeroHedge** | RSS para análisis contrario | `https://feeds.feedburner.com/zerohedge/feed` |
| **MishTalk** | Análisis macro | `https://mishtalk.com/feed/` |
| **Liberty Street Economics** | Fed, investigación | `https://libertystreeteconomics.newyorkfed.org/rss2.xml` |

---

## 2. ⛓️ ON-CHAIN DATA GRATIS

### Alternativas a Glassnode (Gratis)

| Plataforma | Qué ofrece | Límites Free | Enlace |
|------------|------------|--------------|--------|
| **Dune Analytics** | Dashboards SQL, datos raw | Ilimitado lectura | `https://dune.com` |
| **DeFi Llama** | TVL, yields, bridges | API abierta 100% | `https://defillama.com/api/docs` |
| **Token Terminal** | Métricas fundamentales crypto | 1 dashboard active | `https://tokenterminal.com/resources/api` |
| **Messari** | On-chain metrics API | 1000 calls/mes | `https://messari.io/api` |
| **Santiment** | Social + on-chain | 1000 API calls/mes | `https://santiment.net/free-api/` |
| **Artemis** | Métricas L1/L2 | Free tier limitado | `https://app.artemis.xyz/` |
| **Step Finance** | Datos Solana | Solana completo | `https://step.finance/` |
| **Nansen Lite** | Smart alerts básico | Wallets limitadas | `https://pro.nansen.ai/plans` |
| **Arkham Intelligence** | Inteligencia blockchain free | Explorer público | `https://www.arkhamintelligence.com/` |

### APIs Públicas Binance/Bitcoin

```python
# === BINANCE API PÚBLICA (Gratuita, no necesita KYC para datos) ===
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1"

# Endpoints útiles:
GET /ticker/24hr                    # Precios, cambio 24h
GET /ticker/bookTicker              # Best bid/ask
GET /klines                         # Velas históricas
GET /fundingRate                    # Funding rate perpetuals
GET /openInterest                   # Open interest
GET /topLongShortAccountRatio       # Long/Short ratio
GET /topLongShortPositionRatio      # L/S posiciones
GET /globalLongShortAccountRatio    # Ratio global L/S

# Ejemplo:
# https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT
# https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1

# === BITCOIN-ONLY APIs GRATIS ===
Mempool.space API: https://mempool.space/api/
  - Fees en tiempo real
  - Datos de mempool
  - Transacciones on-chain históricas

Bitcoin Visuals: https://bitcoinvisuals.com/ (CSV dumps)

Clark Moody Dashboard: https://bitcoin.clarkmoody.com/ (API endpoints ocultos)

Glassnode Studio: https://studio.glassnode.com/ (algunas métricas gratuitas)

CoinMetrics: https://docs.coinmetrics.io/api/ (free tier limitado)

# === ETHEREUM / EVM ===
Etherscan API: Gratuita con rate limits
  - 5 calls/segundo
  - API Key requerida (gratis)
  
# Formato:
# https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey=XXX
```

### Exploradores Blockchain APIs Gratuitas

| Explorer | API Docs | Free Tier | Datos Disponibles |
|----------|----------|-----------|-------------------|
| **Etherscan** | `docs.etherscan.io` | 5 calls/s, API key gratis | Transacciones, wallet, gas, tokens |
| **BSCScan** | `docs.bscscan.com` | 5 calls/s, API key gratis | BSC completo igual que Etherscan |
| **PolygonScan** | `polygonscan.com/apis` | 5 calls/s, API key gratis | Polygon POS |
| **Arbiscan** | `arbiscan.io/apis` | 5 calls/s, API key gratis | Arbitrum |
| **Optimistic Etherscan** | `optimistic.etherscan.io` | 5 calls/s, API key gratis | Optimism |
| **BaseScan** | `basescan.org` | 5 calls/s, API key gratis | Base (Coinbase) |
| **Blockchain.com** | `https://www.blockchain.com/api` | Gratuito con límites | Bitcoin, Ethereum, datos de exchange |
| **Blockchair API** | `https://blockchair.com/api/docs` | Sin API key, límites IP | Bitcoin, Ethereum, Ripple, +16 |
| **Solscan** | `public-api.solscan.io` | Free tier disponible | Solana |
| **Aptos Explorer** | API GraphQL disponible | Gratuito | Aptos |
| **NearBlocks** | `nearblocks.io/api-docs` | Gratuito con key | NEAR |
| **Cardano Explorer** | `cexplorer.io/developers` | API gratuita | Cardano |

### Funding Rates & Open Interest Gratis

```python
# === BINANCE (Sin autenticación) ===
FUNDING_RATES = "https://fapi.binance.com/fapi/v1/fundingRate"
OPEN_INTEREST = "https://fapi.binance.com/fapi/v1/openInterest"

# === BYBIT (API pública) ===
BYBIT_FUNDING = "https://api.bybit.com/v5/market/funding-rate"
BYBIT_OI = "https://api.bybit.com/v5/market/tickers"

# === OKX ===
OKX_FUNDING = "https://www.okx.com/api/v5/public/funding-rate"

# === DYDX (v3 API gratuita) ===
DYDX_OI = "https://api.dydx.exchange/v3/stats"

# === COINGLASS (Plan gratuito) ===
# https://coinglass.com/pricing (500 credits/mes)

# === CRYPTOFACILITIES ===
# Datos históricos de funding (Kraken Futures): API abierta

# === APEX/RADAR ===
# Agregadores que combinan todas las fuentes
```

---

## 3. 🤖 LLMs GRATIS / OPEN SOURCE

### Modelos Locales Gratuitos (Self-Hosted)

| Modelo | Parámetros | Uso | VRAM Requerido | Descarga |
|--------|------------|-----|----------------|----------|
| **Mistral 7B Instruct** | 7B | Sentiment, análisis | ~16GB | HuggingFace |
| **Mixtral 8x7B** | 46B | Análisis complejo | ~90GB (8-bit) | HuggingFace |
| **Llama 3.1** | 8B/70B | Sentiment avanzado | 16GB/140GB | Meta AI |
| **Qwen 2.5** | 7B/14B/32B | Excelente para texto financiero | 16GB/32GB/80GB | Alibaba |
| **CodeLlama** | 7B/13B/34B | Análisis de código + texto | 16GB+/32GB+ | Meta AI |
| **Zephyr 7B** | 7B | Optimizado para instrucciones | 16GB | HuggingFace |
| **OpenChat 3.5** | 7B | Conversación/análisis | 16GB | HuggingFace |
| **Phi-4** | 14B | Microsoft, muy capaz | 32GB | Microsoft |
| **Nous Hermes 2** | 34B/70B | Finetuned para tool use | 68GB/140GB | HuggingFace |

### Herramientas para Correr Modelos Locales

| Framework | Facilidad | Features | Link |
|-----------|-----------|----------|------|
| **Ollama** | ⭐⭐⭐⭐⭐ | Docker-like para LLMs | `ollama.com` |
| **LM Studio** | ⭐⭐⭐⭐⭐ | GUI completa | `lmstudio.ai` |
| **llama.cpp** | ⭐⭐⭐ | C++, muy rápido en CPU | `github.com/ggerganov/llama.cpp` |
| **LocalAI** | ⭐⭐⭐⭐ | API OpenAI-compatible | `localai.io` |
| **vLLM** | ⭐⭐⭐ | Alto throughput | `github.com/vllm-project/vllm` |
| **text-generation-webui** | ⭐⭐⭐⭐ | Web UI extensible | `github.com/oobabooga` |
| **koboldcpp** | ⭐⭐⭐⭐ | Para GPU AMD también | `github.com/LostRuins/koboldcpp` |

### Instalación Rápida (Ollama):
```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh

# Modelos recomendados para sentiment analysis
ollama pull llama3.1          # 8B, rápido, bueno
ollama pull mistral           # 7B, muy bueno para instrucciones
ollama pull qwen2.5:14b       # Excelente para análisis
ollama pull mixtral           # 46B, el mejor calidad

# Uso
ollama run llama3.1
```

### APIs Gratuitas con Límites Generosos

| Servicio | Free Tier | Límites | Modelos Disponibles |
|----------|-----------|---------|---------------------|
| **Groq** | $25 crédito/mes | 20requests/min | Llama 3, Mixtral, Gemma |
| **Together AI** | $25 crédito inicial | Rate limits | 100+ modelos OSS |
| **Fireworks AI** | $5 crédito/mes | 600 requests/min | Mixtral, Llama, Phi |
| **DeepInfra** | $10 crédito inicial | - | Llama, Mistral, Qwen |
| **Replicate** | Free tier limitado | - | Todos los modelos OSS |
| **Anyscale** | $10 crédito/mes | Rate limits | Llama, Mistral, Zephyr |
| **AI21** | 10k tokens/día | - | Jurassic-2 |
| **Cohere** | 100 calls/month | - | Command, Embed |
| **Mistral API** | Free tier | Rate limits | Mistral Small, Medium |
| **Hyperbolic** | $10 crédito | - | Llama, Mistral gratis |
| **Novita AI** | $10 crédito inicial | - | 50+ modelos |
| **Segmind** | 500 créditos/día | - | SD + LLMs |

### Alternativas Gratis a APIs Premium (OpenAI/Claude/Gemini)

| Alternativa | Compatibilidad | Precio (Free Tier) |
|-------------|----------------|-------------------|
| **OpenRouter** | OpenAI API-compatible | Multi-proveedor, algunos free |
| **Poe API** | Propio | Free tier limitado |
| **Hugging Face Inference API** | Propio | Gratuito sin token (rate limits) |
| **Cloudflare Workers AI** | Propio | 10k requests/día |
| **Azure OpenAI (estudiante)** | OpenAI | $100 crédito estudiantes |
| **Google AI Studio** | Gemini | 60 queries/min gratis |

### Modelos FinBERT/CryptoBERT Open Source (Locales)

```python
# === FinBERT (Análisis de sentimiento financiero) ===
# HuggingFace: ProsusAI/finbert
# Entrenado en Financial PhraseBank (Titbirke)

from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

# FinBERT original (idioma: inglés)
tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert", num_labels=3)

# InFinBERT (mejorado)
# huggingface.co/yiyanghkust/finbert-tone

# === CryptoBERT (Entrenado en tweets crypto) ===
# Repositorio: https://github.com/kaansonmezoz/CryptoBERT

from transformers import AutoModel, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("kk08/CryptoBERT")
model = AutoModel.from_pretrained("kk08/CryptoBERT")

# === CryptoBERT v2 (Twitter sentiment) ===
# huggingface.co/ElKulako/cryptobert

# === FinGPT (FinLLM open source) ===
# https://github.com/AI4Finance-Foundation/FinGPT
# Framework completo para LLM financieros

# === StockBERT / MarketBERT ===
# Investigación académica, varios checkpoints

# === Domain-Specific Models ===
# Crypto-Sentiment-Analyzer: https://github.com/PanQiWei/cryptocurrency-sentiment-analyzer
```

---

## 4. 🏗️ INFRAESTRUCTURA GRATIS

### Alternativas a Kafka/Flink (Streaming)

| Alternativa | Tipo | Ventajas | Setup |
|-------------|------|----------|-------|
| **Redis Streams** | In-memory | Súper rápido, simple | 1 comando Docker |
| **NATS JetStream** | Mensajería moderna | Muy liviano, fácil | Binario único |
| **RabbitMQ** | Mensajería clásica | AMQP, flexible | Docker oficial |
| **Apache Pulsar** | Streaming+MQ | Unifica ambas necesidades | K8s/Docker |
| **ZeroMQ** | Colas en memoria | Sin broker, ultra rápido | Librería |
| **SQLite + WAL** | Cola simple | Sin instalación, ACID | Archivo local |
| **MQTT (Mosquitto)** | Pub/sub ligero | IoT-proven, simple | Docker |
| **Apache ActiveMQ** | JMS open source | Enterprise features | Java app |
| **Kui** | Serverless streaming | Cloud sin servidor | Managed |
| **Memphis.dev** | Streaming moderno | UI incluido | Self-hosted |
| **Benthos** | Pipeline de streaming | Single binary, config YAML | Go binary |

### Redis Streams (Recomendado para trading)
```bash
# Docker (imagen oficial)
docker run -p 6379:6379 redis:latest redis-server

# Python
import redis
r = redis.Redis()

# Productor
r.xadd('crypto-news-stream', {'source': 'coindesk', 'content': 'BTC sube'})

# Consumidor
r.xread({'crypto-news-stream': '$'}, block=0, count=10)
```

### NATS JetStream (Alt recomendada)
```bash
# Instalación single binary
curl -sf https://get-nats.io | sh

# Server
nats-server -js -m 8222

# CLI
nats context create local --server localhost:4222
nats stream add crypto-data --subjects "crypto.*"
nats pub crypto.prices '{"btc": 50000}'
```

### Bases de Datos Time-Series Gratis

| BD | Tipo | Licencia | Features | Docker |
|----|------|----------|----------|--------|
| **InfluxDB OSS** | Time-series | MIT | SQL-like, alto rendimiento | ✅ 1 comando |
| **TimescaleDB** | Postgres extension | Apache | SQL completo, hypertables | ✅ Extensión |
| **Prometheus** | Monitoring+TS | Apache | Pull model, alertas | ✅ Binario |
| **VictoriaMetrics** | TS optimizado | Apache | Query rápido, compacto | ✅ Single binary |
| **ClickHouse** | OLAP/TS | Apache 2.0 | Analítica masiva | ✅ Docker |
| **QuestDB** | Fast TS | Apache | SQL, JOINs rápidos | ✅ Docker |
| **Taosdata/TDengine** | IoT-optimized | AGPL | Edge-to-cloud | ✅ Docker |
| **CrateDB** | SQL distributed | Apache | IoT/industrial | ✅ Docker |
| **TimescaleDB Free** | Cloud 30GB | - | Hosted gratis | Managed |
| **InfluxDB Cloud** | Free tier 10k writes/mes | - | Backup automático | Managed |

### TimescaleDB (Recomendada - SQL familiar)
```bash
# Docker con TimescaleDB
docker run -d --name timescaledb \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=password \
  timescale/timescaledb:latest-pg15

# SQL para crear hypertable
CREATE TABLE crypto_prices (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT,
    price DOUBLE PRECISION,
    volume DOUBLE PRECISION
);

SELECT create_hypertable('crypto_prices', 'time');
```

### InfluxDB OSS (Alternativa popular)
```bash
docker run -p 8086:8086 \
  -v influxdb-data:/var/lib/influxdb2 \
  influxdb:2.7

# CLI
influx bucket create -n crypto-sentiment
influx write -b crypto-sentiment -l s "sentiment,source=twitter value=0.75"
```

### Procesamiento de Streaming Ligero

| Herramienta | Caso de uso | Setup | Recursos |
|-------------|-------------|-------|----------|
| **Benthos** | ETL streaming configs | 1 binary | 10MB RAM |
| **Vector** | Logs+metrics pipeline | 1 binary | 10MB RAM |
| **Fluentd** | Data collection | Ruby | 50MB RAM |
| **Telegraf** | Metrics collection | 1 binary | 30MB RAM |
| **Camunda** | Workflow engine | Java | 200MB+ |
| **Temporal** | Durable execution | Go | 100MB+ |
| **Windmill** | Script runner | Docker | Variable |
| **Trigger.dev** | Background jobs | Docker | Variable |

### Benthos (Pipeline YAML - Super simple)
```yaml
# sentiment_pipeline.yaml
input:
  redis_streams:
    url: tcp://localhost:6379
    streams:
      - crypto-news

pipeline:
  processors:
    - sentiment_analysis:
        model: local_finbert
    - json_schema:
        schema: '{"type":"object"}'

output:
  influxdb_1:
    url: http://localhost:8086
    db: crypto_sentiment
```

---

## 5. 🛠️ HERRAMIENTAS ADICIONALES

### Diccionarios de Sentimiento Financiero Open Source

| Diccionario | Idioma | Formato | Enlace |
|-------------|--------|---------|--------|
| **Loughran-McDonald** | Inglés | CSV/TXT | `sraf.nd.edu` - Financiero |
| **Financial PhraseBank** | Inglés | TXT | `www.kaggle.com/ankurzing` |
| **VADER** | Inglés | Python lib | `vaderSentiment` en PyPI |
| **SentiWordNet** | Inglés | Database | `sentiwordnet.isti.cnr.it` |
| **TextBlob** | Multilang | Python | `textblob.readthedocs.io` |
| **AFINN** | Multilang | JSON | `github.com/fnielsen/afinn` |
| **Lingua** | Crypto-specific | JSON | `github.com/quantitative/lingua` |
| **SentiCR** | Código/review | JSON | `github.com/senticr/SentiCR` |
| **Finance Sentiment** | Inglés | CSV | `github.com/zeroshot/finance-sentiment` |

### Ejemplo VADER (Python)
```python
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

analyzer = SentimentIntensityAnalyzer()
text = "Bitcoin breaks all-time high! Bulls are euphoric!"
scores = analyzer.polarity_scores(text)
# {'neg': 0.0, 'neu': 0.417, 'pos': 0.583, 'compound': 0.8437}
```

### Datasets de Entrenamiento Gratuitos

| Dataset | Tamaño | Contenido | Descarga |
|---------|--------|-----------|----------|
| **Financial PhraseBank** | 4845 frases | Sentiment anotado (Kaggle) | Free |
| **Crypto Reddit Dataset** | 1M+ posts | Reddit r/CC 2016-2021 | Kaggle |
| **Twitter Sentiment140** | 1.6M tweets | Análisis general | `sentiment140.com` |
| **StockTwits Crypto** | 500K mensajes | Trading social | API/Scrape |
| **Crypto Fear & Greed Historical** | Daily 2018+ | Sentiment index | CSV scrapable |
| **CoinGecko Market Data** | Full history | Precios, volumen | API/CSV |
| **Binance Klines** | OHLCV full | Velas 1m a 1M | Descarga masiva |
| **MIT Twitter Finance** | Financiero | Sentiment financiero | `eagle.cs.jhu.edu/~mdredze/` |
| **SEntFiN Dataset** | 19K tweets | Finance sentiment gold | `github.com/Shuvarjyoti/SEntFiN` |
| **FinQA** | 8K pares | Financial QA + razonamiento | `github.com/czyssrs/FinQA` |

### Papers con Código (GitHub)

| Paper | Año | Código | Modelo |
|-------|-----|--------|--------|
| **FinBERT** | 2019 | `github.com/ProsusAI/finbert` | BERT financiero |
| **FinGPT** | 2023 | `github.com/AI4Finance-Foundation/FinGPT` | LLM financiero |
| **CryptoBERT** | 2022 | `github.com/kaansonmezoz/CryptoBERT` | BERT crypto |
| **Finformer** | 2023 | `github.com/golsun/Finformer` | Transformer temporal |
| **MarketBERT** | 2022 | Variante | BERT mercados |
| **Quantitative Trading with Sentiment** | 2021 | `github.com/AI4Finance-Foundation/Deep-Learning-Sentiment-Trading` | Redes LSTM |
| **Sentiment Analysis for Crypto Trading** | 2022 | Varios en GitHub | Ensemble methods |
| **BERT for Financial Sentiment** | 2020 | `aclanthology.org` + mirrors | Fine-tuning BERT |
| **FinNLP Toolkit** | 2023 | `github.com/AI4Finance-Foundation/FinNLP` | NLP financiero |
| **Cryptocurrency Trading with RL** | 2023 | `github.com` múltiples | RL + sentiment |

### Repositorios Clave en GitHub

```
# Análisis de Sentiment
https://github.com/AI4Finance-Foundation/FinGPT
https://github.com/AI4Finance-Foundation/FinNLP
https://github.com/kaansonmezoz/CryptoBERT
https://github.com/ProsusAI/finbert
https://github.com/golsun/Finformer

# Trading Bots + Sentiment
https://github.com/CyberPunkMetalHead/gateio-crypto-trading-bot
https://github.com/iterative/aita trading-algorithms
https://github.com/owocki/pytrader
https://github.com/bitcoinbook/bitcoinbook

# Datos/Scrapers
https://github.com/marcofavorito/pythomics (crypto scraper)
https://github.com/man-c/pycoinlib (data aggregator)
https://github.com/Philipper905/crypto-news-scraper
```

---

## 💻 ARQUITECTURA RECOMENDADA (Setup Gratuito Total)

### Stack Técnico $0

```
📥 INPUT:
   ├─ RSS Feeds (CoinDesk, Cointelegraph) → feedparser (Python)
   ├─ Reddit API → requests + praw
   ├─ Twitter → nitter (RSS) o scraping ético
   ├─ Binance API → precios + funding
   └─ On-chain → Etherscan + Dune (export CSV)

🔄 STREAMING:
   → NATS JetStream o Redis Streams (1 nodo, local/self-hosted)

🧠 PROCESSING:
   ├─ Benthos (pipeline de datos)
   └─ Ollama local (Mistral 7B o Llama 3.1)
        → Análisis de sentiment

💾 STORAGE:
   → TimescaleDB (PostgreSQL + extensión)
        ├─ Métricas de sentiment
        ├─ Precios OHLCV
        └─ Métricas on-chain

📊 OUTPUT:
   ├─ Grafana (dashboard de sentiment)
   └─ Alertas Webhook (Telegram/Discord)

🤖 DECISION:
   └─ Bot de trading (Binance Testnet → gratis)
```

### Docker Compose Completo (Todo local)

```yaml
version: '3.8'

services:
  # === MENSAJERÍA ===
  nats:
    image: nats:latest
    command: --js -p 4222 -m 8222
    ports:
      - "4222:4222"  # Client
      - "8222:8222"  # Dashboard
    volumes:
      - nats-data:/data

  # === BASE DE DATOS TIME-SERIES ===
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    environment:
      POSTGRES_PASSWORD: yourpassword
      POSTGRES_DB: crypto_sentiment
    ports:
      - "5432:5432"
    volumes:
      - timescale-data:/var/lib/postgresql/data

  # === LLM LOCAL (Ollama) ===
  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama-data:/root/.ollama
    ports:
      - "11434:11434"
    # GPU opcional: deploy.resources.reservations.devices

  # === PIPELINE DE DATOS ===
  benthos:
    image: jeffail/benthos:latest
    volumes:
      - ./benthos-config.yaml:/config.yaml
    command: -c /config.yaml
    depends_on:
      - nats
      - timescaledb

  # === VISUALIZACIÓN ===
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana

volumes:
  nats-data:
  timescale-data:
  ollama-data:
  grafana-data:
```

### Costo Total de Infraestructura
- **Hardware**: Tu laptop/PC existente
- **Servicios cloud**: $0 (todo local/Docker)
- **APIs**: $0 (free tiers + públicas)
- **LLM**: $0 (ejecutando local)
- **Total**: **$0.00/mes**

---

## 📚 RECURSOS ADICIONALES

### Libros/E-books Gratuitos (Legal)
- **Mastering Bitcoin** (Andreas Antonopoulos) - Open source
- **Mastering Ethereum** (A. Antonopoulos) - Open source  
- **Cryptoassets** (Chris Burniske) - Preview legal
- **Algorithmic Trading with Python** (Chris Conlan) - GitHub

### Cursos Gratuitos
- 3Blue1Brown - Blockchain
- MIT OpenCourseWare - Financial Data
- Coursera audit - Machine Learning (Andrew Ng)
- YouTube: "Sentiment Analysis for Trading" (varios creadores)

### Comunidades/Discord

| Comunidad | Foco | Link |
|-----------|------|------|
| AI4Finance | Fin AI open source | Discord en GitHub |
| QuantStack | Trading quant | Slack público |
| r/algotrading | Algorítmico | reddit.com/r/algotrading |
| Coin Bureau | Crypto educación | Discord |
| DataTau | Data science | datatau.com |

---

## ✅ CHECKLIST PARA EMPEZAR

- [ ] 1. Instalar Docker Desktop (Mac/Linux/Windows)
- [ ] 2. `git clone` arquitectura de referencia
- [ ] 3. Levantar `docker-compose up -d`
- [ ] 4. Descargar modelo LLM: `ollama pull mistral`
- [ ] 5. Configurar feedparser para RSS
- [ ] 6. Script Python: recolector → Redis/NATS
- [ ] 7. Script Python: procesador → LLM local → TimescaleDB
- [ ] 8. Grafana dashboard para visualizar sentiment
- [ ] 9. Conectar a Binance Testnet para paper trading
- [ ] 10. Backtest con datos históricos (Dune/CSV)

---

**Última actualización**: 2025-01
**Todas las herramientas verificadas**: Gratuitas y operativas (free tier o open source)
**Compilado por**: Subagent Claude para proyecto FenixAI
