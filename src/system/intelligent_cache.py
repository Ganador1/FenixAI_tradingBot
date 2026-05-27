# system/intelligent_cache.py
"""
Sistema de Caché Inteligente Unificado para Fenix Trading Bot
Combina funcionalidades de cache inteligente y smart cache con Redis
"""

import asyncio
import hashlib
import json
import logging
import pickle
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


class CacheStrategy(Enum):
    """Estrategias de caché"""

    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    FIFO = "fifo"  # First In First Out
    TTL = "ttl"  # Time To Live
    ADAPTIVE = "adaptive"  # Adaptativo basado en patrones


class CacheLevel(Enum):
    """Niveles de caché"""

    MEMORY = "memory"
    DISK = "disk"
    DISTRIBUTED = "distributed"


@dataclass
class CacheEntry:
    """Entrada de caché"""

    key: str
    value: Any
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    ttl_seconds: int | None = None
    size_bytes: int = 0
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        """Verificar si la entrada ha expirado"""
        if self.ttl_seconds is None:
            return False
        return (datetime.now() - self.created_at).total_seconds() > self.ttl_seconds

    @property
    def age_seconds(self) -> float:
        """Edad de la entrada en segundos"""
        return (datetime.now() - self.created_at).total_seconds()

    def touch(self):
        """Marcar como accedida"""
        self.last_accessed = datetime.now()
        self.access_count += 1


@dataclass
class CacheStats:
    """Estadísticas de caché"""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size_bytes: int = 0
    entry_count: int = 0

    @property
    def hit_rate(self) -> float:
        """Tasa de aciertos"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def miss_rate(self) -> float:
        """Tasa de fallos"""
        return 1.0 - self.hit_rate


class IntelligentCache:
    """Sistema de caché inteligente con múltiples estrategias y soporte Redis"""

    # --- NUEVAS UTILIDADES ASYNC PARA LIVE_TRADING ---
    async def calculate_volatility_metrics(self, tech_metrics: dict[str, Any]) -> dict[str, float]:
        """Calcular métricas de volatilidad a partir de tech_metrics.
        Retorna un dict con al menos 'volatility_score'. Esta implementación
        es básica para evitar AttributeError; puede mejorarse según necesidad."""
        try:
            # Ejemplo simple: usar desviación estándar de variaciones recientes si existe
            import numpy as np

            price_series = tech_metrics.get("close_buf") or []
            if price_series:
                volatility = float(np.std(price_series[-50:]))  # ventana fija de 50
            else:
                volatility = 0.0
        except Exception:
            volatility = 0.0
        return {"volatility_score": volatility}

    async def update_cache_ttl(self, volatility_metrics: dict[str, float]):
        """Actualizar el TTL dinámicamente según volatilidad.
        A mayor volatilidad, menor TTL para refrescar datos; viceversa."""
        try:
            volatility = volatility_metrics.get("volatility_score", 0.0)
            if volatility >= 5:  # umbral arbitrario
                self.default_ttl = max(300, self.default_ttl // 2 if self.default_ttl else 300)
            elif volatility <= 1:
                self.default_ttl = min(7200, (self.default_ttl or 3600) * 2)
        except Exception as e:
            logger.warning(f"update_cache_ttl error: {e}")
        # No es necesario devolver nada

    async def schedule_model_preload(self, model_manager: Any):
        """Placeholder for pre-loading models based on cache state."""
        logger.info(f"Cache {self.name}: Model pre-loading scheduling not implemented.")
        # En una implementación real, esto podría analizar patrones de acceso
        # y solicitar a model_manager que cargue modelos predictivamente.
        pass

    def __init__(
        self,
        name: str,
        max_size_mb: int = 100,
        default_ttl: int | None = None,
        strategy: CacheStrategy = CacheStrategy.ADAPTIVE,
        cleanup_interval: int = 300,  # 5 minutos
        use_redis: bool = False,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
    ):
        self.name = name
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.default_ttl = default_ttl
        self.strategy = strategy
        self.cleanup_interval = cleanup_interval

        # Redis configuration
        self.use_redis = use_redis and REDIS_AVAILABLE
        self.redis_client = None
        if self.use_redis:
            try:
                self.redis_client = redis.Redis(
                    host=redis_host, port=redis_port, db=redis_db, decode_responses=False
                )
                # Test connection
                self.redis_client.ping()
                logger.info(f"Redis cache enabled for {name}")
            except Exception as e:
                logger.warning(f"Redis not available for cache {name}: {e}")
                self.use_redis = False
                self.redis_client = None

        # Almacenamiento principal
        self._cache: dict[str, CacheEntry] = {}
        self._access_order = OrderedDict()  # Para LRU
        self._frequency_counter = defaultdict(int)  # Para LFU

        # Índices para búsqueda rápida
        self._tag_index: dict[str, set] = defaultdict(set)
        self._dependency_index: dict[str, set] = defaultdict(set)

        # Estadísticas
        self.stats = CacheStats()

        # Control de concurrencia
        self._lock = threading.RLock()

        # Callbacks
        self._eviction_callbacks: list[Callable] = []
        self._hit_callbacks: list[Callable] = []
        self._miss_callbacks: list[Callable] = []

        # Patrones de acceso para estrategia adaptativa
        self._access_patterns: dict[str, list[datetime]] = defaultdict(list)
        self._pattern_analysis_interval = 3600  # 1 hora

        # Tarea de limpieza
        self._cleanup_task: asyncio.Task | None = None
        self._start_cleanup_task()

    def _start_cleanup_task(self):
        """Iniciar tarea de limpieza automática"""
        try:
            loop = asyncio.get_event_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
        except RuntimeError:
            # No hay loop activo, la limpieza se hará manualmente
            pass

    async def _cleanup_loop(self):
        """Loop de limpieza automática"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                self.cleanup_expired()
                self._analyze_access_patterns()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache cleanup loop: {e}")

    def _calculate_size(self, value: Any) -> int:
        """Calcular tamaño aproximado de un valor"""
        try:
            if isinstance(value, (str, bytes)):
                return len(value)
            elif isinstance(value, (int, float, bool)):
                return 8
            elif isinstance(value, (list, tuple)):
                return sum(self._calculate_size(item) for item in value)
            elif isinstance(value, dict):
                return sum(
                    self._calculate_size(k) + self._calculate_size(v) for k, v in value.items()
                )
            else:
                # Usar pickle para estimar tamaño
                return len(pickle.dumps(value))
        except Exception:
            return 1024  # Estimación por defecto

    def _generate_key(self, key: str | tuple | dict) -> str:
        """Generar clave de caché normalizada"""
        if isinstance(key, str):
            return key
        elif isinstance(key, (tuple, list)):
            return hashlib.md5(str(sorted(key)).encode()).hexdigest()
        elif isinstance(key, dict):
            return hashlib.md5(json.dumps(key, sort_keys=True).encode()).hexdigest()
        else:
            return hashlib.md5(str(key).encode()).hexdigest()

    def get(self, key: str | tuple | dict, default: Any = None) -> Any:
        """Obtener valor del caché"""
        cache_key = self._generate_key(key)

        # Intentar Redis primero si está disponible
        if self.use_redis and self.redis_client:
            try:
                redis_key = f"{self.name}:{cache_key}"
                cached_data = self.redis_client.get(redis_key)
                if cached_data:
                    value = pickle.loads(cached_data)
                    self.stats.hits += 1
                    self._execute_callbacks(self._hit_callbacks, cache_key, value)
                    return value
            except Exception as e:
                logger.warning(f"Redis get error for {cache_key}: {e}")

        # Fallback a cache local
        with self._lock:
            entry = self._cache.get(cache_key)

            if entry is None:
                self.stats.misses += 1
                self._execute_callbacks(self._miss_callbacks, cache_key)
                return default

            if entry.is_expired:
                self._remove_entry(cache_key)
                self.stats.misses += 1
                self._execute_callbacks(self._miss_callbacks, cache_key)
                return default

            # Actualizar estadísticas de acceso
            entry.touch()
            self._update_access_tracking(cache_key)

            self.stats.hits += 1
            self._execute_callbacks(self._hit_callbacks, cache_key, entry.value)

            return entry.value

    def set(
        self,
        key: str | tuple | dict,
        value: Any,
        ttl: int | None = None,
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
    ) -> bool:
        """Establecer valor en caché"""
        cache_key = self._generate_key(key)
        ttl = ttl or self.default_ttl
        tags = tags or []
        dependencies = dependencies or []

        # Intentar guardar en Redis primero si está disponible
        if self.use_redis and self.redis_client:
            try:
                redis_key = f"{self.name}:{cache_key}"
                serialized_value = pickle.dumps(value)
                if ttl:
                    self.redis_client.setex(redis_key, ttl, serialized_value)
                else:
                    self.redis_client.set(redis_key, serialized_value)
                logger.debug(f"Cached {cache_key} in Redis")
            except Exception as e:
                logger.warning(f"Redis set error for {cache_key}: {e}")

        with self._lock:
            # Calcular tamaño
            size_bytes = self._calculate_size(value)

            # Verificar si hay espacio suficiente
            if not self._ensure_space(size_bytes):
                logger.warning(f"Cannot cache {cache_key}: insufficient space")
                return False

            # Crear entrada
            entry = CacheEntry(
                key=cache_key,
                value=value,
                created_at=datetime.now(),
                last_accessed=datetime.now(),
                ttl_seconds=ttl,
                size_bytes=size_bytes,
                tags=tags,
                dependencies=dependencies,
            )

            # Remover entrada existente si existe
            if cache_key in self._cache:
                self._remove_entry(cache_key)

            # Agregar nueva entrada
            self._cache[cache_key] = entry
            self._update_indices(cache_key, entry)
            self._update_access_tracking(cache_key)

            # Actualizar estadísticas
            self.stats.size_bytes += size_bytes
            self.stats.entry_count += 1

            return True

    def delete(self, key: str | tuple | dict) -> bool:
        """Eliminar entrada del caché"""
        cache_key = self._generate_key(key)

        with self._lock:
            if cache_key in self._cache:
                self._remove_entry(cache_key)
                return True
            return False

    def invalidate_by_tag(self, tag: str) -> int:
        """Invalidar entradas por tag"""
        with self._lock:
            keys_to_remove = list(self._tag_index.get(tag, set()))
            for key in keys_to_remove:
                self._remove_entry(key)
            return len(keys_to_remove)

    def invalidate_by_dependency(self, dependency: str) -> int:
        """Invalidar entradas por dependencia"""
        with self._lock:
            keys_to_remove = list(self._dependency_index.get(dependency, set()))
            for key in keys_to_remove:
                self._remove_entry(key)
            return len(keys_to_remove)

    def clear(self):
        """Limpiar todo el caché"""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self._frequency_counter.clear()
            self._tag_index.clear()
            self._dependency_index.clear()
            self._access_patterns.clear()
            self.stats = CacheStats()

    def cleanup_expired(self) -> int:
        """Limpiar entradas expiradas"""
        with self._lock:
            expired_keys = [key for key, entry in self._cache.items() if entry.is_expired]

            for key in expired_keys:
                self._remove_entry(key)

            return len(expired_keys)

    def _ensure_space(self, required_bytes: int) -> bool:
        """Asegurar espacio suficiente en caché"""
        if self.stats.size_bytes + required_bytes <= self.max_size_bytes:
            return True

        # Necesitamos liberar espacio
        bytes_to_free = (self.stats.size_bytes + required_bytes) - self.max_size_bytes

        if self.strategy == CacheStrategy.LRU:
            return self._evict_lru(bytes_to_free)
        elif self.strategy == CacheStrategy.LFU:
            return self._evict_lfu(bytes_to_free)
        elif self.strategy == CacheStrategy.FIFO:
            return self._evict_fifo(bytes_to_free)
        elif self.strategy == CacheStrategy.TTL:
            return self._evict_ttl(bytes_to_free)
        elif self.strategy == CacheStrategy.ADAPTIVE:
            return self._evict_adaptive(bytes_to_free)

        return False

    def _evict_lru(self, bytes_to_free: int) -> bool:
        """Evicción LRU (Least Recently Used)"""
        freed_bytes = 0

        # Ordenar por último acceso
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].last_accessed)

        for key, entry in sorted_entries:
            if freed_bytes >= bytes_to_free:
                break

            freed_bytes += entry.size_bytes
            self._remove_entry(key)
            self.stats.evictions += 1

        return freed_bytes >= bytes_to_free

    def _evict_lfu(self, bytes_to_free: int) -> bool:
        """Evicción LFU (Least Frequently Used)"""
        freed_bytes = 0

        # Ordenar por frecuencia de acceso
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].access_count)

        for key, entry in sorted_entries:
            if freed_bytes >= bytes_to_free:
                break

            freed_bytes += entry.size_bytes
            self._remove_entry(key)
            self.stats.evictions += 1

        return freed_bytes >= bytes_to_free

    def _evict_fifo(self, bytes_to_free: int) -> bool:
        """Evicción FIFO (First In First Out)"""
        freed_bytes = 0

        # Ordenar por tiempo de creación
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].created_at)

        for key, entry in sorted_entries:
            if freed_bytes >= bytes_to_free:
                break

            freed_bytes += entry.size_bytes
            self._remove_entry(key)
            self.stats.evictions += 1

        return freed_bytes >= bytes_to_free

    def _evict_ttl(self, bytes_to_free: int) -> bool:
        """Evicción basada en TTL"""
        freed_bytes = 0

        # Primero eliminar expirados
        expired_keys = [key for key, entry in self._cache.items() if entry.is_expired]

        for key in expired_keys:
            entry = self._cache[key]
            freed_bytes += entry.size_bytes
            self._remove_entry(key)
            self.stats.evictions += 1

            if freed_bytes >= bytes_to_free:
                return True

        # Si no es suficiente, usar LRU
        return self._evict_lru(bytes_to_free - freed_bytes)

    def _evict_adaptive(self, bytes_to_free: int) -> bool:
        """Evicción adaptativa basada en patrones"""
        # Combinar múltiples factores para decidir qué evictar
        scored_entries = []

        for key, entry in self._cache.items():
            score = self._calculate_eviction_score(key, entry)
            scored_entries.append((score, key, entry))

        # Ordenar por score (menor score = más probable de ser evictado)
        scored_entries.sort(key=lambda x: x[0])

        freed_bytes = 0
        for score, key, entry in scored_entries:
            if freed_bytes >= bytes_to_free:
                break

            freed_bytes += entry.size_bytes
            self._remove_entry(key)
            self.stats.evictions += 1

        return freed_bytes >= bytes_to_free

    def _calculate_eviction_score(self, key: str, entry: CacheEntry) -> float:
        """Calcular score de evicción para estrategia adaptativa"""
        score = 0.0

        # Factor de frecuencia (más accesos = mayor score)
        score += entry.access_count * 0.3

        # Factor de recencia (más reciente = mayor score)
        age_hours = entry.age_seconds / 3600
        score += max(0, 24 - age_hours) * 0.2

        # Factor de tamaño (más pequeño = mayor score)
        size_mb = entry.size_bytes / (1024 * 1024)
        score += max(0, 10 - size_mb) * 0.1

        # Factor de patrón de acceso
        pattern_score = self._analyze_access_pattern(key)
        score += pattern_score * 0.4

        return score

    def _analyze_access_pattern(self, key: str) -> float:
        """Analizar patrón de acceso para una clave"""
        accesses = self._access_patterns.get(key, [])
        if len(accesses) < 2:
            return 0.0

        # Calcular frecuencia de acceso reciente
        now = datetime.now()
        recent_accesses = [
            access
            for access in accesses
            if (now - access).total_seconds() < 3600  # Última hora
        ]

        return len(recent_accesses) / len(accesses)

    def _analyze_access_patterns(self):
        """Analizar patrones de acceso globales"""
        # Limpiar patrones antiguos
        cutoff = datetime.now() - timedelta(hours=24)

        for key in list(self._access_patterns.keys()):
            self._access_patterns[key] = [
                access for access in self._access_patterns[key] if access > cutoff
            ]

            if not self._access_patterns[key]:
                del self._access_patterns[key]

    def _update_access_tracking(self, key: str):
        """Actualizar seguimiento de accesos"""
        self._access_patterns[key].append(datetime.now())

        # Mantener solo los últimos 100 accesos
        if len(self._access_patterns[key]) > 100:
            self._access_patterns[key] = self._access_patterns[key][-100:]

        # Actualizar orden de acceso para LRU
        if key in self._access_order:
            del self._access_order[key]
        self._access_order[key] = datetime.now()

        # Actualizar contador de frecuencia para LFU
        self._frequency_counter[key] += 1

    def _update_indices(self, key: str, entry: CacheEntry):
        """Actualizar índices de búsqueda"""
        # Índice de tags
        for tag in entry.tags:
            self._tag_index[tag].add(key)

        # Índice de dependencias
        for dependency in entry.dependencies:
            self._dependency_index[dependency].add(key)

    def _remove_entry(self, key: str):
        """Remover entrada y actualizar índices"""
        if key not in self._cache:
            return

        entry = self._cache[key]

        # Remover de caché principal
        del self._cache[key]

        # Remover de índices
        for tag in entry.tags:
            self._tag_index[tag].discard(key)
            if not self._tag_index[tag]:
                del self._tag_index[tag]

        for dependency in entry.dependencies:
            self._dependency_index[dependency].discard(key)
            if not self._dependency_index[dependency]:
                del self._dependency_index[dependency]

        # Remover de estructuras de seguimiento
        self._access_order.pop(key, None)
        self._frequency_counter.pop(key, None)
        self._access_patterns.pop(key, None)

        # Actualizar estadísticas
        self.stats.size_bytes -= entry.size_bytes
        self.stats.entry_count -= 1

        # Ejecutar callbacks de evicción
        self._execute_callbacks(self._eviction_callbacks, key, entry.value)

    def _execute_callbacks(self, callbacks: list[Callable], *args):
        """Ejecutar callbacks de manera segura"""
        for callback in callbacks:
            try:
                callback(*args)
            except Exception as e:
                logger.error(f"Error in cache callback: {e}")

    def register_eviction_callback(self, callback: Callable[[str, Any], None]):
        """Registrar callback de evicción"""
        self._eviction_callbacks.append(callback)

    def register_hit_callback(self, callback: Callable[[str, Any], None]):
        """Registrar callback de hit"""
        self._hit_callbacks.append(callback)

    def register_miss_callback(self, callback: Callable[[str], None]):
        """Registrar callback de miss"""
        self._miss_callbacks.append(callback)

    def get_stats(self) -> CacheStats:
        """Obtener estadísticas del caché"""
        return self.stats

    def get_info(self) -> dict[str, Any]:
        """Obtener información detallada del caché"""
        with self._lock:
            return {
                "name": self.name,
                "strategy": self.strategy.value,
                "max_size_mb": self.max_size_bytes / (1024 * 1024),
                "current_size_mb": self.stats.size_bytes / (1024 * 1024),
                "entry_count": self.stats.entry_count,
                "hit_rate": self.stats.hit_rate,
                "miss_rate": self.stats.miss_rate,
                "total_hits": self.stats.hits,
                "total_misses": self.stats.misses,
                "total_evictions": self.stats.evictions,
                "tags_count": len(self._tag_index),
                "dependencies_count": len(self._dependency_index),
            }

    def __del__(self):
        """Cleanup al destruir el objeto"""
        if self._cleanup_task:
            self._cleanup_task.cancel()


# Decorador para caché automático
def cached(
    cache_name: str = "default",
    ttl: int | None = None,
    tags: list[str] | None = None,
    key_func: Callable | None = None,
):
    """Decorador para caché automático de funciones"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            # Generar clave de caché
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = f"{func.__name__}:{hash((args, tuple(sorted(kwargs.items()))))}"

            # Obtener caché
            cache = get_cache(cache_name)

            # Intentar obtener del caché
            result = cache.get(cache_key)
            if result is not None:
                return result

            # Ejecutar función y cachear resultado
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl=ttl, tags=tags)

            return result

        return wrapper

    return decorator


# Gestor global de caches
_caches: dict[str, IntelligentCache] = {}
_cache_lock = threading.Lock()


def get_cache(name: str, **kwargs) -> IntelligentCache:
    """Obtener o crear caché por nombre"""
    with _cache_lock:
        if name not in _caches:
            _caches[name] = IntelligentCache(name, **kwargs)
        return _caches[name]


def clear_all_caches():
    """Limpiar todos los caches"""
    with _cache_lock:
        for cache in _caches.values():
            cache.clear()


def get_all_cache_stats() -> dict[str, dict[str, Any]]:
    """Obtener estadísticas de todos los caches"""
    with _cache_lock:
        return {name: cache.get_info() for name, cache in _caches.items()}
