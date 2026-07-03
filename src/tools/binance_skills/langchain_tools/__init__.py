"""
LangChain specific tool wrappers for Binance Web3 Skills.
This allows seamless integration with the FenixAI LangGraph orchestrator.
"""

from .smart_money_tools import lctool_get_smart_money_signals

__all__ = [
    "lctool_get_smart_money_signals",
]
