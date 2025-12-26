"""
Configuration module for the trading system.
"""

import yaml
import os
from pathlib import Path

# Path to the configuration file
CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_config():
    """Loads the configuration from the YAML file."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Configuration file not found: {CONFIG_PATH}")
        return {}
    except yaml.YAMLError as e:
        print(f"Error loading configuration: {e}")
        return {}

# Load configuration when the module is imported
config = load_config()

# Export the configuration
__all__ = ['config', 'load_config']
