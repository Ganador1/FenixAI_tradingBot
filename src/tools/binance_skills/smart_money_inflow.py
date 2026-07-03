# src/tools/binance_skills/smart_money_inflow.py
"""
Smart Money Inflow Rank - Binance Skills Hub.

Ranks tokens by smart-money inflows.

API:
POST /v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import BinanceSkillsError, get_skills_client

logger = logging.getLogger(__name__)

PERIOD_5M = "5m"
PERIOD_1H = "1h"
PERIOD_4H = "4h"
PERIOD_24H = "24h"


async def get_smart_money_inflow_rank(
    chain_id: str = "CT_501",
    period: str = PERIOD_24H,
    tag_type: int | None = 2,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Get token ranking by smart-money inflow.

    Args:
        chain_id: Chain ID (56=BSC, CT_501=Solana)
        period: 5m/1h/4h/24h
        tag_type: optional address tag filter
        limit: max items to return
    """
    client = get_skills_client()

    body: dict[str, Any] = {"chainId": chain_id, "period": period}
    if tag_type is not None:
        body["tagType"] = tag_type

    try:
        result = await client.post(
            "/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query",
            body=body,
        )
        rows: list[dict[str, Any]]
        if isinstance(result, list):
            rows = result
        elif isinstance(result, dict):
            rows = result.get("list", [])
        else:
            rows = []
        return rows[: max(0, limit)]
    except BinanceSkillsError as e:
        logger.warning("[BinanceSkills] Smart money inflow failed: %s", e)
        return []
    except Exception as e:
        logger.warning("[BinanceSkills] Smart money inflow unexpected error: %s", e)
        return []


def format_smart_money_inflow_for_agent(
    inflow_rows: list[dict[str, Any]],
    max_items: int = 6,
) -> str:
    """Format inflow rank into a compact LLM-friendly summary."""
    if not inflow_rows:
        return "No smart-money inflow ranking data available."

    lines = ["=== Binance Web3 Smart Money Inflow Rank ==="]
    for i, row in enumerate(inflow_rows[:max_items], 1):
        token = row.get("tokenName") or row.get("symbol") or row.get("ticker") or "?"
        price = row.get("price")
        net_inflow = (
            row.get("netInflowUsd")
            or row.get("netInflow")
            or row.get("inflowUsd")
            or row.get("inflow")
            or "N/A"
        )
        smart_wallets = (
            row.get("smartMoneyAddressCount")
            or row.get("smartMoneyCount")
            or row.get("addressCount")
            or "N/A"
        )
        lines.append(
            f"{i}. {token} | Price:{price} | NetInflow:{net_inflow} | SmartWallets:{smart_wallets}"
        )

    return "\n".join(lines)
