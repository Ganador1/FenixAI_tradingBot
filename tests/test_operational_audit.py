from __future__ import annotations

import json

from src.trading.operational_audit import OperationalAudit, read_runtime_instances


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
