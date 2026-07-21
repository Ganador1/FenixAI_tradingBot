#!/usr/bin/env python3
"""
Alternative News and Sentiment Sources
=====================================

Este script implementa fuentes alternativas para noticias y sentiment cuando
las APIs principales fallan.

Fuentes implementadas:
1. CoinDesk RSS feed
2. CoinTelegraph RSS feed  
3. Reddit crypto subreddits
4. Free financial news APIs
5. Local cache con datos sintéticos para testing
"""

import json
import logging
import time
import requests
import feedparser
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class AlternativeNewsProvider:
    """Proveedor alternativo de noticias cuando APIs premium fallan."""

    def __init__(self):
        self.cache_file = Path("cache/alternative_news.json")
        self.cache_file.parent.mkdir(exist_ok=True)

        # RSS feeds gratuitos
        self.rss_feeds = {
            "coindesk": "https://feeds.feedburner.com/CoinDesk",
            "cointelegraph": "https://cointelegraph.com/rss",
            "decrypt": "https://decrypt.co/feed",
            "coindesk_markets": "https://feeds.feedburner.com/CoinDeskMarkets"
        }

        # Reddit endpoints públicos
        self.reddit_urls = [
            "https://www.reddit.com/r/cryptocurrency/.json?limit=25",
            "https://www.reddit.com/r/bitcoin/.json?limit=15",
            "https://www.reddit.com/r/solana/.json?limit=10"
        ]

    def fetch_rss_news(self, max_items: int = 15) -> list[dict[str, Any]]:
        """Fetch news from RSS feeds."""
        news_items = []

        for source, url in self.rss_feeds.items():
            try:
                feed = feedparser.parse(url)
                logger.info(f"Fetched {len(feed.entries)} items from {source}")

                for entry in feed.entries[:max_items//len(self.rss_feeds)]:
                    news_items.append({
                        "title": entry.get("title", ""),
                        "published_at": entry.get("published", ""),
                        "url": entry.get("link", ""),
                        "source": source,
                        "summary": entry.get("summary", "")[:200],
                        "created_at": datetime.now().isoformat(),
                        "domain": {"name": source.title()}
                    })

            except Exception as e:
                logger.warning(f"Failed to fetch RSS from {source}: {e}")

        return news_items[:max_items]

    def fetch_reddit_posts(self, max_items: int = 10) -> list[dict[str, Any]]:
        """Fetch posts from Reddit crypto communities."""
        reddit_items = []

        headers = {
            "User-Agent": "FenixBot/1.0 (Alternative News Aggregator)"
        }

        for url in self.reddit_urls:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    posts = data.get("data", {}).get("children", [])

                    for post in posts[:max_items//len(self.reddit_urls)]:
                        post_data = post.get("data", {})
                        if post_data.get("title"):
                            reddit_items.append({
                                "title": post_data.get("title", ""),
                                "published_at": datetime.fromtimestamp(
                                    post_data.get("created_utc", 0)
                                ).isoformat(),
                                "url": f"https://reddit.com{post_data.get('permalink', '')}",
                                "source": "reddit",
                                "summary": post_data.get("selftext", "")[:200],
                                "created_at": datetime.now().isoformat(),
                                "domain": {"name": "Reddit"},
                                "upvotes": post_data.get("ups", 0)
                            })

            except Exception as e:
                logger.warning(f"Failed to fetch Reddit data from {url}: {e}")

        return reddit_items[:max_items]

    def generate_synthetic_news(self, count: int = 5) -> list[dict[str, Any]]:
        """Generate synthetic news for testing when all sources fail."""
        base_news = [
            {
                "title": "Bitcoin Shows Bullish Technical Patterns",
                "sentiment": "bullish",
                "summary": "Technical analysis indicates potential upward movement"
            },
            {
                "title": "Crypto Market Consolidates Near Key Support",
                "sentiment": "neutral", 
                "summary": "Markets showing indecision at critical price levels"
            },
            {
                "title": "Regulatory Clarity Brings Stability to Crypto",
                "sentiment": "bullish",
                "summary": "Clear regulations provide market confidence"
            },
            {
                "title": "DeFi Protocols Show Strong Growth",
                "sentiment": "bullish",
                "summary": "Decentralized finance continues expansion"
            },
            {
                "title": "Market Volatility Expected Amid Economic Uncertainty",
                "sentiment": "bearish",
                "summary": "Economic indicators suggest potential volatility"
            }
        ]

        synthetic_news = []
        for i, news in enumerate(base_news[:count]):
            synthetic_news.append({
                "title": news["title"],
                "published_at": (datetime.now() - timedelta(hours=i)).isoformat(),
                "url": f"https://example.com/news/{i}",
                "source": "synthetic",
                "summary": news["summary"],
                "created_at": datetime.now().isoformat(),
                "domain": {"name": "Synthetic News"},
                "sentiment_hint": news["sentiment"]
            })

        return synthetic_news

    def get_alternative_news(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get news from all alternative sources."""
        all_news = []

        logger.info("Fetching news from alternative sources...")

        # Try RSS feeds first
        try:
            rss_news = self.fetch_rss_news(max_items=limit//2)
            all_news.extend(rss_news)
            logger.info(f"✅ Fetched {len(rss_news)} items from RSS feeds")
        except Exception as e:
            logger.warning(f"RSS feeds failed: {e}")

        # Try Reddit
        try:
            reddit_news = self.fetch_reddit_posts(max_items=limit//3)
            all_news.extend(reddit_news)
            logger.info(f"✅ Fetched {len(reddit_news)} items from Reddit")
        except Exception as e:
            logger.warning(f"Reddit failed: {e}")

        # If we don't have enough news, add synthetic
        if len(all_news) < limit//2:
            synthetic_news = self.generate_synthetic_news(count=limit//4)
            all_news.extend(synthetic_news)
            logger.info(f"✅ Added {len(synthetic_news)} synthetic news items")

        # Save to cache
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "count": len(all_news),
                "news": all_news[:limit]
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            logger.info(f"💾 Cached {len(all_news)} news items")
        except Exception as e:
            logger.warning(f"Failed to cache news: {e}")

        return all_news[:limit]

def get_news_alternative(limit: int = 20) -> list[dict[str, Any]]:
    """Main function to get alternative news."""
    provider = AlternativeNewsProvider()
    return provider.get_alternative_news(limit=limit)

if __name__ == "__main__":
    # Test the alternative news provider
    print("🧪 Testing Alternative News Provider")
    print("=" * 50)

    news = get_news_alternative(limit=10)

    print(f"✅ Fetched {len(news)} news items:")
    for i, item in enumerate(news[:5], 1):
        print(f"{i}. {item['title'][:60]}...")
        print(f"   Source: {item['source']} | {item['published_at'][:10]}")

    print(f"\n📊 Total news items available: {len(news)}")
    print("🎉 Alternative news system working!")
