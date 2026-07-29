"""Compatibility facade for environment and encrypted-vault secrets.

The historical manager used a static PBKDF2 salt and could silently fall back
to weak local persistence. New writes are accepted only by
``SecureSecretsManager`` with ``FENIX_MASTER_PASSWORD`` configured.
"""

from __future__ import annotations

import logging
import os

from src.security.secure_secrets_manager import SecureSecretsManager, get_secrets_manager

logger = logging.getLogger(__name__)


class SecretsManager:
    """Backward-compatible secret lookup with secure persistence semantics."""

    def __init__(
        self,
        secrets_file: str = "config/encrypted_secrets.json",
        password: str | None = None,
    ):
        self.secrets_file = secrets_file
        self._delegate: SecureSecretsManager | None = None
        if password:
            logger.warning(
                "The legacy password argument is ignored; configure FENIX_MASTER_PASSWORD"
            )
        if os.getenv("FENIX_MASTER_PASSWORD"):
            try:
                self._delegate = get_secrets_manager()
            except RuntimeError as exc:
                logger.error("Encrypted secret vault is unavailable: %s", exc)

    @staticmethod
    def _key_aliases(key: str) -> list[str]:
        aliases = [key]
        normalized = key.upper()
        if normalized == "BINANCE_API_KEY":
            aliases.extend(["binance_api_key", "BINANCE_TESTNET_API_KEY"])
        if normalized in {"BINANCE_API_SECRET", "BINANCE_SECRET_KEY"}:
            aliases.extend(
                [
                    "BINANCE_API_SECRET",
                    "binance_api_secret",
                    "BINANCE_SECRET_KEY",
                    "binance_secret_key",
                    "BINANCE_TESTNET_API_SECRET",
                ]
            )
        return list(dict.fromkeys(aliases))

    def get_secret(self, key: str, default=None):
        if self._delegate is not None:
            for alias in self._key_aliases(key):
                value = self._delegate.get_secret(alias.lower())
                if value is not None:
                    return value
        for alias in self._key_aliases(key):
            value = os.getenv(alias)
            if value:
                return value
        return default

    def set_secret(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        if self._delegate is None:
            logger.error(
                "Secret persistence requires FENIX_MASTER_PASSWORD; no plaintext fallback was used"
            )
            return False
        return self._delegate.store_secret(
            key.lower(),
            str(value),
            ttl_seconds=ttl_seconds,
        )

    def rotate_key(self) -> bool:
        logger.warning(
            "General vault rotation is not automatic; rotate service credentials explicitly"
        )
        return False

    def validate_integrity(self) -> bool:
        return self._delegate.validate_integrity() if self._delegate is not None else True

    def emergency_lockdown(self) -> None:
        if self._delegate is not None:
            self._delegate.emergency_lockdown()
