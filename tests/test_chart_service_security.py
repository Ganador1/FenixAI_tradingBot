import argparse
import json
import os
from pathlib import Path
import stat
import time

import pytest

import scripts.run_chart_service as chart_service


def test_chart_service_validates_market_identifiers():
    assert chart_service._symbol("ethusdt") == "ETHUSDT"
    assert chart_service._timeframe("15m") == "15m"

    with pytest.raises(argparse.ArgumentTypeError):
        chart_service._symbol("../../etc/passwd")
    with pytest.raises(argparse.ArgumentTypeError):
        chart_service._timeframe("0m")


def test_pid_record_requires_matching_process_identity(monkeypatch, tmp_path):
    pid_file = tmp_path / "chart.pid"
    monkeypatch.setattr(chart_service, "PID_FILE", pid_file)
    pid_file.write_text(
        json.dumps({"pid": 1234, "identity": "a" * 64}),
        encoding="utf-8",
    )

    monkeypatch.setattr(chart_service, "_process_identity", lambda pid: "b" * 64)
    assert chart_service._read_pid_record() is None

    monkeypatch.setattr(chart_service, "_process_identity", lambda pid: "a" * 64)
    assert chart_service._read_pid_record() == {"pid": 1234, "identity": "a" * 64}


def test_pid_reader_does_not_follow_symlinks(monkeypatch, tmp_path):
    target = tmp_path / "valuable"
    target.write_text(
        json.dumps({"pid": 1234, "identity": "a" * 64}),
        encoding="utf-8",
    )
    linked = tmp_path / "chart.pid"
    linked.symlink_to(target)
    monkeypatch.setattr(chart_service, "PID_FILE", linked)
    monkeypatch.setattr(chart_service, "_process_identity", lambda pid: "a" * 64)

    assert chart_service._read_pid_record() is None
    assert target.exists()


def test_write_pid_creates_private_identity_bound_record(monkeypatch, tmp_path):
    pid_file = tmp_path / "runtime_locks" / "chart.pid"
    monkeypatch.setattr(chart_service, "PID_FILE", pid_file)
    monkeypatch.setattr(chart_service, "_process_identity", lambda pid: "c" * 64)

    chart_service.write_pid()

    payload = json.loads(pid_file.read_text(encoding="utf-8"))
    assert payload == {"pid": os.getpid(), "identity": "c" * 64}
    assert stat.S_IMODE(pid_file.stat().st_mode) == 0o600


def test_chart_scheduler_rejects_untrusted_identifiers(tmp_path):
    from src.tools.chart_capture_scheduler import ChartCaptureScheduler

    with pytest.raises(ValueError, match="invalid symbol"):
        ChartCaptureScheduler(
            symbols=["../../etc/passwd"],
            timeframes=["15m"],
            cache_dir=str(tmp_path / "cache"),
        )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        ChartCaptureScheduler(
            symbols=["BTCUSDT"],
            timeframes=["0m"],
            cache_dir=str(tmp_path / "cache"),
        )


def test_chart_cache_ignores_path_traversal_from_tampered_index(tmp_path):
    from src.tools.enhanced_playwright_capture import ChartCache

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = tmp_path / "valuable"
    target.write_text("keep", encoding="utf-8")
    (cache_dir / "cache_index.json").write_text(
        json.dumps(
            {
                "0123456789abcdef": {
                    "filename": "../../valuable",
                    "timestamp": "2000-01-01T00:00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    cache = ChartCache(cache_dir=str(cache_dir), ttl_seconds=1)
    cache.clear_old_entries()

    assert cache.cache_index == {}
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_tradingview_session_state_is_private_and_symlink_safe(monkeypatch, tmp_path):
    from src.tools.tradingview_scraper import TradingViewScraper

    storage_dir = tmp_path / "tradingview"
    monkeypatch.setattr(TradingViewScraper, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(TradingViewScraper, "SESSION_FILE", storage_dir / "session.json")
    monkeypatch.setattr(TradingViewScraper, "INDICATORS_DIR", storage_dir / "indicators")
    monkeypatch.setattr(TradingViewScraper, "SCREENSHOTS_DIR", storage_dir / "screenshots")

    scraper = TradingViewScraper()

    class FakeContext:
        async def storage_state(self):
            return {"cookies": [], "origins": []}

    scraper.context = FakeContext()
    await scraper._save_session()

    assert stat.S_IMODE(scraper.SESSION_FILE.stat().st_mode) == 0o600
    assert scraper._load_private_session() == {"cookies": [], "origins": []}

    scraper.SESSION_FILE.unlink()
    target = tmp_path / "valuable"
    target.write_text("keep", encoding="utf-8")
    scraper.SESSION_FILE.symlink_to(target)

    assert scraper._load_private_session() is None
    with pytest.raises(ValueError, match="symbolic link"):
        await scraper._save_session()
    assert target.read_text(encoding="utf-8") == "keep"


def test_chart_path_security_checks_content_freshness_and_symlinks(tmp_path):
    from src.security.chart_path_security import ChartPathSecurityManager

    chart = tmp_path / "BTCUSDT_5m_chart.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 6_000)
    manager = ChartPathSecurityManager(max_age_minutes=5)

    valid, _ = manager.validate_chart_path_freshness(
        str(chart),
        "BTCUSDT",
        "5m",
    )
    assert valid is True

    linked = tmp_path / "BTCUSDT_5m_link.png"
    linked.symlink_to(chart)
    valid, message = manager.validate_chart_path_freshness(
        str(linked),
        "BTCUSDT",
        "5m",
    )
    assert valid is False
    assert "symlink" in message

    old = time.time() - 601
    os.utime(chart, (old, old))
    valid, message = manager.validate_chart_path_freshness(
        str(chart),
        "BTCUSDT",
        "5m",
    )
    assert valid is False
    assert "stale" in message


def test_chart_age_check_cannot_be_disabled_through_environment(monkeypatch, tmp_path):
    from src.security.chart_path_security import ChartPathSecurityManager

    monkeypatch.setenv("FENIX_DISABLE_CHART_AGE_CHECK", "1")
    chart = tmp_path / "ETHUSDT_15m_chart.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 6_000)
    old = time.time() - 601
    os.utime(chart, (old, old))

    valid, message = ChartPathSecurityManager(max_age_minutes=5).validate_chart_path_freshness(
        str(chart),
        "ETHUSDT",
        "15m",
    )
    assert valid is False
    assert "stale" in message
