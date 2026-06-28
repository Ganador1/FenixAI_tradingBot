from __future__ import annotations

from unittest.mock import MagicMock


def test_find_nanofenix_companion_pids_matches_symbol(monkeypatch):
    import run_fenix

    ps_output = """
    123 /venv/bin/python /repo/run_nanofenixv3.py --symbol ETHUSDC --companion --output-path logs/a.json
    456 /venv/bin/python /repo/run_nanofenixv3.py --symbol ETHUSDT --companion --output-path logs/b.json
    789 rg run_nanofenixv3.py
    """
    monkeypatch.setattr(
        run_fenix.subprocess,
        "check_output",
        lambda *args, **kwargs: ps_output,
    )

    assert run_fenix._find_nanofenix_companion_pids("ETHUSDC") == [123]


def test_start_nanofenix_companion_refuses_duplicate_symbol(monkeypatch):
    import run_fenix

    popen = MagicMock()
    monkeypatch.setattr(run_fenix, "_find_nanofenix_companion_pids", lambda symbol: [1234])
    monkeypatch.setattr(run_fenix.subprocess, "Popen", popen)

    proc, signal_path = run_fenix._start_nanofenix_companion(
        symbol="ETHUSDC",
        observer_only=True,
    )

    assert proc is None
    assert signal_path is None
    popen.assert_not_called()
