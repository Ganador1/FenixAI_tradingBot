import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.binance_service import get_binance_service
from src.trading.executor import OrderExecutor


def verify():
    # Force use of env vars for key/secret if needed, or rely on what's set
    # Using live mode (testnet=False) to check SOLUSDT on production
    try:
        service = get_binance_service(testnet=False)
        if not service.initialize():
            print("Failed to initialize service")
            return

        symbol = "SOLUSDT"
        print(f"Checking {symbol}...")

        # Check raw config
        price_prec, qty_prec = service.get_symbol_precision(symbol)
        print(f"Service reports: Price Precision={price_prec}, Qty Precision={qty_prec}")

        # Check executor
        executor = OrderExecutor(symbol=symbol, testnet=False, timeframe="15m")

        # Test formatting
        test_qty = 9.71456
        formatted = executor.format_quantity(test_qty)
        print(f"Executor formats {test_qty} -> {formatted}")

        # Check if precision was updated
        print(f"Executor internal Qty Precision: {executor.qty_precision}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    verify()
