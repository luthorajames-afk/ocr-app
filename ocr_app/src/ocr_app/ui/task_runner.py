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
from ocr_app.ui.app_settings import AVAILABLE_LANGUAGES
from ocr_app.ui.worker_client import WorkerClient
from ocr_app.ui.worker_commands import accepts_input_document

logger = logging.getLogger(__name__)

# Attribut « portable » posé sur chaque Page chargée depuis un résultat :
# le dossier de travail de la tâche, où le worker écrit page_NNN.json et
# page_NNN.png (plan v1). L'aperçu (ui/widgets/preview.py) s'en sert pour
# afficher l'image réelle de la page en fond. Attribut transient volontaire
# (hors data_model, lecture seule) : il ne survit pas à une sérialisation.
PAGE_WORK_DIR_ATTR = "work_dir"


class TaskRunner(QObject):
    """File de tâches + pilotage des workers en sous-processus."""

    task_changed = Signal(object)            # Task
    page_loaded = Signal(str, object)        # task_id, Page
    task_started = Signal(str)               # task_id
    error_occurred = Signal(str, str)        # task_id, message
    notice = Signal(str)                     # message transitoire
    batch_progress = Signal(int, int)        # phase 8 : (terminées, total)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        worker_commands: Optional[Mapping[str, Sequence[str]]] = None,
        unavailable_modes: Optional[Mapping[str, str]] = None,
    ) -> None:
        """``worker_commands`` associe un mode (simple/medium/advanced) à la
        commande du worker réel. Sans entrée, le worker factice de
        développement (``fake_worker``) est utilisé pour tous les modes : le
        protocole, la pause et l'annulation restent ainsi testables avant le
        branchement des moteurs réels.

        ``unavailable_modes`` associe un mode à un message d'indisponibilité
        (« module non installé », cf. ``worker_commands.resolve_worker_commands``) :
        une tâche d'un tel mode passe en erreur sans lancer de worker.
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

        self._unavailable_modes: Dict[str, str] = {
            str(mode): str(message)
            for mode, message in (unavailable_modes or {}).items()
            if str(message)
        }

        self._pending: List[str] = []
        self._pages: Dict[str, Dict[int, Page]] = {}
        self._current_id: Optional[str] = None
        self._cancelling = False
        self._shutting_down = False
        self._start_error: Optional[str] = None
        self._log_tail: List[str] = []
        self._work_root: Optional[Path] = None

        # Phase 8 : réglages OCR passés aux workers (--lang, --dpi), relances
        # ciblées d'une page (--page) et compteur global des lots.
        self._settings_args: tuple[str, ...] = ()
        self._relaunch_pages: Dict[str, int] = {}
        self._batch_ids: set = set()
        self._batch_counted: set = set()
        self._batch_total = 0
        self._batch_done = 0

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

    def enqueue_all_pending(self) -> int:
        """« Tout traiter » : remet en file toutes les tâches encore en attente.

        Retourne le nombre de tâches (re)mises en file. Les tâches déjà en
        file ou déjà démarrées sont ignorées (``enqueue`` est idempotent).
        """
        queued = 0
        for task in self._queue.list():
            if task.status == TaskStatus.PENDING:
                self.enqueue(task.id)
                queued += 1
        return queued

    def pending_count(self) -> int:
        """Nombre de tâches PENDING dans la file (bouton « Tout traiter »)."""
        return sum(
            1 for task in self._queue.list() if task.status == TaskStatus.PENDING
        )

    def register_page(self, task_id: str, page: Page) -> None:
        """Enregistre une page déjà traitée (mode démonstration ou reprise)."""
        self._pages.setdefault(task_id, {})[page.number] = page

    # --- Réglages workers (phase 8) ---------------------------------------
    def set_worker_settings(self, languages: Sequence[str], dpi: int) -> None:
        """Réglages OCR à passer aux workers réels : ``--lang fra+eng --dpi N``.

        Stocké sous forme d'arguments prêts à l'emploi ; appliqué seulement
        aux workers qui les comprennent (worker_main et exécutables packagés —
        le worker factice de développement n'accepte ni ``--lang`` ni
        ``--dpi``, cf. ``accepts_input_document``).
        """
        wanted = {str(lang).strip().lower() for lang in languages}
        # Ordre canonique unique (cf. app_settings.clean_languages) : les
        # langues connues d'abord, dans l'ordre d'affichage, puis toute langue
        # inconnue en fin (tri alphabétique) plutôt que perdue.
        ordered = [lang for lang in AVAILABLE_LANGUAGES if lang in wanted]
        ordered += [
            lang for lang in sorted(wanted) if lang not in AVAILABLE_LANGUAGES
        ]
        dpi_value = max(1, min(int(dpi), 300))
        args: List[str] = []
        if ordered:
            args += ["--lang", "+".join(ordered)]
        args += ["--dpi", str(dpi_value)]
        self._settings_args = tuple(args)

    # --- Relance d'une page dans un autre mode (phase 8) -------------------
    def create_page_task(self, source_path: str, mode: str, page_number: int) -> Task:
        """Crée une tâche ciblée : UNE seule page d'un document existant.

        Le worker recevra ``--input <source> --page N`` ; le résultat est écrit
        dans le dossier de travail propre à cette nouvelle tâche (le JSON de la
        page d'origine n'est jamais écrasé) et rechargé dans l'aperçu à la fin.
        """
        task = self.create_task(source_path, mode, 1)
        self._relaunch_pages[task.id] = max(1, int(page_number))
        return task

    def relaunch_page(self, task_id: str) -> Optional[int]:
        """Numéro de page ciblé par une tâche de relance (None sinon)."""
        return self._relaunch_pages.get(task_id)

    def is_document_busy(self, source_path: str) -> bool:
        """Vrai si une tâche du même document est en cours ou en pause.

        Utilisé par la fenêtre pour désactiver « Relancer cette page en… »
        pendant un traitement en cours sur le même document.
        """
        normalized = str(source_path)
        for task in self._queue.list():
            if task.source_path != normalized:
                continue
            if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
                return True
        return False

    # --- Lots (phase 8) ----------------------------------------------------
    def begin_batch(self, task_ids: Sequence[str]) -> None:
        """Démarre le comptage global d'un lot de tâches créées ensemble.

        Seules les tâches listées sont comptées : une relance de page lancée
        pendant le lot ne fausse pas le compteur. Chaque tâche qui atteint un
        état terminal (terminée, annulée, erreur) incrémente « Lot : X/N ».
        """
        self._batch_ids = {str(task_id) for task_id in task_ids}
        self._batch_counted = set()
        self._batch_total = len(self._batch_ids)
        self._batch_done = 0
        if self._batch_total:
            self.batch_progress.emit(self._batch_done, self._batch_total)

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

            command = self._worker_commands.get(task.mode)
            # Mode dont le module n'est pas installé : message clair, aucun
            # worker lancé (la tâche reste en erreur consultable).
            if command is None:
                unavailable = self._unavailable_modes.get(task.mode)
                if unavailable:
                    self._queue.update_progress(
                        task_id,
                        status=TaskStatus.ERROR,
                        error_message=unavailable,
                    )
                    self.error_occurred.emit(task_id, unavailable)
                    continue
                # Sans message d'indisponibilité : repli historique sur le
                # worker factice de développement (command=None), qui conserve
                # le protocole, la pause et l'annulation testables. Ne jamais
                # laisser la tâche en PENDING (elle serait perdue de la file).

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

            # worker_main traite le document réel : lui passer --input.
            # (fake_worker, qui n'a pas cette option, reste utilisable tel quel.)
            # Phase 8 : les mêmes workers comprennent --page (relance ciblée)
            # et --lang/--dpi (réglages) ; le worker factice, jamais.
            accepts_real = accepts_input_document(command)
            extra_args: tuple[str, ...] = ()
            if (
                accepts_real
                and task.source_path
                and Path(task.source_path).is_file()
            ):
                extra_args = ("--input", task.source_path)
            relaunch_page = self._relaunch_pages.get(task_id)
            if accepts_real and relaunch_page is not None:
                extra_args += ("--page", str(relaunch_page))
            if accepts_real and self._settings_args:
                extra_args += self._settings_args

            if not self._worker.start(
                total,
                work_dir,
                command=command,
                extra_args=extra_args,
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

    def cancel_all(self) -> int:
        """« Tout annuler » explicite : arrête le lot entier.

        Contrairement à ``cancel_current`` (une tâche annulée n'arrête PAS le
        lot, les suivantes démarrent normalement), cette action est la seule
        qui vide la file d'attente : la tâche en cours est annulée et toutes
        les tâches encore PENDING passent en CANCELLED (elles restent
        consultables dans le panneau latéral et comptent dans « Lot : X/N »).

        Retourne le nombre de tâches annulées (0 si rien à annuler).
        """
        cancelled = 0
        for task in self._queue.list():
            if task.status == TaskStatus.PENDING:
                self._queue.cancel(task.id)
                cancelled += 1
        self._pending.clear()
        if self.cancel_current():
            cancelled += 1
        if cancelled:
            self.notice.emit(f"{cancelled} tâche(s) annulée(s).")
        return cancelled

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
        # L'aperçu charge page_NNN.png depuis ce dossier (plan v1) : on
        # mémorise sur la page le dossier de travail qui l'a produite.
        try:
            setattr(page, PAGE_WORK_DIR_ATTR, str(Path(result_path).parent))
        except (AttributeError, TypeError):  # pragma: no cover - défensif
            logger.debug("impossible de mémoriser le dossier de travail de la page")
        if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
            status = (
                TaskStatus.PAUSED
                if task.status == TaskStatus.PAUSED
                else TaskStatus.RUNNING
            )
            if task.id in self._relaunch_pages:
                # Relance ciblée : une page attendue, mais le numéro réel de
                # la page (ex. 3) est conservé pour l'affichage ; pages_done
                # reste borné au total de la tâche (1) pour que la fin de
                # worker la passe proprement en DONE.
                pages_done_value = 1
            else:
                pages_done_value = max(task.pages_done, page.number)
            self._queue.update_progress(
                task.id,
                status=status,
                pages_done=pages_done_value,
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
        # Phase 8 : compteur global du lot — chaque tâche du lot qui atteint
        # un état terminal est comptée une seule fois (terminée, annulée ou
        # en erreur, peu importe le chemin emprunté).
        if (
            task.id in self._batch_ids
            and task.id not in self._batch_counted
            and task.status
            in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ERROR)
        ):
            self._batch_counted.add(task.id)
            self._batch_done = min(self._batch_total, self._batch_done + 1)
            self.batch_progress.emit(self._batch_done, self._batch_total)
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
        self._relaunch_pages.clear()
        self._reset_batch()
        self._queue = TaskQueue()
        self._queue.set_on_change(self._emit_task_changed)

    def _reset_batch(self) -> None:
        self._batch_ids = set()
        self._batch_counted = set()
        self._batch_total = 0
        self._batch_done = 0

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
