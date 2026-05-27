import base64
import json
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Intentar usar el gestor seguro unificado si está disponible
_SECURE_SM_AVAILABLE = False
try:
    from src.security.secure_secrets_manager import (
        SecureSecretsManager,
        get_secrets_manager,
        migrate_env_secrets,
    )

    _SECURE_SM_AVAILABLE = True
except Exception:
    # No bloquear si el módulo seguro no está disponible
    _SECURE_SM_AVAILABLE = False


class SecretsManager:
    def __init__(
        self, secrets_file: str = "config/encrypted_secrets.json", password: str | None = None
    ):
        self.logger = logging.getLogger(__name__)
        self.secrets_file = secrets_file
        self.password = password or os.getenv("SECRETS_PASSWORD")

        # Si existe el SecureSecretsManager, delegar en él para evitar duplicación
        self._delegate: SecureSecretsManager | None = None
        if _SECURE_SM_AVAILABLE:
            try:
                self._delegate = get_secrets_manager()
                # Migrar secretos desde entorno si aún no están en el vault
                try:
                    migrate_env_secrets(self._delegate)
                except Exception:
                    # No crítico; continuar
                    pass
                self.logger.info(
                    "SecretsManager delegando en SecureSecretsManager (vault encriptado)."
                )
            except Exception as e:
                self.logger.warning(
                    f"Fallo inicializando SecureSecretsManager, usando modo local: {e}"
                )
                self._delegate = None

        # Fallback local (compatibilidad histórica)
        if self._delegate is None:
            if not self.password:
                self.logger.warning(
                    "SECRETS_PASSWORD no está configurado; usando solo variables de entorno como fallback."
                )
                self.fernet = None
                self.secrets = {}
            else:
                self.fernet = self._generate_fernet()
                self.secrets = self._load_secrets()

    def _generate_fernet(self):
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"salt_",
            iterations=390000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.password.encode()))
        return Fernet(key)

    def _load_secrets(self):
        if self.fernet and os.path.exists(self.secrets_file):
            try:
                with open(self.secrets_file, "rb") as f:
                    encrypted = f.read()
                decrypted = self.fernet.decrypt(encrypted)
                return json.loads(decrypted)
            except Exception as e:
                self.logger.error(f"Error cargando secretos cifrados: {e}")
                return {}
        return {}

    def _key_aliases(self, key: str):
        # Mapear alias comunes para credenciales críticas
        aliases = [key]
        # Binance
        if key.upper() == "BINANCE_API_KEY":
            aliases += ["binance_api_key", "BINANCE_TESTNET_API_KEY"]
        if key.upper() == "BINANCE_API_SECRET":
            aliases += [
                "binance_api_secret",
                "BINANCE_SECRET_KEY",
                "binance_secret_key",
                "BINANCE_TESTNET_API_SECRET",
            ]
        return aliases

    def get_secret(self, key, default=None):
        # Si delegamos en el gestor seguro, consultar allí primero
        if self._delegate is not None:
            for alias in self._key_aliases(key):
                # Normalizar a formato usado por SecureSecretsManager (lowercase)
                normalized = alias.lower()
                try:
                    value = self._delegate.get_secret(normalized)
                except Exception:
                    value = None
                if value:
                    return value
        # 1) Buscar en almacén cifrado local (si existe)
        if hasattr(self, "secrets") and isinstance(self.secrets, dict):
            for alias in self._key_aliases(key):
                if alias in self.secrets:
                    return self.secrets.get(alias)
        # 2) Fallback: variables de entorno
        for alias in self._key_aliases(key):
            val = os.getenv(alias)
            if val:
                return val
        return default

    def set_secret(self, key, value, ttl_seconds: int | None = None):
        # Si hay delegado seguro, almacenar allí (TTL opcional)
        if self._delegate is not None:
            try:
                # Normalizar clave a lower para el vault seguro
                normalized = key.lower()
                self._delegate.store_secret(normalized, str(value), ttl_seconds or 3600)
                return True
            except Exception as e:
                self.logger.error(f"Error guardando secreto en SecureSecretsManager: {e}")
                # Si falla el delegado, continuar con almacenamiento local

        # Modo local (compatibilidad)
        if not hasattr(self, "secrets"):
            self.secrets = {}
        self.secrets[key] = value
        self._save_secrets()
        return True

    def _save_secrets(self):
        if self._delegate is not None:
            # El delegado maneja persistencia segura; no guardar duplicado
            return
        if not getattr(self, "fernet", None):
            # Sin password: no podemos guardar cifrado; advertir y salir
            self.logger.warning(
                "No se guardan secretos: SECRETS_PASSWORD no configurado (modo solo entorno)."
            )
            return
        try:
            encrypted = self.fernet.encrypt(json.dumps(self.secrets).encode())
            with open(self.secrets_file, "wb") as f:
                f.write(encrypted)
            self.logger.info(f"Secrets guardados en {self.secrets_file}")
        except Exception as e:
            self.logger.error(f"Error guardando secretos cifrados: {e}")

    def rotate_key(self):
        if self._delegate is not None:
            try:
                # El SecureSecretsManager maneja rotaciones por servicio; aquí no hay rotación general
                self.logger.info(
                    "Rotación gestionada por SecureSecretsManager (use rotate_credentials por servicio)."
                )
                return True
            except Exception:
                pass
        # Lógica simple de rotación local: regenerar Fernet con nueva password
        self.logger.warning(
            "Rotación de keys local no implementada completamente. Actualice SECRETS_PASSWORD manualmente."
        )
        return False

    # Métodos opcionales de compatibilidad para integraciones avanzadas
    def validate_integrity(self) -> bool:
        if self._delegate is not None:
            try:
                return bool(self._delegate.validate_integrity())
            except Exception:
                return False
        # En modo local, consideramos integridad como existencia del archivo si hay password
        if getattr(self, "fernet", None):
            return os.path.exists(self.secrets_file)
        return True

    def emergency_lockdown(self):
        if self._delegate is not None:
            try:
                self._delegate.emergency_lockdown()
            except Exception:
                pass
        # Limpiar memoria local
        try:
            if hasattr(self, "secrets"):
                self.secrets.clear()
        except Exception:
            pass


# Instancia global (usar con precaución)
# secrets_manager = SecretsManager()
