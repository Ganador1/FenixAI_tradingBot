from __future__ import annotations

import pytest

from scripts.testnet_order_lifecycle_smoke import _select_testnet_credentials


def test_testnet_smoke_requires_dedicated_testnet_credentials(monkeypatch):
    for name in (
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "BINANCE_TESTNET_API_KEY_1",
        "BINANCE_TESTNET_API_SECRET_1",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Missing Binance Testnet credentials"):
        _select_testnet_credentials(1)


def test_testnet_smoke_selects_numbered_credentials(monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY_2", "test-key-two")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET_2", "test-secret-two")

    assert _select_testnet_credentials(2) == ("test-key-two", "test-secret-two")
