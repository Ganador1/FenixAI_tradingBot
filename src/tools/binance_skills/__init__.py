# src/tools/binance_skills/__init__.py
"""
Binance Skills Hub Integration for Fenix Trading Bot.

Provides access to Binance's public Web3 APIs for:
- Social hype & sentiment analysis
- Smart money trading signals
- Token rankings & market data
- Token security audits
- On-chain wallet tracking

Based on: https://github.com/binance/binance-skills-hub
"""

from src.tools.binance_skills.client import BinanceSkillsClient, close_skills_client
from src.tools.binance_skills.social_hype import get_social_hype_leaderboard
from src.tools.binance_skills.smart_money import get_smart_money_signals
from src.tools.binance_skills.token_rank import get_token_rankings
from src.tools.binance_skills.token_audit import audit_token_security
from src.tools.binance_skills.smart_money_inflow import get_smart_money_inflow_rank
from src.tools.binance_skills.meme_rush import get_meme_rush_rank
from src.tools.binance_skills.token_info import get_token_snapshot_for_symbol
from src.tools.binance_skills.address_info import get_wallet_active_positions

__all__ = [
    "BinanceSkillsClient",
    "close_skills_client",
    "get_social_hype_leaderboard",
    "get_smart_money_signals",
    "get_token_rankings",
    "audit_token_security",
    "get_smart_money_inflow_rank",
    "get_meme_rush_rank",
    "get_token_snapshot_for_symbol",
    "get_wallet_active_positions",
]
