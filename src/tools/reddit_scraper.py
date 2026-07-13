# tools/reddit_scraper.py
import requests
from pydantic import BaseModel
class BaseTool(BaseModel):
    """Basic tool base class for scrapers."""
    name: str = ""
    description: str = ""

import logging
import os
import time
from typing import List, Dict, Optional

import feedparser

from src.config.mlx_models import get_agent_model

logger = logging.getLogger(__name__)

# Caché módulo-level: Reddit limita ~1 req/s por IP en RSS y el engine da un
# presupuesto de 10s por fuente (FENIX_SENTIMENT_FETCH_TIMEOUT_SEC), así que
# se reusan títulos recientes entre ciclos de análisis.
_TITLE_CACHE: Dict[str, tuple[float, List[str]]] = {}
# Último intento (éxito o no) por subreddit, para rotar de forma justa aunque
# haya 429s repetidos.
_LAST_ATTEMPT: Dict[str, float] = {}
# A 429 must be treated as a degraded source, not as an invitation to retry on
# every analysis cycle.  The backoff is shared by all scraper instances in one
# bot process, exactly like the title cache.
_BACKOFF_UNTIL: Dict[str, float] = {}

class RedditScraper(BaseTool):
    name: str = "RedditPostTitleScraper" # More descriptive name
    description: str = (
        "Extracts titles of the most recent posts from specified subreddits "
        "using Reddit's public RSS feeds. Handles common request errors."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mlx_model_name = get_agent_model('sentiment')

    def get_source_health(self) -> Dict[str, object]:
        """Expose cache/backoff state so sentiment can discount unavailable Reddit data."""
        now = time.time()
        blocked = {
            subreddit: round(max(0.0, until - now), 1)
            for subreddit, until in _BACKOFF_UNTIL.items()
            if until > now
        }
        return {
            "status": "degraded" if blocked else "ok",
            "backoff_seconds_by_subreddit": blocked,
            "cached_subreddits": sorted(_TITLE_CACHE),
        }

    def _run(
        self,
        subreddits: Optional[List[str]] = None,
        limit_per_subreddit: int = 25 # Reduced default for efficiency
    ) -> Dict[str, List[str]]:
        """
        Args:
            subreddits: List of subreddits (without 'r/' prefix). 
                        Defaults to a crypto-focused list if None.
            limit_per_subreddit: Maximum number of post titles to retrieve per subreddit.

        Returns:
            A dictionary where keys are subreddit names and values are lists of post titles.
            If a subreddit scrape fails, its list will be empty.
        """
        if subreddits is None:
            # Lista corta de alta señal: con el rate limit de Reddit (~1 req/s en
            # RSS) y el presupuesto de 10s del engine no caben 8 subreddits.
            subreddits = ["CryptoCurrency", "bitcoin", "ethereum", "CryptoMarkets"]
        
        # Reddit bloquea los endpoints JSON públicos (403 con HTML) desde ~2026;
        # el feed RSS/Atom sigue abierto, así que se usa ese con feedparser.
        headers = {
            "User-Agent": "FenixAI-sentiment/1.0 (crypto news aggregator)"
        }
        all_titles_by_subreddit: Dict[str, List[str]] = {}
        request_timeout = 8 # seconds
        try:
            cache_ttl = float(os.getenv("FENIX_REDDIT_CACHE_TTL_SEC", "600"))
        except ValueError:
            cache_ttl = 600.0
        try:
            spacing = float(os.getenv("FENIX_REDDIT_REQUEST_SPACING_SEC", "2.5"))
        except ValueError:
            spacing = 2.5
        try:
            max_fetch = int(os.getenv("FENIX_REDDIT_MAX_FETCH_PER_CYCLE", "2"))
        except ValueError:
            max_fetch = 2
        try:
            rate_limit_backoff = max(
                30.0, float(os.getenv("FENIX_REDDIT_429_BACKOFF_SEC", "900"))
            )
        except ValueError:
            rate_limit_backoff = 900.0
        try:
            transient_backoff = max(
                15.0, float(os.getenv("FENIX_REDDIT_ERROR_BACKOFF_SEC", "120"))
            )
        except ValueError:
            transient_backoff = 120.0

        def _cached_titles(sub_name: str, max_age: float) -> Optional[List[str]]:
            entry = _TITLE_CACHE.get(sub_name)
            if entry and (time.time() - entry[0]) < max_age:
                return entry[1][:limit_per_subreddit]
            return None

        # Rotación: se fetchean como mucho `max_fetch` subreddits por llamada
        # (los de caché más vieja primero); el resto sale de caché. Así cada
        # llamada cabe en el presupuesto de 10s del engine y en ~2 ciclos
        # todas las subs quedan calientes.
        cleaned = [s.strip().replace('r/', '') for s in subreddits]
        now = time.time()
        pending = [
            s
            for s in cleaned
            if _cached_titles(s, cache_ttl) is None and now >= _BACKOFF_UNTIL.get(s, 0.0)
        ]
        pending.sort(key=lambda s: _LAST_ATTEMPT.get(s, 0.0))
        to_fetch = set(pending[: max(0, max_fetch)])

        made_request = False
        for subreddit_name_cleaned in cleaned:
            fresh = _cached_titles(subreddit_name_cleaned, cache_ttl)
            if fresh is not None:
                all_titles_by_subreddit[subreddit_name_cleaned] = fresh
                continue
            if time.time() < _BACKOFF_UNTIL.get(subreddit_name_cleaned, 0.0):
                all_titles_by_subreddit[subreddit_name_cleaned] = (
                    _cached_titles(subreddit_name_cleaned, float("inf")) or []
                )
                continue
            if subreddit_name_cleaned not in to_fetch:
                all_titles_by_subreddit[subreddit_name_cleaned] = (
                    _cached_titles(subreddit_name_cleaned, float("inf")) or []
                )
                continue

            # Espaciar requests para no disparar el rate limit (429) de Reddit.
            if made_request and spacing > 0:
                time.sleep(spacing)

            url = f"https://www.reddit.com/r/{subreddit_name_cleaned}/new/.rss"
            logger.info(f"[RedditScraper] Scraping /r/{subreddit_name_cleaned} for {limit_per_subreddit} new post titles.")

            try:
                made_request = True
                _LAST_ATTEMPT[subreddit_name_cleaned] = time.time()
                response = requests.get(url, headers=headers, timeout=request_timeout)
                response.raise_for_status()  # Raise HTTPError for bad responses (4XX or 5XX)

                feed = feedparser.parse(response.content)
                titles = [
                    str(entry.get("title", "")).strip()
                    for entry in feed.entries[:limit_per_subreddit]
                    if entry.get("title")
                ]

                all_titles_by_subreddit[subreddit_name_cleaned] = titles
                # Cache empty but successful feeds too; otherwise a quiet feed
                # becomes a request on every cycle.
                _TITLE_CACHE[subreddit_name_cleaned] = (time.time(), titles)
                _BACKOFF_UNTIL.pop(subreddit_name_cleaned, None)
                logger.debug(f"[RedditScraper] Found {len(titles)} titles for /r/{subreddit_name_cleaned}.")

            except requests.exceptions.Timeout:
                logger.warning(f"[RedditScraper] Timeout connecting to Reddit for /r/{subreddit_name_cleaned}.")
                _BACKOFF_UNTIL[subreddit_name_cleaned] = time.time() + transient_backoff
                all_titles_by_subreddit[subreddit_name_cleaned] = _cached_titles(subreddit_name_cleaned, float("inf")) or []
            except requests.exceptions.HTTPError as http_err:
                response = http_err.response
                status_code = getattr(response, "status_code", None)
                retry_after = 0.0
                try:
                    retry_after = float((getattr(response, "headers", {}) or {}).get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
                delay = max(rate_limit_backoff, retry_after) if status_code == 429 else transient_backoff
                _BACKOFF_UNTIL[subreddit_name_cleaned] = time.time() + delay
                logger.warning(
                    "[RedditScraper] HTTP error %s for /r/%s; backing off %.0fs",
                    status_code,
                    subreddit_name_cleaned,
                    delay,
                )
                all_titles_by_subreddit[subreddit_name_cleaned] = _cached_titles(subreddit_name_cleaned, float("inf")) or []
            except requests.exceptions.RequestException as req_err:
                logger.warning(f"[RedditScraper] Request error for /r/{subreddit_name_cleaned}: {req_err}")
                _BACKOFF_UNTIL[subreddit_name_cleaned] = time.time() + transient_backoff
                all_titles_by_subreddit[subreddit_name_cleaned] = _cached_titles(subreddit_name_cleaned, float("inf")) or []
            except Exception as e:
                logger.error(f"[RedditScraper] Unexpected error scraping /r/{subreddit_name_cleaned}: {e}", exc_info=True)
                _BACKOFF_UNTIL[subreddit_name_cleaned] = time.time() + transient_backoff
                all_titles_by_subreddit[subreddit_name_cleaned] = _cached_titles(subreddit_name_cleaned, float("inf")) or []
        
        total_titles_scraped = sum(len(t) for t in all_titles_by_subreddit.values())
        logger.info(f"[RedditScraper] Reddit scraping complete. Total titles fetched: {total_titles_scraped}")
        return all_titles_by_subreddit

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = RedditScraper()
    
    # Example usage:
    # specific_subs = ["wallstreetbets", "stocks"]
    # results = scraper._run(subreddits=specific_subs, limit_per_subreddit=5)
    
    results = scraper._run(limit_per_subreddit=3) # Test with default subreddits

    for subreddit, titles_list in results.items():
        if titles_list:
            print(f"\n--- Titles from r/{subreddit} ---")
            for i, title_text in enumerate(titles_list):
                print(f"{i+1}. {title_text}")
        else:
            print(f"\n--- No titles found for r/{subreddit} ---")
