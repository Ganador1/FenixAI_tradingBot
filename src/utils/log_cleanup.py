#!/usr/bin/env python3
"""
Log Cleanup Utility for FenixAI.

Automatically cleans old log files to prevent disk bloat.
Run as cron job or scheduled task.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def clean_old_logs(
    log_dir: str = "logs",
    days_old: int = 30,
    dry_run: bool = False,
    patterns: list | None = None,
) -> dict:
    """
    Clean log files older than specified days.

    Args:
        log_dir: Directory containing logs
        days_old: Delete files older than this many days
        dry_run: If True, only report what would be deleted
        patterns: List of glob patterns to match (default: all log files)

    Returns:
        Dict with 'deleted', 'kept', 'bytes_freed' counts
    """
    if patterns is None:
        patterns = ["*.log", "*.jsonl"]

    log_path = Path(log_dir)
    if not log_path.exists():
        logger.warning(f"Log directory does not exist: {log_dir}")
        return {"deleted": 0, "kept": 0, "bytes_freed": 0}

    cutoff = datetime.now() - timedelta(days=days_old)
    stats = {"deleted": 0, "kept": 0, "bytes_freed": 0}

    for pattern in patterns:
        for filepath in log_path.glob(pattern):
            if not filepath.is_file():
                continue

            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            size = filepath.stat().st_size

            if mtime < cutoff:
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would delete: {filepath.name} ({size / 1024:.1f} KB, {mtime.date()})"
                    )
                else:
                    try:
                        filepath.unlink()
                        logger.info(f"Deleted: {filepath.name} ({size / 1024:.1f} KB)")
                    except Exception as e:
                        logger.error(f"Failed to delete {filepath}: {e}")
                        continue
                stats["deleted"] += 1
                stats["bytes_freed"] += size
            else:
                stats["kept"] += 1

    logger.info(
        f"Cleanup complete: {stats['deleted']} deleted, {stats['kept']} kept, "
        f"{stats['bytes_freed'] / (1024 * 1024):.2f} MB freed"
    )
    return stats


def clean_empty_logs(log_dir: str = "logs", dry_run: bool = False) -> dict:
    """Remove empty log files."""
    log_path = Path(log_dir)
    stats = {"deleted": 0, "bytes_checked": 0}

    for pattern in ["*.log", "*.jsonl"]:
        for filepath in log_path.glob(pattern):
            if filepath.stat().st_size == 0:
                if dry_run:
                    logger.info(f"[DRY RUN] Would remove empty: {filepath.name}")
                else:
                    filepath.unlink()
                    logger.info(f"Removed empty file: {filepath.name}")
                stats["deleted"] += 1

    return stats


def get_log_stats(log_dir: str = "logs") -> dict:
    """Get statistics about log directory."""
    log_path = Path(log_dir)

    stats = {
        "total_files": 0,
        "total_size_mb": 0,
        "by_type": {},
        "oldest": None,
        "newest": None,
    }

    for pattern in ["*.log", "*.jsonl", "*.txt"]:
        files = list(log_path.glob(pattern))
        if not files:
            continue

        total_size = sum(f.stat().st_size for f in files if f.is_file())
        stats["by_type"][pattern] = {
            "count": len(files),
            "size_mb": total_size / (1024 * 1024),
        }
        stats["total_files"] += len(files)
        stats["total_size_mb"] += total_size / (1024 * 1024)

        mtimes = [datetime.fromtimestamp(f.stat().st_mtime) for f in files if f.is_file()]
        if mtimes:
            oldest = min(mtimes)
            newest = max(mtimes)
            if stats["oldest"] is None or oldest < stats["oldest"]:
                stats["oldest"] = oldest
            if stats["newest"] is None or newest > stats["newest"]:
                stats["newest"] = newest

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FenixAI Log Cleanup Utility")
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory containing log files",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Delete files older than this many days",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show log directory statistics",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Also remove empty log files",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if args.stats:
        stats = get_log_stats(args.log_dir)
        print(f"\n📊 Log Directory Stats: {args.log_dir}")
        print(f"   Total files: {stats['total_files']}")
        print(f"   Total size: {stats['total_size_mb']:.2f} MB")
        if stats["oldest"]:
            print(f"   Oldest file: {stats['oldest'].date()}")
        if stats["newest"]:
            print(f"   Newest file: {stats['newest'].date()}")
        print("\n   By type:")
        for ext, info in stats["by_type"].items():
            print(f"     {ext}: {info['count']} files, {info['size_mb']:.2f} MB")
    else:
        print(f"\n🧹 Cleaning logs older than {args.days} days...")
        result = clean_old_logs(
            log_dir=args.log_dir,
            days_old=args.days,
            dry_run=args.dry_run,
        )

        if args.empty:
            print("\n🧹 Removing empty files...")
            clean_empty_logs(args.log_dir, dry_run=args.dry_run)

        if args.dry_run:
            print("\n⚠️ DRY RUN - No files were actually deleted")
            print("   Run without --dry-run to actually delete files")
