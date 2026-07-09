from __future__ import annotations

import asyncio
import atexit
import inspect
import os
import tempfile

import pytest


def _force_test_database_url() -> None:
    """Never let the test suite touch the production SQLite DB.

    The default DATABASE_URL is the relative ``sqlite+aiosqlite:///./fenix_trading.db``,
    so running pytest from the repo root would write test fixtures (fill:123,
    position:1234, price 100.0, ...) straight into the live trading DB — which is
    exactly what happened on 2026-07-04.

    We point DATABASE_URL at a throwaway temp FILE (not ``:memory:`` — an
    in-memory aiosqlite DB is per-connection, so tables created on one async
    connection are invisible to the next and the API e2e tests would fail with
    "no such table"). The file is removed at interpreter exit. A caller that sets
    DATABASE_URL explicitly always wins.
    """
    if os.getenv("DATABASE_URL"):
        return
    fd, path = tempfile.mkstemp(prefix="fenix_test_", suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"

    def _cleanup() -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    atexit.register(_cleanup)


# Apply at import time so it wins even before any module-level engine is built.
_force_test_database_url()

# The AGENT_CONSENSUS gate (FENIX_MIN_AGENT_CONSENSUS, default 2 in production)
# blocks any _process_decision test whose fixture data doesn't supply >=2/3
# agreeing directional agents — which is most legacy filter/risk-gate tests that
# predate the gate. Disable it by default for the suite; tests that exercise the
# gate itself re-enable it explicitly with monkeypatch.setenv.
os.environ.setdefault("FENIX_MIN_AGENT_CONSENSUS", "0")


@pytest.fixture(autouse=True, scope="session")
def _isolate_test_database():
    _force_test_database_url()
    yield


@pytest.fixture(autouse=True)
def isolate_runtime_risk_manager_storage(monkeypatch, tmp_path):
    """Keep RuntimeRiskManager tests from reading or writing live run state."""
    monkeypatch.setenv("FENIX_RISK_MANAGER_STORAGE_PATH", str(tmp_path / "risk_manager.jsonl"))
    try:
        from src.risk import runtime_risk_manager

        runtime_risk_manager._risk_manager = None
    except Exception:
        pass


@pytest.fixture
def device():
    """Torch device fixture for standalone NanoFenix validation tests."""
    import torch

    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def pytest_pyfunc_call(pyfuncitem):
    """Fallback async test runner when pytest-asyncio is unavailable."""
    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        funcargs = {
            name: pyfuncitem.funcargs[name]
            for name in pyfuncitem._fixtureinfo.argnames
        }
        loop.run_until_complete(testfunction(**funcargs))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(None)
        loop.close()
    return True
