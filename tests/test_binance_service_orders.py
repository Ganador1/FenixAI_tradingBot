# tests/test_binance_service_orders.py
"""Unit tests for new BinanceService methods: cancel_all_open_orders, get_position, validate_permissions."""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBinanceServiceOrders(unittest.TestCase):
    """Tests for order-related methods in BinanceService."""

    def setUp(self):
        """Create a BinanceService with mocked client."""
        # Mock modules needed for import using patch.dict
        self.module_patcher = patch.dict('sys.modules', {
            'binance': MagicMock(),
            'binance.client': MagicMock(),
            'binance.enums': MagicMock(),
            'binance.exceptions': MagicMock()
        })
        self.module_patcher.start()
        
        from src.services.binance_service import BinanceService
        self.service = BinanceService(api_key="test", api_secret="test", testnet=True)
        self.service._client = Mock()
        self.service._initialized = True

    def tearDown(self):
        self.module_patcher.stop()

    def test_cancel_all_open_orders_success(self):
        """Test cancel_all_open_orders calls correct Binance API."""
        self.service._client.futures_cancel_all_open_orders.return_value = {"code": 200}
        
        result = self.service.cancel_all_open_orders("BTCUSDT")
        
        self.service._client.futures_cancel_all_open_orders.assert_called_once_with(symbol="BTCUSDT")
        self.assertEqual(result, {"code": 200})

    def test_cancel_all_open_orders_no_client(self):
        """Test cancel_all_open_orders raises error when client not initialized."""
        self.service._client = None
        
        with self.assertRaisesRegex(Exception, "not initialized"):
            self.service.cancel_all_open_orders("BTCUSDT")

    def test_get_position_returns_first(self):
        """Test get_position returns first position from list."""
        self.service._client.futures_position_information.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.5"},
            {"symbol": "BTCUSDT", "positionAmt": "0.0"}
        ]
        
        result = self.service.get_position("BTCUSDT")
        
        self.service._client.futures_position_information.assert_called_once_with(symbol="BTCUSDT")
        self.assertEqual(result, {"symbol": "BTCUSDT", "positionAmt": "0.5"})

    def test_get_position_retries_transient_failure(self):
        """Transient transport failures should be retried automatically."""
        self.service._client.futures_position_information.side_effect = [
            Exception("Remote end closed connection without response"),
            [{"symbol": "BTCUSDT", "positionAmt": "0.25"}],
        ]

        with patch("src.services.binance_service.time.sleep", return_value=None):
            result = self.service.get_position("BTCUSDT")

        self.assertEqual(result, {"symbol": "BTCUSDT", "positionAmt": "0.25"})
        self.assertEqual(self.service._client.futures_position_information.call_count, 2)

    def test_get_position_empty_returns_dict(self):
        """Test get_position returns empty dict when no positions."""
        self.service._client.futures_position_information.return_value = []
        
        result = self.service.get_position("BTCUSDT")
        
        self.assertEqual(result, {})

    def test_get_position_no_client(self):
        """Test get_position raises error when client not initialized."""
        self.service._client = None
        
        with self.assertRaisesRegex(Exception, "not initialized"):
            self.service.get_position("BTCUSDT")

    def test_get_balance_usdt_prefers_total_margin_balance_over_available(self):
        """Risk sizing/drawdown should use futures equity, not free collateral."""
        self.service._client.futures_account.return_value = {
            "totalMarginBalance": "250.5",
            "availableBalance": "180.0",
            "assets": [
                {"asset": "USDC", "marginBalance": "250.5", "availableBalance": "180.0"},
            ],
        }

        result = self.service.get_balance_usdt()

        self.assertEqual(result, 250.5)

    def test_get_balance_usdt_uses_asset_margin_balance_before_available(self):
        """USDC-margined accounts may report 0 totalMarginBalance but non-zero asset margin."""
        self.service._client.futures_account.return_value = {
            "totalMarginBalance": "0",
            "availableBalance": "180.0",
            "assets": [
                {
                    "asset": "USDC",
                    "marginBalance": "229.4",
                    "walletBalance": "229.4",
                    "availableBalance": "180.0",
                },
            ],
        }

        result = self.service.get_balance_usdt()

        self.assertEqual(result, 229.4)

    def test_get_balance_usdt_retries_transient_account_failure(self):
        self.service._client.futures_account.side_effect = [
            Exception("Connection aborted by peer"),
            {
                "totalMarginBalance": "111.7",
                "assets": [{"asset": "USDC", "marginBalance": "111.7"}],
            },
        ]

        with patch("src.services.binance_service.time.sleep", return_value=None):
            result = self.service.get_balance_usdt()

        self.assertEqual(result, 111.7)
        self.assertEqual(self.service._client.futures_account.call_count, 2)

    def test_validate_permissions_success(self):
        """Test validate_permissions returns True when canTrade is True."""
        self.service.get_account_info = Mock(return_value={"canTrade": True})
        
        is_valid, errors = self.service.validate_permissions()
        
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_validate_permissions_no_trade_permission(self):
        """Test validate_permissions returns error when canTrade is False."""
        self.service.get_account_info = Mock(return_value={"canTrade": False})
        
        is_valid, errors = self.service.validate_permissions()
        
        self.assertFalse(is_valid)
        self.assertIn("trading permission", errors[0])

    def test_validate_permissions_no_client(self):
        """Test validate_permissions handles missing client."""
        self.service._client = None
        
        is_valid, errors = self.service.validate_permissions()
        
        self.assertFalse(is_valid)
        self.assertIn("not initialized", errors[0])

if __name__ == '__main__':
    unittest.main()
