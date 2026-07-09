"""A/B offline test: sentiment agent response WITHOUT vs WITH macro alerts +
F&G trend. Does NOT touch the running bots.

Usage: .venv/bin/python scripts/test_sentiment_macro_ab.py
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, ".")

from src.core.orchestrator.llm_factory import LLMFactory
from src.prompts.agent_prompts import format_prompt
from src.security.prompt_sanitizer import sanitize_news_items
from src.tools.fear_greed import FearGreedTool
from src.tools.macro_news import get_macro_alerts

CRYPTO_NEWS = [
    {"source": "decrypt", "title": "SEC's Long-Promised Crypto Safe Harbor to Be Introduced as Soon as This Month", "summary": "The SEC updated its agenda to indicate that a key crypto rulemaking..."},
    {"source": "cointelegraph", "title": "SEC crypto rule changes are high on its 2026 agenda", "summary": "The financial regulator's agenda included proposed rule changes..."},
    {"source": "decrypt", "title": "Tether Invests $20 Million in Mercado Bitcoin", "summary": "Brazilian crypto exchange Mercado Bitcoin has raised $20 million..."},
    {"source": "cointelegraph", "title": "Ether climbs toward $2K as Bitmine buys ETH, Robinhood L2 boost", "summary": "ETH charts a path toward $2,000 as TradFi adoption grows..."},
    {"source": "decrypt", "title": "Ethereum Foundation restructuring concludes", "summary": "The EF changes shape after a months-long reorganization..."},
]

# Fallback if the live feeds have no fresh severe headline right now.
SIMULATED_MACRO = [
    {
        "source": "MACRO/bbc-world",
        "title": "US launches military strikes on Iranian nuclear facilities",
        "summary": "US forces carried out air strikes against targets in Iran; oil prices spike and global markets sell off as escalation fears mount.",
        "severity": "severe",
        "age_hours": 2.1,
    }
]


def build_news_summary(news_list):
    safe = sanitize_news_items(news_list[:5])
    return "\n".join(
        f"- [{n.get('source', 'N/A')}] {n.get('title', 'Untitled')}: {n.get('summary', '')[:100]}..."
        for n in safe
    )


async def run_case(llm, label, news_list, fg_value):
    messages = format_prompt(
        "sentiment_analyst",
        symbol="ETHUSDC",
        news_summary=build_news_summary(news_list),
        social_data="{}",
        fear_greed_value=fg_value,
        additional_context=f"Total available articles: {len(news_list)}. Social: none (offline test).",
    )
    llm_messages = [
        {"role": "system", "content": messages[0]["content"]},
        {"role": "user", "content": messages[1]["content"]},
    ]
    resp = await llm.ainvoke(llm_messages)
    raw = getattr(resp, "content", None) or str(resp)
    print(f"\n{'=' * 70}\n### {label}\n{'=' * 70}")
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        print(f"  overall_sentiment : {parsed.get('overall_sentiment')}")
        print(f"  confidence        : {parsed.get('confidence_score')}")
        print(f"  key_events        : {parsed.get('key_events')}")
        print(f"  impact            : {parsed.get('impact_assessment')}")
        print(f"  reasoning         : {str(parsed.get('reasoning'))[:400]}")
    except Exception:
        print(raw[:800])


async def main():
    print("Fetching live macro alerts from world-news feeds...")
    macro = get_macro_alerts(max_items=3, use_cache=False)
    if macro:
        print(f"  -> {len(macro)} live alerts found:")
        for a in macro:
            print(f"     [{a['severity']}] ({a['age_hours']}h) {a['title'][:90]}")
    else:
        print("  -> no fresh live alerts matched; using simulated Iran headline")
        macro = SIMULATED_MACRO

    print("\nFetching Fear & Greed with trend...")
    fg_trend = FearGreedTool().get_value_with_trend() or "27"
    fg_plain = fg_trend.split(" ")[0]
    print(f"  plain: {fg_plain} | with trend: {fg_trend}")

    factory = LLMFactory()
    llm = factory.get_llm_for_agent("sentiment")

    await run_case(llm, "A) ANTES — solo noticias cripto, F&G plano", CRYPTO_NEWS, fg_plain)
    await run_case(
        llm,
        "B) DESPUÉS — macro alerts + F&G con tendencia",
        list(macro) + CRYPTO_NEWS,
        fg_trend,
    )


if __name__ == "__main__":
    asyncio.run(main())
