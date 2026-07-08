"""Macro/geopolitical news scanner for the sentiment pipeline.

The crypto-only RSS sources (CoinDesk/Cointelegraph/Decrypt) are blind to
geopolitical shocks (2026-07-07: US strikes on Iran never reached the sentiment
agent; only the Fear & Greed index reflected it, with lag and no causal
context). This module scans free world-news RSS feeds and returns only FRESH,
HIGH-IMPACT headlines so they can be injected into the sentiment prompt without
diluting the crypto context.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import feedparser

logger = logging.getLogger(__name__)

MACRO_FEEDS: dict[str, str] = {
    "bbc-world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "cnbc-world": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
}

# A headline must contain at least one of these (lowercase) to qualify.
HIGH_IMPACT_KEYWORDS: tuple[str, ...] = (
    # Geopolitical / military
    "war",
    "strike",
    "strikes",
    "attack",
    "missile",
    "drone",
    "nuclear",
    "invasion",
    "escalation",
    "military",
    "airstrike",
    "bomb",
    "sanction",
    "sanctions",
    "conflict",
    "hostilities",
    "ceasefire",
    "state of emergency",
    # Macro / monetary
    "federal reserve",
    "fed ",
    "rate hike",
    "rate cut",
    "interest rate",
    "inflation",
    "cpi",
    "recession",
    "default",
    "tariff",
    "tariffs",
    "trade war",
    "bank collapse",
    "bailout",
    "debt ceiling",
)

# Severe subset: these justify a risk-off read on their own when fresh.
SEVERE_KEYWORDS: tuple[str, ...] = (
    "war",
    "strike",
    "strikes",
    "attack",
    "missile",
    "nuclear",
    "invasion",
    "airstrike",
    "bomb",
    "state of emergency",
    "bank collapse",
    "default",
)

_cache: dict[str, Any] = {"ts": 0.0, "alerts": []}
_CACHE_TTL_SEC = 600  # do not hammer the feeds on every 15m cycle


def _entry_age_hours(entry: Any) -> float | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        published = datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    except (OverflowError, ValueError):
        return None
    return (datetime.now(timezone.utc) - published).total_seconds() / 3600.0


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Whole-word/phrase match ("war" must not match inside "toward")."""
    return any(
        re.search(rf"\b{re.escape(kw.strip())}\b", text) for kw in keywords
    )


def classify_headline(title: str, summary: str = "") -> str | None:
    """Return "severe"/"high" when the text matches impact keywords, else None."""
    text = f"{title} {summary}".lower()
    if _matches_any(text, SEVERE_KEYWORDS):
        return "severe"
    if _matches_any(text, HIGH_IMPACT_KEYWORDS):
        return "high"
    return None


def get_macro_alerts(
    max_items: int = 3,
    max_age_hours: float = 12.0,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Fetch fresh high-impact macro headlines from world-news RSS feeds.

    Returns news-item dicts compatible with the sentiment pipeline
    (source/title/summary), plus ``severity`` and ``age_hours``.
    """
    now = time.time()
    if use_cache and (now - _cache["ts"]) < _CACHE_TTL_SEC:
        return _cache["alerts"][:max_items]

    alerts: list[dict[str, Any]] = []
    for source, url in MACRO_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception:
            logger.debug("Macro feed failed: %s", source, exc_info=True)
            continue
        for entry in getattr(feed, "entries", [])[:30]:
            title = str(entry.get("title") or "").strip()
            summary = str(entry.get("summary") or "")[:300]
            if not title:
                continue
            severity = classify_headline(title, summary)
            if severity is None:
                continue
            age = _entry_age_hours(entry)
            if age is not None and age > max_age_hours:
                continue
            alerts.append(
                {
                    "source": f"MACRO/{source}",
                    "title": title,
                    "summary": summary,
                    "severity": severity,
                    "age_hours": round(age, 1) if age is not None else None,
                    "url": str(entry.get("link") or ""),
                }
            )

    # Severe first, then freshest.
    alerts.sort(key=lambda a: (a["severity"] != "severe", a["age_hours"] if a["age_hours"] is not None else 99.0))
    _cache["ts"] = now
    _cache["alerts"] = alerts
    if alerts:
        logger.info(
            "🌍 Macro alerts: %d high-impact headlines (%d severe)",
            len(alerts),
            sum(1 for a in alerts if a["severity"] == "severe"),
        )
    return alerts[:max_items]
