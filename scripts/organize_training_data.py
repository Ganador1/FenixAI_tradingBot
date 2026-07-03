#!/usr/bin/env python3
"""
Organize agent logs and charts into a structured dataset for future training.

Creates a self-contained dataset under data/training_dataset/ with:
  - charts/       → symlinked chart PNGs (organized by symbol/timeframe/date)
  - agent_logs/   → JSON logs from logs/llm_responses/ (organized by agent)
  - reasoning/    → ReasoningBank entries (exported as JSON)
  - trades/       → Trade history from DB
  - manifest.json → Index of all files with metadata

Usage:
    python scripts/organize_training_data.py [--max-charts 10000] [--max-logs 10000]

This does NOT delete or move the original files — it creates symlinks and
copies so the original structure remains intact for the running system.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("TrainingData")


def organize_charts(source_dir: Path, dest_dir: Path, max_charts: int) -> int:
    """Create symlinks to chart PNGs, organized by symbol/timeframe/date."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    charts = sorted(source_dir.glob("*_pro.png"), key=lambda f: f.stat().st_mtime, reverse=True)

    for chart in charts[:max_charts]:
        # Parse filename: SYMBOL_TIMEFRAME_YYYYMMDD_HHMMSS_pro.png
        parts = chart.stem.split("_")
        if len(parts) < 5:
            continue
        symbol = parts[0]
        timeframe = parts[1]
        date = parts[2] if len(parts) > 2 else "unknown"

        # Create subdirectory: charts/SYMBOL/TIMEFRAME/DATE/
        subdir = dest_dir / symbol / timeframe / date
        subdir.mkdir(parents=True, exist_ok=True)

        link_path = subdir / chart.name
        if not link_path.exists():
            try:
                link_path.symlink_to(chart.resolve())
                count += 1
            except Exception:
                pass

    logger.info(f"Organized {count} charts into {dest_dir}")
    return count


def organize_agent_logs(source_dir: Path, dest_dir: Path, max_logs: int) -> int:
    """Copy agent LLM response JSONs, organized by agent name."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for agent_dir in sorted(source_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_name = agent_dir.name
        target = dest_dir / agent_name
        target.mkdir(parents=True, exist_ok=True)

        logs = sorted(agent_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for log_file in logs[:max_logs]:
            link_path = target / log_file.name
            if not link_path.exists():
                try:
                    link_path.symlink_to(log_file.resolve())
                    count += 1
                except Exception:
                    pass

    # Also copy standalone agent log files (e.g. decision_agent_*.json)
    standalone = sorted(source_dir.glob("*_agent_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for log_file in standalone[:max_logs]:
        agent_name = log_file.stem.rsplit("_", 3)[0] if "_" in log_file.stem else "unknown"
        target = dest_dir / agent_name
        target.mkdir(parents=True, exist_ok=True)
        link_path = target / log_file.name
        if not link_path.exists():
            try:
                link_path.symlink_to(log_file.resolve())
                count += 1
            except Exception:
                pass

    logger.info(f"Organized {count} agent logs into {dest_dir}")
    return count


def export_reasoning_bank(db_path: Path, dest_dir: Path) -> int:
    """Export ReasoningBank entries from SQLite to JSON files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    if not db_path.exists():
        logger.warning(f"Database not found: {db_path}")
        return 0

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if reasoning_entries table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]

        if "reasoning_entries" in tables:
            cursor.execute("SELECT * FROM reasoning_entries ORDER BY timestamp DESC LIMIT 50000")
            entries = cursor.fetchall()
            output = [dict(row) for row in entries]
            output_file = dest_dir / "reasoning_entries.json"
            with open(output_file, "w") as f:
                json.dump(output, f, indent=2, default=str)
            count = len(output)
            logger.info(f"Exported {count} reasoning entries to {output_file}")
        else:
            logger.info("No reasoning_entries table found in DB")

        conn.close()
    except Exception as e:
        logger.error(f"Error exporting reasoning bank: {e}")

    return count


def export_trades(db_path: Path, dest_dir: Path) -> int:
    """Export trade history from SQLite to JSON."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        return 0

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]

        if "trades" in tables:
            cursor.execute("SELECT * FROM trades ORDER BY executed_at DESC")
            trades = [dict(row) for row in cursor.fetchall()]
            output_file = dest_dir / "trades.json"
            with open(output_file, "w") as f:
                json.dump(trades, f, indent=2, default=str)
            logger.info(f"Exported {len(trades)} trades to {output_file}")
            conn.close()
            return len(trades)

        conn.close()
    except Exception as e:
        logger.error(f"Error exporting trades: {e}")

    return 0


def create_manifest(base_dir: Path, chart_count: int, log_count: int, reasoning_count: int, trade_count: int) -> None:
    """Create a manifest.json with dataset metadata."""
    manifest = {
        "created_at": datetime.utcnow().isoformat(),
        "description": "Training dataset for FenixAI vision models and agent fine-tuning",
        "structure": {
            "charts/": "Symlinks to chart PNGs organized by SYMBOL/TIMEFRAME/DATE",
            "agent_logs/": "Symlinks to LLM response JSONs organized by agent name",
            "reasoning/": "ReasoningBank entries exported as JSON",
            "trades/": "Trade history exported as JSON",
        },
        "counts": {
            "charts": chart_count,
            "agent_logs": log_count,
            "reasoning_entries": reasoning_count,
            "trades": trade_count,
        },
        "notes": [
            "Original files are NOT moved or deleted — this dataset uses symlinks",
            "Charts include EMA 9/21/50, BB, SuperTrend, VWAP, Pivot levels",
            "Agent logs contain raw LLM responses with action, confidence, reasoning",
            "Use manifest.json to index and sample data for training",
        ],
    }
    manifest_path = base_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written to {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="Organize training data for FenixAI")
    parser.add_argument("--base", default="data/training_dataset", help="Output base directory")
    parser.add_argument("--charts-dir", default="cache/charts", help="Source charts directory")
    parser.add_argument("--logs-dir", default="logs/llm_responses", help="Source agent logs directory")
    parser.add_argument("--db", default="fenix_trading.db", help="SQLite database path")
    parser.add_argument("--max-charts", type=int, default=50000, help="Max charts to index")
    parser.add_argument("--max-logs", type=int, default=50000, help="Max logs per agent")
    args = parser.parse_args()

    base = Path(args.base)
    base.mkdir(parents=True, exist_ok=True)

    logger.info(f"Building training dataset at {base}/")

    chart_count = organize_charts(
        Path(args.charts_dir), base / "charts", args.max_charts
    )
    log_count = organize_agent_logs(
        Path(args.logs_dir), base / "agent_logs", args.max_logs
    )
    reasoning_count = export_reasoning_bank(Path(args.db), base / "reasoning")
    trade_count = export_trades(Path(args.db), base / "trades")

    create_manifest(base, chart_count, log_count, reasoning_count, trade_count)

    logger.info(f"Done! Dataset ready at {base}/")
    logger.info(f"  Charts: {chart_count}")
    logger.info(f"  Agent logs: {log_count}")
    logger.info(f"  Reasoning entries: {reasoning_count}")
    logger.info(f"  Trades: {trade_count}")


if __name__ == "__main__":
    main()