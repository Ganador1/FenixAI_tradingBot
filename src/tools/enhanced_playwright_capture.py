#!/usr/bin/env python3

"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ENHANCED PLAYWRIGHT CHART CAPTURE SYSTEM                                ║
║  Improved TradingView chart capture with better reliability and quality  ║
╚══════════════════════════════════════════════════════════════════════════╝

Features:
  • Better wait conditions for chart rendering
  • Screenshot quality optimization
  • Comprehensive error handling
  • Retry logic for transient failures
  • Caching for recently captured charts
  • Multiple browser support (Chromium, Firefox, WebKit)
  • Performance monitoring
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.security.private_files import (
    ensure_private_directory,
    read_private_text,
    write_private_bytes,
    write_private_text,
)

try:
    from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Session file for TradingView authentication
SESSION_FILE = Path("tradingview_session_state.json")

logger = logging.getLogger(__name__)


class ChartCache:
    """Cache for recently captured charts"""

    def __init__(self, cache_dir: str = "cache/charts", ttl_seconds: int = 180):
        self.cache_dir = Path(cache_dir)
        ensure_private_directory(self.cache_dir)
        self.ttl_seconds = int(ttl_seconds)
        if not 1 <= self.ttl_seconds <= 86_400:
            raise ValueError("cache TTL must be between 1 and 86400 seconds")
        self.cache_index_file = self.cache_dir / "cache_index.json"
        self.cache_index = self._load_index()

    def _load_index(self) -> dict:
        """Load cache index"""
        if self.cache_index_file.exists():
            try:
                raw_index = json.loads(
                    read_private_text(self.cache_index_file, max_bytes=1_000_000)
                )
                if not isinstance(raw_index, dict) or len(raw_index) > 1_000:
                    raise ValueError("cache index is invalid or unbounded")
                safe_index = {}
                for key, entry in raw_index.items():
                    if (
                        not re.fullmatch(r"[0-9a-f]{16}", str(key))
                        or not isinstance(entry, dict)
                        or entry.get("filename") != f"chart_{key}.txt"
                        or not isinstance(entry.get("timestamp"), str)
                    ):
                        continue
                    datetime.fromisoformat(entry["timestamp"])
                    safe_index[str(key)] = entry
                return safe_index
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Could not load chart cache index securely")
        return {}

    def _save_index(self):
        """Save cache index"""
        try:
            write_private_text(
                self.cache_index_file,
                json.dumps(self.cache_index, indent=2, allow_nan=False) + "\n",
            )
        except (OSError, TypeError, ValueError):
            logger.warning("Could not save chart cache index securely")

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        """Generate cache key"""
        key_str = f"{symbol}_{timeframe}"
        return hashlib.sha256(key_str.encode()).hexdigest()[:16]  # Use SHA256 instead of MD5

    def get(self, symbol: str, timeframe: str) -> str | None:
        """Get cached chart if available and fresh"""
        cache_key = self._get_cache_key(symbol, timeframe)

        if cache_key in self.cache_index:
            entry = self.cache_index[cache_key]
            cached_time = datetime.fromisoformat(entry['timestamp'])

            # Check if still fresh
            if datetime.now() - cached_time < timedelta(seconds=self.ttl_seconds):
                cache_file = self.cache_dir / entry['filename']

                if cache_file.exists():
                    try:
                        cached_data = read_private_text(cache_file, max_bytes=20_000_000)

                        logger.info(f"✅ Cache HIT for {symbol} {timeframe} (age: {(datetime.now() - cached_time).seconds}s)")
                        return cached_data
                    except (OSError, TypeError, ValueError):
                        logger.warning("Error reading chart cache securely")

        logger.info(f"❌ Cache MISS for {symbol} {timeframe}")
        return None

    def set(self, symbol: str, timeframe: str, image_b64: str):
        """Cache a chart capture"""
        if not isinstance(image_b64, str) or len(image_b64) > 20_000_000:
            raise ValueError("chart cache payload is invalid or exceeds 20 MB")
        cache_key = self._get_cache_key(symbol, timeframe)
        filename = f"chart_{cache_key}.txt"
        cache_file = self.cache_dir / filename

        try:
            write_private_text(cache_file, image_b64)

            # Update index
            self.cache_index[cache_key] = {
                'symbol': symbol,
                'timeframe': timeframe,
                'filename': filename,
                'timestamp': datetime.now().isoformat(),
                'size': len(image_b64)
            }

            self._save_index()
            logger.info(f"💾 Cached chart for {symbol} {timeframe}")

        except (OSError, TypeError, ValueError):
            logger.warning("Could not cache chart securely")

    def clear_old_entries(self):
        """Remove expired cache entries"""
        now = datetime.now()
        expired_keys = []

        for cache_key, entry in self.cache_index.items():
            cached_time = datetime.fromisoformat(entry['timestamp'])
            if now - cached_time > timedelta(seconds=self.ttl_seconds):
                expired_keys.append(cache_key)

                # Delete file
                cache_file = self.cache_dir / f"chart_{cache_key}.txt"
                if cache_file.exists() and not cache_file.is_symlink():
                    cache_file.unlink()

        # Remove from index
        for key in expired_keys:
            del self.cache_index[key]

        if expired_keys:
            self._save_index()
            logger.info(f"🗑️ Cleared {len(expired_keys)} expired cache entries")


class EnhancedPlaywrightCapture:
    """Enhanced chart capture with Playwright"""

    def __init__(self, 
                 headless: bool = True,
                 browser_type: str = "chromium",
                 enable_cache: bool = True,
                 cache_ttl: int = 180):
        """
        Initialize capture system

        Args:
            headless: Run browser in headless mode
            browser_type: Browser to use (chromium, firefox, webkit)
            enable_cache: Enable chart caching
            cache_ttl: Cache TTL in seconds
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright not available. Install with: pip install playwright && playwright install")

        self.headless = headless
        self.browser_type = browser_type
        self.playwright = None
        self.browser = None
        self.context = None

        # Cache system
        self.enable_cache = enable_cache
        self.cache = ChartCache(ttl_seconds=cache_ttl) if enable_cache else None

        # Performance metrics
        self.metrics = {
            'total_captures': 0,
            'successful_captures': 0,
            'failed_captures': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'avg_capture_time': 0,
            'total_capture_time': 0
        }

    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

    async def initialize(self):
        """Initialize Playwright and browser"""
        if self.playwright:
            return  # Already initialized

        logger.info(f"🚀 Initializing Playwright with {self.browser_type}...")

        self.playwright = await async_playwright().start()

        # Select browser
        if self.browser_type == "chromium":
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
        elif self.browser_type == "firefox":
            self.browser = await self.playwright.firefox.launch(headless=self.headless)
        elif self.browser_type == "webkit":
            self.browser = await self.playwright.webkit.launch(headless=self.headless)
        else:
            raise ValueError(f"Unknown browser type: {self.browser_type}")

        # Create context with optimal settings
        context_params = {
            'viewport': {'width': 1920, 'height': 1080},
            'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'locale': 'en-US'
        }

        # Load TradingView session if available
        if SESSION_FILE.exists():
            try:
                storage_state = json.loads(
                    read_private_text(
                        SESSION_FILE,
                        max_bytes=1_000_000,
                        require_owner_private=True,
                    )
                )
                if not isinstance(storage_state, dict):
                    raise ValueError("browser session must be a JSON object")
                logger.info("🔄 Using saved TradingView session")
                self.context = await self.browser.new_context(storage_state=storage_state, **context_params)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("⚠️ Error loading the private session. Using clean context.")
                self.context = await self.browser.new_context(**context_params)
        else:
            logger.warning(f"⚠️ Session file not found: {SESSION_FILE}. Chart may require login.")
            self.context = await self.browser.new_context(**context_params)

        logger.info(f"✅ {self.browser_type.title()} browser initialized")

    async def close(self):
        """Close browser and Playwright"""
        if self.context:
            await self.context.close()

        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()

        logger.info("🔒 Browser closed")

    async def capture_chart(self, 
                          symbol: str = "SOLUSDT", 
                          timeframe: str = "1",
                          max_retries: int = 3,
                          required_indicators: list[str] | None = None) -> str | None:
        """
        Capture chart with retry logic

        Args:
            symbol: Trading symbol (e.g., "SOLUSDT")
            timeframe: Chart timeframe (e.g., "1", "5", "15", "1H")
            max_retries: Maximum number of retry attempts

        Returns:
            Base64 encoded PNG image or None on failure
        """
        self.metrics['total_captures'] += 1

        # Check cache first
        if self.enable_cache and self.cache:
            cached = self.cache.get(symbol, timeframe)
            if cached:
                self.metrics['cache_hits'] += 1
                self.metrics['successful_captures'] += 1
                return cached

            self.metrics['cache_misses'] += 1

        # Try capture with retries
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"📊 Capturing {symbol} {timeframe} (attempt {attempt}/{max_retries})...")

                start_time = time.time()
                result = await self._capture_chart_once(symbol, timeframe, required_indicators)
                capture_time = time.time() - start_time

                # Update metrics
                self.metrics['total_capture_time'] += capture_time
                self.metrics['avg_capture_time'] = (
                    self.metrics['total_capture_time'] / 
                    (self.metrics['successful_captures'] + 1)
                )

                if result:
                    self.metrics['successful_captures'] += 1

                    # Cache the result
                    if self.enable_cache and self.cache:
                        self.cache.set(symbol, timeframe, result)

                    logger.info(f"✅ Chart captured successfully in {capture_time:.2f}s")
                    return result

            except Exception as e:
                logger.warning(f"❌ Capture attempt {attempt} failed: {e}")

                if attempt < max_retries:
                    wait_time = attempt * 2  # Exponential backoff
                    logger.info(f"⏳ Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)

        # All retries failed
        self.metrics['failed_captures'] += 1
        logger.error(f"❌ Failed to capture {symbol} {timeframe} after {max_retries} attempts")
        return None

    async def _capture_chart_once(self, symbol: str, timeframe: str, required_indicators: list[str] | None) -> str | None:
        """Single capture attempt"""
        if not self.context:
            await self.initialize()

        page = await self.context.new_page()

        try:
            # Build URL
            url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}&interval={timeframe}"

            # Navigate to chart
            logger.info("🌐 Loading TradingView chart...")
            await page.goto(url, wait_until='networkidle', timeout=30000)

            # Wait for chart to be visible
            await self._wait_for_chart(page)

            # Ensure required indicators are present on legend to avoid missing overlays
            if required_indicators:
                if not await self._ensure_required_indicators(page, required_indicators):
                    logger.error("❌ Required indicators missing on chart legend: %s", ", ".join(required_indicators))
                    return None

            # Close popups/ads
            await self._dismiss_dialogs(page)

            # Hide unnecessary UI elements
            await self._clean_interface(page)

            # Wait for final rendering
            await asyncio.sleep(2)

            # Take screenshot
            logger.info("📸 Taking screenshot...")
            screenshot_bytes = await page.screenshot(
                type='png',
                full_page=False,
                clip=None  # Full viewport
            )

            # Convert to base64
            img_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')

            # Validate size
            if len(screenshot_bytes) < 1000:  # Less than 1KB is suspicious
                logger.warning("⚠️ Screenshot suspiciously small")
                return None

            return img_b64

        except PlaywrightTimeout:
            logger.error("⏱️ Timeout waiting for chart to load")
            return None

        except Exception as e:
            logger.error(f"❌ Error during capture: {e}")
            return None

        finally:
            await page.close()

    async def _wait_for_chart(self, page: Page, timeout: int = 20000):
        """Wait for chart to be fully loaded"""
        # Wait for multiple indicators that chart is ready
        selectors = [
            'canvas',
            '.chart-container',
            '[data-name="legend-source-item"]'
        ]

        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=timeout, state='visible')
                logger.info(f"✓ Found: {selector}")
                return  # Success
            except Exception:
                continue

        # If no selector worked, just wait a bit
        logger.warning("⚠️ Could not detect chart elements, waiting anyway...")
        await asyncio.sleep(5)

    async def _ensure_required_indicators(self, page: Page, required_indicators: list[str]) -> bool:
        """Check legend text to confirm required indicators are present"""
        try:
            legend_items = await page.eval_on_selector_all(
                '[data-name="legend-source-item"], .item-legend', 'elements => elements.map(e => e.innerText || "")'
            )
        except Exception:
            legend_items = []

        legend_text = " ".join(legend_items).lower()
        missing = [ind for ind in required_indicators if ind.lower() not in legend_text]
        if missing:
            logger.warning("⚠️ Missing indicators on legend: %s", ", ".join(missing))
            return False
        return True

    async def _dismiss_dialogs(self, page: Page):
        """Dismiss popups, ads, and dialogs"""
        # Common selectors for TradingView popups
        close_selectors = [
            'button[aria-label="Close"]',
            '.tv-dialog__close',
            '.close-button',
            '[data-name="close"]',
            'button:has-text("Close")',
            'button:has-text("No thanks")',
            'button:has-text("Maybe later")'
        ]

        for selector in close_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    await element.click(timeout=1000)
                    logger.info(f"✓ Closed popup: {selector}")
            except Exception:
                continue

    async def _clean_interface(self, page: Page):
        """Hide unnecessary UI elements for cleaner screenshot"""
        try:
            await page.evaluate("""
                () => {
                    // Hide common UI elements
                    const hideSelectors = [
                        '.tv-header',
                        '.tv-header__area--left',
                        '.tv-header__area--right',
                        '.tv-floating-toolbar',
                        '.tv-toast-logger',
                        '.tv-screener-popup',
                        '.tv-dialog',
                        '[data-name="header"]',
                        '.header-chart-panel',
                        '.layout__area--top',
                        '.banner',
                        '.ad-container'
                    ];

                    hideSelectors.forEach(selector => {
                        const elements = document.querySelectorAll(selector);
                        elements.forEach(el => {
                            if (el) el.style.display = 'none';
                        });
                    });

                    // Maximize chart area
                    const chartArea = document.querySelector('.layout__area--center, .chart-container');
                    if (chartArea) {
                        chartArea.style.width = '100%';
                        chartArea.style.height = '100%';
                    }
                }
            """)
            logger.info("✓ Interface cleaned")
        except Exception as e:
            logger.warning(f"Could not clean interface: {e}")

    async def capture_multiple_timeframes(self, 
                                         symbol: str, 
                                         timeframes: list[str] = None) -> dict[str, str | None]:
        """
        Capture charts for multiple timeframes

        Args:
            symbol: Trading symbol
            timeframes: List of timeframes (default: ["1", "5", "15", "1H"])

        Returns:
            Dict mapping timeframe to base64 image
        """
        if timeframes is None:
            timeframes = ["1", "5", "15", "1H"]

        results = {}

        for tf in timeframes:
            logger.info(f"📊 Capturing {symbol} - {tf}")
            result = await self.capture_chart(symbol, tf)
            results[tf] = result

            # Brief pause between captures
            if result:
                await asyncio.sleep(1)

        return results

    def get_metrics(self) -> dict:
        """Get performance metrics"""
        success_rate = 0
        if self.metrics['total_captures'] > 0:
            success_rate = (
                self.metrics['successful_captures'] / 
                self.metrics['total_captures'] * 100
            )

        cache_hit_rate = 0
        total_cache_ops = self.metrics['cache_hits'] + self.metrics['cache_misses']
        if total_cache_ops > 0:
            cache_hit_rate = self.metrics['cache_hits'] / total_cache_ops * 100

        return {
            **self.metrics,
            'success_rate': success_rate,
            'cache_hit_rate': cache_hit_rate
        }

    def print_metrics(self):
        """Print performance metrics"""
        metrics = self.get_metrics()

        print("\n" + "="*60)
        print("📊 CHART CAPTURE METRICS")
        print("="*60)
        print(f"Total Captures: {metrics['total_captures']}")
        print(f"Successful: {metrics['successful_captures']}")
        print(f"Failed: {metrics['failed_captures']}")
        print(f"Success Rate: {metrics['success_rate']:.1f}%")
        print(f"Avg Capture Time: {metrics['avg_capture_time']:.2f}s")

        if self.enable_cache:
            print("\nCache Performance:")
            print(f"Cache Hits: {metrics['cache_hits']}")
            print(f"Cache Misses: {metrics['cache_misses']}")
            print(f"Cache Hit Rate: {metrics['cache_hit_rate']:.1f}%")

        print("="*60)


# Convenience functions

async def capture_chart_async(symbol: str = "SOLUSDT", 
                             timeframe: str = "1",
                             browser_type: str = "chromium",
                             required_indicators: list[str] | None = None) -> str | None:
    """
    Async convenience function to capture a single chart

    Args:
        symbol: Trading symbol
        timeframe: Chart timeframe
        browser_type: Browser to use

    Returns:
        Base64 encoded PNG or None
    """
    async with EnhancedPlaywrightCapture(browser_type=browser_type) as capture:
        return await capture.capture_chart(symbol, timeframe, required_indicators=required_indicators)


def capture_chart_sync(symbol: str = "SOLUSDT", 
                       timeframe: str = "1",
                       browser_type: str = "chromium",
                       required_indicators: list[str] | None = None) -> str | None:
    """
    Synchronous wrapper for chart capture

    Args:
        symbol: Trading symbol
        timeframe: Chart timeframe
        browser_type: Browser to use

    Returns:
        Base64 encoded PNG or None
    """
    return asyncio.run(capture_chart_async(symbol, timeframe, browser_type, required_indicators=required_indicators))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    async def test_capture():
        """Test the capture system"""
        print("🧪 Testing Enhanced Playwright Chart Capture")
        print("="*60)

        async with EnhancedPlaywrightCapture(headless=True, enable_cache=True) as capture:
            # Test single capture
            print("\n1️⃣ Testing single capture...")
            result1 = await capture.capture_chart("SOLUSDT", "1")

            if result1:
                print(f"✅ Capture successful! Size: {len(result1)} chars")

                # Save to file for verification
                output_file = Path("test_chart_capture.png")
                img_bytes = base64.b64decode(result1)
                write_private_bytes(output_file, img_bytes)
                print(f"💾 Saved to: {output_file}")
            else:
                print("❌ Capture failed!")

            # Test cache (should hit cache)
            print("\n2️⃣ Testing cache...")
            result2 = await capture.capture_chart("SOLUSDT", "1")

            if result2:
                print("✅ Cache test successful!")

            # Test multiple timeframes
            print("\n3️⃣ Testing multiple timeframes...")
            results = await capture.capture_multiple_timeframes("BTCUSDT", ["1", "5", "15"])

            successful = sum(1 for r in results.values() if r)
            print(f"✅ Captured {successful}/{len(results)} timeframes")

            # Print metrics
            capture.print_metrics()

    # Run test
    asyncio.run(test_capture())
