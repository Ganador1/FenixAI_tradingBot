# src/tools/binance_skills/smart_money.py
"""
Smart Money Trading Signals - Binance Skills Hub.

Retrieves on-chain Smart Money buy/sell signals, trigger prices,
max gain, and exit rates. Essential for the Web3 Intelligence Agent.

API: POST https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import get_skills_client, BinanceSkillsError

logger = logging.getLogger(__name__)


async def get_smart_money_signals(
    chain_id: str = "CT_501",
    signal_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """
    Get Smart Money trading signals.

    Args:
        chain_id: Chain ID (56=BSC, CT_501=Solana)
        signal_type: Filter by signal type (empty for all)
        page: Page number (starting from 1)
        page_size: Items per page (max 100)

    Returns:
        List of signal entries with:
        - signalId, ticker, chainId, contractAddress
        - smartSignalType (SMART_MONEY)
        - smartMoneyCount (number of addresses)
        - direction (buy/sell)
        - alertPrice, currentPrice, highestPrice
        - exitRate (%), maxGain (%)
        - status (active/timeout/completed)
    """
    client = get_skills_client()

    try:
        body = {
            "smartSignalType": signal_type,
            "page": page,
            "pageSize": min(page_size, 100),
            "chainId": chain_id,
        }

        result = await client.post(
            "/v1/public/wallet-direct/buw/wallet/web/signal/smart-money",
            body=body,
        )

        signals = result if isinstance(result, list) else []
        logger.info(
            f"[BinanceSkills] Smart money signals: {len(signals)} for chain {chain_id}"
        )
        return signals

    except BinanceSkillsError as e:
        logger.warning(f"[BinanceSkills] Smart money signals failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"[BinanceSkills] Smart money signals unexpected error: {e}")
        return []


def format_smart_money_for_agent(
    signals: list[dict[str, Any]],
    max_items: int = 8,
) -> str:
    """
    Format smart money signals into a concise summary for LLM agents.

    Args:
        signals: Raw signal data from API
        max_items: Max items to include

    Returns:
        Formatted string summary
    """
    if not signals:
        return "No smart money signals available."

    lines = ["=== Binance Web3 Smart Money Signals ==="]

    # Separate active buy/sell
    active = [s for s in signals if s.get("status") == "active"]
    if not active:
        active = signals  # Show all if none active

    for i, sig in enumerate(active[:max_items], 1):
        ticker = sig.get("ticker", "?")
        direction = sig.get("direction", "?").upper()
        sm_count = sig.get("smartMoneyCount", 0)
        alert_price = sig.get("alertPrice", "?")
        current_price = sig.get("currentPrice", "?")
        max_gain = sig.get("maxGain", "?")
        exit_rate = sig.get("exitRate", "?")
        status = sig.get("status", "?")

        # Price change since signal
        try:
            pct_change = (
                (float(current_price) - float(alert_price)) / float(alert_price) * 100
            )
            pct_str = f"{pct_change:+.2f}%"
        except (ValueError, TypeError, ZeroDivisionError):
            pct_str = "N/A"

        lines.append(
            f"{i}. {ticker} | {direction} | SmartMoney:{sm_count} | "
            f"Since Signal:{pct_str} | MaxGain:{max_gain}% | "
            f"ExitRate:{exit_rate}% | Status:{status}"
        )

    # Summary stats
    buy_count = sum(1 for s in active if s.get("direction") == "buy")
    sell_count = sum(1 for s in active if s.get("direction") == "sell")
    lines.append(f"\nSummary: {buy_count} BUY signals, {sell_count} SELL signals (active)")

    return "\n".join(lines)
