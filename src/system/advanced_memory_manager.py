# system/advanced_memory_manager.py
import asyncio
import gc
import logging
import os
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil

try:
    import mlx.core as mx

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class MemoryThreshold:
    """Umbrales de memoria"""

    warning: float = 0.75  # 75%
    critical: float = 0.85  # 85%
    emergency: float = 0.95  # 95%


@dataclass
class MemoryStats:
    """Estadísticas de memoria"""

    total_gb: float
    used_gb: float
    available_gb: float
    usage_percent: float
    mlx_memory_gb: float = 0.0
    cache_memory_gb: float = 0.0
    buffer_memory_gb: float = 0.0


class MemoryPool:
    """Pool de memoria para objetos reutilizables"""

    def __init__(self, name: str, max_size: int = 100):
        self.name = name
        self.max_size = max_size
        self.pool = []
        self.in_use = set()
        self.created_count = 0
        self.reused_count = 0

    def get(self, factory: Callable = None):
        """Obtener objeto del pool"""
        if self.pool:
            obj = self.pool.pop()
            self.in_use.add(id(obj))
            self.reused_count += 1
            return obj
        elif factory:
            obj = factory()
            self.in_use.add(id(obj))
            self.created_count += 1
            return obj
        return None

    def return_object(self, obj):
        """Devolver objeto al pool"""
        obj_id = id(obj)
        if obj_id in self.in_use:
            self.in_use.remove(obj_id)
            if len(self.pool) < self.max_size:
                # Limpiar objeto antes de devolverlo al pool
                if hasattr(obj, "reset"):
                    obj.reset()
                self.pool.append(obj)

    def clear(self):
        """Limpiar pool"""
        self.pool.clear()
        self.in_use.clear()


class AdvancedMemoryManager:
    """Gestor avanzado de memoria con monitoreo y limpieza automática"""

    _instance: Optional["AdvancedMemoryManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.memory_limits = {
            "mlx_models": 4.0,  # GB
            "cache": 2.0,  # GB
            "buffers": 1.0,  # GB
            "general": 8.0,  # GB
        }

        self.thresholds = MemoryThreshold()
        self.auto_cleanup_enabled = True
        self.monitoring_interval = 30  # segundos
        self.cleanup_callbacks = []
        self.memory_pools = {}
        self.tracked_objects = weakref.WeakSet()
        self.allocation_history = []
        self.cleanup_stats = {
            "automatic_cleanups": 0,
            "emergency_cleanups": 0,
            "objects_freed": 0,
            "memory_freed_gb": 0.0,
        }

        # MLX-specific attributes
        self.model_cache: dict[str, Any] = {}
        self.model_stats: dict[str, dict] = {}
        self.model_lru: list[str] = []
        self.cleanup_threshold = 0.85

        # Iniciar monitoreo
        self._monitoring_task = None
        self._instance_lock = threading.Lock()

        if MLX_AVAILABLE:
            self._setup_mlx_monitoring()

    @classmethod
    def get_instance(cls) -> "AdvancedMemoryManager":
        """Obtener instancia singleton del gestor de memoria"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _setup_mlx_monitoring(self):
        """Configurar monitoreo específico de MLX"""
        try:
            # Configurar límites de memoria para MLX
            mx.set_memory_limit(int(self.memory_limits["mlx_models"] * 1024**3))
            logger.info(f"MLX memory limit set to {self.memory_limits['mlx_models']} GB")
        except Exception as e:
            logger.warning(f"Could not set MLX memory limit: {e}")

    def start_monitoring(self):
        """Iniciar monitoreo automático de memoria"""
        if self._monitoring_task is None:
            self._monitoring_task = asyncio.create_task(self._memory_monitor_loop())
            logger.info("Memory monitoring started")

    def stop_monitoring(self):
        """Detener monitoreo de memoria"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            self._monitoring_task = None
            logger.info("Memory monitoring stopped")

    async def _memory_monitor_loop(self):
        """Loop de monitoreo de memoria"""
        while True:
            try:
                await self.monitor_and_cleanup()
                await asyncio.sleep(self.monitoring_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in memory monitoring loop: {e}")
                await asyncio.sleep(self.monitoring_interval)

    async def monitor_and_cleanup(self):
        """Monitorear memoria y ejecutar limpieza si es necesario"""
        stats = self.get_memory_stats()

        # Log estadísticas periódicamente
        if datetime.now().minute % 5 == 0:  # Cada 5 minutos
            logger.info(
                f"Memory usage: {stats.usage_percent:.1f}% ({stats.used_gb:.2f}/{stats.total_gb:.2f} GB)"
            )

        # Verificar umbrales y ejecutar limpieza
        if stats.usage_percent > self.thresholds.emergency:
            await self.emergency_cleanup()
        elif stats.usage_percent > self.thresholds.critical:
            await self.critical_cleanup()
        elif stats.usage_percent > self.thresholds.warning:
            await self.warning_cleanup()

    def get_memory_stats(self) -> MemoryStats:
        """Obtener estadísticas detalladas de memoria"""
        # Memoria del sistema
        memory = psutil.virtual_memory()
        total_gb = memory.total / (1024**3)
        used_gb = memory.used / (1024**3)
        available_gb = memory.available / (1024**3)
        usage_percent = memory.percent / 100.0

        # Memoria MLX
        mlx_memory_gb = 0.0
        if MLX_AVAILABLE:
            try:
                mlx_memory_gb = mx.metal.get_active_memory() / (1024**3)
            except Exception:
                pass

        # Memoria de cache (estimada)
        cache_memory_gb = self._estimate_cache_memory()

        # Memoria de buffers (estimada)
        buffer_memory_gb = self._estimate_buffer_memory()

        return MemoryStats(
            total_gb=total_gb,
            used_gb=used_gb,
            available_gb=available_gb,
            usage_percent=usage_percent,
            mlx_memory_gb=mlx_memory_gb,
            cache_memory_gb=cache_memory_gb,
            buffer_memory_gb=buffer_memory_gb,
        )

    def _estimate_cache_memory(self) -> float:
        """Estimar memoria usada por caches"""
        # Implementar estimación basada en caches conocidos
        return 0.0

    def _estimate_buffer_memory(self) -> float:
        """Estimar memoria usada por buffers"""
        # Implementar estimación basada en buffers conocidos
        return 0.0

    async def warning_cleanup(self):
        """Limpieza de nivel warning"""
        logger.info("Executing warning-level memory cleanup")

        # Limpiar caches menos críticos
        await self._cleanup_caches(priority="low")

        # Garbage collection suave
        gc.collect()

    async def critical_cleanup(self):
        """Limpieza de nivel crítico"""
        logger.warning("Executing critical-level memory cleanup")
        self.cleanup_stats["automatic_cleanups"] += 1

        # Limpiar todos los caches
        await self._cleanup_caches(priority="all")

        # Limpiar pools de memoria
        self._cleanup_memory_pools()

        # Ejecutar callbacks de limpieza
        await self._execute_cleanup_callbacks("critical")

        # Garbage collection agresivo
        for _ in range(3):
            gc.collect()

    async def emergency_cleanup(self):
        """Limpieza de emergencia"""
        logger.error("Executing emergency memory cleanup")
        self.cleanup_stats["emergency_cleanups"] += 1

        # Limpiar todo agresivamente
        await self._cleanup_caches(priority="all")
        self._cleanup_memory_pools()
        await self._cleanup_mlx_memory()
        await self._execute_cleanup_callbacks("emergency")

        # Garbage collection muy agresivo
        for _ in range(5):
            gc.collect()

        # Forzar liberación de memoria del sistema
        if hasattr(os, "sync"):
            os.sync()

    async def _cleanup_caches(self, priority: str):
        """Limpiar caches según prioridad"""
        # Implementar limpieza específica de caches
        pass

    def _cleanup_memory_pools(self):
        """Limpiar pools de memoria"""
        for pool in self.memory_pools.values():
            pool.clear()
        logger.debug("Memory pools cleared")

    async def _cleanup_mlx_memory(self):
        """Limpiar memoria específica de MLX"""
        if MLX_AVAILABLE:
            try:
                # Limpiar modelos MLX cacheados
                await self.smart_model_cleanup(force=True)

                # Limpiar cache de MLX
                mx.metal.clear_cache()
                logger.debug("MLX memory cache cleared")
            except Exception as e:
                logger.warning(f"Could not clear MLX cache: {e}")

    async def _execute_cleanup_callbacks(self, level: str):
        """Ejecutar callbacks de limpieza registrados"""
        for callback in self.cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(level)
                else:
                    callback(level)
            except Exception as e:
                logger.error(f"Error in cleanup callback: {e}")

    def register_cleanup_callback(self, callback: Callable):
        """Registrar callback de limpieza"""
        self.cleanup_callbacks.append(callback)
        logger.debug(f"Cleanup callback registered: {callback.__name__}")

    def create_memory_pool(self, name: str, max_size: int = 100) -> MemoryPool:
        """Crear pool de memoria"""
        pool = MemoryPool(name, max_size)
        self.memory_pools[name] = pool
        return pool

    def get_memory_pool(self, name: str) -> MemoryPool | None:
        """Obtener pool de memoria"""
        return self.memory_pools.get(name)

    def get_from_pool(self, pool_name: str, factory: Callable = None):
        """Obtener objeto de un pool de memoria específico"""
        pool = self.get_memory_pool(pool_name)
        if pool is None:
            # Crear pool si no existe
            pool = self.create_memory_pool(pool_name)
        return pool.get(factory)

    def return_to_pool(self, pool_name: str, obj):
        """Devolver objeto a un pool de memoria específico"""
        pool = self.get_memory_pool(pool_name)
        if pool is not None:
            pool.return_object(obj)

    def track_object(self, obj: Any, category: str = "general"):
        """Rastrear objeto para gestión de memoria"""
        self.tracked_objects.add(obj)
        self.allocation_history.append(
            {
                "timestamp": datetime.now(),
                "category": category,
                "object_type": type(obj).__name__,
                "size_estimate": self._estimate_object_size(obj),
            }
        )

    def _estimate_object_size(self, obj: Any) -> int:
        """Estimar tamaño de objeto en bytes"""
        try:
            import sys

            return sys.getsizeof(obj)
        except Exception:
            return 0

    def set_memory_limit(self, category: str, limit_gb: float):
        """Establecer límite de memoria para categoría"""
        self.memory_limits[category] = limit_gb
        logger.info(f"Memory limit for {category} set to {limit_gb} GB")

    def get_cleanup_stats(self) -> dict[str, Any]:
        """Obtener estadísticas de limpieza"""
        return {
            **self.cleanup_stats,
            "tracked_objects": len(self.tracked_objects),
            "memory_pools": len(self.memory_pools),
            "allocation_history_size": len(self.allocation_history),
        }

    def force_cleanup(self, level: str = "critical"):
        """Forzar limpieza manual"""
        if level == "emergency":
            asyncio.create_task(self.emergency_cleanup())
        elif level == "critical":
            asyncio.create_task(self.critical_cleanup())
        else:
            asyncio.create_task(self.warning_cleanup())

    # MLX-specific methods
    def can_load_model(self, model_size_mb: int) -> bool:
        """Verificar si hay suficiente memoria para cargar un modelo MLX"""
        current_usage = self.get_memory_stats()
        available = current_usage.available_gb * 1024  # Convertir a MB

        # Dejar margen de seguridad del 20%
        required = model_size_mb * 1.2
        return available > required

    def estimate_model_size(self, model_path: str) -> int:
        """Estimar tamaño del modelo basado en archivos safetensors"""
        try:
            model_dir = Path.home() / ".cache" / "mlx_models" / model_path.replace("/", "_")
            if model_dir.exists():
                total_size = 0
                for file in model_dir.rglob("*.safetensors"):
                    total_size += file.stat().st_size
                return int(total_size / 1024 / 1024)  # MB
            return 500  # Estimación por defecto para modelos de 4-8GB
        except:
            return 500

    def register_model(
        self, model_path: str, model_instance: Any = None, model_info: dict = None
    ) -> None:
        """Registrar modelo MLX cargado en caché"""
        with self._lock:
            if isinstance(model_path, dict):
                model_info = model_path
                model_path = model_info.get("name", "unknown_model")

            if model_info:
                size_mb = model_info.get("size_mb", self.estimate_model_size(model_path))
            else:
                size_mb = self.estimate_model_size(model_path)

            if model_instance is not None:
                self.model_cache[model_path] = model_instance

            self.model_stats[model_path] = {
                "loaded_at": time.time(),
                "last_used": time.time(),
                "use_count": 0,
                "size_mb": size_mb,
            }

            # Actualizar LRU
            if model_path in self.model_lru:
                self.model_lru.remove(model_path)
            self.model_lru.append(model_path)

    def get_model(self, model_path: str) -> Any | None:
        """Obtener modelo MLX del caché"""
        with self._lock:
            if model_path in self.model_cache:
                self.model_stats[model_path]["last_used"] = time.time()
                self.model_stats[model_path]["use_count"] += 1

                # Actualizar LRU
                if model_path in self.model_lru:
                    self.model_lru.remove(model_path)
                self.model_lru.append(model_path)

                return self.model_cache[model_path]
            return None

    def release_model(self, model_path: str) -> bool:
        """Liberar modelo MLX de memoria"""
        with self._lock:
            if model_path in self.model_cache:
                try:
                    # Intentar liberar recursos del modelo
                    model = self.model_cache[model_path]
                    if hasattr(model, "clear_cache"):
                        model.clear_cache()
                    if hasattr(model, "unload"):
                        model.unload()

                    del self.model_cache[model_path]
                    del self.model_stats[model_path]

                    if model_path in self.model_lru:
                        self.model_lru.remove(model_path)

                    # Forzar garbage collection
                    gc.collect()

                    logger.info(f"Modelo {model_path} liberado exitosamente")
                    return True

                except Exception as e:
                    logger.error(f"Error liberando modelo {model_path}: {e}")
                    return False
            return False

    async def smart_model_cleanup(self, force: bool = False) -> int:
        """Limpieza inteligente de modelos MLX menos usados"""
        with self._lock:
            memory_usage = self.get_memory_stats()
            current_percent = memory_usage.usage_percent

            if not force and current_percent < self.cleanup_threshold:
                return 0

            # Ordenar modelos por prioridad (menos usados primero)
            models_to_remove = []
            for model_path in self.model_lru:
                if model_path not in self.model_stats:
                    continue

                stats = self.model_stats[model_path]
                score = (
                    stats["use_count"] * -1  # Menos usados primero
                    + (time.time() - stats["last_used"]) / 3600  # Más antiguos primero
                )
                models_to_remove.append((model_path, score))

            models_to_remove.sort(key=lambda x: x[1])

            removed = 0
            target_percent = 0.60 if force else 0.75

            for model_path, _ in models_to_remove:
                if self.get_memory_stats().usage_percent <= target_percent:
                    break

                if self.release_model(model_path):
                    removed += 1

                # Pequeña pausa para permitir liberación de memoria
                await asyncio.sleep(0.1)

            logger.info(f"Smart model cleanup: removed {removed} models")
            return removed

    def __del__(self):
        """Cleanup al destruir el objeto"""
        self.stop_monitoring()


# Instancia global del gestor de memoria
_memory_manager = None


def get_memory_manager() -> AdvancedMemoryManager:
    """Obtener instancia singleton del gestor de memoria"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = AdvancedMemoryManager()
    return _memory_manager


def init_memory_management():
    """Inicializar gestión de memoria"""
    manager = get_memory_manager()
    manager.start_monitoring()
    logger.info("Advanced memory management initialized")
    return manager
