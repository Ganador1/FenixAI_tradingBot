from __future__ import annotations

import asyncio
import json

import pytest

from src.trading.operational_audit import (
    OperationalAudit,
    _alert_stale_heartbeat,
    read_runtime_instances,
)


def test_operational_audit_writes_instance_heartbeat_and_durable_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("FENIX_OPERATIONAL_STATE_DIR", str(tmp_path))
    audit = OperationalAudit(
        project_root=tmp_path,
        symbol="SOLUSDT",
        timeframe="15m",
        paper_trading=False,
        allow_live_trading=True,
        instance_id="sol live / primary",
    )

    audit.write_heartbeat(
        status="running",
        tracked_position={"side": "SHORT", "quantity": 1.15, "entry_price": 77.92},
    )
    audit.append_ledger_record(
        {
            "record_type": "position_closed",
            "trade_id": "entry-123",
            "exchange_fill_reconciled": True,
            "exit_fills": [{"order_id": "exit-456", "commission": 0.01}],
        }
    )

    instances = read_runtime_instances(freshness_seconds=20)
    assert len(instances) == 1
    assert instances[0]["instance_id"] == "sol-live-primary"
    assert instances[0]["fresh"] is True
    assert instances[0]["tracked_position"]["quantity"] == 1.15

    records = [
        json.loads(line)
        for line in audit.ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["record_type"] == "position_closed"
    assert records[0]["instance_id"] == "sol-live-primary"
    assert records[0]["exit_fills"][0]["order_id"] == "exit-456"


def test_runtime_instances_marks_expired_heartbeat_as_not_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FENIX_OPERATIONAL_STATE_DIR", str(tmp_path))
    audit = OperationalAudit(
        project_root=tmp_path,
        symbol="ETHUSDC",
        timeframe="15m",
        paper_trading=False,
        allow_live_trading=True,
        instance_id="eth-live",
    )
    audit.write_heartbeat(status="running")

    instances = read_runtime_instances(freshness_seconds=0.000001)

    assert len(instances) == 1
    assert instances[0]["fresh"] is False


def test_alert_stale_heartbeat_without_running_loop_is_safe():
    """Outside an event loop the alert is skipped observably, never raised."""
    _alert_stale_heartbeat({"instance_id": "eth-live", "symbol": "ETHUSDC"}, 120.0)


@pytest.mark.asyncio
async def test_alert_stale_heartbeat_schedules_alert(monkeypatch):
    import src.risk.safety_alerts as safety_alerts

    calls: list[tuple[str, dict | None]] = []

    async def record_alert(event_type, message, context=None):
        calls.append((event_type, context))
        return True

    monkeypatch.setattr(safety_alerts, "alert_safety_event", record_alert)

    _alert_stale_heartbeat({"instance_id": "eth-live", "symbol": "ETHUSDC"}, 99.0)

    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.01)

    assert calls, "stale heartbeat alert was never scheduled"
    assert calls[0][0] == "STALE_HEARTBEAT"
    assert calls[0][1]["age_seconds"] == 99.0
