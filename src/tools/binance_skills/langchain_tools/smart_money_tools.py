from __future__ import annotations

import logging
from typing import Annotated, Optional

from langchain_core.tools import tool
from pydantic import Field

from src.tools.binance_skills.smart_money import (
    get_smart_money_signals,
    format_smart_money_for_agent,
)

logger = logging.getLogger(__name__)

@tool("get_smart_money_signals")
async def lctool_get_smart_money_signals(
    chain_id: Annotated[str, Field(description="Chain ID: 'CT_501' for Solana, '56' for BSC. Default is CT_501.")] = "CT_501",
    signal_type: Annotated[str, Field(description="Filter by signal type (empty for all, or use specific strings if known)")] = "",
) -> str:
    """Useful to discover what Institutional/Smart-Money wallets are trading.
    Retrieves on-chain buy/sell actions from successful addresses, including token tickers,
    buy percentages, and signal statuses. 
    Use this to identify strong macroeconomic inflows or imminent dumps.
    """
    try:
        signals = await get_smart_money_signals(chain_id=chain_id, signal_type=signal_type)
        if not signals:
            return "No smart money signals found currently for this network."

        return format_smart_money_for_agent(signals, max_items=8)
    except Exception as e:
        logger.error(f"Error fetching smart money signals: {e}")
        return f"Error: The Binance Web3 API is currently unavailable ({e}). Could not retrieve signals."
