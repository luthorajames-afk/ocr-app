"""Orchestration des tâches OCR côté interface.

Le ``TaskRunner`` relie la ``TaskQueue`` existante au ``WorkerClient`` QProcess :
il empile les documents, lance un worker par tâche, met à jour la progression,
puis recharge chaque page sauvegardée par le worker (R6/R14).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from ocr_app.core.data_model import Document, Page
from ocr_app.core.task_queue import Task, TaskQueue, TaskStatus
from ocr_app.ui import file_utils
from ocr_app.ui.worker_client import WorkerClient

logger = logging.getLogger(__name__)


class TaskRunner(QObject):
    """File de tâches + pilotage des workers en sous-processus."""

    task_changed = Signal(object)            # Task
    page_loaded = Signal(str, object)        # task_id, Page
    task_started = Signal(str)               # task_id
    error_occurred = Signal(str, str)        # task_id, message
    notice = Signal(str)                     # message transitoire

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        worker_commands: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        """``worker_commands`` associe un mode (simple/medium/advanced) à la
        commande du worker réel. Sans entrée, le worker factice de
        développement (``fake_worker``) est utilisé pour tous les modes : le
        protocole, la pause et l'annulation restent ainsi testables avant le
        branchement des moteurs réels.
        """
        super().__init__(parent)
        self._queue = TaskQueue()
        self._queue.set_on_change(self._emit_task_changed)

        self._worker = WorkerClient(self)
        self._worker.progress.connect(self._on_progress)
        self._worker.page_done.connect(self._on_page_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.log_line.connect(self._on_log_line)

        self._worker_commands: Dict[str, tuple[str, ...]] = {}
        for mode, command in (worker_commands or {}).items():
            if isinstance(command, (str, bytes)):
                raise TypeError(
                    "la commande du worker doit être une séquence, pas une chaîne"
                )
            parts = tuple(str(part) for part in command)
            if parts:
                self._worker_commands[str(mode)] = parts

        self._pending: List[str] = []
        self._pages: Dict[str, Dict[int, Page]] = {}
        self._current_id: Optional[str] = None
        self._cancelling = False
        self._shutting_down = False
        self._start_error: Optional[str] = None
        self._log_tail: List[str] = []
        self._work_root: Optional[Path] = None

        # Filet de sécurité : même si la fenêtre n'est pas fermée par
        # closeEvent (arrêt de Qt, session Windows), aucun worker ne survit.
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # --- Accès ------------------------------------------------------------
    @property
    def queue(self) -> TaskQueue:
        return self._queue

    @property
    def current_task_id(self) -> Optional[str]:
        return self._current_id

    def task(self, task_id: str) -> Optional[Task]:
        return self._queue.get(task_id)

    def last_log_lines(self, limit: int = 20) -> List[str]:
        return self._log_tail[-limit:]

    # --- Création / file --------------------------------------------------
    def create_task(self, source_path: str, mode: str, total_pages: int) -> Task:
        """Ajoute une tâche à la file sans la démarrer."""
        task = self._queue.add(
            source_path,
            mode=mode,
            total_pages=max(1, int(total_pages)),
        )
        self._pages.setdefault(task.id, {})
        return task

    def add_task(
        self,
        source_path: str,
        mode: str,
        total_pages: int,
        auto_start: bool = True,
    ) -> Task:
        task = self.create_task(source_path, mode, total_pages)
        if auto_start:
            self.enqueue(task.id)
        return task

    def enqueue(self, task_id: str) -> None:
        task = self._queue.get(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return
        if task_id not in self._pending:
            self._pending.append(task_id)
        self._start_next()

    def register_page(self, task_id: str, page: Page) -> None:
        """Enregistre une page déjà traitée (mode démonstration ou reprise)."""
        self._pages.setdefault(task_id, {})[page.number] = page

    def _root(self) -> Path:
        if self._work_root is None:
            self._work_root = file_utils.work_root()
        return self._work_root

    # --- Démarrage --------------------------------------------------------
    def _start_next(self) -> None:
        if self._shutting_down or self._current_id is not None:
            return

        while self._pending:
            task_id = self._pending.pop(0)
            task = self._queue.get(task_id)
            if task is None or task.status != TaskStatus.PENDING:
                continue

            try:
                work_dir = file_utils.task_work_dir(
                    self._root(), task.source_path, task.id
                )
            except OSError as exc:
                message = f"dossier de travail inaccessible : {exc}"
                self._queue.update_progress(
                    task_id, status=TaskStatus.ERROR, error_message=message
                )
                self.error_occurred.emit(task_id, message)
                continue

            self._current_id = task_id
            self._cancelling = False
            self._start_error = None
            self._log_tail.clear()

            total = max(1, task.total_pages)
            # TaskQueue ne gère pas result_dir : le champ est porté par Task.
            task.result_dir = str(work_dir)
            self._queue.update_progress(
                task_id,
                status=TaskStatus.RUNNING,
                pages_done=0,
                current_page=1,
                stage="render",
                total_pages=total,
            )

            if not self._worker.start(
                total,
                work_dir,
                command=self._worker_commands.get(task.mode),
            ):
                message = "un worker est déjà en cours"
                self._queue.update_progress(
                    task_id, status=TaskStatus.ERROR, error_message=message
                )
                self._current_id = None
                self.error_occurred.emit(task_id, message)
                continue

            self.task_started.emit(task_id)
            return

    # --- Commandes --------------------------------------------------------
    def pause_current(self) -> bool:
        task = self._current_task()
        if task is None or task.status != TaskStatus.RUNNING:
            return False
        if self._worker.send_command("pause"):
            self._queue.pause(task.id)
            return True
        return False

    def resume_current(self) -> bool:
        task = self._current_task()
        if task is None or task.status != TaskStatus.PAUSED:
            return False
        if self._worker.send_command("resume"):
            self._queue.resume(task.id)
            return True
        return False

    def toggle_pause_current(self) -> bool:
        task = self._current_task()
        if task is None:
            return False
        if task.status == TaskStatus.PAUSED:
            return self.resume_current()
        return self.pause_current()

    def cancel_current(self) -> bool:
        task = self._current_task()
        if task is None or task.status not in (
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
        ):
            return False
        self._cancelling = True
        self._worker.request_cancel()
        self._queue.cancel(task.id)
        self.notice.emit("Annulation demandée…")
        return True

    def _current_task(self) -> Optional[Task]:
        if self._current_id is None:
            return None
        return self._queue.get(self._current_id)

    # --- Messages worker --------------------------------------------------
    def _on_progress(self, payload: dict) -> None:
        task = self._current_task()
        if task is None:
            return
        # Un message tardif ne doit pas ressusciter une tâche terminée, annulée
        # ou en erreur (certains workers continuent après une erreur de page).
        if task.status not in (TaskStatus.RUNNING, TaskStatus.PAUSED):
            return
        try:
            page = int(payload.get("page", 0))
            total = int(payload.get("total", 0))
        except (TypeError, ValueError):
            return
        if page < 1:
            return
        stage = str(payload.get("stage", ""))
        status = TaskStatus.PAUSED if task.status == TaskStatus.PAUSED else TaskStatus.RUNNING
        self._queue.update_progress(
            task.id,
            status=status,
            current_page=page,
            stage=stage,
            total_pages=total or task.total_pages,
        )

    def _on_page_done(self, payload: dict) -> None:
        task = self._current_task()
        if task is None:
            return
        try:
            page_number = int(payload.get("page", 0))
        except (TypeError, ValueError):
            return
        result_path = str(payload.get("result_path", "")).strip()
        if page_number < 1 or not result_path:
            logger.debug("page_done invalide ignoré : %r", payload)
            return
        try:
            page = Page.load_json(result_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if task.status in (TaskStatus.CANCELLED, TaskStatus.ERROR):
                # Le worker avait déjà signalé l'échec : ne pas écraser l'état.
                logger.debug("page %s illisible après erreur : %s", page_number, exc)
                return
            message = f"résultat illisible pour la page {page_number} : {exc}"
            self._queue.update_progress(
                task.id, status=TaskStatus.ERROR, error_message=message
            )
            self.error_occurred.emit(task.id, message)
            return

        self._pages.setdefault(task.id, {})[page.number] = page
        if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
            status = (
                TaskStatus.PAUSED
                if task.status == TaskStatus.PAUSED
                else TaskStatus.RUNNING
            )
            self._queue.update_progress(
                task.id,
                status=status,
                pages_done=max(task.pages_done, page.number),
                current_page=page.number,
                stage="",
            )
        self.page_loaded.emit(task.id, page)

    def _on_worker_error(self, payload: dict) -> None:
        task = self._current_task()
        if task is None:
            return
        message = str(payload.get("message", "")).strip() or "erreur inconnue"
        lower = message.lower()
        # L'intention d'annulation prime sur le texte du message : un worker
        # réel peut répondre « stopped » ou « interrupted » au lieu de
        # « annulé ». Inversement, une erreur pendant l'annulation ne doit pas
        # transformer la tâche en échec. Le préfixe (et non une sous-chaîne)
        # évite de classer « impossible de cancel... » comme une annulation.
        cancelled = (
            self._cancelling
            or task.status == TaskStatus.CANCELLED
            or lower.startswith(("annul", "cancel"))
        )
        if cancelled:
            if task.status != TaskStatus.CANCELLED:
                self._queue.update_progress(task.id, status=TaskStatus.CANCELLED)
            self.notice.emit("Traitement annulé")
            return
        if task.status in (TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.DONE):
            # Conserver la première erreur (ou l'état terminal) : les messages
            # d'erreur suivants ne doivent pas réécrire le diagnostic.
            return
        self._queue.update_progress(
            task.id, status=TaskStatus.ERROR, error_message=message
        )
        self.error_occurred.emit(task.id, message)

    def _on_worker_failed(self, message: str) -> None:
        self._start_error = message

    def _on_worker_finished(self, exit_code: int, normal: bool) -> None:
        task_id = self._current_id
        self._current_id = None

        if task_id is not None:
            task = self._queue.get(task_id)
            if task is not None:
                total = max(1, task.total_pages)
                if task.status in (TaskStatus.CANCELLED, TaskStatus.ERROR):
                    # L'état terminal a déjà été décidé par un message error
                    # ou par cancel_current() : ne pas le réécrire.
                    pass
                elif self._cancelling:
                    self._queue.update_progress(
                        task_id, status=TaskStatus.CANCELLED
                    )
                elif normal and task.pages_done >= total:
                    self._queue.update_progress(
                        task_id,
                        status=TaskStatus.DONE,
                        pages_done=total,
                        current_page=total,
                        stage="",
                    )
                elif normal:
                    message = (
                        f"traitement incomplet "
                        f"({task.pages_done}/{total} pages)"
                    )
                    self._queue.update_progress(
                        task_id,
                        status=TaskStatus.ERROR,
                        error_message=message,
                    )
                    self.error_occurred.emit(task_id, message)
                else:
                    message = self._start_error or (
                        f"worker arrêté (code {exit_code})"
                    )
                    self._queue.update_progress(
                        task_id,
                        status=TaskStatus.ERROR,
                        error_message=message,
                    )
                    self.error_occurred.emit(task_id, message)

        self._cancelling = False
        self._start_error = None
        # Ne pas réutiliser le QProcess à l'intérieur de son propre signal
        # « finished » : laisser Qt terminer la transition d'état (sinon un
        # démarrage intermittent échoue sur Windows). Le tour de boucle suivant
        # démarre le document en attente, s'il y en a un.
        QTimer.singleShot(0, self._start_next)

    def _on_log_line(self, line: str) -> None:
        self._log_tail.append(line)
        if len(self._log_tail) > 40:
            del self._log_tail[: len(self._log_tail) - 40]

    # --- Résultats --------------------------------------------------------
    def latest_page(self, task_id: str) -> Optional[Page]:
        pages = self._pages.get(task_id)
        if not pages:
            return None
        return max(pages.values(), key=lambda page: page.number)

    def pages_for(self, task_id: str) -> List[Page]:
        pages = self._pages.get(task_id)
        if not pages:
            return []
        return [pages[number] for number in sorted(pages)]

    def document_for(self, task_id: str) -> Optional[Document]:
        task = self._queue.get(task_id)
        if task is None:
            return None
        pages = self.pages_for(task_id)
        title = Path(task.source_path).stem if task.source_path else "Document"
        return Document(title=title, pages=pages, source_path=task.source_path)

    # --- Nettoyage --------------------------------------------------------
    def _emit_task_changed(self, task: Task) -> None:
        self.task_changed.emit(task)

    def clear(self) -> None:
        """Vide la file (utilisé pour quitter l'état de démonstration)."""
        self._pending.clear()
        self._current_id = None
        self._cancelling = False
        if self._worker.is_running:
            # Bloquant court : l'interface quitte un état (démo, nouvelle
            # analyse) et aucun worker ne doit continuer en arrière-plan.
            self._worker.request_cancel(grace_ms=500)
            self._worker.stop(grace_ms=500)
        self._pages.clear()
        self._start_error = None
        self._queue = TaskQueue()
        self._queue.set_on_change(self._emit_task_changed)

    def shutdown(self) -> None:
        """Arrêt de l'application : aucun worker ne doit survivre.

        Idempotent : appelé par ``MainWindow.closeEvent`` et par
        ``QCoreApplication.aboutToQuit``. Si le worker ne répond pas à la
        commande ``cancel``, il est terminé puis tué (voir WorkerClient.stop).
        """
        self._shutting_down = True
        self._pending.clear()
        self._current_id = None
        self._cancelling = False
        if self._worker.is_running:
            logger.debug("arrêt du worker à la fermeture de l'application")
        self._worker.stop()
