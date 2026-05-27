# src/core/orchestrator/agents/web3_intel.py
"""
Web3 Intelligence Agent for Fenix Trading Bot.

NEW AGENT powered by Binance Skills Hub.
Analyzes on-chain data: smart money signals, token rankings,
social hype, and market sentiment from DeFi/Web3 ecosystem.

This agent runs in PARALLEL with Technical/QABBA/Sentiment
and feeds additional context into the Decision Agent.

Only active when FENIX_ENABLE_BINANCE_SKILLS=1
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)

# Map CEX symbols to Web3 chain IDs for lookup
# Most major tokens on Binance have BSC and Solana presence
SYMBOL_CHAIN_MAP: dict[str, list[str]] = {
    "BTCUSDT": ["56"],
    "ETHUSDT": ["56", "1"],
    "BNBUSDT": ["56"],
    "SOLUSDT": ["CT_501"],
    "XRPUSDT": ["56"],
    "DOGEUSDT": ["56"],
    "ADAUSDT": ["56"],
    "AVAXUSDT": ["56"],
    "DOTUSDT": ["56"],
    "MATICUSDT": ["56"],
    "LINKUSDT": ["56"],
    "UNIUSDT": ["56", "1"],
    "SHIBUSDT": ["56"],
    "LTCUSDT": ["56"],
    "ATOMUSDT": ["56"],
}

# Tokens with DeFi/meme presence benefit most from Web3 data
WEB3_HEAVY_SYMBOLS = {
    "SOLUSDT",
    "BNBUSDT",
    "ETHUSDT",
    "UNIUSDT",
    "SHIBUSDT",
    "DOGEUSDT",
    "PEPEUSDT",
    "WIFUSDT",
    "FLOKIUSDT",
    "BOMEUSDT",
}

# System prompt for Web3 Intelligence Agent
WEB3_INTEL_SYSTEM_PROMPT = """You are the Web3 Intelligence Agent for Fenix Trading Bot.

Your role is to analyze on-chain and DeFi ecosystem data to provide a trading signal.
You receive data from Binance Skills Hub APIs including:
- Smart Money signals (institutional/whale buy/sell activity)
- Social hype rankings (community buzz and sentiment)
- Token trending/ranking data
- Smart-money inflow rankings
- Meme-rush lifecycle data
- Token snapshot + token security audit

ANALYSIS FRAMEWORK:
1. Smart Money Flow: Are whales buying or selling? How many addresses?
2. Social Sentiment: Is social hype increasing or decreasing?
3. Market Momentum: Token rankings, volume trends
4. Risk Signals: Exit rates, honeypot checks

OUTPUT FORMAT - Return ONLY a valid JSON object:
{
    "signal": "BUY" | "SELL" | "HOLD",
    "confidence": 0.0 to 1.0,
    "rationale": "Brief explanation of your analysis",
    "smart_money_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
    "social_hype_level": "HIGH" | "MEDIUM" | "LOW" | "NONE",
    "web3_risk_flags": ["list of any detected risks"]
}

RULES:
- If no data is available, return HOLD with confidence 0.3
- Smart money buy signals with >5 addresses are strong indicators
- High social hype + smart money buying = stronger signal
- Smart money selling + high exit rate = bearish warning
- Always remain objective, do not recommend based on hype alone"""


WEB3_INTEL_USER_TEMPLATE = """Analyze the following Web3/on-chain data for {symbol}:

CURRENT MARKET:
- Symbol: {symbol}
- Price: ${current_price}
- Timeframe: {timeframe}

SMART MONEY SIGNALS:
{smart_money_data}

SOCIAL HYPE DATA:
{social_hype_data}

TOKEN RANKINGS:
{token_rankings_data}

SMART MONEY INFLOW RANK:
{smart_money_inflow_data}

MEME RUSH:
{meme_rush_data}

TOKEN SNAPSHOT:
{token_snapshot_data}

TOKEN SECURITY AUDIT:
{token_audit_data}

Based on this Web3 intelligence, provide your trading signal.
Remember: Output ONLY a valid JSON object, nothing else."""


def create_web3_intel_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the Web3 Intelligence agent node."""

    async def web3_intel_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            symbol = state.get("symbol", "BTCUSDT")
            current_price = state.get("current_price", 0.0)
            timeframe = state.get("timeframe", "15m")

            # Fetch Web3 data in parallel
            web3_data = await _fetch_web3_data(symbol=symbol, timeframe=timeframe)

            # Build prompt
            messages = [
                {"role": "system", "content": WEB3_INTEL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": WEB3_INTEL_USER_TEMPLATE.format(
                        symbol=symbol,
                        current_price=f"{current_price:,.2f}",
                        timeframe=timeframe,
                        smart_money_data=web3_data["smart_money_data"],
                        social_hype_data=web3_data["social_hype_data"],
                        token_rankings_data=web3_data["token_rankings_data"],
                        smart_money_inflow_data=web3_data["smart_money_inflow_data"],
                        meme_rush_data=web3_data["meme_rush_data"],
                        token_snapshot_data=web3_data["token_snapshot_data"],
                        token_audit_data=web3_data["token_audit_data"],
                    ),
                },
            ]

            # Shorter retries since this is supplementary data
            max_retries = 2

            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=messages,
                agent_type="web3_intel",
                max_retries=max_retries,
                base_delay=0.3,
                required_keys=["signal", "confidence", "rationale"],
            )

            # Get raw response for logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("web3_intel", messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()

            # Store in ReasoningBank
            store_to_reasoning_bank(
                reasoning_bank=reasoning_bank,
                agent_name="web3_intel",
                prompt=messages[1]["content"][:500],
                result=report,
                raw_response=raw_response,
                llm=llm,
                elapsed_ms=elapsed * 1000,
            )

            report["_attempts"] = attempts
            report["_validation_errors"] = errors

            logger.info(
                f"🌐 Web3 Intel: {report.get('signal', 'N/A')} "
                f"(conf={report.get('confidence', 0):.2f}, "
                f"sm_bias={report.get('smart_money_bias', 'N/A')}, "
                f"attempts={attempts}, {elapsed:.1f}s)"
            )

            return {
                "web3_intel_report": report,
                "execution_times": {"web3_intel": elapsed},
                "messages": [f"Web3 Intel completed in {elapsed:.1f}s"],
                "errors": [],
            }

        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            error_msg = f"Web3 Intel error: {str(e)[:200]}"
            logger.warning(f"🌐 {error_msg}")

            return {
                "web3_intel_report": {
                    "signal": "HOLD",
                    "confidence": 0.0,
                    "rationale": f"Web3 Intelligence unavailable: {str(e)[:100]}",
                    "smart_money_bias": "NEUTRAL",
                    "social_hype_level": "NONE",
                    "web3_risk_flags": ["data_unavailable"],
                    "_validation_failed": True,
                },
                "execution_times": {"web3_intel": elapsed},
                "messages": [f"Web3 Intel failed: {str(e)[:100]}"],
                "errors": [error_msg],
            }

    async def traced_web3_intel_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("web3_intel_agent"):
            return await web3_intel_node(state)

    return traced_web3_intel_node


def _default_web3_sections() -> dict[str, str]:
    return {
        "smart_money_data": "Smart money data unavailable.",
        "social_hype_data": "Social hype data unavailable.",
        "token_rankings_data": "Token rankings unavailable.",
        "smart_money_inflow_data": "Smart-money inflow ranking unavailable.",
        "meme_rush_data": "Meme-rush data unavailable.",
        "token_snapshot_data": "Token snapshot unavailable.",
        "token_audit_data": "Token audit unavailable.",
    }


async def _fetch_web3_data(symbol: str, timeframe: str) -> dict[str, str]:
    """
    Fetch Web3 data from Binance Skills Hub APIs in parallel.

    Returns:
        Mapping of text blocks to feed the Web3 prompt.
    """
    sections = _default_web3_sections()

    # Lazy imports to avoid circular deps and allow graceful fallback
    try:
        from src.tools.binance_skills.meme_rush import (
            MEME_FINALIZING,
            MEME_NEW,
            format_meme_rush_for_agent,
            get_meme_rush_rank,
        )
        from src.tools.binance_skills.smart_money import (
            format_smart_money_for_agent,
            get_smart_money_signals,
        )
        from src.tools.binance_skills.smart_money_inflow import (
            PERIOD_24H,
            format_smart_money_inflow_for_agent,
            get_smart_money_inflow_rank,
        )
        from src.tools.binance_skills.social_hype import (
            format_social_hype_for_agent,
            get_social_hype_leaderboard,
        )
        from src.tools.binance_skills.token_audit import (
            audit_token_security,
            format_token_audit_for_agent,
        )
        from src.tools.binance_skills.token_info import (
            format_token_snapshot_for_agent,
            get_token_snapshot_for_symbol,
        )
        from src.tools.binance_skills.token_rank import (
            RANK_TRENDING,
            format_token_rankings_for_agent,
            get_token_rankings,
        )
    except ImportError as e:
        logger.warning(f"BinanceSkills not available: {e}")
        return sections

    chains = SYMBOL_CHAIN_MAP.get(symbol, ["56"])
    primary_chain = chains[0]
    rank_type = MEME_FINALIZING if symbol in WEB3_HEAVY_SYMBOLS else MEME_NEW
    inflow_period = PERIOD_24H
    if timeframe in {"1m", "3m", "5m"}:
        inflow_period = "1h"

    async def _maybe_fetch_meme() -> list[dict[str, Any]]:
        # Memes are most relevant for Web3-heavy symbols.
        if symbol not in WEB3_HEAVY_SYMBOLS:
            return []
        return await get_meme_rush_rank(
            chain_id=primary_chain,
            rank_type=rank_type,
            limit=10,
        )

    # Run all API calls in parallel with timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                get_smart_money_signals(chain_id=primary_chain, page_size=15),
                get_social_hype_leaderboard(chain_id=primary_chain),
                get_token_rankings(rank_type=RANK_TRENDING, chain_id=primary_chain, size=10),
                get_smart_money_inflow_rank(
                    chain_id=primary_chain,
                    period=inflow_period,
                    tag_type=2,
                    limit=10,
                ),
                _maybe_fetch_meme(),
                get_token_snapshot_for_symbol(symbol=symbol, chain_id=primary_chain),
                return_exceptions=True,
            ),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[Web3Intel] Timeout fetching Web3 data")
        sections["smart_money_data"] = "Smart money data timed out."
        sections["social_hype_data"] = "Social hype data timed out."
        sections["token_rankings_data"] = "Token rankings timed out."
        sections["smart_money_inflow_data"] = "Smart-money inflow rank timed out."
        sections["meme_rush_data"] = "Meme-rush data timed out."
        sections["token_snapshot_data"] = "Token snapshot timed out."
        sections["token_audit_data"] = "Token audit unavailable due to timeout."
        return sections

    (
        smart_money_raw,
        social_hype_raw,
        rankings_raw,
        inflow_raw,
        meme_raw,
        token_snapshot_raw,
    ) = results

    # Format results, handling exceptions
    if isinstance(smart_money_raw, Exception):
        sections["smart_money_data"] = f"Smart money error: {smart_money_raw}"
    else:
        sections["smart_money_data"] = format_smart_money_for_agent(smart_money_raw)

    if isinstance(social_hype_raw, Exception):
        sections["social_hype_data"] = f"Social hype error: {social_hype_raw}"
    else:
        sections["social_hype_data"] = format_social_hype_for_agent(social_hype_raw)

    if isinstance(rankings_raw, Exception):
        sections["token_rankings_data"] = f"Token rankings error: {rankings_raw}"
    else:
        sections["token_rankings_data"] = format_token_rankings_for_agent(rankings_raw)

    if isinstance(inflow_raw, Exception):
        sections["smart_money_inflow_data"] = f"Smart-money inflow rank error: {inflow_raw}"
    else:
        sections["smart_money_inflow_data"] = format_smart_money_inflow_for_agent(inflow_raw)

    if isinstance(meme_raw, Exception):
        sections["meme_rush_data"] = f"Meme-rush error: {meme_raw}"
    else:
        sections["meme_rush_data"] = format_meme_rush_for_agent(meme_raw)

    token_snapshot: dict[str, Any] = {}
    if isinstance(token_snapshot_raw, Exception):
        sections["token_snapshot_data"] = f"Token snapshot error: {token_snapshot_raw}"
    else:
        token_snapshot = token_snapshot_raw if isinstance(token_snapshot_raw, dict) else {}
        sections["token_snapshot_data"] = format_token_snapshot_for_agent(token_snapshot)

    if token_snapshot.get("found") and token_snapshot.get("contract_address"):
        try:
            audit_raw = await asyncio.wait_for(
                audit_token_security(
                    chain_id=str(token_snapshot.get("chain_id", primary_chain)),
                    contract_address=str(token_snapshot.get("contract_address", "")),
                ),
                timeout=6.0,
            )
            sections["token_audit_data"] = format_token_audit_for_agent(audit_raw)
        except Exception as e:
            sections["token_audit_data"] = f"Token audit error: {e}"

    return sections
