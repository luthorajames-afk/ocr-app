"""Gestionnaire de tâches : lance le bon worker selon le mode,
reçoit la progression, gère pause, reprise et annulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Callable
from enum import Enum
import uuid
import time


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

    def set_on_change(self, callback: Callable[[Task], None]) -> None:
        self._on_change = callback

    def add(self, source_path: str, mode: str, total_pages: int = 0) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            source_path=source_path,
            mode=mode,
            total_pages=total_pages,
        )
        self._tasks[task.id] = task
        self._order.append(task.id)
        if self._on_change:
            self._on_change(task)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        return [self._tasks[tid] for tid in self._order]

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
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if status is not None:
            task.status = status
        if pages_done is not None:
            task.pages_done = pages_done
        if current_page is not None:
            task.current_page = current_page
        if stage is not None:
            task.stage = stage
        if total_pages is not None:
            task.total_pages = total_pages
        if error_message is not None:
            task.error_message = error_message
        task.touch()
        if self._on_change:
            self._on_change(task)
        return task

    def cancel(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.CANCELLED)

    def pause(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.PAUSED)

    def resume(self, task_id: str) -> Optional[Task]:
        return self.update_progress(task_id, status=TaskStatus.RUNNING)