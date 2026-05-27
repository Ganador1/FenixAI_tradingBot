# src/core/orchestrator/state.py
"""
Shared state definition for Fenix Trading Bot LangGraph orchestrator.

FenixAgentState is the TypedDict that flows through all graph nodes.
Merge/append reducers allow parallel agents to write to the same keys.
"""

from typing import Annotated, Any, TypedDict


def merge_dicts(a: dict, b: dict) -> dict:
    """Merges two dictionaries (for execution_times)."""
    return {**a, **b}


def append_lists(a: list, b: list) -> list:
    """Concatenates two lists (for errors and messages)."""
    return a + b


class FenixAgentState(TypedDict, total=False):
    """Shared state between all graph agents."""

    # Identifiers
    symbol: str
    timeframe: str
    timestamp: str

    # Market Data
    kline_data: dict[str, list]
    current_price: float
    current_volume: float
    account_balance_usdt: float

    # Technical Indicators
    indicators: dict[str, Any]
    mtf_context: dict[str, Any]

    # Microstructure
    obi: float
    cvd: float
    mid_price: float
    microprice: float
    microprice_bps: float
    spread: float
    spread_pct: float
    tob_liquidity: float
    ofi: float
    ofi_norm: float
    qi: float
    mlofi: float
    mlofi_norm: float
    volume_imbalance: float
    wdi: float
    liquidity_gap_pct: float
    vpin_proxy: float
    trade_imbalance_5s: float
    trade_volume_5s: float
    trade_count_5s: int
    trade_buy_vol_5s: float
    trade_sell_vol_5s: float
    cvd_delta_5s: float
    recent_trades_5s: list[dict[str, Any]]
    trade_intensity_5s: float
    avg_trade_size_5s: float
    orderbook_depth: dict[str, float]

    # Generated Chart
    chart_image_b64: str | None
    chart_indicators_summary: dict[str, Any]

    # News data for sentiment agent
    news_data: list[dict[str, Any]]
    # Social data & metrics (Twitter/Reddit/fear_greed)
    social_data: dict[str, Any]
    fear_greed_value: str | None

    # Agent Results (each writes to its own field)
    technical_report: dict[str, Any]
    sentiment_report: dict[str, Any]
    visual_report: dict[str, Any]
    qabba_report: dict[str, Any]
    web3_intel_report: dict[str, Any]  # Binance Skills Hub Web3 Intelligence

    # Decision and Risk
    decision_report: dict[str, Any]
    risk_assessment: dict[str, Any]
    final_trade_decision: dict[str, Any]
    judge_verdict: dict[str, Any]

    # Metadata - Using Annotated to allow multiple writes
    messages: Annotated[list[Any], append_lists]
    errors: Annotated[list[str], append_lists]
    execution_times: Annotated[dict[str, float], merge_dicts]
