"""
Cache configuration for different types of analysis
"""

from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class CacheConfig:
    """Cache configuration by analysis type"""
    ttl_seconds: int
    max_entries: int = 1000
    invalidation_patterns: list = None
    priority: int = 1  # 1=highest


# Optimized TTL configurations by analysis type
CACHE_CONFIGS: Dict[str, CacheConfig] = {
    # Sentiment Analysis - Changes frequently
    'sentiment': CacheConfig(
        ttl_seconds=300,  # 5 minutes
        max_entries=500,
        invalidation_patterns=['news', 'social'],
        priority=2
    ),
    
    # Technical Analysis - Stable for short periods
    'technical': CacheConfig(
        ttl_seconds=180,  # 3 minutes
        max_entries=800,
        invalidation_patterns=['price', 'volume'],
        priority=1
    ),
    
    # Visual Analysis - More expensive, cache for longer
    'visual': CacheConfig(
        ttl_seconds=600,  # 10 minutes
        max_entries=200,
        invalidation_patterns=['chart', 'image'],
        priority=1
    ),
    
    # QABBA Analysis - Mathematical calculations, cache for longer
    'qabba': CacheConfig(
        ttl_seconds=240,  # 4 minutes
        max_entries=600,
        invalidation_patterns=['price', 'indicators'],
        priority=1
    ),
    
    # Decision Analysis - Combines other analyses, short TTL
    'decision': CacheConfig(
        ttl_seconds=120,  # 2 minutes
        max_entries=300,
        invalidation_patterns=['all'],
        priority=3
    ),
    
    # Model Responses - General cache for model responses
    'model_response': CacheConfig(
        ttl_seconds=300,  # 5 minutes
        max_entries=1000,
        priority=2
    )
}


def get_cache_config(analysis_type: str) -> CacheConfig:
    """Gets cache configuration for an analysis type"""
    return CACHE_CONFIGS.get(analysis_type, CACHE_CONFIGS['model_response'])
