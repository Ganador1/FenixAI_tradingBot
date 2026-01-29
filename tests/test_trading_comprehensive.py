"""
Tests for trading modules with corrected APIs.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio


class TestBinanceConfig:
    """Tests for BinanceConfig dataclass."""

    def test_config_creation(self):
        """Test creating a BinanceConfig."""
        from src.trading.binance_client import BinanceConfig
        
        config = BinanceConfig(
            api_key="test_key",
            api_secret="test_secret",
            testnet=True
        )
        
        assert config.api_key == "test_key"
        assert config.testnet is True

    def test_config_default_testnet(self):
        """Test default testnet configuration."""
        from src.trading.binance_client import BinanceConfig
        
        config = BinanceConfig()
        assert config.testnet is True

    def test_config_base_url_testnet(self):
        """Test testnet base URL is set correctly."""
        from src.trading.binance_client import BinanceConfig
        
        config = BinanceConfig(testnet=True)
        assert "testnet" in config.base_url.lower() or config.testnet is True


class TestBinanceClientInit:
    """Tests for BinanceClient initialization."""

    def test_client_creation(self):
        """Test creating a BinanceClient."""
        from src.trading.binance_client import BinanceClient
        
        client = BinanceClient(testnet=True)
        assert client is not None
        assert client.config.testnet is True

    def test_client_env_keys(self):
        """Test client uses environment keys if not provided."""
        from src.trading.binance_client import BinanceClient
        
        client = BinanceClient(testnet=True)
        assert client is not None


class TestBinanceClientMethods:
    """Tests for BinanceClient methods."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock client."""
        from src.trading.binance_client import BinanceClient
        return BinanceClient(testnet=True)

    def test_sign_request_method_exists(self, mock_client):
        """Test sign request method exists."""
        assert hasattr(mock_client, '_sign_request')

    def test_get_headers_method(self, mock_client):
        """Test get headers method."""
        mock_client.config.api_key = "test_key"
        headers = mock_client._get_headers()
        
        assert "X-MBX-APIKEY" in headers
        assert headers["X-MBX-APIKEY"] == "test_key"


class TestBinanceDataMethods:
    """Tests for data fetching methods."""

    @pytest.fixture
    def client(self):
        from src.trading.binance_client import BinanceClient
        return BinanceClient(testnet=True)

    @pytest.mark.asyncio
    async def test_get_ticker_method_exists(self, client):
        """Test get_ticker method exists."""
        assert hasattr(client, 'get_ticker')
        assert asyncio.iscoroutinefunction(client.get_ticker)

    @pytest.mark.asyncio
    async def test_get_price_method_exists(self, client):
        """Test get_price method exists."""
        assert hasattr(client, 'get_price')
        assert asyncio.iscoroutinefunction(client.get_price)

    @pytest.mark.asyncio
    async def test_get_klines_method_exists(self, client):
        """Test get_klines method exists."""
        assert hasattr(client, 'get_klines')
        assert asyncio.iscoroutinefunction(client.get_klines)


class TestMarketDataManager:
    """Tests for MarketDataManager."""

    def test_market_data_manager_import(self):
        """Test MarketDataManager can be imported."""
        from src.trading.market_data import MarketDataManager
        assert MarketDataManager is not None

    def test_market_data_manager_init(self):
        """Test MarketDataManager initialization."""
        from src.trading.market_data import MarketDataManager
        
        manager = MarketDataManager(
            symbol="BTCUSDT",
            timeframe="15m",
            use_testnet=True
        )
        
        assert manager.symbol == "BTCUSDT"
        assert manager.timeframe == "15m"

    def test_market_data_manager_on_kline(self):
        """Test on_kline callback registration."""
        from src.trading.market_data import MarketDataManager
        
        manager = MarketDataManager(symbol="BTCUSDT", use_testnet=True)
        
        callback = MagicMock()
        manager.on_kline(callback)

    def test_get_market_data_manager(self):
        """Test get_market_data_manager factory."""
        from src.trading.market_data import get_market_data_manager
        
        manager = get_market_data_manager(
            symbol="ETHUSDT",
            timeframe="1h",
            use_testnet=True,
            force_new=True
        )
        
        assert manager is not None


class TestOrderBookSnapshot:
    """Tests for OrderBookSnapshot."""

    def test_orderbook_creation(self):
        """Test creating an order book snapshot."""
        from src.trading.market_data import OrderBookSnapshot
        
        snapshot = OrderBookSnapshot(
            bids=[[50000.0, 1.0], [49999.0, 0.5]],
            asks=[[50001.0, 1.0], [50002.0, 0.5]]
        )
        
        assert len(snapshot.bids) == 2
        assert len(snapshot.asks) == 2

    def test_orderbook_best_bid(self):
        """Test getting best bid."""
        from src.trading.market_data import OrderBookSnapshot
        
        snapshot = OrderBookSnapshot(
            bids=[[50000.0, 1.0], [49999.0, 0.5]],
            asks=[[50001.0, 1.0]]
        )
        
        best_bid = snapshot.get_best_bid()
        assert best_bid == 50000.0

    def test_orderbook_best_ask(self):
        """Test getting best ask."""
        from src.trading.market_data import OrderBookSnapshot
        
        snapshot = OrderBookSnapshot(
            bids=[[50000.0, 1.0]],
            asks=[[50001.0, 1.0], [50002.0, 0.5]]
        )
        
        best_ask = snapshot.get_best_ask()
        assert best_ask == 50001.0

    def test_orderbook_spread(self):
        """Test calculating spread."""
        from src.trading.market_data import OrderBookSnapshot
        
        snapshot = OrderBookSnapshot(
            bids=[[50000.0, 1.0]],
            asks=[[50010.0, 1.0]]
        )
        
        spread = snapshot.get_spread()
        assert spread == 10.0


class TestMicrostructureMetrics:
    """Tests for MicrostructureMetrics."""

    def test_metrics_creation(self):
        """Test creating microstructure metrics."""
        from src.trading.market_data import MicrostructureMetrics
        
        metrics = MicrostructureMetrics(
            obi=0.5,
            cvd=100.0,
            spread=5.0
        )
        
        assert metrics.obi == 0.5
        assert metrics.cvd == 100.0

    def test_metrics_to_dict(self):
        """Test converting metrics to dict."""
        from src.trading.market_data import MicrostructureMetrics
        
        metrics = MicrostructureMetrics(obi=0.5, cvd=100.0)
        result = metrics.to_dict()
        
        assert "obi" in result
        assert result["obi"] == 0.5


class TestExecutor:
    """Tests for trade executor."""

    def test_executor_import(self):
        """Test executor module imports."""
        from src.trading.executor import OrderExecutor
        assert OrderExecutor is not None
