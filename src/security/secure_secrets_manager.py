# security/secure_secrets_manager.py
import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Any
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

class EncryptedVault:
    """Vault encriptado para almacenar secretos de forma segura"""
    
    def __init__(self, encryption_key: bytes):
        self.fernet = Fernet(encryption_key)
        self.vault_file = Path("security/.vault.enc")
        self.vault_file.parent.mkdir(exist_ok=True)
        self._vault_data = self._load_vault()
    
    def _load_vault(self) -> Dict[str, Any]:
        """Cargar vault desde archivo encriptado"""
        if not self.vault_file.exists():
            return {}
        
        try:
            with open(self.vault_file, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error(f"Error loading vault: {e}")
            return {}
    
    def _save_vault(self):
        """Guardar vault en archivo encriptado"""
        try:
            json_data = json.dumps(self._vault_data, indent=2)
            encrypted_data = self.fernet.encrypt(json_data.encode())
            
            with open(self.vault_file, 'wb') as f:
                f.write(encrypted_data)
        except Exception as e:
            logger.error(f"Error saving vault: {e}")
    
    def encrypt(self, value: str) -> str:
        """Encriptar un valor"""
        return self.fernet.encrypt(value.encode()).decode()
    
    def decrypt(self, encrypted_value: str) -> str:
        """Desencriptar un valor"""
        return self.fernet.decrypt(encrypted_value.encode()).decode()
    
    def store(self, key: str, encrypted_value: str, ttl: int = 3600):
        """Almacenar valor encriptado con TTL"""
        expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        self._vault_data[key] = {
            'value': encrypted_value,
            'expiry': expiry.isoformat(),
            'created': datetime.now(timezone.utc).isoformat()
        }
        self._save_vault()
    
    def retrieve(self, key: str) -> Optional[str]:
        """Recuperar valor del vault"""
        if key not in self._vault_data:
            return None
        
        entry = self._vault_data[key]
        expiry = datetime.fromisoformat(entry['expiry'])
        
        if datetime.now(timezone.utc) > expiry:
            del self._vault_data[key]
            self._save_vault()
            return None
        
        return entry['value']

class SecureSecretsManager:
    """Gestor de secretos con encriptación y rotación automática"""
    
    def __init__(self, master_password: Optional[str] = None):
        # SECURITY: Never default to a known password. Require FENIX_MASTER_PASSWORD
        # env var or explicit argument. Fall back to a random ephemeral key only in
        # development (ENCRYPTION_KEY) so the vault is unusable without configuration.
        env_pw = os.getenv('FENIX_MASTER_PASSWORD')
        if master_password:
            self.master_password = master_password
        elif env_pw:
            self.master_password = env_pw
        elif os.getenv('FENIX_ALLOW_INSECURE_DEV') == '1':
            logger.warning("FENIX_MASTER_PASSWORD not set — using insecure dev fallback. DO NOT use in production.")
            self.master_password = 'insecure_dev_only'
        else:
            raise RuntimeError(
                "FENIX_MASTER_PASSWORD is required. Set it in your .env or environment."
            )
        self.encryption_key = self._generate_or_load_key()
        self.vault = EncryptedVault(self.encryption_key)
        self.rotation_schedule = {}
        
    def _generate_or_load_key(self) -> bytes:
        """Generar o cargar clave de encriptación"""
        key_file = Path("security/.key")
        key_file.parent.mkdir(exist_ok=True)
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        
        # Generar nueva clave
        password = self.master_password.encode()
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        
        # Guardar clave y salt
        with open(key_file, 'wb') as f:
            f.write(key)
        
        with open(Path("security/.salt"), 'wb') as f:
            f.write(salt)
        
        return key
    
    def store_secret(self, key: str, value: str, ttl_seconds: int = 3600, auto_rotate: bool = False) -> bool:
        """Almacenar secreto encriptado"""
        try:
            encrypted_value = self.vault.encrypt(value)
            self.vault.store(key, encrypted_value, ttl_seconds)
            
            if auto_rotate:
                self.rotation_schedule[key] = {
                    'interval': ttl_seconds // 2,  # Rotar a la mitad del TTL
                    'last_rotation': datetime.now(timezone.utc).isoformat()
                }
            
            logger.info(f"Secret stored: {key} (TTL: {ttl_seconds}s)")
            return True
        except Exception as e:
            logger.error(f"Error storing secret {key}: {e}")
            return False
    
    def get_secret(self, key: str) -> Optional[str]:
        """Recuperar secreto desencriptado"""
        try:
            encrypted_value = self.vault.retrieve(key)
            if encrypted_value is None:
                return None
            
            return self.vault.decrypt(encrypted_value)
        except Exception as e:
            logger.error(f"Error retrieving secret {key}: {e}")
            return None
    
    def rotate_credentials(self, service: str) -> bool:
        """Rotar credenciales de un servicio"""
        try:
            # Implementar lógica específica por servicio
            if service == 'binance':
                return self._rotate_binance_credentials()
            elif service == 'openai':
                return self._rotate_openai_credentials()
            else:
                logger.warning(f"No rotation logic for service: {service}")
                return False
        except Exception as e:
            logger.error(f"Error rotating credentials for {service}: {e}")
            return False
    
    def _rotate_binance_credentials(self) -> bool:
        """Rotar credenciales de Binance"""
        # Placeholder - implementar con API de Binance
        logger.info("Binance credential rotation not implemented yet")
        return True
    
    def _rotate_openai_credentials(self) -> bool:
        """Rotar credenciales de OpenAI"""
        # Placeholder - implementar con API de OpenAI
        logger.info("OpenAI credential rotation not implemented yet")
        return True
    
    def check_rotation_schedule(self):
        """Verificar y ejecutar rotaciones programadas"""
        current_time = datetime.now(timezone.utc)
        
        for key, schedule in self.rotation_schedule.items():
            last_rotation = datetime.fromisoformat(schedule['last_rotation'])
            interval = timedelta(seconds=schedule['interval'])
            
            if current_time - last_rotation > interval:
                service = key.split('_')[0]  # Asumir formato: service_credential
                if self.rotate_credentials(service):
                    self.rotation_schedule[key]['last_rotation'] = current_time.isoformat()
                    logger.info(f"Rotated credentials for {service}")
    
    def validate_integrity(self) -> bool:
        """Validar integridad de configuraciones críticas"""
        try:
            # Verificar que los secretos críticos existen
            critical_secrets = ['binance_api_key', 'binance_secret_key', 'openai_api_key']
            
            for secret in critical_secrets:
                if self.get_secret(secret) is None:
                    logger.error(f"Critical secret missing: {secret}")
                    return False
            
            # Verificar integridad del vault
            if not self.vault.vault_file.exists():
                logger.error("Vault file missing")
                return False
            
            logger.info("Secrets integrity validation passed")
            return True
        except Exception as e:
            logger.error(f"Integrity validation failed: {e}")
            return False
    
    def emergency_lockdown(self):
        """Bloqueo de emergencia - eliminar secretos sensibles de memoria"""
        try:
            self.vault._vault_data.clear()
            self.rotation_schedule.clear()
            logger.warning("Emergency lockdown activated - secrets cleared from memory")
        except Exception as e:
            logger.error(f"Error during emergency lockdown: {e}")

# Instancia global del gestor de secretos
_secrets_manager = None

def get_secrets_manager() -> SecureSecretsManager:
    """Obtener instancia singleton del gestor de secretos"""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecureSecretsManager()
    return _secrets_manager

def migrate_env_secrets(secrets_manager: SecureSecretsManager):
    """Migrar secretos desde variables de entorno al gestor seguro"""
    import os
    
    # Lista de variables de entorno que contienen secretos
    secret_env_vars = [
        'BINANCE_API_KEY',
        'BINANCE_SECRET_KEY',
        'OPENAI_API_KEY',
        'ANTHROPIC_API_KEY',
        'DATABASE_PASSWORD',
        'JWT_SECRET',
        'ENCRYPTION_KEY'
    ]
    
    migrated_count = 0
    
    for env_var in secret_env_vars:
        value = os.getenv(env_var)
        if value:
            # Almacenar en el gestor seguro
            success = secrets_manager.store_secret(
                key=env_var.lower(),
                value=value,
                ttl_seconds=86400 * 30  # 30 días
            )
            
            if success:
                migrated_count += 1
                logger.info(f"Migrated secret: {env_var}")
                
                # Opcional: remover de variables de entorno
                # os.environ.pop(env_var, None)
    
    logger.info(f"Migration completed: {migrated_count} secrets migrated")
    return migrated_count

def init_secrets():
    """Inicializar sistema de secretos"""
    manager = get_secrets_manager()
    
    # Migrar secretos existentes si es necesario
    if not manager.validate_integrity():
        logger.warning("Migrating existing secrets to secure vault...")
        _migrate_existing_secrets(manager)
    
    return manager

def _migrate_existing_secrets(manager: SecureSecretsManager):
    """Migrar secretos existentes al vault seguro"""
    try:
        # Migrar desde variables de entorno
        env_secrets = {
            'binance_api_key': 'BINANCE_API_KEY',
            'binance_secret_key': 'BINANCE_SECRET_KEY',
            'openai_api_key': 'OPENAI_API_KEY',
            'anthropic_api_key': 'ANTHROPIC_API_KEY'
        }
        
        for secret_key, env_var in env_secrets.items():
            value = os.getenv(env_var)
            if value:
                manager.store_secret(secret_key, value, ttl=86400, auto_rotate=True)
                logger.info(f"Migrated {secret_key} to secure vault")
        
    except Exception as e:
        logger.error(f"Error migrating secrets: {e}")