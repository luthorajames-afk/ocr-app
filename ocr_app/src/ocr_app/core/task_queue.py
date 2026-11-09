"""Gestionnaire de tâches : lance le bon worker selon le mode,
reçoit la progression, gère pause, reprise et annulation.

La file est protégée par un verrou : les callbacks du worker (thread de lecture
du sous-processus) et l'interface peuvent la mettre à jour sans course. Les
callbacks ``on_change`` sont appelés hors verrou pour éviter tout interblocage.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class Task:
    id: str
    source_path: str
    mode: str  # "simple", "medium", "advanced"
    status: TaskStatus = TaskStatus.PENDING
    total_pages: int = 0
    pages_done: int = 0
    current_page: int = 0
    stage: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error_message: Optional[str] = None
    result_dir: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = time.time()


class TaskQueue:
    """File de tâches en mémoire. Le worker réel pilote la mise à jour des tâches."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}
        self._order: list[str] = []
        self._on_change: Optional[Callable[[Task], None]] = None
        self._lock = threading.RLock()

    def set_on_change(self, callback: Callable[[Task], None]) -> None:
        with self._lock:
            self._on_change = callback

    def add(self, source_path: str, mode: str, total_pages: int = 0) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            source_path=str(source_path),
            mode=str(mode),
            total_pages=max(0, int(total_pages)),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._order.append(task.id)
            callback = self._on_change
        if callback is not None:
            callback(task)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        with self._lock:
            return [self._tasks[tid] for tid in self._order if tid in self._tasks]

    def update_progress(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        pages_done: Optional[int] = None,
        current_page: Optional[int] = None,
        stage: Optional[str] = None,
        total_pages: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if status is not None:
                if not isinstance(status, TaskStatus):
                    try:
                        status = TaskStatus(status)
                    except (TypeError, ValueError):
                        pass
                task.status = status
            if pages_done is not None:
                task.pages_done = max(0, int(pages_done))
            if current_page is not None:
                task.current_page = max(0, int(current_page))
            if stage is not None:
                task.stage = str(stage)
            if total_pages is not None:
                task.total_pages = max(0, int(total_pages))
            if error_message is not None:
                task.error_message = str(error_message)
            task.touch()
            callback = self._on_change
        if callback is not None:
            callback(task)
        return task

    def cancel(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.CANCELLED)

    def pause(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.PAUSED)

    def resume(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.RUNNING)
