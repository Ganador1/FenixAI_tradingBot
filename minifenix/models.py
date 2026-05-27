from dataclasses import dataclass
from typing import Literal
import time

@dataclass
class TradingRegime:
    """
    The 'Trading Regime' is the macro state published by the Brain (LLM).
    The Trigger (Fast Loop) only reads it; it never modifies it.
    """
    bias: Literal["LONG", "SHORT", "NEUTRAL"]
    confidence: float

    # Quantitative parameters the LLM sets for the Trigger to consume.
    min_ofi_required: float      # Minimum Order Flow Imbalance required to enter
    max_spread_bps: float        # Maximum spread allowed (to avoid slippage)
    z_score_threshold: float     # Statistical threshold for mean reversion

    macro_context: str           # e.g. "BTC trending up, SP500 stable"
    timestamp: float = 0.0

    def is_stale(self, max_age_seconds: int = 900) -> bool:
        """Returns True if the regime is older than max_age_seconds (default 15 min)."""
        return (time.time() - self.timestamp) > max_age_seconds
