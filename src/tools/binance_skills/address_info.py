# src/tools/binance_skills/address_info.py
"""
Wallet Address Info - Binance Skills Hub.

API:
GET /v3/public/wallet-direct/buw/wallet/address/pnl/active-position-list
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import BinanceSkillsError, get_skills_client

logger = logging.getLogger(__name__)

ADDRESS_INFO_HEADERS = {
    "clienttype": "web",
    "clientversion": "1.2.0",
}


async def get_wallet_active_positions(
    address: str,
    chain_id: str = "56",
    offset: int = 0,
) -> dict[str, Any]:
    """
    Query wallet active token positions.

    Args:
        address: wallet address
        chain_id: 56=BSC, 8453=Base, CT_501=Solana
        offset: pagination offset
    """
    client = get_skills_client()
    params = {"address": address, "chainId": chain_id, "offset": max(0, offset)}
    try:
        result = await client.get(
            "/v3/public/wallet-direct/buw/wallet/address/pnl/active-position-list",
            params=params,
            headers=ADDRESS_INFO_HEADERS,
        )
        if isinstance(result, dict):
            return result
        return {"list": []}
    except BinanceSkillsError as e:
        logger.warning("[BinanceSkills] Address info failed: %s", e)
        return {"list": [], "error": str(e)}
    except Exception as e:
        logger.warning("[BinanceSkills] Address info unexpected error: %s", e)
        return {"list": [], "error": str(e)}


def format_wallet_active_positions_for_agent(data: dict[str, Any], max_items: int = 8) -> str:
    """Format wallet positions into a compact summary."""
    rows = data.get("list", []) if isinstance(data, dict) else []
    if not rows:
        return "No wallet active-position data available."

    lines = ["=== Binance Web3 Wallet Active Positions ==="]
    for i, row in enumerate(rows[:max_items], 1):
        symbol = row.get("symbol", "?")
        qty = row.get("remainQty", "0")
        price = row.get("price", "N/A")
        pct_24h = row.get("percentChange24h", "N/A")
        lines.append(f"{i}. {symbol} | Qty:{qty} | Price:{price} | 24h:{pct_24h}%")

    return "\n".join(lines)
