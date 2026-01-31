# Sistemas Avanzados de Sentiment Analysis para Trading de Criptomonedas
## Research Compilation 2024-2025

---

## 1. ARQUITECTURAS MODERNAS DE AGREGACIÓN DE NOTICIAS FINANCIERAS

### Arquitecturas en Producción

#### Bloomberg Terminal Architecture
- **Event-Driven Feed Handler**: Procesa 10M+ eventos/segundo
- **B-PIPE (Bloomberg Platform API)**: Streaming en tiempo real con <10ms latencia
- **BNEF (BloombergNEF)**: Agregación especializada en energía y cripto
- **Key Components**:
  - News Analytics Engine (NAE) para clasificación automática
  - Entity Recognition para identificar tickers/crypto assets mencionados
  - Relevancy Scoring basado en portfolio del usuario

#### Reuters Refinitiv
- **News Feed Direct**: FIX protocol adaptado para news
- **Reuters News Archive**: 50+ años de datos históricos etiquetados
- **Intelligent Tagging**: ML para extracción de entidades financieras

#### Open Source Alternatives

##### Apache Kafka + NLP Pipeline
```
[News Sources] → [Kafka Topics] → [Preprocessing] → [NLP Engine] → [Event Store] → [Trading Signals]
```
**Stack recomendado**:
- Apache Kafka: Stream processing (>100k mensajes/seg)
- Apache Flink: Stateful processing de ventanas de tiempo
- spaCy/NLTK: Entity recognition (crypto tickers, exchanges)
- Elasticsearch: Indexación y búsqueda semántica

##### NewsCatcher API / NewsAPI + Custom Pipeline
- Costo efectivo para startups
- Web scraping ético con RSS + robots.txt
- Normalización de fuentes heterogéneas (tiempos, formatos, idiomas)

### Arquitectura Híbrida para Crypto

```
┌─────────────────────────────────────────────────────────┐
│                    INGESTION LAYER                      │
├─────────────────────────────────────────────────────────┤
│  Traditional News    │    Crypto-Native Sources         │
│  • Bloomberg API     │    • CoinDesk API                │
│  • Reuters           │    • CoinTelegraph               │
│  • WSJ               │    • The Block                   │
│  • Financial Times   │    • Crypto Twitter (v2 API)     │
└──────────────────┬──────────────────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │   NORMALIZATION     │  ← Clean HTML, unify timestamps,
        │   & DEDUPLICATION   │    detect near-duplicate stories
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │  PRIORITY QUEUE     │  ← Latency-critical path para
        │  (Kafka/RabbitMQ)   │    breaking news
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │   NLP PROCESSING    │  ← Entity extraction, sentiment,
        │   (spaCy/Transformers)│   impact classification
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │  SIGNAL GENERATION  │  ← Feature vectors para modelos
        └─────────────────────┘         de trading
```

---

## 2. DETECCIÓN Y PRIORIZACIÓN DE BREAKING NEWS DE ALTO IMPACTO

### Sistemas de Detección en Tiempo Real

#### 1. Early Detection via Twitter/X
- **Filter Stream API**: Keywords específicos ("hacked", "exploit", "SEC", "Binance")
- **Velocity Indicators**:
  ```python
  velocity_score = (tweets_last_minute - baseline) / std_dev
  if velocity_score > 3:  # 3 sigma event
      flag_as_breaking()
  ```
- **Account Authority Scoring**: Peso por followers, verificación, histórico de precisión

#### 2. News Wire Detection
- **Comparación cruzada**: Mismo evento reportado por múltiples fuentes
- **Time-decay scoring**: `priority = impact_score * exp(-λ * minutes_since_publish)`

#### 3. On-Chain Anomaly Detection como Proxy
- **Whale Alert pattern**: Movimientos inusuales de grandes wallets preceden noticias
- **Exchange Inflow/Outflow spikes**: Glassnode/Santiment datos en tiempo real

### Algoritmos de Priorización

#### Multi-Factor Priority Score
```python
def calculate_priority(news_item):
    factors = {
        'source_credibility': get_source_weight(news_item.source),  # 0-1
        'velocity': calculate_velocity(news_item),                   # tweets/min
        'entity_relevance': match_portfolio(news_item.mentions),     # 0-1
        'semantic_urgency': classify_urgency(news_item.text),        # ML model
        'time_decay': exp(-0.1 * minutes_since_publish),
        'historical_impact': get_historical_volatility(news_item.source, news_item.category)
    }
    
    return weighted_sum(factors, weights=[0.2, 0.2, 0.25, 0.2, 0.1, 0.05])
```

#### Urgency Classification (NER + Keywords)
- **LABEL_URGENT**: "hack", "exploit", "collapse", "ban", "securities fraud"
- **LABEL_HIGH**: "partnership", "listing", "upgrade", "regulation"
- **LABEL_MEDIUM**: "analysis", "prediction", "interview"
- **LABEL_LOW**: "opinion", "general crypto news"

### Implementación de Alertas
- **WebSocket notifications** para trades de alta frecuencia (<500ms)
- **Pub/Sub (Redis)** para distribución multi-strategy
- **Deduplication circuit**: Evita múltiples alertas por mismo evento

---

## 3. SISTEMAS DE CLASIFICACIÓN DE IMPACTO DE NOTICIAS

### Taxonomía de Impacto

| Categoría | Volatilidad Esperada | Latencia Tolerable | Estrategia |
|-----------|---------------------|-------------------|------------|
| **ALTO** | >10% en 1h | <5s | HFT/Directional |
| **MEDIO** | 3-10% en 1h | <60s | Momentum/Swing |
| **BAJO** | <3% en 1h | <5min | Sentiment bias |

### Modelos de Clasificación

#### 1. FinBERT y Derivados Crypto
- **CryptoBERT**: Fine-tuned en corpus crypto (r/CryptoCurrency, CT)
- **FinGPT**: LLM específico para finanzas con contexto crypto
- **Output**: Probabilidad de impacto alto/medio/bajo

#### 2. Feature Engineering para Impact Prediction
```python
features = {
    # Textuales
    'sentiment_score': model.predict(text),  # -1 a 1
    'urgency_keywords': count_keywords(text, URGENT_LIST),
    'entity_count': len(extract_tickers(text)),
    
    # Contextuales
    'market_regime': get_current_regime(),  # bull/bear/range
    'time_of_day': hour_of_day,  # crypto 24/7 pero hay sesiones
    'historical_source_accuracy': accuracy_by_source[source],
    
    # On-chain leading indicators
    'exchange_inflows_1h': glassnode_data['inflows'],
    'funding_rate': exchange_data['funding']
}
```

#### 3. Price Impact Prediction Models
- **LSTM con Attention**: Historial de noticias + OHLCV → predicted volatility
- **XGBoost/LightGBM**: Features estructurados (más interpretable, usado en producción)
- **Transformer-based**: BERT embeddings → MLP → impact score

### Validación Backtest
```python
# Backtest de clasificador
def validate_impact_classifier():
    for news in historical_news:
        predicted = classifier.predict(news)
        actual = measure_price_move(news.timestamp, window='1h')
        
        log_confusion_matrix(predicted, actual)
        # Target: F1 > 0.75 para clase HIGH
```

---

## 4. FUENTES ALTERNATIVAS DE DATOS PARA SENTIMIENTO CRYPTO

### On-Chain Analytics (Más Predictivo que Social)

#### Glassnode Metrics Clave
| Métrica | Señal | Frecuencia |
|---------|-------|------------|
| **Exchange Netflow** | Inflow = Selling pressure | 1h |
| **Active Addresses** | Incremento = Interés creciente | 24h |
| **NUPL** (Net Unrealized Profit/Loss) | Zonas de euforia/capitalización | 1d |
| **MVRV Ratio** | Sobre/sobvaloración | 1d |
| **Long-Term Holder SOPR** | Profit-taking de hodlers | 1d |
| **Funding Rates** | Extremos = Reversión potencial | 1h |

#### Santiment
- **Social Dominance**: Menciones de BTC vs otros assets
- **Weighted Social Sentiment**: Sentimiento ponderado por volumen
- **Development Activity**: GitHub commits como proxy de salud
- **Whale Transactions**: Movimientos +$100k

#### CryptoQuant
- **Exchange Reserves**: Agregado de múltiples exchanges
- **Miners' Position Index**: Comportamiento de mineros
- **Estimated Leverage Ratio**: Riesgo de liquidaciones

### Datos Alternativos Niche

#### 1. Google Trends
- **Keywords**: "buy bitcoin", "crypto crash", "altseason"
- **Regional analysis**: Retail interest por país

#### 2. GitHub Activity (Developer Sentiment)
- **Commit frequency** en repos principales
- **Issues/PRs** (engagement técnico)

#### 3. Futures & Options Data
- **Open Interest**: Dinero en juego
- **Options skew**: Fear/greed institucional
- **CME Gap**: Referencia para traders

#### 4. NFT/Gaming Metrics (Meta-sector)
- **OpenSea volume**: Sentimiento de mercado NFT
- **GameFi user retention**: Adopción real vs especulación

### Integración Multi-Fuente

```python
class CryptoSentimentAggregator:
    def compute_composite(self):
        return {
            'on_chain_score': self.glassnode.weighted_score(),  # 40%
            'social_score': self.santiment.sentiment(),          # 25%
            'derivatives_score': self.funding_rates.score(),     # 20%
            'news_score': self.news.sentiment(),                 # 10%
            'developer_score': self.github.activity(),           # 5%
        }
```

---

## 5. MEJORES PRÁCTICAS PARA PONDERAR FUENTES DE SENTIMIENTO

### Framework de Ponderación Dinámica

#### 1. Performance-Based Weighting (Online Learning)
```python
# Actualizar pesos basado en señales pasadas
def update_weights(predictions, returns, actual_impacts):
    for source in sources:
        correlation = np.corrcoef(predictions[source], actual_impacts)[0,1]
        weights[source] = max(0, correlation)  # Solo positivos
    
    # Normalizar
    weights /= sum(weights.values())
```

#### 2. Regime-Dependent Weights
| Régimen | On-Chain | Social | News | Derivatives |
|---------|----------|--------|------|-------------|
| **Bull** | 30% | 35% | 15% | 20% |
| **Bear** | 45% | 20% | 20% | 15% |
| **Sideways** | 40% | 10% | 30% | 20% |
| **Evento** | 20% | 25% | 45% | 10% |

#### 3. Signal Decay
```python
# Noticias viejas pierden relevancia
def time_decay_weight(timestamp, half_life=3600):  # 1h
    age = current_time - timestamp
    return 0.5 ** (age / half_life)
```

### Gestión de Confianza (Confidence Scoring)

```python
class ConfidenceManager:
    def calculate_confidence(self, source, signal):
        factors = {
            'historical_accuracy': self.accuracy_history[source],
            'sample_size': self.samples_count[source] / 1000,  # Normalizado
            'agreement_with_others': self.consensus_score(source),
            'recent_performance': self.last_30d_accuracy[source],
        }
        return np.mean(list(factors.values()))
```

### Anti-Pattern: Evitar Overfitting

**NO hacer**:
- ❌ Cambiar pesos demasiado rápido (<1 día de lookback)
- ❌ Incluir fuentes con lag (tutoriales YouTube, newsletters mensuales)
- ❌ Ignorar survivorship bias (fuentes que dejaron de existir)

**SÍ hacer**:
- ✅ Regimen detection antes de aplicar señales
- ✅ Out-of-sample testing continuo
- ✅ Peso mínimo para fuentes nuevas (prueba de fuego)

---

## 6. PAPERS Y RECURSOS CLAVE

### Papers Académicos Recientes (2023-2025)

#### 2024-2025
1. **"FinGPT: Open-Source Financial Large Language Models"** (2024)
   - LLM especializado para análisis de noticias financieras
   - Buen rendimiento con few-shot learning
   - HuggingFace: `FinGPT/fingpt-sentiment_llama2`

2. **"CryptoBERT: Pre-trained Language Model for Cryptocurrency Text Mining"** (2024)
   - Fine-tuning en corpus crypto específico
   - Mejora +15% sobre BERT-base en clasificación de sentimiento

3. **"High-Frequency News Sentiment and Cryptocurrency Price Predictability"** - Journal of Financial Data Science (2024)
   - Análisis de causalidad Granger entre noticias y retornos
   - Ventanas óptimas de 5-15 minutos para máxima predictibilidad

4. **"On-Chain Metrics as Leading Indicators of Cryptocurrency Returns"** (2024)
   - Glassnode metrics MVRV, NUPL como predictores
   - Sharpe ratio 2.5x mejor que buy-and-hold

#### Event-Driven Trading
5. **"Machine Learning for Event-Driven Trading in Cryptocurrency Markets"** (2023)
   - Framework para detección y trading de eventos exógenos
   - Mejor rendimiento con Random Forest sobre SVM

6. **"News-Driven Algorithmic Trading Strategies: A Survey"** - ACM Computing Surveys (2024)
   - Review exhaustivo de arquitecturas 2015-2024
   - Taxonomía de estrategias: directional, volatility, arbitrage

7. **"Reinforcement Learning for News-Driven Trading"** - ICML 2024 Workshop
   - PPO y SAC adaptados para trading de noticias
   - State: market microstructure + news sentiment

### Papers Clásicos Fundamentales

8. **"Textual Analysis in Finance"** - Loughran & McDonald (2016) - Base para todo
9. **"Analyzing Sentiment in Financial News"** - Kaggle FNS2020 shared task
10. **"The Impact of News on Cryptocurrency Returns"** - Baur & Dimpfl (2021)

### Recursos Técnicos y Librerías

#### Libraries Python
```python
# NLP/Transformers
from transformers import pipeline
sentiment = pipeline("sentiment-analysis", model="ProsusAI/finbert")

# On-chain data
import glassnode  # Unofficial wrapper
# o API directa: requests.get('https://api.glassnode.com/v1/...')

# Crypto-specific sentiment
from pysentiment2 import CryptoSentimentAnalyzer
```

#### Plataformas y Datos
- **The Graph**: Datos on-chain indexed
- **Dune Analytics**: Queries SQL sobre blockchain
- **Nansen**: Análisis de wallets inteligentes
- **Arkham**: Intel de wallet labels
- **Messari**: Event calendar API

#### Blogs y Recursos Quants
- **QuantStack**
- **Marcos López de Prado**: Papers en SSRN
- **Ernest Chan's Blog**: Quantitative Trading
- **CryptoQuant Academy**: Tutoriales on-chain

---

## IMPLEMENTACIÓN PRÁCTICA: STACK RECOMENDADO

### Para Startup/Proyecto Personal ($0-500/mes)
```
Ingestión: NewsAPI + RSS + Twitter API v2 (basic)
Storage: PostgreSQL + Redis
NLP: HuggingFace Inference API (FinBERT)
On-chain: Glassnode free tier
Dashboard: Grafana o Streamlit
```

### Para Trading Profesional ($2000+/mes)
```
Ingestión: Ravenpack / Bloomberg API / Refinitiv
Stream: Apache Kafka + Flink
NLP: Self-hosted LLM (Llama-2 + fine-tune)
On-chain: Glassnode/Santiment enterprise + Dune
Infra: AWS/GCP con GPU instances para inference
```

### Benchmarks de Latencia
| Componente | Latencia Target |
|------------|----------------|
| News ingestion | <2s desde publicación |
| NLP processing | <500ms |
| Signal generation | <100ms |
| Order execution | <50ms (REST) / <10ms (WebSocket) |

---

## CONCLUSIONES CLAVE

1. **On-chain > Social**: Para crypto, on-chain metrics tienen más alpha que social media (menos ruido, más verificable)

2. **Multi-timeframe**: Sentimiento funciona mejor conventanas de 1-4h que con high-frequency (<5min)

3. **Regime matters**: Lo que funciona en bull market no funciona en bear - detectar régimen es crucial

4. **Execution edge**: En noticias, la ventaja está en la velocidad de ingestión/proceso, no en el modelo más complejo

5. **Risk management**: News-based trading requiere stops más anchos (volatilidad impredecible)

---

*Documento compilado para proyecto Fenix AI Trading System*
*Fecha: Enero 2025*
