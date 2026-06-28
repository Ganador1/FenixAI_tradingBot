#!/usr/bin/env python3

"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ENHANCED NEWS & SOCIAL SCRAPERS                                         ║
║  Improved reliability, error handling, and data quality                  ║
╚══════════════════════════════════════════════════════════════════════════╝

Improvements:
  • Better error handling and retries
  • Rate limiting protection
  • Improved cache management
  • Data deduplication
  • Quality validation
  • Additional RSS sources
  • Fallback sources for reliability
"""

import logging
import hashlib
import json
import os
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from collections import defaultdict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)


class EnhancedCache:
    """Enhanced caching system with better management"""
    
    def __init__(self, cache_dir: str, ttl_minutes: int = 30):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_minutes = ttl_minutes
        self.hits = 0
        self.misses = 0
    
    def _get_cache_file(self, key: str) -> Path:
        """Get cache file path"""
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]  # Use SHA256 instead of MD5
        return self.cache_dir / f"{key_hash}.json"
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached data if fresh"""
        cache_file = self._get_cache_file(key)
        
        if not cache_file.exists():
            self.misses += 1
            return None
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Check freshness
            cached_time = datetime.fromisoformat(data['timestamp'])
            if datetime.now() - cached_time < timedelta(minutes=self.ttl_minutes):
                self.hits += 1
                logger.info(f"Cache HIT: {key} (age: {(datetime.now() - cached_time).seconds}s)")
                return data['content']
            else:
                logger.info(f"Cache EXPIRED: {key}")
                cache_file.unlink()  # Remove stale cache
                self.misses += 1
                return None
                
        except Exception as e:
            logger.warning(f"Error reading cache for {key}: {e}")
            self.misses += 1
            return None
    
    def set(self, key: str, content: Any):
        """Cache content"""
        cache_file = self._get_cache_file(key)
        
        try:
            data = {
                'key': key,
                'content': content,
                'timestamp': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            logger.debug(f"Cached: {key}")
            
        except Exception as e:
            logger.warning(f"Could not cache {key}: {e}")
    
    def clear_old(self):
        """Remove expired cache entries"""
        removed = 0
        
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                cached_time = datetime.fromisoformat(data['timestamp'])
                if datetime.now() - cached_time > timedelta(minutes=self.ttl_minutes):
                    cache_file.unlink()
                    removed += 1
                    
            except Exception:
                continue
        
        if removed > 0:
            logger.info(f"Cleared {removed} expired cache entries")
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        
        return {
            'hits': self.hits,
            'misses': self.misses,
            'total_requests': total,
            'hit_rate': hit_rate
        }


class ResilientHTTPSession:
    """HTTP session with retries and rate limiting"""
    
    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.5):
        self.session = requests.Session()
        
        # Configure retries
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set user agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        
        # Rate limiting
        self.last_request_time = defaultdict(float)
        self.min_interval = 1.0  # Minimum 1s between requests to same domain
    
    def get(self, url: str, timeout: int = 10, **kwargs) -> requests.Response:
        """GET request with rate limiting"""
        # Extract domain
        domain = self._extract_domain(url)
        
        # Rate limit check
        elapsed = time.time() - self.last_request_time[domain]
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s for {domain}")
            time.sleep(wait_time)
        
        # Make request
        try:
            response = self.session.get(url, timeout=timeout, **kwargs)
            self.last_request_time[domain] = time.time()
            return response
        except requests.exceptions.RequestException as e:
            logger.warning(f"HTTP request failed for {url}: {e}")
            raise
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        match = re.match(r'https?://([^/]+)', url)
        return match.group(1) if match else 'unknown'


class EnhancedNewsScraper:
    """Enhanced news scraper with improved reliability"""
    
    # Expanded RSS sources (free, reliable sources)
    RSS_SOURCES = {
        'coindesk': 'https://www.coindesk.com/arc/outboundfeeds/rss/',
        'cointelegraph': 'https://cointelegraph.com/rss',
        'decrypt': 'https://decrypt.co/feed',
        'cryptoslate': 'https://cryptoslate.com/feed/',
        'blockworks': 'https://blockworks.co/feed/',
        'beincrypto': 'https://beincrypto.com/feed/',
        'coinmarketcap': 'https://coinmarketcap.com/headlines/rss/',
        'theblock': 'https://www.theblock.co/rss.xml',
        'bitcoinmagazine': 'https://bitcoinmagazine.com/.rss/full/',
        # Alternative/backup sources
        'cryptonews': 'https://cryptonews.com/news/feed/',
        'coingape': 'https://coingape.com/feed/'
    }
    
    def __init__(self, cache_dir: str = "cache/news", cache_ttl: int = 30):
        """
        Initialize enhanced news scraper
        
        Args:
            cache_dir: Directory for caching
            cache_ttl: Cache TTL in minutes
        """
        if not FEEDPARSER_AVAILABLE:
            raise ImportError("feedparser not available. Install with: pip install feedparser")
        
        self.cache = EnhancedCache(cache_dir, ttl_minutes=cache_ttl)
        self.http = ResilientHTTPSession()
        try:
            self.request_timeout = float(os.getenv("FENIX_RSS_REQUEST_TIMEOUT_SEC", "10"))
        except ValueError:
            self.request_timeout = 10.0
        
        # Track source performance
        self.source_stats = defaultdict(lambda: {
            'attempts': 0,
            'successes': 0,
            'failures': 0,
            'total_articles': 0
        })
        
        # Deduplication
        self.seen_articles: Set[str] = set()
    
    def fetch_crypto_news(self, 
                         limit: int = 50,
                         min_sources: int = 3,
                         deduplicate: bool = True) -> List[Dict[str, Any]]:
        """
        Fetch crypto news from multiple sources
        
        Args:
            limit: Maximum articles to return
            min_sources: Minimum successful sources required
            deduplicate: Remove duplicate articles
            
        Returns:
            List of news articles
        """
        logger.info(f"Fetching crypto news (limit={limit}, min_sources={min_sources})")
        
        all_articles = []
        successful_sources = 0
        
        for source_name, url in self.RSS_SOURCES.items():
            try:
                articles = self._fetch_from_rss(source_name, url, max_articles=10)
                
                if articles:
                    all_articles.extend(articles)
                    successful_sources += 1
                    logger.info(f"✓ {source_name}: {len(articles)} articles")
                else:
                    logger.warning(f"✗ {source_name}: No articles")
                    
            except Exception as e:
                logger.warning(f"✗ {source_name} failed: {e}")
                continue
            
            # Stop if we have enough
            if len(all_articles) >= limit and successful_sources >= min_sources:
                break
        
        # Check if we met minimum requirement
        if successful_sources < min_sources:
            logger.warning(
                f"Only {successful_sources}/{min_sources} sources succeeded. "
                f"Results may be incomplete."
            )
        
        # Deduplicate
        if deduplicate:
            all_articles = self._deduplicate_articles(all_articles)
        
        # Sort by published date (newest first)
        all_articles.sort(key=lambda x: x.get('published', ''), reverse=True)
        
        # Limit results
        all_articles = all_articles[:limit]
        
        logger.info(f"✅ Fetched {len(all_articles)} unique articles from {successful_sources} sources")
        
        return all_articles
    
    def _fetch_from_rss(self, 
                       source_name: str, 
                       url: str, 
                       max_articles: int = 10) -> List[Dict[str, Any]]:
        """Fetch articles from a single RSS source"""
        self.source_stats[source_name]['attempts'] += 1
        
        # Check cache first
        cache_key = f"rss_{source_name}"
        cached = self.cache.get(cache_key)
        if cached:
            self.source_stats[source_name]['successes'] += 1
            self.source_stats[source_name]['total_articles'] += len(cached)
            return cached
        
        try:
            # Fetch with the resilient HTTP client so source outages cannot hang indefinitely.
            response = self.http.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            payload = response.content or getattr(response, "text", "")
            feed = feedparser.parse(
                payload,
                request_headers=self.http.session.headers,
                response_headers=getattr(response, "headers", None),
            )
            
            if feed.get('bozo', False):
                # Feed has errors
                logger.warning(f"{source_name}: Feed parsing error")
            
            articles = []
            
            for entry in feed.entries[:max_articles]:
                article = {
                    'title': entry.get('title', '').strip(),
                    'link': entry.get('link', ''),
                    'published': entry.get('published', entry.get('updated', '')),
                    'summary': self._clean_text(entry.get('summary', entry.get('description', ''))),
                    'source': source_name,
                    'fetched_at': datetime.now().isoformat()
                }
                
                # Validate article
                if article['title'] and article['link']:
                    articles.append(article)
            
            # Cache results
            if articles:
                self.cache.set(cache_key, articles)
                self.source_stats[source_name]['successes'] += 1
                self.source_stats[source_name]['total_articles'] += len(articles)
            else:
                self.source_stats[source_name]['failures'] += 1
            
            return articles
            
        except Exception as e:
            logger.warning(f"Error fetching from {source_name}: {e}")
            self.source_stats[source_name]['failures'] += 1
            return []
    
    def _deduplicate_articles(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate articles based on title similarity"""
        unique_articles = []
        seen_hashes = set()
        
        for article in articles:
            # Create hash from normalized title
            title_normalized = self._normalize_text(article['title'])
            title_hash = hashlib.sha256(title_normalized.encode()).hexdigest()[:16]  # Use SHA256 instead of MD5
            
            if title_hash not in seen_hashes:
                seen_hashes.add(title_hash)
                unique_articles.append(article)
        
        removed = len(articles) - len(unique_articles)
        if removed > 0:
            logger.info(f"Removed {removed} duplicate articles")
        
        return unique_articles
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        # Remove special characters, convert to lowercase
        text = re.sub(r'[^\w\s]', '', text.lower())
        # Remove extra whitespace
        text = ' '.join(text.split())
        return text
    
    def _clean_text(self, html_text: str) -> str:
        """Clean HTML from text"""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', html_text)
        # Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        # Clean whitespace
        text = ' '.join(text.split())
        return text[:500]  # Limit summary length
    
    def get_source_stats(self) -> Dict[str, Any]:
        """Get statistics about source performance"""
        stats = dict(self.source_stats)
        
        # Calculate success rates
        for source_name, data in stats.items():
            total = data['attempts']
            if total > 0:
                data['success_rate'] = data['successes'] / total * 100
                data['avg_articles'] = data['total_articles'] / data['successes'] if data['successes'] > 0 else 0
        
        return stats
    
    def print_stats(self):
        """Print scraper statistics"""
        print("\n" + "="*60)
        print("📊 NEWS SCRAPER STATISTICS")
        print("="*60)
        
        stats = self.get_source_stats()
        cache_stats = self.cache.get_stats()
        
        print(f"\nCache Performance:")
        print(f"  Hits: {cache_stats['hits']}")
        print(f"  Misses: {cache_stats['misses']}")
        print(f"  Hit Rate: {cache_stats['hit_rate']:.1f}%")
        
        print(f"\nSource Performance:")
        for source_name, data in sorted(stats.items()):
            print(f"\n  {source_name}:")
            print(f"    Attempts: {data['attempts']}")
            print(f"    Successes: {data['successes']}")
            print(f"    Success Rate: {data.get('success_rate', 0):.1f}%")
            print(f"    Total Articles: {data['total_articles']}")
            print(f"    Avg Articles/Success: {data.get('avg_articles', 0):.1f}")
        
        print("="*60)


# Convenience function (backward compatible)
def fetch_crypto_news_enhanced(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Enhanced crypto news fetching
    
    Args:
        limit: Maximum articles to return
        
    Returns:
        List of news articles
    """
    scraper = EnhancedNewsScraper()
    return scraper.fetch_crypto_news(limit=limit)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("🧪 Testing Enhanced News Scraper")
    print("="*60)
    
    scraper = EnhancedNewsScraper()
    
    # Fetch news
    print("\n📰 Fetching crypto news...")
    articles = scraper.fetch_crypto_news(limit=30)
    
    print(f"\n✅ Fetched {len(articles)} articles")
    
    # Show sample articles
    print("\n📄 Sample Articles:")
    for i, article in enumerate(articles[:5], 1):
        print(f"\n{i}. {article['title']}")
        print(f"   Source: {article['source']}")
        print(f"   Link: {article['link']}")
        print(f"   Summary: {article['summary'][:100]}...")
    
    # Print stats
    scraper.print_stats()
