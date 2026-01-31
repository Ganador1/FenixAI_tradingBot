#!/usr/bin/env python3
"""
Example Integration: Free Sentiment Analysis System for Crypto Trading
All components use FREE and OPEN SOURCE resources only.

Requirements:
  pip install requests feedparser nats-py redis ollama psycopg2-binary pandas
"""

import asyncio
import json
import requests
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any

# RSS Parser
import feedparser

# Message Queue - choose one:
import redis.asyncio as redis  # pip install redis
import nats  # pip install nats-py

# LLM - Local via Ollama
import ollama

# Database
import psycopg2

# ============================================
# 1. FREE NEWS SOURCES (RSS + APIs)
# ============================================

CRYPTO_RSS_FEEDS = [
    "https://www.coindesk.com/feed/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed/",
    "https://www.theblock.co/rss.xml",
    "https://crypto.news/feed/",
]

REDDIT_CRYPTO_JSON = "https://www.reddit.com/r/CryptoCurrency/.json"
FEAR_GREED_API = "https://api.alternative.me/fng/?limit=1"

class FreeNewsAggregator:
    """Collects crypto news from free sources."""
    
    def get_rss_news(self) -> List[Dict]:
        """Fetch news from RSS feeds."""
        news_items = []
        for feed_url in CRYPTO_RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:5]:  # Last 5 per source
                    news_items.append({
                        'source': feed.feed.title,
                        'title': entry.title,
                        'summary': getattr(entry, 'summary', '')[:200],
                        'published': getattr(entry, 'published', datetime.now().isoformat()),
                        'link': entry.link
                    })
            except Exception as e:
                print(f"Error parsing {feed_url}: {e}")
        return news_items
    
    def get_reddit_sentiment(self) -> Dict:
        """Fetch top Reddit posts as sentiment proxy."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (FenixBot/1.0)'}
            response = requests.get(REDDIT_CRYPTO_JSON, headers=headers, timeout=10)
            data = response.json()
            posts = data['data']['children']
            
            return {
                'top_post_ups': posts[0]['data']['ups'],
                'top_post_title': posts[0]['data']['title'],
                'avg_ups': sum(p['data']['ups'] for p in posts) / len(posts),
                'positive_ratio': sum(1 for p in posts if p['data']['ups'] > 100) / len(posts)
            }
        except Exception as e:
            print(f"Reddit fetch error: {e}")
            return {}
    
    def get_fear_greed(self) -> Dict:
        """Fetch Crypto Fear & Greed Index (free API)."""
        try:
            response = requests.get(FEAR_GREED_API, timeout=10)
            data = response.json()
            return data['data'][0] if data.get('data') else {}
        except Exception as e:
            print(f"Fear & Greed fetch error: {e}")
            return {}

# ============================================
# 2. FREE ON-CHAIN DATA (Binance + Etherscan)
# ============================================

class FreeOnChainData:
    """Fetches on-chain metrics from free APIs."""
    
    BINANCE_SPOT = "https://api.binance.com/api/v3"
    BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1"
    
    def get_funding_rate(self, symbol: str = "BTCUSDT") -> Dict:
        """Get current funding rate (perpetual futures)."""
        try:
            url = f"{self.BINANCE_FUTURES}/fundingRate?symbol={symbol}&limit=1"
            response = requests.get(url, timeout=10)
            data = response.json()
            if data:
                return {
                    'symbol': symbol,
                    'funding_rate': float(data[0]['fundingRate']),
                    'funding_time': data[0]['fundingTime'],
                    'timestamp': datetime.now().isoformat()
                }
        except Exception as e:
            print(f"Funding rate error: {e}")
        return {}
    
    def get_open_interest(self, symbol: str = "BTCUSDT") -> Dict:
        """Get open interest (futures)."""
        try:
            url = f"{self.BINANCE_FUTURES}/openInterest?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()
            return {
                'symbol': symbol,
                'open_interest': float(data['openInterest']),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"Open interest error: {e}")
        return {}
    
    def get_long_short_ratio(self, symbol: str = "BTCUSDT") -> Dict:
        """Get long/short ratio (account vs positions)."""
        try:
            # Global account ratio
            url = f"{self.BINANCE_FUTURES}/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=1"
            response = requests.get(url, timeout=10)
            data = response.json()
            if data:
                return {
                    'symbol': symbol,
                    'long_account_ratio': float(data[0]['longAccount']),
                    'short_account_ratio': float(data[0]['shortAccount']),
                    'timestamp': datetime.now().isoformat()
                }
        except Exception as e:
            print(f"Long/Short ratio error: {e}")
        return {}
    
    def get_24h_ticker(self, symbol: str = "BTCUSDT") -> Dict:
        """24 hour price change statistics."""
        try:
            url = f"{self.BINANCE_SPOT}/ticker/24hr?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()
            return {
                'symbol': symbol,
                'price_change_percent': float(data['priceChangePercent']),
                'volume': float(data['volume']),
                'quote_volume': float(data['quoteVolume']),
                'price': float(data['lastPrice']),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"24h ticker error: {e}")
        return {}

# ============================================
# 3. LOCAL LLM SENTIMENT ANALYSIS (Ollama)
# ============================================

class LocalSentimentAnalyzer:
    """Uses locally-running LLM via Ollama for sentiment."""
    
    def __init__(self, model: str = "llama3.1"):
        self.model = model
        
    def analyze_text(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of crypto news text."""
        prompt = f"""Analyze the sentiment of this crypto news. 
Return ONLY a JSON with: sentiment (bullish/bearish/neutral), confidence (0-1), and short_reason.

Text: {text}

JSON Response:"""
        
        try:
            response = ollama.chat(
                model=self.model,
                messages=[{'role': 'user', 'content': prompt}],
                options={'temperature': 0.3}
            )
            
            # Parse the response (basic cleanup)
            content = response['message']['content']
            
            # Extract JSON if wrapped in code blocks
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
                
            result = json.loads(content.strip())
            result['analyzed_at'] = datetime.now().isoformat()
            return result
            
        except Exception as e:
            return {
                'sentiment': 'neutral',
                'confidence': 0.0,
                'reason': f'Analysis failed: {str(e)}',
                'analyzed_at': datetime.now().isoformat()
            }

# ============================================
# 4. TIME-SERIES DATABASE (TimescaleDB Free)
# ============================================

class LocalTimescaleDB:
    """Store sentiment and market data in local TimescaleDB."""
    
    def __init__(self, host='localhost', port=5432, 
                 database='crypto_sentiment', user='postgres', password='yourpassword'):
        self.conn_str = f"host={host} port={port} dbname={database} user={user} password={password}"
        self.init_tables()
    
    def init_tables(self):
        """Create hypertables if they don't exist."""
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                # Sentiment data table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sentiment_data (
                        time TIMESTAMPTZ NOT NULL,
                        source TEXT,
                        symbol TEXT,
                        sentiment TEXT,
                        confidence FLOAT,
                        raw_text TEXT,
                        metadata JSONB
                    );
                """)
                cur.execute(
                    "SELECT create_hypertable('sentiment_data', 'time', if_not_exists => TRUE);"
                )
                
                # Market data table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS market_data (
                        time TIMESTAMPTZ NOT NULL,
                        symbol TEXT,
                        price FLOAT,
                        price_change_24h FLOAT,
                        volume FLOAT,
                        funding_rate FLOAT,
                        open_interest FLOAT,
                        long_short_ratio FLOAT,
                        metadata JSONB
                    );
                """)
                cur.execute(
                    "SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE);"
                )
                
                conn.commit()
    
    def insert_sentiment(self, data: Dict):
        """Insert sentiment analysis result."""
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sentiment_data (time, source, symbol, sentiment, confidence, raw_text, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    data.get('timestamp', datetime.now()),
                    data.get('source'),
                    data.get('symbol'),
                    data.get('sentiment'),
                    data.get('confidence'),
                    data.get('text', '')[:500],
                    json.dumps(data.get('metadata', {}))
                ))
                conn.commit()
    
    def insert_market_data(self, data: Dict):
        """Insert market/on-chain data."""
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_data (time, symbol, price, price_change_24h, volume, 
                                          funding_rate, open_interest, long_short_ratio, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    data.get('timestamp', datetime.now()),
                    data.get('symbol'),
                    data.get('price'),
                    data.get('price_change_percent'),
                    data.get('volume'),
                    data.get('funding_rate'),
                    data.get('open_interest'),
                    data.get('long_account_ratio'),
                    json.dumps({k: v for k, v in data.items() if k not in [
                        'timestamp', 'symbol', 'price', 'price_change_percent', 'volume',
                        'funding_rate', 'open_interest', 'long_account_ratio'
                    ]})
                ))
                conn.commit()

# ============================================
# 5. STREAMING WITH REDIS (Alternative to Kafka)
# ============================================

class RedisStreamHandler:
    """Lightweight streaming with Redis Streams."""
    
    def __init__(self, host='localhost', port=6379):
        self.redis = redis.Redis(host=host, port=port, decode_responses=True)
        self.streams = ['crypto-news', 'sentiment-results', 'market-data']
        
    async def publish_news(self, news_item: Dict):
        """Publish news to Redis Stream."""
        await self.redis.xadd('crypto-news', {'data': json.dumps(news_item)})
        
    async def consume_and_process(self, callback):
        """Consume from stream and process."""
        last_id = '0'
        while True:
            messages = await self.redis.xread(
                {'crypto-news': last_id}, 
                block=1000, 
                count=10
            )
            
            if messages:
                for stream_name, msgs in messages:
                    for msg_id, msg_data in msgs:
                        data = json.loads(msg_data['data'])
                        await callback(data)
                        last_id = msg_id
            
            await asyncio.sleep(0.1)  # 100ms poll interval

# ============================================
# MAIN INTEGRATION EXAMPLE
# ============================================

async def main():
    """Example workflow integrating all free components."""
    
    print("🚀 Starting FREE Crypto Sentiment Analysis System\n")
    
    # Initialize components (all free/open source)
    news_agg = FreeNewsAggregator()
    market_data = FreeOnChainData()
    sentiment_analyzer = LocalSentimentAnalyzer(model="mistral")  # or "llama3.1"
    # db = LocalTimescaleDB()  # Uncomment when DB is ready
    # stream = RedisStreamHandler()  # Uncomment when Redis is ready
    
    # 1. Collect Free Data
    print("📰 Collecting news from free RSS feeds...")
    rss_news = news_agg.get_rss_news()
    print(f"   Found {len(rss_news)} articles")
    
    print("📊 Fetching free market data from Binance...")
    btc_ticker = market_data.get_24h_ticker("BTCUSDT")
    btc_funding = market_data.get_funding_rate("BTCUSDT")
    btc_oi = market_data.get_open_interest("BTCUSDT")
    btc_ls = market_data.get_long_short_ratio("BTCUSDT")
    
    print(f"   BTC Price: ${btc_ticker.get('price', 'N/A')}")
    print(f"   Funding Rate: {btc_funding.get('funding_rate', 'N/A')}")
    print(f"   Open Interest: {btc_oi.get('open_interest', 'N/A')}")
    print(f"   Long/Short Ratio: {btc_ls.get('long_account_ratio', 'N/A')}")
    
    print("😱 Fetching Fear & Greed Index...")
    fear_greed = news_agg.get_fear_greed()
    print(f"   Index: {fear_greed.get('value', 'N/A')} ({fear_greed.get('value_classification', 'N/A')})")
    
    # 2. Analyze with Local LLM
    print("\n🤖 Analyzing sentiment with local LLM (Ollama)...")
    if rss_news:
        sample_news = rss_news[0]['title']
        sentiment_result = sentiment_analyzer.analyze_text(sample_news)
        print(f"   Text: {sample_news[:80]}...")
        print(f"   Sentiment: {sentiment_result['sentiment']} (confidence: {sentiment_result['confidence']:.2f})")
        print(f"   Reason: {sentiment_result.get('reason', 'N/A')[:100]}...")
    
    # 3. Store in TimescaleDB
    # print("\n💾 Storing in TimescaleDB...")
    # db.insert_sentiment({
    #     'timestamp': datetime.now(),
    #     'source': rss_news[0]['source'] if rss_news else 'unknown',
    #     'symbol': 'BTC',
    #     'sentiment': sentiment_result['sentiment'],
    #     'confidence': sentiment_result['confidence'],
    #     'text': sample_news,
    #     'metadata': {'model': 'mistral', 'reason': sentiment_result.get('reason')}
    # })
    
    print("\n✅ Demo complete! All using FREE resources.")
    print("\nNext steps:")
    print("  1. Start Redis: docker run -p 6379:6379 redis:latest")
    print("  2. Start TimescaleDB: docker run -p 5432:5432 timescale/timescaledb:latest-pg15")
    print("  3. Start Ollama: ollama serve (or docker)")
    print("  4. Download model: ollama pull mistral")
    print("  5. Uncomment DB and Stream code to enable full pipeline")

if __name__ == "__main__":
    asyncio.run(main())
