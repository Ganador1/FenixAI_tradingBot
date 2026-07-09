"""Tests for deterministic position sizing.

Regression coverage for the 2026-07-05 losing streak, where the LLM-decided
notional varied 163× between consecutive candles ($10.73 on a winning setup
that got skipped as sub-min, vs $1748.53 on a losing one right after 3 losses).
The notional is now derived from balance × risk% × leverage and the LLM value
is treated as a ratio clamped to [min, max].
"""

import pytest

from src.trading.engine import _deterministic_notional


# base_notional example: balance 534 * risk 0.02 * leverage 10 = 106.8
BASE = 106.8
MIN, MAX = 0.5, 1.5


def test_oversized_llm_request_is_clamped_down():
    """LLM asks $1748.53 (16.4× base) -> clamped to 1.5× base."""
    notional, ratio, clamped = _deterministic_notional(1748.53, BASE, MIN, MAX)
    assert clamped == pytest.approx(MAX)
    assert notional == pytest.approx(BASE * MAX)
    assert ratio > MAX


def test_undersized_llm_request_is_clamped_up():
    """LLM asks $10.73 (0.1× base) -> clamped to 0.5× base, above min notional."""
    notional, ratio, clamped = _deterministic_notional(10.73, BASE, MIN, MAX)
    assert clamped == pytest.approx(MIN)
    assert notional == pytest.approx(BASE * MIN)
    assert notional > 20.0  # would have been skipped as sub-min before


def test_reasonable_llm_request_passes_through():
    """LLM asks near base -> ratio inside band, notional unchanged."""
    notional, ratio, clamped = _deterministic_notional(BASE, BASE, MIN, MAX)
    assert clamped == pytest.approx(1.0)
    assert notional == pytest.approx(BASE)


def test_no_llm_value_defaults_to_base():
    for empty in (None, 0.0):
        notional, ratio, clamped = _deterministic_notional(empty, BASE, MIN, MAX)
        assert notional == pytest.approx(BASE)
        assert clamped == pytest.approx(1.0)


def test_zero_base_notional_is_safe():
    notional, ratio, clamped = _deterministic_notional(500.0, 0.0, MIN, MAX)
    # No base to scale against -> ratio stays 1.0, notional stays 0 (caller aborts)
    assert notional == pytest.approx(0.0)
    assert clamped == pytest.approx(1.0)


def test_custom_bounds_are_respected():
    notional, ratio, clamped = _deterministic_notional(1000.0, BASE, 0.25, 3.0)
    assert clamped == pytest.approx(3.0)
    assert notional == pytest.approx(BASE * 3.0)


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
