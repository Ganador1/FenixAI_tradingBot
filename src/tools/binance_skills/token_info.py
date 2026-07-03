# src/tools/binance_skills/token_info.py
"""
Token info utilities for Binance Skills Hub.

Endpoints:
- Token Search (v5)
- Token Metadata (v1)
- Token Dynamic Data (v4)
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.binance_skills.client import BinanceSkillsError, get_skills_client

logger = logging.getLogger(__name__)


def _symbol_to_keyword(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    for quote in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD"):
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


def _normalize_base_symbol(raw: str) -> str:
    s = (raw or "").upper().strip()
    if s.startswith("1000") and len(s) > 4:
        return s[4:]
    return s


def _pick_best_token(symbol: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None

    base = _normalize_base_symbol(_symbol_to_keyword(symbol))

    for item in candidates:
        candidate_symbol = _normalize_base_symbol(str(item.get("symbol", "")))
        if candidate_symbol == base:
            return item

    return candidates[0]


async def search_tokens(
    keyword: str,
    chain_ids: list[str] | None = None,
    order_by: str = "volume24h",
) -> list[dict[str, Any]]:
    """Search tokens by symbol/name/contract."""
    client = get_skills_client()
    params: dict[str, Any] = {
        "keyword": keyword,
        "orderBy": order_by,
    }
    if chain_ids:
        params["chainIds"] = ",".join(chain_ids)

    try:
        result = await client.get(
            "/v5/public/wallet-direct/buw/wallet/market/token/search",
            params=params,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("list", [])
        return []
    except BinanceSkillsError as e:
        logger.warning("[BinanceSkills] Token search failed: %s", e)
        return []
    except Exception as e:
        logger.warning("[BinanceSkills] Token search unexpected error: %s", e)
        return []


async def get_token_meta_info(chain_id: str, contract_address: str) -> dict[str, Any]:
    """Get token metadata by chain+contract."""
    client = get_skills_client()
    try:
        return await client.get(
            "/v1/public/wallet-direct/buw/wallet/dex/market/token/meta/info",
            params={"chainId": chain_id, "contractAddress": contract_address},
        )
    except BinanceSkillsError:
        return {}
    except Exception:
        return {}


async def get_token_dynamic_info(chain_id: str, contract_address: str) -> dict[str, Any]:
    """Get token dynamic metrics by chain+contract."""
    client = get_skills_client()
    try:
        return await client.get(
            "/v4/public/wallet-direct/buw/wallet/market/token/dynamic/info",
            params={"chainId": chain_id, "contractAddress": contract_address},
        )
    except BinanceSkillsError:
        return {}
    except Exception:
        return {}


async def get_token_snapshot_for_symbol(
    symbol: str,
    chain_id: str,
) -> dict[str, Any]:
    """
    Resolve a CEX symbol (e.g., SOLUSDT) to a Web3 token snapshot.

    Returns:
        {
            "found": bool,
            "chain_id": str,
            "contract_address": str,
            "symbol": str,
            "name": str,
            "price": str|float,
            "percent_change_24h": str|float,
            "volume_24h": str|float,
            "market_cap": str|float,
            "liquidity": str|float,
            "holders": str|int|None,
        }
    """
    keyword = _symbol_to_keyword(symbol)
    search = await search_tokens(keyword=keyword, chain_ids=[chain_id])
    best = _pick_best_token(symbol=symbol, candidates=search)
    if not best:
        return {"found": False, "symbol": keyword, "chain_id": chain_id}

    contract = str(best.get("contractAddress") or "")
    snapshot: dict[str, Any] = {
        "found": True,
        "symbol": best.get("symbol") or keyword,
        "name": best.get("name") or keyword,
        "chain_id": best.get("chainId") or chain_id,
        "contract_address": contract,
        "price": best.get("price"),
        "percent_change_24h": best.get("percentChange24h"),
        "volume_24h": best.get("volume24h"),
        "market_cap": best.get("marketCap"),
        "liquidity": best.get("liquidity"),
        "holders": best.get("holders"),
    }

    if contract:
        dynamic = await get_token_dynamic_info(str(snapshot["chain_id"]), contract)
        if dynamic:
            snapshot["price"] = dynamic.get("price", snapshot.get("price"))
            snapshot["percent_change_24h"] = dynamic.get(
                "percentChange24h", snapshot.get("percent_change_24h")
            )
            snapshot["volume_24h"] = dynamic.get("volume24h", snapshot.get("volume_24h"))
            snapshot["market_cap"] = dynamic.get("marketCap", snapshot.get("market_cap"))
            snapshot["liquidity"] = dynamic.get("liquidity", snapshot.get("liquidity"))

        meta = await get_token_meta_info(str(snapshot["chain_id"]), contract)
        if meta:
            snapshot["creator_address"] = meta.get("creatorAddress")
            snapshot["description"] = meta.get("description")

    return snapshot


def format_token_snapshot_for_agent(snapshot: dict[str, Any]) -> str:
    """Format token snapshot for LLM prompts."""
    if not snapshot.get("found"):
        symbol = snapshot.get("symbol", "token")
        return f"No detailed Web3 token snapshot available for {symbol}."

    lines = [
        "=== Binance Web3 Token Snapshot ===",
        f"Token: {snapshot.get('name', '?')} ({snapshot.get('symbol', '?')})",
        f"Chain: {snapshot.get('chain_id', '?')}",
        f"Price: {snapshot.get('price', 'N/A')}",
        f"24h Change: {snapshot.get('percent_change_24h', 'N/A')}%",
        f"24h Volume: {snapshot.get('volume_24h', 'N/A')}",
        f"Market Cap: {snapshot.get('market_cap', 'N/A')}",
        f"Liquidity: {snapshot.get('liquidity', 'N/A')}",
    ]
    if snapshot.get("holders") is not None:
        lines.append(f"Holders: {snapshot.get('holders')}")
    if snapshot.get("contract_address"):
        lines.append(f"Contract: {snapshot.get('contract_address')}")

    return "\n".join(lines)
