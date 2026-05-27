#!/usr/bin/env python3
"""
Migration management script for Fenix Trading Bot.

Provides convenient commands for Alembic database migrations.

Usage:
    python scripts/manage_migrations.py init          # Initialize Alembic (first time only)
    python scripts/manage_migrations.py create "msg"  # Create new migration
    python scripts/manage_migrations.py upgrade       # Run all pending migrations
    python scripts/manage_migrations.py downgrade     # Rollback one migration
    python scripts/manage_migrations.py current       # Show current revision
    python scripts/manage_migrations.py history       # Show migration history
    python scripts/manage_migrations.py stamp <rev>   # Stamp database with revision
"""

import argparse
import subprocess
import sys


def run_alembic_command(args: list[str]) -> int:
    """Run an Alembic command and return exit code."""
    cmd = ["python", "-m", "alembic"] + args
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def cmd_init():
    """Initialize Alembic (only needed once)."""
    return run_alembic_command(["init", "alembic"])


def cmd_create(message: str, autogenerate: bool = True):
    """Create a new migration."""
    args = ["revision"]
    if autogenerate:
        args.append("--autogenerate")
    args.extend(["-m", message])
    return run_alembic_command(args)


def cmd_upgrade(revision: str = "head"):
    """Run migrations up to specified revision."""
    return run_alembic_command(["upgrade", revision])


def cmd_downgrade(revision: str = "-1"):
    """Rollback migrations to specified revision."""
    return run_alembic_command(["downgrade", revision])


def cmd_current():
    """Show current database revision."""
    return run_alembic_command(["current"])


def cmd_history():
    """Show migration history."""
    return run_alembic_command(["history", "--verbose"])


def cmd_stamp(revision: str):
    """Stamp database with specific revision without running migrations."""
    return run_alembic_command(["stamp", revision])


def main():
    parser = argparse.ArgumentParser(
        description="Manage database migrations for Fenix Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s create "add user preferences table"
  %(prog)s upgrade
  %(prog)s downgrade -1
  %(prog)s current
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    subparsers.add_parser("init", help="Initialize Alembic (first time only)")

    # create
    create_parser = subparsers.add_parser("create", help="Create a new migration")
    create_parser.add_argument("message", help="Migration message/description")
    create_parser.add_argument(
        "--no-autogenerate",
        action="store_true",
        help="Create empty migration without auto-detection",
    )

    # upgrade
    upgrade_parser = subparsers.add_parser("upgrade", help="Run pending migrations")
    upgrade_parser.add_argument(
        "revision", nargs="?", default="head", help="Target revision (default: head)"
    )

    # downgrade
    downgrade_parser = subparsers.add_parser("downgrade", help="Rollback migrations")
    downgrade_parser.add_argument(
        "revision", nargs="?", default="-1", help="Target revision (default: -1, one step back)"
    )

    # current
    subparsers.add_parser("current", help="Show current database revision")

    # history
    subparsers.add_parser("history", help="Show migration history")

    # stamp
    stamp_parser = subparsers.add_parser("stamp", help="Stamp database with revision")
    stamp_parser.add_argument("revision", help="Revision to stamp")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "create": lambda: cmd_create(args.message, not args.no_autogenerate),
        "upgrade": lambda: cmd_upgrade(args.revision),
        "downgrade": lambda: cmd_downgrade(args.revision),
        "current": cmd_current,
        "history": cmd_history,
        "stamp": lambda: cmd_stamp(args.revision),
    }

    exit_code = commands[args.command]()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
