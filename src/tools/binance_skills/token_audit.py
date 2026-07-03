# src/tools/binance_skills/token_audit.py
"""
Token Security Audit - Binance Skills Hub.

Query token security audit to detect scams, honeypots, and malicious contracts.
Essential for risk management before any trade.

API: POST https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.tools.binance_skills.client import get_skills_client, BinanceSkillsError

logger = logging.getLogger(__name__)


async def audit_token_security(
    chain_id: str,
    contract_address: str,
) -> dict[str, Any]:
    """
    Audit a token for security risks.

    Args:
        chain_id: Chain ID (CT_501=Solana, 56=BSC, 8453=Base, 1=Ethereum)
        contract_address: Token contract address

    Returns:
        Audit result with:
        - hasResult (bool): Whether audit data is available
        - isSupported (bool): Whether token is supported for audit
        - riskLevelEnum: LOW, MEDIUM, HIGH
        - riskLevel: 0-5 numeric
        - extraInfo: {buyTax, sellTax, isVerified}
        - riskItems[]: Detailed risk checks
    """
    client = get_skills_client()

    try:
        body = {
            "binanceChainId": chain_id,
            "contractAddress": contract_address,
            "requestId": str(uuid.uuid4()),
        }

        result = await client.post(
            "/v1/public/wallet-direct/security/token/audit",
            body=body,
        )

        logger.info(
            f"[BinanceSkills] Token audit for {contract_address[:10]}... on chain {chain_id}: "
            f"risk={result.get('riskLevelEnum', 'N/A')}"
        )
        return result

    except BinanceSkillsError as e:
        logger.warning(f"[BinanceSkills] Token audit failed: {e}")
        return {"hasResult": False, "isSupported": False, "error": str(e)}
    except Exception as e:
        logger.warning(f"[BinanceSkills] Token audit unexpected error: {e}")
        return {"hasResult": False, "isSupported": False, "error": str(e)}


def format_token_audit_for_agent(audit: dict[str, Any]) -> str:
    """
    Format token audit data into a concise summary for LLM agents.

    Args:
        audit: Raw audit data from API

    Returns:
        Formatted string summary
    """
    if not audit.get("hasResult") or not audit.get("isSupported"):
        return "Token audit: No data available or token not supported for audit."

    risk_level = audit.get("riskLevelEnum")
    risk_score = audit.get("riskLevel")
    if not risk_level and risk_score in (None, "", "N/A"):
        return "Token audit: Incomplete audit data."

    risk_level = risk_level or "UNKNOWN"
    risk_score = risk_score if risk_score is not None else "?"
    extra = audit.get("extraInfo", {})

    lines = [
        "=== Token Security Audit ===",
        f"Risk Level: {risk_level} ({risk_score}/5)",
        f"Buy Tax: {extra.get('buyTax', 'N/A')}%",
        f"Sell Tax: {extra.get('sellTax', 'N/A')}%",
        f"Contract Verified: {extra.get('isVerified', 'N/A')}",
    ]

    # Add risk items
    risk_items = audit.get("riskItems", [])
    if risk_items:
        lines.append("\nRisk Checks:")
        for item in risk_items:
            item_name = item.get("name", "Unknown")
            details = item.get("details", [])
            for detail in details:
                hit = detail.get("isHit", False)
                title = detail.get("title", "?")
                risk_type = detail.get("riskType", "?")
                status = "⚠️ DETECTED" if hit else "✅ Clear"
                lines.append(f"  [{risk_type}] {title}: {status}")

    # Action recommendation based on risk level
    actions = {
        "LOW": "Proceed with caution",
        "MEDIUM": "Exercise caution, review risk items",
        "HIGH": "Avoid trading - critical risks detected",
    }
    action = actions.get(risk_level, "Insufficient data")
    lines.append(f"\nRecommended Action: {action}")

    return "\n".join(lines)
