from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from src.api import server
from src.api.nano_routes import _nanofenix_subprocess_environment


def test_public_health_is_minimal_and_sensitive_routes_require_authentication():
    client = TestClient(server.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    for path in (
        "/api/system/status",
        "/api/trading/positions",
        "/api/reasoning/entries",
        "/api/v25/release-info",
        "/api/nanofenix/status?symbol=SOLUSDT",
    ):
        response = client.get(path)
        assert response.status_code == 401, path


def test_api_rejects_untrusted_hosts_origins_and_oversized_bodies():
    client = TestClient(server.app)

    bad_host = client.get("/health", headers={"Host": "attacker.example"})
    assert bad_host.status_code == 400

    preflight = client.options(
        "/api/system/status",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers

    oversized = client.post(
        "/api/auth/login",
        content=b"x" * (server._MAX_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413


def test_validation_errors_do_not_echo_submitted_passwords():
    client = TestClient(server.app)
    secret = "do-not-reflect-this-password"

    response = client.post(
        "/api/auth/login",
        json={"email": 123, "password": secret, "unexpected": secret},
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert '"input"' not in response.text


def test_security_headers_and_docs_default():
    client = TestClient(server.app)
    response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_nanofenix_child_environment_does_not_inherit_parent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_SECRET", "binance-secret")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-secret")
    signal_path = tmp_path / "signal.json"

    child = _nanofenix_subprocess_environment(signal_path, observer_only=True)

    assert "BINANCE_API_SECRET" not in child
    assert "JWT_SECRET" not in child
    assert "OPENROUTER_API_KEY" not in child
    assert child["FENIX_SKIP_DOTENV"] == "1"
    assert child["NANOFENIXV3_COMPANION_OBSERVER_ONLY"] == "1"
    assert child["NANOFENIXV3_RUNTIME_STATE_PATH"].endswith(".json")


def test_generic_live_flag_never_grants_api_live_capability(monkeypatch):
    monkeypatch.setenv("FENIX_LIVE", "1")
    monkeypatch.delenv("FENIX_API_ALLOW_LIVE", raising=False)

    assert server._api_live_capability_enabled() is False

    monkeypatch.setenv("FENIX_API_ALLOW_LIVE", "true")
    assert server._api_live_capability_enabled() is True


def test_order_payloads_are_strict_bounded_and_type_consistent():
    from pydantic import ValidationError

    for payload in (
        {"symbol": "btcusdt", "type": "market", "side": "buy", "quantity": 1.0},
        {"symbol": "BTCUSDT", "type": "market", "side": "BUY", "quantity": 1.0},
        {"symbol": "BTCUSDT", "type": "market", "side": "buy", "quantity": float("inf")},
        {"symbol": "BTCUSDT", "type": "limit", "side": "buy", "quantity": 1.0},
        {
            "symbol": "BTCUSDT",
            "type": "market",
            "side": "buy",
            "quantity": 1.0,
            "unexpected": "not-allowed",
        },
    ):
        try:
            server.OrderCreate.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError(f"unsafe order payload was accepted: {payload!r}")


def test_market_inputs_and_agent_outputs_are_bounded():
    from fastapi import HTTPException
    from pydantic import ValidationError

    assert server._market_symbol("ethusdt") == "ETHUSDT"
    assert server._market_interval("5m") == "5m"

    for unsafe_symbol in ("../../etc/passwd", "BTC/USDT", "A" * 100):
        try:
            server._market_symbol(unsafe_symbol)
        except HTTPException:
            continue
        raise AssertionError(f"unsafe symbol was accepted: {unsafe_symbol!r}")

    for unsafe_interval in ("0m", "45m", "../1m"):
        try:
            server._market_interval(unsafe_interval)
        except HTTPException:
            continue
        raise AssertionError(f"unsafe interval was accepted: {unsafe_interval!r}")

    with pytest.raises(ValidationError):
        server.AgentOutputResponse.model_validate(
            {
                "id": "id",
                "agent_id": "../../escape",
                "agent_name": "agent",
                "timestamp": "now",
                "reasoning": "x",
                "decision": "HOLD",
                "confidence": 0.5,
            }
        )
