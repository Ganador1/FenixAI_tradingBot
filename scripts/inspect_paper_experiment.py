#!/usr/bin/env python3
"""Summarize an isolated Fenix paper experiment without exposing raw prompts."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_TABLE_COUNT_QUERIES = {
    "orders": 'SELECT COUNT(*) FROM "orders"',
    "positions": 'SELECT COUNT(*) FROM "positions"',
    "trades": 'SELECT COUNT(*) FROM "trades"',
    "agent_outputs": 'SELECT COUNT(*) FROM "agent_outputs"',
}


def _process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _table_count(connection: sqlite3.Connection, table: str) -> int | None:
    query = _TABLE_COUNT_QUERIES.get(table)
    if query is None:
        raise ValueError("table is not part of the experiment schema")
    try:
        row = connection.execute(query).fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else 0


def inspect_database(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result["counts"] = {
                table: _table_count(connection, table)
                for table in ("orders", "positions", "trades", "agent_outputs")
            }
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    return result


def inspect_log(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    result.update(
        {
            "bytes": path.stat().st_size,
            "simulated_trade_events": text.count("trade:simulated")
            + text.count("PAPER TRADE"),
            "error_lines": sum(
                1 for line in text.splitlines() if " ERROR " in line or "| ERROR" in line
            ),
            "warning_lines": sum(
                1 for line in text.splitlines() if " WARNING " in line or "| WARNING" in line
            ),
        }
    )
    return result


def inspect_experiment(root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "root": str(root.resolve()),
        "processes": [],
        "databases": [],
        "logs": [],
    }
    pidfile = root / "pids.txt"
    if pidfile.exists():
        for line in pidfile.read_text(encoding="utf-8").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2 or not fields[0].isdigit():
                continue
            pid, role = int(fields[0]), fields[1]
            payload["processes"].append(
                {"pid": pid, "role": role, "running": _process_running(pid)}
            )
    payload["databases"] = [inspect_database(path) for path in sorted(root.glob("fenix_*.db"))]
    payload["logs"] = [inspect_log(path) for path in sorted(root.glob("*.log"))]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Experiment runtime directory")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    payload = inspect_experiment(args.root)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Experiment: {payload['root']}")
        for process in payload["processes"]:
            state = "running" if process["running"] else "stopped"
            print(f"  process {process['role']}: pid={process['pid']} {state}")
        for database in payload["databases"]:
            print(f"  database {database['path']}: {database.get('counts', database.get('error'))}")
        for log in payload["logs"]:
            print(
                f"  log {log['path']}: simulated={log.get('simulated_trade_events', 0)} "
                f"warnings={log.get('warning_lines', 0)} errors={log.get('error_lines', 0)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
