from __future__ import annotations

from unittest.mock import MagicMock

import requests

from src.tools import reddit_scraper as module


def _reset_reddit_state() -> None:
    module._TITLE_CACHE.clear()
    module._LAST_ATTEMPT.clear()
    module._BACKOFF_UNTIL.clear()


def test_reddit_429_enters_backoff_and_is_not_retried_each_cycle(monkeypatch):
    _reset_reddit_state()
    monkeypatch.setattr(module, "get_agent_model", lambda _agent: "test-model")
    monkeypatch.setenv("FENIX_REDDIT_429_BACKOFF_SEC", "600")
    monkeypatch.setenv("FENIX_REDDIT_MAX_FETCH_PER_CYCLE", "1")

    response = MagicMock(status_code=429, headers={"Retry-After": "30"})
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    request = MagicMock(return_value=response)
    monkeypatch.setattr(module.requests, "get", request)

    scraper = module.RedditScraper()
    first = scraper._run(subreddits=["bitcoin"], limit_per_subreddit=3)
    second = scraper._run(subreddits=["bitcoin"], limit_per_subreddit=3)

    assert first == {"bitcoin": []}
    assert second == {"bitcoin": []}
    request.assert_called_once()
    health = scraper.get_source_health()
    assert health["status"] == "degraded"
    assert health["backoff_seconds_by_subreddit"]["bitcoin"] > 0


def test_empty_successful_reddit_feed_is_cached(monkeypatch):
    _reset_reddit_state()
    monkeypatch.setattr(module, "get_agent_model", lambda _agent: "test-model")
    monkeypatch.setenv("FENIX_REDDIT_CACHE_TTL_SEC", "600")
    monkeypatch.setenv("FENIX_REDDIT_MAX_FETCH_PER_CYCLE", "1")

    response = MagicMock(content=b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")
    response.raise_for_status.return_value = None
    request = MagicMock(return_value=response)
    monkeypatch.setattr(module.requests, "get", request)

    scraper = module.RedditScraper()
    assert scraper._run(subreddits=["ethereum"], limit_per_subreddit=3) == {"ethereum": []}
    assert scraper._run(subreddits=["ethereum"], limit_per_subreddit=3) == {"ethereum": []}

    request.assert_called_once()
    assert scraper.get_source_health()["status"] == "ok"
