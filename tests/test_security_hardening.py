from __future__ import annotations

import os
import socket
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


def _configure_secret_vault(monkeypatch, tmp_path) -> None:
    from src.security.secure_secrets_manager import reset_secrets_manager_for_tests

    monkeypatch.setenv("FENIX_MASTER_PASSWORD", "test-master-password-at-least-32-chars")
    monkeypatch.setenv("FENIX_VAULT_PATH", str(tmp_path / "vault.enc"))
    monkeypatch.setenv("FENIX_VAULT_SALT_PATH", str(tmp_path / "vault.salt"))
    reset_secrets_manager_for_tests()


def test_smtp_destination_rejects_loopback_by_default(monkeypatch):
    from src.security.smtp_client import SMTPDestinationError, resolve_smtp_destination

    monkeypatch.delenv("FENIX_SMTP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 587))
        ],
    )

    with pytest.raises(SMTPDestinationError, match="non-public"):
        resolve_smtp_destination("smtp.example.test", 587)


def test_smtp_destination_pins_explicitly_allowlisted_internal_host(monkeypatch):
    from src.security.smtp_client import resolve_smtp_destination

    monkeypatch.setenv("FENIX_SMTP_ALLOWED_HOSTS", "mail.internal.test")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", 465))
        ],
    )

    destination = resolve_smtp_destination("mail.internal.test", 465)

    assert destination.address == "10.0.0.5"
    assert destination.hostname == "mail.internal.test"


def test_smtp_destination_rejects_unapproved_ports(monkeypatch):
    from src.security.smtp_client import SMTPDestinationError, resolve_smtp_destination

    with pytest.raises(SMTPDestinationError, match="465 or 587"):
        resolve_smtp_destination("smtp.example.test", 25)


@pytest.mark.parametrize(
    "url",
    (
        "http://api.ollama.com",
        "https://user:password@api.ollama.com",
        "https://api.ollama.com/attacker-controlled-path",
        "https://api.ollama.com?redirect=https://attacker.example",
    ),
)
def test_ollama_cloud_rejects_unsafe_origins(monkeypatch, url):
    from src.inference.providers.base import ProviderError
    from src.inference.providers.ollama_cloud_provider import OllamaCloudProvider

    monkeypatch.setenv("OLLAMA_CLOUD_URL", url)
    monkeypatch.delenv("FENIX_ALLOW_INSECURE_OLLAMA_CLOUD", raising=False)

    with pytest.raises(ProviderError):
        OllamaCloudProvider()


def test_ollama_cloud_custom_host_requires_explicit_allowlist(monkeypatch):
    from src.inference.providers.base import ProviderError
    from src.inference.providers.ollama_cloud_provider import OllamaCloudProvider

    monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://models.internal.example")
    monkeypatch.delenv("OLLAMA_CLOUD_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ProviderError, match="explicitly allowed"):
        OllamaCloudProvider()

    monkeypatch.setenv("OLLAMA_CLOUD_ALLOWED_HOSTS", "models.internal.example")
    assert OllamaCloudProvider()._base_url == "https://models.internal.example"


def test_smtp_identity_change_requires_password_reentry(monkeypatch, tmp_path):
    from src.api.system_settings import SettingsValidationError, update_system_settings

    _configure_secret_vault(monkeypatch, tmp_path)
    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(tmp_path / "settings.json"))
    update_system_settings(
        "notifications",
        {
            "email_host": "smtp.example.test",
            "email_username": "mailer",
            "email_password": "stored-password",
        },
    )

    with pytest.raises(SettingsValidationError, match="re-entered"):
        update_system_settings(
            "notifications",
            {"email_host": "attacker.example.test", "email_password": ""},
        )


def test_settings_reject_unbounded_and_unsafe_values(monkeypatch, tmp_path):
    from src.api.system_settings import SettingsValidationError, update_system_settings

    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(SettingsValidationError):
        update_system_settings("general", {"site_name": "x" * 101})
    with pytest.raises(SettingsValidationError):
        update_system_settings("api", {"cors_origins": ["https://trusted.test", "*"]})
    with pytest.raises(SettingsValidationError):
        update_system_settings("trading", {"leverage_max": 1000})


def test_plaintext_settings_secret_is_migrated_and_removed(monkeypatch, tmp_path):
    import json

    from src.api.system_settings import load_system_settings

    _configure_secret_vault(monkeypatch, tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(settings_path))
    settings_path.write_text(
        json.dumps(
            {
                "notifications": {
                    "email_host": "smtp.example.test",
                    "email_password": "legacy-plaintext-password",
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_system_settings()
    persisted = settings_path.read_text(encoding="utf-8")

    assert loaded["notifications"]["email_password"] == "legacy-plaintext-password"
    assert "legacy-plaintext-password" not in persisted
    assert "email_password" not in json.loads(persisted)["notifications"]


def test_settings_refuse_symlinked_storage(monkeypatch, tmp_path):
    from src.api.system_settings import SettingsValidationError, load_system_settings

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    settings_path = tmp_path / "settings.json"
    settings_path.symlink_to(target)
    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(settings_path))

    with pytest.raises(SettingsValidationError, match="symbolic link"):
        load_system_settings()


def test_secret_vault_round_trip_uses_private_files(monkeypatch, tmp_path):
    from src.security.secure_secrets_manager import get_secrets_manager

    _configure_secret_vault(monkeypatch, tmp_path)
    manager = get_secrets_manager()

    assert manager.store_secret("smtp_password", "correct horse battery staple")
    assert manager.get_secret("smtp_password") == "correct horse battery staple"
    assert stat.S_IMODE((tmp_path / "vault.enc").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "vault.salt").stat().st_mode) == 0o600
    assert not (tmp_path / ".key").exists()


def test_secret_vault_refuses_corruption_without_overwrite(monkeypatch, tmp_path):
    from src.security.secure_secrets_manager import (
        get_secrets_manager,
        reset_secrets_manager_for_tests,
    )

    _configure_secret_vault(monkeypatch, tmp_path)
    manager = get_secrets_manager()
    assert manager.store_secret("smtp_password", "original-secret")
    vault_path = tmp_path / "vault.enc"
    vault_path.write_bytes(b"corrupted-ciphertext")
    vault_path.chmod(0o600)
    reset_secrets_manager_for_tests()

    corrupted = get_secrets_manager()

    assert corrupted.validate_integrity() is False
    assert corrupted.get_secret("smtp_password") is None
    assert corrupted.store_secret("replacement", "must-not-overwrite") is False
    assert vault_path.read_bytes() == b"corrupted-ciphertext"


def test_secret_vault_refuses_symlinked_salt(monkeypatch, tmp_path):
    from src.security.secure_secrets_manager import get_secrets_manager

    _configure_secret_vault(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"x" * 16)
    target.chmod(0o600)
    (tmp_path / "vault.salt").symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink|opened safely"):
        get_secrets_manager()


def test_legacy_secret_facade_never_falls_back_to_plaintext(monkeypatch, tmp_path):
    from src.config.secrets_manager import SecretsManager

    monkeypatch.delenv("FENIX_MASTER_PASSWORD", raising=False)
    legacy_path = tmp_path / "legacy-secrets.json"
    manager = SecretsManager(secrets_file=str(legacy_path))

    assert manager.set_secret("api_key", "must-not-be-persisted") is False
    assert not legacy_path.exists()


def test_structured_logger_redacts_values_and_uses_private_files(tmp_path):
    from src.utils.structured_logger import StructuredLogger

    logger = StructuredLogger("security-redaction-test", str(tmp_path / "logs"))
    logger.info(
        "provider api_key=message-secret and Authorization: Bearer abcdefghijklmnop",
        password="field-secret",
        nested={"access_token": "nested-secret"},
    )
    for handler in logger.logger.handlers:
        handler.flush()

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*")
    )
    assert "message-secret" not in combined
    assert "abcdefghijklmnop" not in combined
    assert "field-secret" not in combined
    assert "nested-secret" not in combined
    assert "[REDACTED]" in combined
    assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (tmp_path / "logs").glob("*")
    )


def test_private_file_write_does_not_chmod_existing_parent(tmp_path):
    from src.security.private_files import write_private_text

    shared_parent = tmp_path / "shared"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    output = shared_parent / "private-state.json"

    write_private_text(output, "{}\n")

    assert stat.S_IMODE(shared_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_corrupt_risk_state_blocks_trading_without_overwrite(tmp_path):
    from src.risk.runtime_risk_manager import RuntimeRiskManager

    state = tmp_path / "risk.jsonl"
    corrupt_payload = b'{"current_mode":"NORMAL","peak_balance":"NaN"}\n'
    state.write_bytes(corrupt_payload)

    manager = RuntimeRiskManager(storage_path=str(state))
    allowed, status = manager.check_trade_allowed("BTCUSDT", 10.0)
    manager._save_state()

    assert allowed is False
    assert status.mode == "SEVERE"
    assert state.read_bytes() == corrupt_payload


def test_symlinked_risk_state_blocks_trading(tmp_path):
    from src.risk.runtime_risk_manager import RuntimeRiskManager

    target = tmp_path / "valuable.jsonl"
    target.write_text('{"owner":"operator"}\n', encoding="utf-8")
    state = tmp_path / "risk.jsonl"
    state.symlink_to(target)

    manager = RuntimeRiskManager(storage_path=str(state))
    allowed, status = manager.check_trade_allowed("BTCUSDT", 10.0)

    assert allowed is False
    assert status.mode == "SEVERE"
    assert target.read_text(encoding="utf-8") == '{"owner":"operator"}\n'


def test_prompt_experiment_rejects_path_traversal_and_writes_private_state(tmp_path):
    from src.prompts.ab_testing import PromptExperiment
    from src.prompts.agent_prompts import AgentType, PromptTemplate

    with pytest.raises(ValueError, match="unsafe"):
        PromptExperiment("../escape", AgentType.TECHNICAL, storage_dir=tmp_path / "experiments")

    experiment = PromptExperiment(
        "safe-experiment",
        AgentType.TECHNICAL,
        storage_dir=tmp_path / "experiments",
    )
    template = PromptTemplate(
        name="test-template",
        agent_type=AgentType.TECHNICAL,
        system_prompt="system",
        user_template="user",
    )
    experiment.add_variant("control", template, is_control=True)
    experiment.record_invocation("control", 10.0, True, signal="HOLD")

    state = tmp_path / "experiments" / "safe-experiment.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


def test_reasoning_maintenance_is_dry_run_data_until_explicit_apply(tmp_path):
    from scripts.reasoning_bank_maintenance import apply_repairs, build_repairs

    bank = tmp_path / "bank"
    bank.mkdir()
    risk = bank / "risk_manager.jsonl"
    sentiment = bank / "sentiment_agent.jsonl"
    risk.write_text('{"id":1}\n{"id":2}\n', encoding="utf-8")
    sentiment.write_text(
        '{"action":"NEUTRAL","success":false,"reward":-1}\n',
        encoding="utf-8",
    )

    replacements, summary = build_repairs(bank, keep_risk=1)

    assert summary == {"risk_before": 2, "risk_after": 1, "sentiment_reset": 1}
    assert risk.read_text(encoding="utf-8") == '{"id":1}\n{"id":2}\n'

    apply_repairs(replacements, ".bak-test")
    assert risk.read_text(encoding="utf-8") == '{"id":2}\n'
    assert (bank / "risk_manager.jsonl.bak-test").exists()
    assert stat.S_IMODE(risk.stat().st_mode) == 0o600


def test_dotenv_loader_rejects_public_permissions(monkeypatch, tmp_path):
    from src.security.dotenv_security import DotenvSecurityError, secure_load_dotenv

    dotenv = tmp_path / ".env"
    dotenv.write_text("FENIX_TEST_SECRET=not-loaded\n", encoding="utf-8")
    dotenv.chmod(0o644)
    monkeypatch.delenv("FENIX_TEST_SECRET", raising=False)

    with pytest.raises(DotenvSecurityError, match="chmod 600"):
        secure_load_dotenv(dotenv)
    assert "FENIX_TEST_SECRET" not in os.environ


def test_dotenv_loader_parses_without_command_execution(monkeypatch, tmp_path):
    from src.security.dotenv_security import secure_load_dotenv

    marker = tmp_path / "must-not-exist"
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"FENIX_TEST_SECRET=$(touch {marker})\n",
        encoding="utf-8",
    )
    dotenv.chmod(0o600)
    monkeypatch.delenv("FENIX_TEST_SECRET", raising=False)

    assert secure_load_dotenv(dotenv)
    assert os.environ["FENIX_TEST_SECRET"] == f"$(touch {marker})"
    assert not marker.exists()


def test_dotenv_loader_refuses_symlinks(monkeypatch, tmp_path):
    from src.security.dotenv_security import DotenvSecurityError, secure_load_dotenv

    target = tmp_path / "real.env"
    target.write_text("FENIX_TEST_SECRET=value\n", encoding="utf-8")
    target.chmod(0o600)
    dotenv = tmp_path / ".env"
    dotenv.symlink_to(target)
    monkeypatch.delenv("FENIX_TEST_SECRET", raising=False)

    with pytest.raises(DotenvSecurityError, match="symbolic link"):
        secure_load_dotenv(dotenv)


@pytest.mark.parametrize(
    "name",
    ["PATH", "PYTHONPATH", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"],
)
def test_dotenv_loader_rejects_process_loader_environment(name, tmp_path):
    from src.security.dotenv_security import DotenvSecurityError, secure_load_dotenv

    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{name}=untrusted\n", encoding="utf-8")
    dotenv.chmod(0o600)

    with pytest.raises(DotenvSecurityError, match="process-loader"):
        secure_load_dotenv(dotenv)


def test_legacy_system_config_keeps_secure_network_defaults():
    from pathlib import Path

    import yaml

    config_path = Path(__file__).resolve().parents[1] / "src/config/system_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["system"]["debug"] is False
    assert config["api"]["host"] == "127.0.0.1"
    assert config["api"]["authentication"]["secret_key"] == ""
    assert "*" not in config["api"]["cors"]["origins"]


@pytest.mark.asyncio
async def test_loopback_control_is_denied_without_explicit_opt_in(monkeypatch):
    import src.api.auth as auth

    monkeypatch.setattr(auth, "SECRET_KEY", None)
    monkeypatch.delenv("FENIX_ALLOW_UNAUTHENTICATED_LOOPBACK_CONTROL", raising=False)
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(path="/api/engine/start"),
        headers={},
    )

    with pytest.raises(HTTPException) as exc:
        await auth.require_control_access(request=request, db=AsyncMock())

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_loopback_control_requires_explicit_development_opt_in(monkeypatch):
    import src.api.auth as auth

    monkeypatch.setattr(auth, "SECRET_KEY", None)
    monkeypatch.setenv("FENIX_ALLOW_UNAUTHENTICATED_LOOPBACK_CONTROL", "1")
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(path="/api/engine/start"),
        headers={},
    )

    await auth.require_control_access(request=request, db=AsyncMock())
