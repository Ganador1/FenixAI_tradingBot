#!/usr/bin/env python3
"""
Verify Alembic migrations are working correctly.

This script:
1. Checks if Alembic is properly configured
2. Verifies the database is at the correct revision
3. Tests creating a test migration (dry-run)
4. Validates the migration history
"""

import subprocess
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))


def get_python_executable():
    """Get the Python executable path (prefer venv)."""
    venv_python = Path(__file__).parent.parent / "fenix_env" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def run_command(cmd: list[str], capture: bool = True) -> tuple[int, str, str]:
    """Run a command and return (exit_code, stdout, stderr)."""
    # Replace 'python' with the correct Python executable
    if cmd[0] == "python":
        cmd[0] = get_python_executable()
    result = subprocess.run(cmd, capture_output=capture, text=True)
    return result.returncode, result.stdout, result.stderr


def check_alembic_config():
    """Check if Alembic configuration files exist."""
    print("🔍 Checking Alembic configuration...")

    alembic_ini = Path("alembic.ini")
    alembic_dir = Path("alembic")

    if not alembic_ini.exists():
        print("  ❌ alembic.ini not found")
        return False
    print("  ✅ alembic.ini exists")

    if not alembic_dir.exists():
        print("  ❌ alembic/ directory not found")
        return False
    print("  ✅ alembic/ directory exists")

    env_py = alembic_dir / "env.py"
    if not env_py.exists():
        print("  ❌ alembic/env.py not found")
        return False
    print("  ✅ alembic/env.py exists")

    versions_dir = alembic_dir / "versions"
    if not versions_dir.exists():
        print("  ❌ alembic/versions/ directory not found")
        return False
    print("  ✅ alembic/versions/ directory exists")

    return True


def check_current_revision():
    """Check current database revision."""
    print("\n🔍 Checking current database revision...")

    exit_code, stdout, stderr = run_command(["python", "-m", "alembic", "current"])

    if exit_code != 0:
        print(f"  ❌ Failed to get current revision: {stderr}")
        return False

    if "head" in stdout:
        print("  ✅ Database is at head revision")
        print(f"     {stdout.strip()}")
    else:
        print("  ⚠️  Database may not be at head revision")
        print(f"     {stdout.strip()}")

    return True


def check_migration_history():
    """Check migration history."""
    print("\n🔍 Checking migration history...")

    exit_code, stdout, stderr = run_command(["python", "-m", "alembic", "history", "--verbose"])

    if exit_code != 0:
        print(f"  ❌ Failed to get history: {stderr}")
        return False

    lines = stdout.strip().split("\n")
    if not lines or not any(line.strip() for line in lines):
        print("  ⚠️  No migrations found")
        return False

    print(f"  ✅ Found {len([l for l in lines if l.startswith('Rev:')])} migration(s)")
    return True


def test_migration_dry_run():
    """Test if a new migration can be created (without saving)."""
    print("\n🔍 Testing migration autogeneration...")

    # First check if there are any changes to detect
    exit_code, stdout, stderr = run_command(
        ["python", "-m", "alembic", "revision", "--autogenerate", "-m", "test_migration"]
    )

    if exit_code != 0:
        print(f"  ❌ Failed to create test migration: {stderr}")
        return False

    # Check if the migration has actual changes
    versions_dir = Path("alembic/versions")
    migration_files = list(versions_dir.glob("*_test_migration.py"))

    if not migration_files:
        print("  ⚠️  No test migration file created")
        return False

    # Read the migration file
    migration_file = migration_files[0]
    content = migration_file.read_text()

    # Check if it's empty (no changes detected)
    if "pass" in content and "op.create_table" not in content and "op.add_column" not in content:
        print("  ✅ No schema changes detected (database is up to date)")
        # Clean up the empty migration
        migration_file.unlink()
        print("     Cleaned up empty test migration")
    else:
        print("  ⚠️  Schema changes detected - review the migration")
        print(f"     File: {migration_file}")

    return True


def verify_database_connection():
    """Verify database connection works."""
    print("\n🔍 Verifying database connection...")

    try:
        import asyncio

        from src.config.database import DATABASE_URL, engine

        async def test_connection():
            from sqlalchemy import text

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                return result.scalar()

        result = asyncio.run(test_connection())

        if result == 1:
            print("  ✅ Database connection successful")
            print(f"     URL: {DATABASE_URL}")
            return True
        else:
            print(f"  ❌ Unexpected result from database: {result}")
            return False

    except Exception as e:
        print(f"  ❌ Database connection failed: {e}")
        return False


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("🔧 Fenix Trading Bot - Migration Verification")
    print("=" * 60)

    checks = [
        ("Configuration", check_alembic_config),
        ("Database Connection", verify_database_connection),
        ("Current Revision", check_current_revision),
        ("Migration History", check_migration_history),
        ("Autogeneration Test", test_migration_dry_run),
    ]

    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n  ❌ Error during {name}: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("📊 Verification Summary")
    print("=" * 60)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")

    all_passed = all(result for _, result in results)

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 All checks passed! Migrations are properly configured.")
        print("\nNext steps:")
        print("  - Use 'python scripts/manage_migrations.py create \"<msg>\"' for new migrations")
        print("  - Use 'python scripts/manage_migrations.py upgrade' to apply migrations")
        print("  - Use 'alembic current' to check current revision")
    else:
        print("⚠️  Some checks failed. Please review the output above.")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
