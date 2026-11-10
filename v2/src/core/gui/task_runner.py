"""T4.2 — File de tâches de l'IHM : une tâche à la fois, progression par page."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.gui.worker_client import WorkerClient


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
    mode: str = "simple"
    status: TaskStatus = TaskStatus.PENDING
    total_pages: int = 0
    pages_done: int = 0
    current_page: int = 1
    stage: str = ""
    error: str | None = None
    work_dir: str = ""


def work_root() -> Path:
    env = os.environ.get("AOCRI_WORK_DIR")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".aocri"
    return base / "AOCRI" / "work"


class TaskRunner(QObject):
    task_changed = Signal(object)       # Task
    page_ready = Signal(str, int)       # task_id, page
    all_done = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tasks: list[Task] = []
        self._current: Task | None = None
        self._client = WorkerClient(self)
        self._client.progress.connect(self._on_progress)
        self._client.page_done.connect(self._on_page_done)
        self._client.error.connect(self._on_error)
        self._client.finished.connect(self._on_finished)
        self._client.log_line.connect(lambda _line: None)

    # ------------------------------------------------------------------ API
    def tasks(self) -> list[Task]:
        return list(self._tasks)

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self._tasks if t.id == task_id), None)

    def add(self, source_path: str, mode: str = "simple") -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            source_path=str(Path(source_path).resolve()),
            mode=mode,
        )
        task.work_dir = str(work_root() / f"{Path(source_path).stem}_{task.id[:8]}")
        self._tasks.append(task)
        self._emit(task)
        return task

    def start(self, task_id: str) -> bool:
        if self._current is not None:
            return False
        task = self.task(task_id)
        if task is None or task.status not in (TaskStatus.PENDING, TaskStatus.PAUSED):
            return False
        Path(task.work_dir).mkdir(parents=True, exist_ok=True)
        task.status = TaskStatus.RUNNING
        self._current = task
        self._emit(task)
        if not self._client.start(task.source_path, task.work_dir):
            task.status = TaskStatus.ERROR
            task.error = "worker indisponible"
            self._current = None
            self._emit(task)
            return False
        return True

    def pause(self) -> None:
        if self._current is not None:
            self._client.pause()
            self._current.status = TaskStatus.PAUSED
            self._emit(self._current)

    def resume(self) -> None:
        if self._current is not None:
            self._client.resume()
            self._current.status = TaskStatus.RUNNING
            self._emit(self._current)

    def cancel(self) -> None:
        if self._current is not None:
            self._client.cancel()

    def shutdown(self) -> None:
        self._client.stop()

    # -------------------------------------------------------------- interne
    def _emit(self, task: Task) -> None:
        self.task_changed.emit(task)

    def _on_progress(self, page: int, total: int, stage: str) -> None:
        task = self._current
        if task is None:
            return
        task.current_page = page
        task.total_pages = total
        task.stage = stage
        self._emit(task)

    def _on_page_done(self, page: int, _result_path: str) -> None:
        task = self._current
        if task is None:
            return
        task.pages_done = page
        self._emit(task)
        self.page_ready.emit(task.id, page)
        # Le worker reste vivant après la dernière page (document unique par
        # worker) : la tâche est close ici, puis le processus est arrêté.
        if task.total_pages and task.pages_done >= task.total_pages:
            task.status = TaskStatus.DONE
            self._emit(task)
            self._client.stop()
            self._current = None
            self.all_done.emit()

    def _on_error(self, message: str) -> None:
        task = self._current
        if task is None:
            return
        if "annul" in message.lower():
            task.status = TaskStatus.CANCELLED
        else:
            task.status = TaskStatus.ERROR
            task.error = message
        self._emit(task)

    def _on_finished(self, code: int) -> None:
        task = self._current
        if task is None:
            return
        if task.status not in (TaskStatus.CANCELLED, TaskStatus.ERROR):
            if code == 0 and (task.total_pages == 0 or task.pages_done >= task.total_pages):
                task.status = TaskStatus.DONE
            elif code != 0:
                task.status = TaskStatus.ERROR
                task.error = f"worker arrêté (code {code})"
            elif task.status == TaskStatus.RUNNING and task.total_pages and task.pages_done < task.total_pages:
                # EOF sans cancel : le worker n'a pas terminé son document.
                task.status = TaskStatus.ERROR
                task.error = "worker interrompu avant la fin du document"
        self._current = None
        self._emit(task)
        self.all_done.emit()
