# system/advanced_parallel_processor.py
import asyncio
import concurrent.futures
import logging
import os
import pickle
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import psutil

logger = logging.getLogger(__name__)


def validate_pickle_serializable(obj: Any, name: str = "object") -> bool:
    """
    Valida que un objeto pueda ser serializado con pickle.

    Args:
        obj: El objeto a validar
        name: Nombre para logging

    Returns:
        bool: True si es serializable, False si no

    Raises:
        ValueError: Con detalles sobre por qué no es serializable
    """
    try:
        pickle.dumps(obj)
        return True
    except (pickle.PicklingError, TypeError) as e:
        raise ValueError(f"{name} is not pickle-serializable: {e}")
    except Exception as e:
        raise ValueError(f"Unexpected serialization error for {name}: {e}")


class ProcessingMode(Enum):
    """Modos de procesamiento"""

    THREAD = "thread"
    PROCESS = "process"
    ASYNC = "async"
    HYBRID = "hybrid"
    SEQUENTIAL = "sequential"


class TaskPriority(Enum):
    """Prioridades de tareas"""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class WorkerState(Enum):
    """Estados de workers"""

    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass
class Task:
    """Tarea de procesamiento"""

    id: str
    func: Callable
    args: tuple
    kwargs: dict
    priority: TaskPriority = TaskPriority.NORMAL
    timeout: float | None = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = f"task_{int(time.time() * 1000000)}"


@dataclass
class TaskResult:
    """Resultado de tarea"""

    task_id: str
    success: bool
    result: Any = None
    error: Exception | None = None
    execution_time: float = 0.0
    worker_id: str = ""
    completed_at: datetime = field(default_factory=datetime.now)


@dataclass
class WorkerStats:
    """Estadísticas de worker"""

    worker_id: str
    state: WorkerState
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_execution_time: float = 0.0
    last_activity: datetime = field(default_factory=datetime.now)
    cpu_usage: float = 0.0
    memory_usage: float = 0.0


class LoadBalancer:
    """Balanceador de carga para workers"""

    def __init__(self):
        self.worker_stats: dict[str, WorkerStats] = {}
        self.task_queue_sizes: dict[str, int] = defaultdict(int)
        self.worker_capabilities: dict[str, list[str]] = defaultdict(list)

    def select_worker(self, task: Task, available_workers: list[str]) -> str | None:
        """Seleccionar el mejor worker para una tarea"""
        if not available_workers:
            return None

        # Filtrar workers por capacidades si la tarea tiene tags específicos
        capable_workers = available_workers
        if task.tags:
            capable_workers = [
                worker
                for worker in available_workers
                if any(tag in self.worker_capabilities.get(worker, []) for tag in task.tags)
            ]
            if not capable_workers:
                capable_workers = available_workers

        # Calcular scores para cada worker
        worker_scores = {}
        for worker_id in capable_workers:
            score = self._calculate_worker_score(worker_id, task)
            worker_scores[worker_id] = score

        # Seleccionar worker con mejor score
        best_worker = max(worker_scores.items(), key=lambda x: x[1])[0]
        return best_worker

    def _calculate_worker_score(self, worker_id: str, task: Task) -> float:
        """Calcular score de worker para una tarea"""
        stats = self.worker_stats.get(worker_id)
        if not stats or stats.state != WorkerState.IDLE:
            return 0.0

        score = 100.0  # Score base

        # Penalizar por carga de CPU y memoria
        score -= stats.cpu_usage * 0.5
        score -= stats.memory_usage * 0.3

        # Penalizar por tamaño de cola
        queue_size = self.task_queue_sizes.get(worker_id, 0)
        score -= queue_size * 10

        # Bonificar por tasa de éxito
        total_tasks = stats.tasks_completed + stats.tasks_failed
        if total_tasks > 0:
            success_rate = stats.tasks_completed / total_tasks
            score += success_rate * 20

        # Bonificar por velocidad promedio
        if stats.tasks_completed > 0:
            avg_time = stats.total_execution_time / stats.tasks_completed
            if avg_time > 0:
                score += min(50, 10 / avg_time)  # Más rápido = mejor score

        return max(0, score)

    def update_worker_stats(self, worker_id: str, stats: WorkerStats):
        """Actualizar estadísticas de worker"""
        self.worker_stats[worker_id] = stats

    def update_queue_size(self, worker_id: str, size: int):
        """Actualizar tamaño de cola de worker"""
        self.task_queue_sizes[worker_id] = size

    def register_worker_capability(self, worker_id: str, capabilities: list[str]):
        """Registrar capacidades de worker"""
        self.worker_capabilities[worker_id] = capabilities


class ResourceMonitor:
    """Monitor de recursos del sistema"""

    def __init__(self, check_interval: float = 1.0):
        self.check_interval = check_interval
        self.cpu_threshold = 80.0  # %
        self.memory_threshold = 85.0  # %
        self.monitoring = False
        self._monitor_task: asyncio.Task | None = None

        # Callbacks para eventos de recursos
        self.high_cpu_callbacks: list[Callable] = []
        self.high_memory_callbacks: list[Callable] = []
        self.resource_available_callbacks: list[Callable] = []

    async def start_monitoring(self):
        """Iniciar monitoreo de recursos"""
        if not self.monitoring:
            self.monitoring = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    def stop_monitoring(self):
        """Detener monitoreo de recursos"""
        self.monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()

    async def _monitor_loop(self):
        """Loop de monitoreo de recursos"""
        while self.monitoring:
            try:
                cpu_percent = psutil.cpu_percent(interval=None)
                memory_percent = psutil.virtual_memory().percent

                # Verificar umbrales
                if cpu_percent > self.cpu_threshold:
                    await self._execute_callbacks(self.high_cpu_callbacks, cpu_percent)

                if memory_percent > self.memory_threshold:
                    await self._execute_callbacks(self.high_memory_callbacks, memory_percent)

                if cpu_percent < self.cpu_threshold and memory_percent < self.memory_threshold:
                    await self._execute_callbacks(
                        self.resource_available_callbacks, cpu_percent, memory_percent
                    )

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in resource monitoring: {e}")
                await asyncio.sleep(self.check_interval)

    async def _execute_callbacks(self, callbacks: list[Callable], *args):
        """Ejecutar callbacks de manera segura"""
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args)
                else:
                    callback(*args)
            except Exception as e:
                logger.error(f"Error in resource callback: {e}")

    def register_high_cpu_callback(self, callback: Callable):
        """Registrar callback para CPU alta"""
        self.high_cpu_callbacks.append(callback)

    def register_high_memory_callback(self, callback: Callable):
        """Registrar callback para memoria alta"""
        self.high_memory_callbacks.append(callback)

    def register_resource_available_callback(self, callback: Callable):
        """Registrar callback para recursos disponibles"""
        self.resource_available_callbacks.append(callback)


class AdvancedParallelProcessor:
    """Procesador paralelo avanzado con balanceador de carga"""

    def __init__(
        self,
        max_workers: int | None = None,
        mode: ProcessingMode = ProcessingMode.HYBRID,
        enable_monitoring: bool = True,
    ):
        self.max_workers = max_workers or min(32, (os.cpu_count() or 1) + 4)
        self.mode = mode
        self.enable_monitoring = enable_monitoring

        # Colas de tareas por prioridad
        self.task_queues: dict[TaskPriority, asyncio.Queue] = {
            TaskPriority.CRITICAL: asyncio.Queue(),
            TaskPriority.HIGH: asyncio.Queue(),
            TaskPriority.NORMAL: asyncio.Queue(),
            TaskPriority.LOW: asyncio.Queue(),
        }

        # Resultados y callbacks
        self.results: dict[str, TaskResult] = {}
        self.result_callbacks: dict[str, list[Callable]] = defaultdict(list)

        # Workers y executors
        self.thread_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self.process_executor: concurrent.futures.ProcessPoolExecutor | None = None
        self.async_workers: list[asyncio.Task] = []

        # Gestión de estado
        self.running = False
        self.worker_stats: dict[str, WorkerStats] = {}
        self.task_dependencies: dict[str, set] = defaultdict(set)
        self.completed_tasks: set = set()

        # Componentes auxiliares
        self.load_balancer = LoadBalancer()
        self.resource_monitor = ResourceMonitor() if enable_monitoring else None

        # Métricas
        self.total_tasks_submitted = 0
        self.total_tasks_completed = 0
        self.total_tasks_failed = 0
        self.total_execution_time = 0.0

        # Lock para thread safety
        self._lock = threading.Lock()

    async def start(self):
        """Iniciar el procesador"""
        if self.running:
            return

        self.running = True

        # Inicializar executors según el modo
        if self.mode in [ProcessingMode.THREAD, ProcessingMode.HYBRID]:
            self.thread_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_workers // 2
                if self.mode == ProcessingMode.HYBRID
                else self.max_workers,
                thread_name_prefix="fenix_thread",
            )

        if self.mode in [ProcessingMode.PROCESS, ProcessingMode.HYBRID]:
            self.process_executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=self.max_workers // 2
                if self.mode == ProcessingMode.HYBRID
                else self.max_workers
            )

        # Iniciar workers async
        if self.mode in [ProcessingMode.ASYNC, ProcessingMode.HYBRID]:
            worker_count = (
                self.max_workers // 2 if self.mode == ProcessingMode.HYBRID else self.max_workers
            )
            for i in range(worker_count):
                worker = asyncio.create_task(self._async_worker(f"async_worker_{i}"))
                self.async_workers.append(worker)

        # Iniciar monitor de recursos
        if self.resource_monitor:
            await self.resource_monitor.start_monitoring()
            self._setup_resource_callbacks()

        # Iniciar dispatcher de tareas
        asyncio.create_task(self._task_dispatcher())

        logger.info(
            f"Advanced parallel processor started with {self.max_workers} workers in {self.mode.value} mode"
        )

    async def stop(self):
        """Detener el procesador"""
        if not self.running:
            return

        self.running = False

        # Detener workers async
        for worker in self.async_workers:
            worker.cancel()

        # Esperar a que terminen las tareas pendientes
        await asyncio.sleep(0.1)

        # Cerrar executors
        if self.thread_executor:
            self.thread_executor.shutdown(wait=True)

        if self.process_executor:
            self.process_executor.shutdown(wait=True)

        # Detener monitor de recursos
        if self.resource_monitor:
            self.resource_monitor.stop_monitoring()

        logger.info("Advanced parallel processor stopped")

    async def submit_task(
        self,
        func: Callable,
        *args,
        task_id: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: float | None = None,
        max_retries: int = 3,
        dependencies: list[str] | None = None,
        tags: list[str] | None = None,
        callback: Callable | None = None,
        **kwargs,
    ) -> str:
        """Enviar tarea para procesamiento"""
        task = Task(
            id=task_id or f"task_{self.total_tasks_submitted}",
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            dependencies=dependencies or [],
            tags=tags or [],
        )

        # Registrar dependencias
        if dependencies:
            self.task_dependencies[task.id] = set(dependencies)

        # Registrar callback
        if callback:
            self.result_callbacks[task.id].append(callback)

        # Agregar a cola apropiada
        await self._enqueue_task(task)

        self.total_tasks_submitted += 1

        return task.id

    async def _enqueue_task(self, task: Task):
        """Agregar tarea a la cola apropiada"""
        await self.task_queues[task.priority].put(task)

    async def _task_dispatcher(self):
        """Dispatcher principal de tareas"""
        while self.running:
            try:
                # Buscar tarea con mayor prioridad
                task = await self._get_next_task()
                if task is None:
                    await asyncio.sleep(0.01)
                    continue

                # Verificar dependencias
                if not self._check_dependencies(task):
                    # Reencolar tarea si las dependencias no están listas
                    await self._enqueue_task(task)
                    await asyncio.sleep(0.01)
                    continue

                # Ejecutar tarea
                await self._execute_task(task)

            except Exception as e:
                logger.error(f"Error in task dispatcher: {e}")
                await asyncio.sleep(0.1)

    async def _get_next_task(self) -> Task | None:
        """Obtener siguiente tarea por prioridad"""
        for priority in [
            TaskPriority.CRITICAL,
            TaskPriority.HIGH,
            TaskPriority.NORMAL,
            TaskPriority.LOW,
        ]:
            try:
                task = self.task_queues[priority].get_nowait()
                return task
            except asyncio.QueueEmpty:
                continue
        return None

    def _check_dependencies(self, task: Task) -> bool:
        """Verificar si las dependencias de una tarea están completadas"""
        dependencies = self.task_dependencies.get(task.id, set())
        return dependencies.issubset(self.completed_tasks)

    async def _execute_task(self, task: Task):
        """Ejecutar tarea en el worker apropiado"""
        start_time = time.time()

        try:
            # Determinar tipo de ejecución basado en tags y función
            execution_mode = self._determine_execution_mode(task)

            if execution_mode == ProcessingMode.ASYNC:
                result = await self._execute_async_task(task)
            elif execution_mode == ProcessingMode.THREAD:
                result = await self._execute_thread_task(task)
            elif execution_mode == ProcessingMode.PROCESS:
                result = await self._execute_process_task(task)
            else:
                raise ValueError(f"Unknown execution mode: {execution_mode}")

            execution_time = time.time() - start_time

            # Crear resultado exitoso
            task_result = TaskResult(
                task_id=task.id, success=True, result=result, execution_time=execution_time
            )

            self.total_tasks_completed += 1
            self.total_execution_time += execution_time

        except Exception as e:
            execution_time = time.time() - start_time

            # Manejar retry
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                logger.warning(
                    f"Task {task.id} failed, retrying ({task.retry_count}/{task.max_retries}): {e}"
                )
                await asyncio.sleep(min(2**task.retry_count, 30))  # Backoff exponencial
                await self._enqueue_task(task)
                return

            # Crear resultado fallido
            task_result = TaskResult(
                task_id=task.id, success=False, error=e, execution_time=execution_time
            )

            self.total_tasks_failed += 1
            logger.error(f"Task {task.id} failed permanently: {e}")

        # Almacenar resultado
        self.results[task.id] = task_result
        self.completed_tasks.add(task.id)

        # Ejecutar callbacks
        await self._execute_result_callbacks(task.id, task_result)

    def _determine_execution_mode(self, task: Task) -> ProcessingMode:
        """Determinar modo de ejecución para una tarea"""
        # Verificar tags específicos
        if "cpu_intensive" in task.tags:
            return ProcessingMode.PROCESS
        elif "io_intensive" in task.tags:
            return ProcessingMode.THREAD
        elif "async" in task.tags:
            return ProcessingMode.ASYNC

        # Usar modo por defecto del procesador
        if self.mode == ProcessingMode.HYBRID:
            # Para híbrido, usar heurística simple
            if asyncio.iscoroutinefunction(task.func):
                return ProcessingMode.ASYNC
            else:
                return ProcessingMode.THREAD

        return self.mode

    async def _execute_async_task(self, task: Task) -> Any:
        """Ejecutar tarea async"""
        if asyncio.iscoroutinefunction(task.func):
            if task.timeout:
                return await asyncio.wait_for(
                    task.func(*task.args, **task.kwargs), timeout=task.timeout
                )
            else:
                return await task.func(*task.args, **task.kwargs)
        else:
            # Función síncrona en worker async
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: task.func(*task.args, **task.kwargs))

    async def _execute_thread_task(self, task: Task) -> Any:
        """Ejecutar tarea en thread pool"""
        if not self.thread_executor:
            raise RuntimeError("Thread executor not available")

        loop = asyncio.get_event_loop()
        future = self.thread_executor.submit(task.func, *task.args, **task.kwargs)

        if task.timeout:
            return await asyncio.wait_for(
                loop.run_in_executor(None, future.result), timeout=task.timeout
            )
        else:
            return await loop.run_in_executor(None, future.result)

    async def _execute_process_task(self, task: Task) -> Any:
        """Ejecutar tarea en process pool"""
        if not self.process_executor:
            raise RuntimeError("Process executor not available")

        # Verificar que la función y sus argumentos sean serializables
        try:
            validate_pickle_serializable(task.func, f"Task {task.id} function")
            validate_pickle_serializable(task.args, f"Task {task.id} arguments")
            validate_pickle_serializable(task.kwargs, f"Task {task.id} keyword arguments")
        except ValueError as e:
            logger.error(f"Task serialization validation failed: {e}")
            raise ValueError(
                f"Task not serializable for process execution: {e}. "
                f"Consider using thread mode or making the function pickle-compatible."
            )

        loop = asyncio.get_event_loop()
        future = self.process_executor.submit(task.func, *task.args, **task.kwargs)

        if task.timeout:
            return await asyncio.wait_for(
                loop.run_in_executor(None, future.result), timeout=task.timeout
            )
        else:
            return await loop.run_in_executor(None, future.result)

    async def _async_worker(self, worker_id: str):
        """Worker async individual"""
        logger.debug(f"Async worker {worker_id} started")

        while self.running:
            try:
                await asyncio.sleep(0.01)  # Yield control
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in async worker {worker_id}: {e}")

        logger.debug(f"Async worker {worker_id} stopped")

    async def _execute_result_callbacks(self, task_id: str, result: TaskResult):
        """Ejecutar callbacks de resultado"""
        callbacks = self.result_callbacks.get(task_id, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(result)
                else:
                    callback(result)
            except Exception as e:
                logger.error(f"Error in result callback for task {task_id}: {e}")

    def _setup_resource_callbacks(self):
        """Configurar callbacks de recursos"""
        if not self.resource_monitor:
            return

        async def on_high_cpu(cpu_percent):
            logger.warning(f"High CPU usage detected: {cpu_percent}%")
            # Reducir workers si es necesario
            # Implementar lógica de throttling

        async def on_high_memory(memory_percent):
            logger.warning(f"High memory usage detected: {memory_percent}%")
            # Pausar nuevas tareas si es necesario
            # Implementar lógica de limpieza de memoria

        async def on_resources_available(cpu_percent, memory_percent):
            # Reanudar operaciones normales
            pass

        self.resource_monitor.register_high_cpu_callback(on_high_cpu)
        self.resource_monitor.register_high_memory_callback(on_high_memory)
        self.resource_monitor.register_resource_available_callback(on_resources_available)

    async def get_result(self, task_id: str, timeout: float | None = None) -> TaskResult:
        """Obtener resultado de tarea"""
        start_time = time.time()

        while task_id not in self.results:
            if timeout and (time.time() - start_time) > timeout:
                raise TimeoutError(f"Timeout waiting for task {task_id}")

            await asyncio.sleep(0.01)

        return self.results[task_id]

    def get_stats(self) -> dict[str, Any]:
        """Obtener estadísticas del procesador"""
        return {
            "running": self.running,
            "mode": self.mode.value,
            "max_workers": self.max_workers,
            "total_tasks_submitted": self.total_tasks_submitted,
            "total_tasks_completed": self.total_tasks_completed,
            "total_tasks_failed": self.total_tasks_failed,
            "success_rate": self.total_tasks_completed / max(1, self.total_tasks_submitted),
            "average_execution_time": self.total_execution_time
            / max(1, self.total_tasks_completed),
            "pending_tasks": sum(q.qsize() for q in self.task_queues.values()),
            "completed_tasks": len(self.completed_tasks),
            "active_workers": len([w for w in self.async_workers if not w.done()]),
        }

    async def wait_for_completion(self, timeout: float | None = None):
        """Esperar a que se completen todas las tareas pendientes"""
        start_time = time.time()

        while any(q.qsize() > 0 for q in self.task_queues.values()):
            if timeout and (time.time() - start_time) > timeout:
                raise TimeoutError("Timeout waiting for task completion")

            await asyncio.sleep(0.1)

    async def run_agents_parallel(
        self, agents_data: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Compatibility method for running agents in parallel (legacy API)"""
        # Ensure processor is started
        if not self.running:
            await self.start()

        results = {}
        task_ids = []

        # Submit all agent tasks
        for agent_name, agent_info in agents_data.items():
            if "agent" in agent_info:
                agent = agent_info["agent"]

                # Create a wrapper function that returns the agent result
                async def run_agent_wrapper(agent_obj, agent_name=agent_name):
                    try:
                        if hasattr(agent_obj, "run"):
                            if asyncio.iscoroutinefunction(agent_obj.run):
                                result = await agent_obj.run()
                            else:
                                result = agent_obj.run()
                            return {"result": result, "success": True}
                        else:
                            return {
                                "result": None,
                                "success": False,
                                "error": "Agent has no run method",
                            }
                    except Exception as e:
                        return {"result": None, "success": False, "error": str(e)}

                task_id = await self.submit_task(
                    run_agent_wrapper,
                    agent,
                    task_id=f"agent_{agent_name}",
                    priority=TaskPriority.HIGH,
                    timeout=60,
                )
                task_ids.append((agent_name, task_id))

        # Wait for all results
        for agent_name, task_id in task_ids:
            try:
                task_result = await self.get_result(task_id, timeout=70)
                results[agent_name] = task_result
            except Exception as e:
                logger.error(f"Error getting result for agent {agent_name}: {e}")
                results[agent_name] = TaskResult(
                    task_id=task_id, success=False, error=e, execution_time=0.0
                )

        return results

    def __del__(self):
        """Cleanup al destruir el objeto"""
        if hasattr(self, "running") and self.running:
            try:
                # Intentar obtener el loop actual
                loop = asyncio.get_running_loop()
                if loop and not loop.is_closed():
                    asyncio.create_task(self.stop())
            except RuntimeError:
                # No hay loop corriendo, hacer cleanup síncrono
                self.running = False
                if hasattr(self, "async_workers"):
                    for worker in self.async_workers:
                        if hasattr(worker, "cancel"):
                            worker.cancel()


# Instancia global del procesador
_processor: AdvancedParallelProcessor | None = None


async def get_processor() -> AdvancedParallelProcessor:
    """Obtener instancia global del procesador"""
    global _processor
    if _processor is None:
        _processor = AdvancedParallelProcessor()
        await _processor.start()
    return _processor


async def submit_parallel_task(
    func: Callable,
    *args,
    priority: TaskPriority = TaskPriority.NORMAL,
    timeout: float | None = None,
    **kwargs,
) -> str:
    """Función global para enviar tareas al procesador paralelo"""
    processor = await get_processor()
    return await processor.submit_task(func, *args, priority=priority, timeout=timeout, **kwargs)
