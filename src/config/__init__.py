"""
Módulo de configuración del sistema de trading.
"""

import os
from pathlib import Path

import yaml

# Ruta al archivo de configuración
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    """Carga la configuración desde el archivo YAML."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Archivo de configuración no encontrado: {CONFIG_PATH}")
        return {}
    except yaml.YAMLError as e:
        print(f"Error al cargar configuración: {e}")
        return {}


# Cargar configuración al importar el módulo
config = load_config()

# Exportar la configuración
__all__ = ["config", "load_config"]
