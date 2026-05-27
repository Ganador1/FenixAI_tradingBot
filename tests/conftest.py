from __future__ import annotations

import asyncio
import inspect

import pytest


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
