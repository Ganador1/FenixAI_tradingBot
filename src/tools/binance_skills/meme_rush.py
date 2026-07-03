# src/tools/binance_skills/meme_rush.py
"""
Meme Rush Rank - Binance Skills Hub.

Fetches meme token lifecycle rankings (new/finalizing/migrated).

API:
POST /v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import BinanceSkillsError, get_skills_client

logger = logging.getLogger(__name__)

MEME_NEW = 10
MEME_FINALIZING = 20
MEME_MIGRATED = 30


async def get_meme_rush_rank(
    chain_id: str = "CT_501",
    rank_type: int = MEME_FINALIZING,
    limit: int = 20,
    keywords: list[str] | None = None,
    excludes: list[str] | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """
    Get meme rank list for a chain/stage.

    Args:
        chain_id: Chain ID (56=BSC, CT_501=Solana)
        rank_type: 10=new, 20=finalizing, 30=migrated
        limit: max results (max endpoint supports 200)
        keywords/excludes: optional symbol filters
        filters: optional endpoint filters
    """
    client = get_skills_client()
    body: dict[str, Any] = {
        "chainId": chain_id,
        "rankType": rank_type,
        "limit": min(max(limit, 1), 200),
    }
    if keywords:
        body["keywords"] = keywords[:5]
    if excludes:
        body["excludes"] = excludes[:5]
    for key, value in filters.items():
        if value is not None:
            body[key] = value

    try:
        result = await client.post(
            "/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list",
            body=body,
        )
        if isinstance(result, dict):
            rows = result.get("tokens", [])
        elif isinstance(result, list):
            rows = result
        else:
            rows = []
        return rows[: min(limit, len(rows))]
    except BinanceSkillsError as e:
        logger.warning("[BinanceSkills] Meme rush failed: %s", e)
        return []
    except Exception as e:
        logger.warning("[BinanceSkills] Meme rush unexpected error: %s", e)
        return []


def format_meme_rush_for_agent(rows: list[dict[str, Any]], max_items: int = 6) -> str:
    """Format meme-rush ranking data for LLM prompts."""
    if not rows:
        return "No meme-rush ranking data available."

    lines = ["=== Binance Web3 Meme Rush ==="]
    for i, row in enumerate(rows[:max_items], 1):
        symbol = row.get("symbol", "?")
        progress = row.get("progress", "N/A")
        mcap = row.get("marketCap", "N/A")
        volume = row.get("volume", "N/A")
        price_change = row.get("priceChange", "N/A")
        dev_sell = row.get("devSellPercent", "N/A")
        wash_flag = row.get("tagDevWashTrading", 0)
        lines.append(
            f"{i}. {symbol} | Progress:{progress}% | 24h:{price_change}% | "
            f"MC:{mcap} | Vol:{volume} | DevSell:{dev_sell}% | DevWash:{wash_flag}"
        )

    return "\n".join(lines)
