"""
Utilidades de Cache para FenixAI Trading Bot
"""

import hashlib
import json
import time
from typing import Any, Optional, Dict, Union
import logging

logger = logging.getLogger(__name__)


def generate_cache_key(
    agent_type: str,
    prompt: str,
    model_id: Optional[str] = None,
    **kwargs
) -> str:
    """
    Genera una clave de cache única basada en los parámetros de entrada
    
    Args:
        agent_type: Tipo de agente
        prompt: Prompt utilizado
        model_id: ID del modelo (opcional)
        **kwargs: Parámetros adicionales (temperature, max_tokens, etc.)
    
    Returns:
        Clave de cache única
    """
    # Normalizar parámetros para generar clave consistente
    cache_data = {
        'agent_type': agent_type,
        'prompt': prompt[:500],  # Limitar longitud del prompt
        'model_id': model_id,
        **{k: v for k, v in kwargs.items() if k in ['temperature', 'max_tokens', 'top_p']}
    }
    
    # Stable cache identity; SHA-256 also avoids legacy weak-hash scanners.
    content = json.dumps(cache_data, sort_keys=True, ensure_ascii=False)
    hash_obj = hashlib.sha256(content.encode('utf-8'))
    
    # Incluir timestamp truncado para agrupación por tiempo
    timestamp_group = int(time.time() // 60)  # Grupo por minutos
    
    return f"{agent_type}:{hash_obj.hexdigest()[:12]}:{timestamp_group}"


def is_cache_valid(cached_data: Dict[str, Any], ttl_seconds: int) -> bool:
    """
    Verifica si los datos cacheados siguen siendo válidos
    
    Args:
        cached_data: Datos del cache con timestamp
        ttl_seconds: Tiempo de vida en segundos
    
    Returns:
        True si el cache es válido
    """
    if not cached_data or 'timestamp' not in cached_data:
        return False
    
    age_seconds = time.time() - cached_data['timestamp']
    return age_seconds < ttl_seconds


def should_invalidate_cache(analysis_type: str, invalidation_trigger: str) -> bool:
    """
    Determina si se debe invalidar el cache basado en triggers
    
    Args:
        analysis_type: Tipo de análisis
        invalidation_trigger: Trigger que podría invalidar el cache
    
    Returns:
        True si se debe invalidar el cache
    """
    from .cache_config import get_cache_config
    
    config = get_cache_config(analysis_type)
    
    if not config.invalidation_patterns:
        return False
    
    return any(
        pattern in invalidation_trigger.lower() 
        for pattern in config.invalidation_patterns
    )


def serialize_for_cache(data: Any) -> Dict[str, Any]:
    """
    Serializa datos para almacenamiento en cache
    
    Args:
        data: Datos a serializar
    
    Returns:
        Datos serializados con metadata
    """
    return {
        'data': data,
        'timestamp': time.time(),
        'size_bytes': len(str(data)) if data else 0
    }


def deserialize_from_cache(cached_data: Dict[str, Any]) -> Any:
    """
    Deserializa datos del cache
    
    Args:
        cached_data: Datos cacheados con metadata
    
    Returns:
        Datos originales
    """
    return cached_data.get('data') if cached_data else None


def calculate_cache_priority(
    agent_type: str,
    frequency_score: float,
    size_bytes: int
) -> float:
    """
    Calcula prioridad de cache para políticas de eviction
    
    Args:
        agent_type: Tipo de agente
        frequency_score: Score de frecuencia de acceso (0-1)
        size_bytes: Tamaño en bytes
    
    Returns:
        Prioridad (mayor = más importante)
    """
    from .cache_config import get_cache_config
    
    config = get_cache_config(agent_type)
    
    # Fórmula: config_priority * frequency_score * size_penalty
    size_penalty = max(0.1, 1.0 - (size_bytes / 10000))  # Penalizar elementos grandes
    
    return config.priority * frequency_score * size_penalty
