import os
import sys

# Asegurar que el directorio raíz del proyecto esté en sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config.secrets_manager import SecretsManager

sm = SecretsManager()
api_key = sm.get_secret("BINANCE_API_KEY")
api_secret = sm.get_secret("BINANCE_API_SECRET")
