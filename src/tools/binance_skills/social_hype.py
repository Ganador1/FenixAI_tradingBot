# src/tools/binance_skills/social_hype.py
"""
Social Hype Leaderboard - Binance Skills Hub.

Retrieves social buzz rankings, sentiment analysis, and social summaries
for tokens across chains. Useful for the Sentiment Agent.

API: GET https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import get_skills_client, BinanceSkillsError

logger = logging.getLogger(__name__)

# Chain IDs
CHAIN_BSC = "56"
CHAIN_BASE = "8453"
CHAIN_SOLANA = "CT_501"


async def get_social_hype_leaderboard(
    chain_id: str = CHAIN_BSC,
    sentiment: str = "All",
    time_range: int = 1,
    target_language: str = "en",
    social_language: str = "ALL",
) -> list[dict[str, Any]]:
    """
    Get social hype leaderboard for tokens.

    Args:
        chain_id: Chain ID (56=BSC, 8453=Base, CT_501=Solana)
        sentiment: Filter: All, Positive, Negative, Neutral
        time_range: 1 = 24 hours
        target_language: Translation target (en, zh)
        social_language: Content language filter (ALL for all)

    Returns:
        List of token entries with social hype data:
        - metaInfo (logo, symbol, chainId, contractAddress, tokenAge, marketCap, priceChange)
        - socialHypeInfo (socialHype score, sentiment, socialSummaryBrief, socialSummaryDetail)
    """
    client = get_skills_client()

    try:
        params = {
            "chainId": chain_id,
            "sentiment": sentiment,
            "socialLanguage": social_language,
            "targetLanguage": target_language,
            "timeRange": time_range,
        }

        result = await client.get(
            "/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard",
            params=params,
        )

        leaderboard = result.get("leaderBoardList", []) if isinstance(result, dict) else result
        logger.info(f"[BinanceSkills] Social hype: {len(leaderboard)} tokens for chain {chain_id}")
        return leaderboard

    except BinanceSkillsError as e:
        logger.warning(f"[BinanceSkills] Social hype failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"[BinanceSkills] Social hype unexpected error: {e}")
        return []


def format_social_hype_for_agent(
    leaderboard: list[dict[str, Any]],
    max_items: int = 5,
) -> str:
    """
    Format social hype data into a concise summary for LLM agents.

    Args:
        leaderboard: Raw leaderboard data from API
        max_items: Max items to include

    Returns:
        Formatted string summary for prompts
    """
    if not leaderboard:
        return "No social hype data available."

    lines = ["=== Binance Web3 Social Hype Leaderboard ==="]

    for i, entry in enumerate(leaderboard[:max_items], 1):
        meta = entry.get("metaInfo", {})
        market = entry.get("marketInfo", {})
        social = entry.get("socialHypeInfo", {})

        symbol = meta.get("symbol", "?")
        market_cap = market.get("marketCap", meta.get("marketCap"))
        price_change = market.get("priceChange", meta.get("priceChange"))
        hype_score = social.get("socialHype", 0)
        sentiment = social.get("sentiment", "N/A")
        summary = social.get("socialSummaryBrief", "")

        mc_str = f"${market_cap:,.0f}" if market_cap else "N/A"
        pc_str = f"{price_change:+.2f}%" if price_change else "N/A"

        lines.append(
            f"{i}. {symbol} | Hype:{hype_score:.0f} | Sentiment:{sentiment} | "
            f"MC:{mc_str} | 24h:{pc_str}"
        )
        if summary:
            lines.append(f"   Summary: {summary[:120]}")

    return "\n".join(lines)
