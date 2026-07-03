from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.tools import tool
from pydantic import Field

from src.tools.binance_skills.meme_rush import (
    get_meme_rush_rank,
    format_meme_rush_for_agent,
)

logger = logging.getLogger(__name__)

@tool("get_meme_rush_ranklist")
async def lctool_get_meme_rush_ranklist(
    chain_id: Annotated[str, Field(description="Chain ID: 'CT_501' for Solana, '56' for BSC. Default is CT_501.")] = "CT_501",
    rank_type: Annotated[int, Field(description="Rank Type: 10=New, 20=Finalizing, 30=Migrated. Default is 10 (New).")] = 10,
    k_limit: Annotated[int, Field(description="Number of tokens to retrieve (max 20).")] = 10,
) -> str:
    """Useful to discover currently trending or viral low-cap coins directly from Binance Web3 APIs.
    Retrieves the Meme Rush Rank List indicating where retail liquidity is aggressively flowing. 
    Use this to identify sudden market rotations or retail hype cycles on Solana/BSC.
    """
    try:
        rank_data = await get_meme_rush_rank(chain_id=chain_id, rank_type=rank_type, limit=k_limit)
        if not rank_data:
            return "No trending meme tokens found for the specified criteria."
        
        return format_meme_rush_for_agent(rank_data, max_items=k_limit)
    except Exception as e:
        logger.error(f"Error fetching meme rush signals: {e}")
        return f"Error: The Binance Web3 API is currently unavailable ({e}). Could not retrieve trending memes."
