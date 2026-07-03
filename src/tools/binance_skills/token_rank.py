# src/tools/binance_skills/token_rank.py
"""
Unified Token Rank - Binance Skills Hub.

Query trending tokens, top searched, alpha picks, smart money inflow rankings.

API: POST https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import get_skills_client, BinanceSkillsError

logger = logging.getLogger(__name__)

# Rank types
RANK_TRENDING = 10
RANK_TOP_SEARCH = 11
RANK_ALPHA = 20
RANK_STOCK = 40

# Time periods
PERIOD_1M = 10
PERIOD_5M = 20
PERIOD_1H = 30
PERIOD_4H = 40
PERIOD_24H = 50


async def get_token_rankings(
    rank_type: int = RANK_TRENDING,
    chain_id: str | None = None,
    period: int = PERIOD_24H,
    sort_by: int = 0,
    page: int = 1,
    size: int = 20,
    **filters: Any,
) -> dict[str, Any]:
    """
    Get unified token rankings.

    Args:
        rank_type: 10=Trending, 11=TopSearch, 20=Alpha, 40=Stock
        chain_id: Chain filter (56, 8453, CT_501, or None for all)
        period: 10=1m, 20=5m, 30=1h, 40=4h, 50=24h
        sort_by: Sort field (0=default, 40=marketCap, 50=priceChange, 70=volume)
        page: Page number
        size: Page size (max 200)
        **filters: Additional filters (percentChangeMin/Max, marketCapMin/Max, etc.)

    Returns:
        Dict with tokens list and pagination info
    """
    client = get_skills_client()

    try:
        body: dict[str, Any] = {
            "rankType": rank_type,
            "period": period,
            "sortBy": sort_by,
            "orderAsc": False,
            "page": page,
            "size": min(size, 200),
        }

        if chain_id:
            body["chainId"] = chain_id

        # Add optional filters
        for key, value in filters.items():
            if value is not None:
                body[key] = value

        result = await client.post(
            "/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list",
            body=body,
        )

        tokens = result.get("tokens", []) if isinstance(result, dict) else []
        total = result.get("total", len(tokens)) if isinstance(result, dict) else len(tokens)

        logger.info(
            f"[BinanceSkills] Token rank (type={rank_type}): {len(tokens)} tokens (total: {total})"
        )
        return {"tokens": tokens, "total": total, "page": page}

    except BinanceSkillsError as e:
        logger.warning(f"[BinanceSkills] Token rankings failed: {e}")
        return {"tokens": [], "total": 0, "page": page}
    except Exception as e:
        logger.warning(f"[BinanceSkills] Token rankings unexpected error: {e}")
        return {"tokens": [], "total": 0, "page": page}


def format_token_rankings_for_agent(
    data: dict[str, Any],
    rank_type: int = RANK_TRENDING,
    max_items: int = 10,
) -> str:
    """
    Format token ranking data into a concise summary for LLM agents.

    Args:
        data: Raw ranking data from API
        rank_type: Type of ranking
        max_items: Max items to include

    Returns:
        Formatted string summary
    """
    tokens = data.get("tokens", [])
    if not tokens:
        return "No token ranking data available."

    type_names = {
        RANK_TRENDING: "Trending",
        RANK_TOP_SEARCH: "Top Searched",
        RANK_ALPHA: "Alpha Picks",
        RANK_STOCK: "Tokenized Stocks",
    }
    title = type_names.get(rank_type, f"Type {rank_type}")

    lines = [f"=== Binance Web3 Token Rankings: {title} ==="]

    for i, token in enumerate(tokens[:max_items], 1):
        symbol = token.get("symbol", "?")
        chain = token.get("chainId", "?")
        price = token.get("price", "?")
        mc = token.get("marketCap", "?")
        vol_24h = token.get("volume24h", "?")
        pct_24h = token.get("percentChange24h", "?")
        holders = token.get("holders", "?")

        try:
            price_str = f"${float(price):,.6f}" if float(price) < 1 else f"${float(price):,.2f}"
        except (ValueError, TypeError):
            price_str = str(price)

        try:
            mc_str = f"${float(mc):,.0f}"
        except (ValueError, TypeError):
            mc_str = str(mc)

        try:
            pct_str = f"{float(pct_24h):+.2f}%"
        except (ValueError, TypeError):
            pct_str = str(pct_24h)

        lines.append(
            f"{i}. {symbol} (chain:{chain}) | Price:{price_str} | "
            f"MC:{mc_str} | 24h:{pct_str} | Holders:{holders}"
        )

    lines.append(f"\nTotal ranked tokens: {data.get('total', '?')}")
    return "\n".join(lines)
