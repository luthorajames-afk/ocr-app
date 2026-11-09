"""Fenêtre principale AOCRI (phase 8 — finitions v1.0).

La fenêtre ne fait aucun OCR : elle affiche les résultats produits par les
workers en sous-processus et pilote pause / reprise / annulation (R2).
Phase 5 : édition avant copie (R9) — l'``EditSession`` porte le document
courant, l'annulation (Ctrl+Z) / le rétablissement (Ctrl+Y) et le lien
bidirectionnel bloc ↔ texte ; l'aperçu et les trois onglets se rafraîchissent
depuis le modèle édité.
Phase 6 : copie riche (texte + HTML ``<table>`` + Markdown, sans ancres
d'édition) avec « Copier la page affichée » / « Copier tout le document », et
export réel DOCX / XLSX / PDF via une boîte de dialogue format + chemin (R14).
Phase 8 : traitement par lots (« Ajouter un dossier », compteur global
« Lot : X/N terminés », boutons « Tout traiter » / « Tout annuler »), relance
d'une page dans un autre mode (menu contextuel de l'aperçu → tâche ciblée
``--page``) et réglages OCR persistants (langues + DPI) ouverts depuis la
barre d'outils.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QPoint,
    QStandardPaths,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.clipboard import document_payload, page_payload
from ocr_app.core.data_model import Document, Page
from ocr_app.core.exports import Exporter
from ocr_app.core.task_queue import Task, TaskStatus
from ocr_app.ui import file_utils
from ocr_app.ui.app_settings import (
    AppSettings,
    load_settings,
    save_settings,
    settings_summary,
)
from ocr_app.ui.clipboard_qt import put_on_clipboard
from ocr_app.ui.demo_data import DEMO_FILES
from ocr_app.ui.edit_session import (
    EditSession,
    block_identity,
    load_pages_from_dir,
)
from ocr_app.ui.export_dialog import ask_export_path
from ocr_app.ui.icons import app_icon
from ocr_app.ui.modules_info import demo_modules, detect_modules
from ocr_app.ui.settings_dialog import SettingsDialog
from ocr_app.ui.task_format import mode_label
from ocr_app.ui.task_runner import TaskRunner
from ocr_app.ui.worker_commands import resolve_worker_commands
from ocr_app.ui.widgets.file_sidebar import FileSidebar
from ocr_app.ui.widgets.preview import PreviewPanel
from ocr_app.ui.widgets.results_panel import ResultsPanel
from ocr_app.ui.widgets.status_bar import BottomBar
from ocr_app.ui.widgets.titlebar import TitleBar
from ocr_app.ui.widgets.toolbar import AppToolbar

WINDOW_TITLE = "AOCRI — Advanced Optical Character Recognition for Itamba"
VERSION_LABEL = "v1.0"


def _full_window_title(dirty: bool = False) -> str:
    """Titre avec branding de version (« v1.0 ») et marque de modification."""
    marker = "● " if dirty else ""
    return f"{marker}{WINDOW_TITLE} — {VERSION_LABEL}"


class MainWindow(QMainWindow):
    """Fenêtre principale : branding, barre d'outils, trois panneaux, statut."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        demo: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_full_window_title())
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1000, 700)

        self._mode = "simple"
        self._selected_id: Optional[str] = None
        self._demo_active = False

        # Phase 8 : réglages OCR persistants (langues, DPI), chargés avant la
        # création des tâches pour que chaque worker reçoive --lang / --dpi.
        self._settings: AppSettings = load_settings()

        # Phase 7 : workers réels par mode (worker_main) quand le module du
        # mode est installé ; message clair « module non installé » sinon.
        commands, unavailable_modes = resolve_worker_commands()
        self._unavailable_modes = dict(unavailable_modes)
        self._runner = TaskRunner(
            self,
            worker_commands=commands,
            unavailable_modes=unavailable_modes,
        )
        self._runner.set_worker_settings(
            self._settings.languages, self._settings.dpi
        )
        self._edit_session = EditSession(self)
        self._build_ui()
        self._connect_signals()

        self._toolbar.set_settings_indicator(settings_summary(self._settings))
        self._sidebar.set_modules(detect_modules())
        self._reset_view()
        if demo:
            self.load_demo()

    # --- Construction -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._titlebar = TitleBar(root)
        root_layout.addWidget(self._titlebar)

        self._toolbar = AppToolbar(root)
        root_layout.addWidget(self._toolbar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)

        self._sidebar = FileSidebar(self._splitter)
        self._preview = PreviewPanel(self._splitter)
        self._results = ResultsPanel(self._splitter)
        self._splitter.addWidget(self._sidebar)
        self._splitter.addWidget(self._preview)
        self._splitter.addWidget(self._results)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([252, 668, 360])
        root_layout.addWidget(self._splitter, 1)

        self._bottom = BottomBar(root)
        root_layout.addWidget(self._bottom)

        self.setCentralWidget(root)

    def _connect_signals(self) -> None:
        self._toolbar.add_files_requested.connect(self._choose_files)
        self._toolbar.add_folder_requested.connect(self._choose_folder)
        self._toolbar.settings_requested.connect(self._open_settings)
        self._toolbar.mode_changed.connect(self._on_mode_changed)

        self._sidebar.file_selected.connect(self._on_file_selected)
        self._preview.block_selected.connect(self._on_block_selected)
        # Phase 8 : menu contextuel de l'aperçu → « Relancer cette page en… ».
        self._preview.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._preview.customContextMenuRequested.connect(
            self._show_relaunch_menu
        )
        self._results.block_clicked.connect(self._on_results_block_clicked)
        self._results.block_text_committed.connect(
            self._on_block_text_committed
        )
        self._results.notice.connect(self._on_notice)
        # Éditeur inline ouvert : Ctrl+Z/Ctrl+Y restent locaux à l'éditeur.
        self._results.editing_changed.connect(self._on_editing_changed)

        self._bottom.pause_clicked.connect(self._toggle_pause)
        self._bottom.cancel_clicked.connect(self._cancel_current)
        self._bottom.reset_clicked.connect(self._reset_edits)
        self._bottom.copy_clicked.connect(self._show_copy_menu)
        self._bottom.export_clicked.connect(self._export_document)
        self._bottom.process_all_clicked.connect(self._process_all_pending)
        self._bottom.cancel_all_clicked.connect(self._cancel_all)

        self._runner.task_changed.connect(self._on_task_changed)
        self._runner.page_loaded.connect(self._on_page_loaded)
        self._runner.task_started.connect(self._on_task_started)
        self._runner.error_occurred.connect(self._on_error)
        self._runner.notice.connect(self._on_notice)
        self._runner.batch_progress.connect(self._on_batch_progress)

        # Session d'édition : le modèle est la source de vérité, les vues se
        # rafraîchissent à chaque documentChanged (édition, undo, redo).
        self._edit_session.documentChanged.connect(self._on_document_changed)
        self._edit_session.dirtyChanged.connect(self._on_dirty_changed)
        self._edit_session.documentReloaded.connect(self._refresh_edit_status)

        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_shortcut.activated.connect(self._undo_edits)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self._redo_shortcut.activated.connect(self._redo_edits)
        self._redo_alt_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        self._redo_alt_shortcut.activated.connect(self._redo_edits)

        shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        shortcut.activated.connect(self._choose_files)

    def _reset_view(self) -> None:
        self._selected_id = None
        self._edit_session.clear()
        self._preview.show_placeholder()
        self._results.clear(
            "Aucun résultat à afficher — ajoutez des fichiers PDF, JPG ou PNG."
        )
        self._bottom.set_task(None)
        self._bottom.set_content_available(False)
        self._refresh_edit_status()

    # --- Ajout de fichiers ------------------------------------------------
    def _choose_files(self) -> None:
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        start = documents if documents and Path(documents).is_dir() else str(
            Path.home()
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Ajouter des fichiers",
            start,
            "Documents pris en charge (*.pdf *.jpg *.jpeg *.png);;"
            "Tous les fichiers (*)",
        )
        if paths:
            self.add_paths(paths)

    def _choose_folder(self) -> None:
        """Phase 8 : « Ajouter un dossier » → tous les PDF/JPG/PNG en lot.

        Le parcours récursif est optionnel (case à cocher) ; les fichiers sont
        ajoutés dans l'ordre alphabétique et traités séquentiellement par la
        file, avec compteur global dans la barre de statut.
        """
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        start = documents if documents and Path(documents).is_dir() else str(
            Path.home()
        )
        directory = QFileDialog.getExistingDirectory(
            self, "Ajouter un dossier", start
        )
        if not directory:
            return

        question = QMessageBox(self)
        question.setWindowTitle("Traitement par lots")
        question.setIcon(QMessageBox.Icon.Question)
        question.setText(
            "Ajouter tous les PDF, JPG et PNG de ce dossier au lot de "
            f"traitement ?\n\n{directory}"
        )
        recursive_box = QCheckBox("Inclure les sous-dossiers", question)
        question.setCheckBox(recursive_box)
        question.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        question.button(QMessageBox.StandardButton.Yes).setText("Ajouter")
        question.button(QMessageBox.StandardButton.No).setText("Annuler")
        if question.exec() != QMessageBox.StandardButton.Yes:
            return

        paths, skipped = self._collect_folder_documents(
            directory, recursive=recursive_box.isChecked()
        )
        if not paths:
            self._bottom.show_toast(
                "Aucun fichier pris en charge (PDF, JPG, PNG) dans ce dossier.",
                error=True,
            )
            return
        added = self.add_paths([str(path) for path in paths])
        # Phase 8 : les fichiers non pris en charge du dossier ne bloquent pas
        # le reste du lot, mais ils sont signalés clairement (jamais ignorés
        # en silence) — le lot continue sur les fichiers valides.
        if added and skipped:
            self._bottom.show_toast(
                f"{skipped} fichier(s) ignoré(s) : format non pris en charge "
                "(PDF, JPG, PNG attendus).",
                error=True,
                timeout=6000,
            )

    @staticmethod
    def _collect_folder_documents(
        directory: str, recursive: bool
    ) -> Tuple[List[Path], int]:
        """Documents pris en charge d'un dossier, tri alphabétique (R14).

        Retourne ``(documents retenus, nombre de fichiers ignorés)`` : un
        fichier non pris en charge (extension autre que PDF/JPG/PNG) n'entre
        pas dans le lot mais est compté pour être signalé à l'utilisateur.
        """
        base = Path(directory)
        pattern = "**/*" if recursive else "*"
        try:
            files = [path for path in sorted(base.glob(pattern)) if path.is_file()]
        except OSError:
            return [], 0
        candidates = [
            path
            for path in files
            if file_utils.is_supported_input(str(path))
        ]
        # Tri alphabétique insensible à la casse (a.pdf, B.jpg, c.png…).
        return (
            sorted(candidates, key=lambda path: path.name.lower()),
            len(files) - len(candidates),
        )

    def add_paths(self, paths: List[str]) -> int:
        """Ajoute des fichiers et démarre leur traitement (API utilisable hors dialog)."""
        valid = [
            str(Path(path))
            for path in paths
            if Path(path).is_file() and file_utils.is_supported_input(path)
        ]
        if not valid:
            self._bottom.show_toast(
                "Aucun fichier pris en charge (PDF, JPG, PNG).", error=True
            )
            return 0

        if self._demo_active:
            self._exit_demo()

        # Phase 8 : lot de N fichiers → compteur global « Lot : X/N terminés ».
        batch = len(valid) >= 2
        task_ids: List[str] = []
        first_id: Optional[str] = None
        for path in valid:
            total = file_utils.probe_page_count(path)
            task = self._runner.create_task(path, self._mode, total)
            self._sidebar.add_task(task)
            task_ids.append(task.id)
            if first_id is None:
                first_id = task.id

        if batch:
            self._runner.begin_batch(task_ids)
        for task_id in task_ids:
            self._runner.enqueue(task_id)

        if first_id is not None:
            self._select_task(first_id)
        self._refresh_pending_button()
        message = f"{len(valid)} fichier(s) ajouté(s) — traitement lancé."
        if batch:
            message += " Lot : 0/" + str(len(valid)) + " terminés."
        self._bottom.show_toast(message)
        return len(valid)

    # --- Démonstration ----------------------------------------------------
    def load_demo(self) -> None:
        """Charge l'état exact du mockup validé (aucun worker lancé)."""
        self._exit_demo()
        self._demo_active = True
        self._sidebar.set_modules(demo_modules())

        first_id: Optional[str] = None
        for spec in DEMO_FILES:
            task = self._runner.create_task(
                spec.filename, self._mode, spec.total_pages
            )
            if spec.status != TaskStatus.PENDING:
                self._runner.queue.update_progress(
                    task.id,
                    status=spec.status,
                    total_pages=spec.total_pages,
                    current_page=spec.current_page,
                    pages_done=spec.pages_done,
                    stage=spec.stage,
                )
            if spec.page is not None:
                # Copie profonde : les pages de DEMO_FILES sont des singletons
                # de module ; sans copie, une édition de la fenêtre démo fuit
                # vers la fenêtre démo suivante (contamination inter-tests).
                self._runner.register_page(
                    task.id, Page.from_dict(spec.page.to_dict())
                )
            updated = self._runner.task(task.id)
            self._sidebar.add_task(updated)
            if first_id is None:
                first_id = updated.id

        if first_id is not None:
            self._select_task(first_id)
        self._refresh_pending_button()

    def _exit_demo(self) -> None:
        if not self._demo_active and self._sidebar.row_count() == 0:
            return
        self._runner.clear()
        self._sidebar.clear()
        self._sidebar.set_modules(detect_modules())
        self._demo_active = False
        self._bottom.set_batch_progress(None)
        self._reset_view()
        self._refresh_pending_button()

    # --- Sélection --------------------------------------------------------
    def _select_task(self, task_id: str) -> None:
        task = self._runner.task(task_id)
        if task is None:
            return
        self._selected_id = task_id
        self._sidebar.select_task(task_id)
        self._show_task(task_id)

    def _on_file_selected(self, task_id: str) -> None:
        self._selected_id = task_id
        self._show_task(task_id)

    def _show_task(self, task_id: str) -> None:
        task = self._runner.task(task_id)
        if task is None:
            self._reset_view()
            return

        name = Path(task.source_path).name if task.source_path else task_id
        info = self._task_info(task)
        page = self._runner.latest_page(task_id)

        self._preview.set_title_info(name, info)
        self._bottom.set_task(task, title_override=self._relaunch_title(task))
        if page is not None:
            self._preview.show_page(page)
            self._results.set_page(page)
            self._bottom.set_content_available(True)
            # Nouveau document affiché : la session d'édition démarre propre
            # (instantané « origine » pris sur les pages du runner).
            self._edit_session.load_document(
                self._runner.document_for(task_id), task_id=task_id
            )
        else:
            self._edit_session.clear()
            self._preview.show_placeholder(title=name, info=info)
            if task.status == TaskStatus.PENDING:
                message = "Ce document est en attente de traitement."
            elif task.status == TaskStatus.ERROR:
                message = task.error_message or "Le traitement a échoué."
            elif task.status == TaskStatus.CANCELLED:
                message = "Le traitement a été annulé."
            else:
                message = "En attente du premier résultat…"
            self._results.clear(message)
            self._bottom.set_content_available(False)

        self._refresh_edit_status()

    def _relaunch_title(self, task: Optional[Task]) -> Optional[str]:
        """Titre distinct d'une tâche de relance : « Relance page 3 »."""
        if task is None:
            return None
        relaunch = self._runner.relaunch_page(task.id)
        if relaunch is None:
            return None
        return f"Relance page {relaunch}"

    def _task_info(self, task: Optional[Task]) -> str:
        """Info d'en-tête de l'aperçu : relance, pages, DPI des réglages."""
        dpi_suffix = f"{self._settings.effective_dpi()} DPI"
        if task is None:
            return dpi_suffix
        relaunch = self._runner.relaunch_page(task.id)
        if relaunch is not None:
            return (
                f"Relance page {relaunch} · {mode_label(task.mode)} · "
                f"{dpi_suffix}"
            )
        total = task.total_pages
        if total <= 0:
            return dpi_suffix
        if task.status == TaskStatus.PENDING:
            return f"{total} pages · {dpi_suffix}"
        if task.current_page > 0:
            return f"Page {task.current_page} / {total} · {dpi_suffix}"
        return f"{total} pages · {dpi_suffix}"

    # --- Signaux tâches ---------------------------------------------------
    def _on_task_changed(self, task: Task) -> None:
        self._sidebar.update_task(task)
        self._refresh_pending_button()
        if task.id != self._selected_id:
            return

        self._bottom.set_task(task, title_override=self._relaunch_title(task))
        self._preview.set_info(self._task_info(task))
        page = self._runner.latest_page(task.id)
        self._bottom.set_content_available(page is not None)
        if page is None and task.status in (
            TaskStatus.ERROR,
            TaskStatus.CANCELLED,
        ):
            message = task.error_message or (
                "Le traitement a été annulé."
                if task.status == TaskStatus.CANCELLED
                else "Le traitement a échoué."
            )
            self._results.clear(message)
        # Temps de traitement : recalculé à chaque progression de la tâche.
        self._refresh_edit_status()

    def _on_task_started(self, task_id: str) -> None:
        selected = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        if selected is None or selected.status in (
            TaskStatus.DONE,
            TaskStatus.CANCELLED,
            TaskStatus.ERROR,
        ):
            self._select_task(task_id)
        elif selected.id == task_id:
            self._show_task(task_id)

    def _on_page_loaded(self, task_id: str, page) -> None:
        if task_id != self._selected_id:
            return
        task = self._runner.task(task_id)
        self._preview.set_title_info(
            Path(task.source_path).name if task and task.source_path else "",
            self._task_info(task),
        )
        self._preview.show_page(page)
        self._results.set_page(page)
        self._bottom.set_content_available(True)
        if task is not None:
            self._bottom.set_task(
                task, title_override=self._relaunch_title(task)
            )
        # La page fraîchement traitée rejoint la session d'édition courante :
        # les éditions des autres pages restent annulables.
        if self._edit_session.task_id == task_id and (
            self._edit_session.document is not None
        ):
            self._edit_session.adopt_page(page)
        else:
            self._edit_session.load_document(
                self._runner.document_for(task_id), task_id=task_id
            )
        self._refresh_edit_status()

    def _on_error(self, task_id: str, message: str) -> None:
        self._bottom.show_toast(f"Erreur : {message}", error=True, timeout=7000)
        if task_id == self._selected_id:
            task = self._runner.task(task_id)
            if task is not None:
                # title_override : une relance en erreur affiche « Relance
                # page N — erreur », pas « Page 1 / 1 — erreur ».
                self._bottom.set_task(
                    task, title_override=self._relaunch_title(task)
                )

    def _on_notice(self, message: str) -> None:
        self._bottom.show_toast(message)

    # --- Lot et file d'attente (phase 8) ----------------------------------
    def _on_batch_progress(self, done: int, total: int) -> None:
        """Compteur global du lot dans la barre de statut."""
        self._bottom.set_batch_progress((done, total))
        if 0 < total <= done:
            self._bottom.show_toast(
                f"Lot terminé : {done}/{total} fichier(s) traité(s)."
            )

    def _process_all_pending(self) -> None:
        """Bouton « Tout traiter » : (re)lance toutes les tâches en attente."""
        if self._demo_active:
            self._bottom.show_toast(
                "Mode démonstration : aucun worker actif.", error=True
            )
            return
        queued = self._runner.enqueue_all_pending()
        if queued:
            self._bottom.show_toast(f"{queued} tâche(s) remise(s) en file.")

    def _cancel_all(self) -> None:
        """Bouton « Tout annuler » : seule action qui arrête un lot entier.

        Le bouton « Annuler » d'une tâche n'arrête PAS le lot (les suivantes
        démarrent normalement) ; celui-ci annule la tâche en cours et vide la
        file d'attente (les tâches restent visibles, marquées « annulé »).
        """
        if self._demo_active:
            self._bottom.show_toast(
                "Mode démonstration : aucun worker actif.", error=True
            )
            return
        cancelled = self._runner.cancel_all()
        if not cancelled:
            self._bottom.show_toast("Aucune tâche à annuler.", error=True)

    def _refresh_pending_button(self) -> None:
        """« Tout traiter » visible seulement si des tâches attendent."""
        if self._demo_active:
            self._bottom.set_pending_count(0)
            return
        self._bottom.set_pending_count(self._runner.pending_count())

    # --- Relance d'une page dans un autre mode (phase 8) -------------------
    def _show_relaunch_menu(self, pos) -> None:
        """Menu contextuel de l'aperçu : « Relancer cette page en… ».

        Désactivé pendant un traitement en cours sur le même document, et
        pour les modes dont le module n'est pas installé (message clair).
        """
        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        page = self._selected_page()
        if task is None or page is None:
            return
        source = Path(task.source_path) if task.source_path else None
        if source is None or not source.is_file():
            return
        page_number = page.number

        busy = self._runner.is_document_busy(task.source_path)
        menu = QMenu(self)
        submenu = menu.addMenu(f"Relancer la page {page_number} en…")
        # Les raisons de désactivation (module non installé, traitement en
        # cours) sont portées par les tooltips : sans cette ligne, Qt ne les
        # affiche pas dans un menu et l'action grisée reste inexpliquée.
        submenu.setToolTipsVisible(True)
        for mode in ("simple", "medium", "advanced"):
            action = submenu.addAction(mode_label(mode))
            unavailable = self._unavailable_modes.get(mode)
            if busy:
                action.setEnabled(False)
                action.setToolTip(
                    "Un traitement est déjà en cours sur ce document."
                )
            elif unavailable:
                action.setEnabled(False)
                action.setToolTip(unavailable)
            else:
                action.triggered.connect(
                    lambda checked=False, chosen=mode, number=page_number: (
                        self._relaunch_page(chosen, number)
                    )
                )
        menu.exec(self._preview.mapToGlobal(pos))
        menu.deleteLater()  # même raison que les menus copie/export

    def _relaunch_page(self, mode: str, page_number: int) -> None:
        """Crée une tâche ciblée (mode choisi, --input + --page) et l'enfile.

        La relance ne bloque jamais l'interface : elle passe par la file
        ``TaskRunner`` (sous-processus, R2) et démarre quand le worker est
        libre. Le résultat est écrit dans le dossier de travail de la nouvelle
        tâche — le JSON de la page d'origine n'est jamais écrasé ; en cas
        d'échec du mode, le résultat d'origine reste affiché (erreur signalée
        dans la barre de statut).
        """
        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        if task is None or not task.source_path:
            return
        if self._runner.is_document_busy(task.source_path):
            self._bottom.show_toast(
                "Un traitement est déjà en cours sur ce document.", error=True
            )
            return

        relaunch = self._runner.create_page_task(
            task.source_path, mode, page_number
        )
        self._sidebar.add_task(relaunch)
        self._runner.enqueue(relaunch.id)
        self._select_task(relaunch.id)
        self._refresh_pending_button()
        self._bottom.show_toast(
            f"Relance de la page {page_number} en {mode_label(mode)}…",
            timeout=5000,
        )

    # --- Commandes --------------------------------------------------------
    def _toggle_pause(self) -> None:
        if self._demo_active:
            self._bottom.show_toast(
                "Mode démonstration : aucun worker actif.", error=True
            )
            return
        if not self._runner.toggle_pause_current():
            self._bottom.show_toast(
                "Aucune tâche en cours à mettre en pause.", error=True
            )

    def _cancel_current(self) -> None:
        if self._demo_active:
            self._bottom.show_toast(
                "Mode démonstration : aucun worker actif.", error=True
            )
            return
        if not self._runner.cancel_current():
            self._bottom.show_toast("Aucune tâche en cours à annuler.", error=True)

    # --- Copie / export ---------------------------------------------------
    def _selected_page(self):
        """Page actuellement affichée dans le panneau de résultats.

        On copie / réinitialise ce qui est à l'écran (R9 : édition avant
        copie), pas nécessairement la dernière page reçue du worker.
        """
        page = self._results.current_page()
        if page is not None:
            return page
        if self._selected_id is None:
            return None
        return self._runner.latest_page(self._selected_id)

    def _current_document(self) -> Optional[Document]:
        """Document courant avec ses éditions (les Page sont partagées, R9)."""
        document = self._edit_session.document
        if document is not None and document.pages:
            return document
        if self._selected_id is None:
            return None
        return self._runner.document_for(self._selected_id)

    def _show_copy_menu(self) -> None:
        """Bouton « Copier » : page affichée ou document complet."""
        menu = QMenu(self)
        menu.addAction("Copier la page affichée", self._copy_page)
        menu.addAction("Copier tout le document", self._copy_document)
        button = self._bottom.copy_button
        anchor = button.mapToGlobal(QPoint(0, -menu.sizeHint().height()))
        menu.exec(anchor)
        # Un menu parenté à la fenêtre s'accumule à chaque ouverture : libéré
        # après la fermeture (exec est bloquant, deleteLater est donc sûr ici).
        menu.deleteLater()

    def _copy_page(self) -> None:
        """Copie la page affichée : texte brut + HTML (tableaux) + Markdown."""
        page = self._selected_page()
        if page is None:
            self._bottom.show_toast("Aucune page à copier.", error=True)
            return
        copied_as = put_on_clipboard(page_payload(page))
        self._bottom.show_toast(f"Page {page.number} copiée ({copied_as}).")

    def _copy_document(self) -> None:
        """Copie tout le document courant (une section par page)."""
        document = self._current_document()
        if document is None or not document.pages:
            self._bottom.show_toast("Aucun document à copier.", error=True)
            return
        copied_as = put_on_clipboard(document_payload(document))
        self._bottom.show_toast(
            f"Document copié — {len(document.pages)} page(s) ({copied_as})."
        )

    def _export_document(self) -> None:
        """Bouton « Exporter » : format + chemin (R14), exécution rapide dans
        le thread GUI (curseur d'attente), erreur signalée, retour dans la
        barre de statut."""
        document = self._current_document()
        if document is None or not document.pages:
            self._bottom.show_toast("Aucune page à exporter.", error=True)
            return

        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        source = (
            Path(task.source_path) if task and task.source_path else Path("document")
        )
        directory = (
            source.parent
            if source.parent.is_dir()
            else Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.DocumentsLocation
                )
                or Path.home()
            )
        )

        choice = ask_export_path(
            self,
            default_dir=directory,
            default_stem=source.stem or "resultat_ocr",
        )
        if choice is None:
            return
        fmt, output = choice

        # Retour visuel immédiat : l'écriture d'un gros document DOCX peut
        # prendre une à deux secondes (curseur d'attente + message, sans
        # traiter les clics — un processEvents complet permettrait de
        # relancer l'export pendant l'export, en réentrance).
        self._bottom.show_toast(f"Export {fmt.label} en cours…", timeout=15000)
        failure: Optional[str] = None
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            QCoreApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
            )
            exporter = Exporter(document)
            getattr(exporter, fmt.method)(str(output))
        except Exception as exc:  # dépendances optionnelles ou disque
            failure = str(exc)
        finally:
            QGuiApplication.restoreOverrideCursor()

        if failure is not None:
            QMessageBox.warning(
                self,
                "Export impossible",
                f"L'export {fmt.label} a échoué.\n\n{failure}",
            )
            return
        self._bottom.show_toast(f"Exporté : {output.name}", timeout=5000)

    # --- Réglages ---------------------------------------------------------
    def _open_settings(self) -> None:
        """Bouton réglages de la toolbar : dialogue langues + DPI (phase 8).

        Les réglages validés sont persistés (``%LOCALAPPDATA%\\AOCRI\\
        settings.json``, UTF-8) puis appliqués : indicateur de la toolbar
        rafraîchi et ``--lang`` / ``--dpi`` passés aux prochains workers.
        """
        dialog = SettingsDialog(
            self._settings,
            self,
            show_demo_entry=(
                self._sidebar.row_count() == 0 and not self._demo_active
            ),
        )
        dialog.work_dir_requested.connect(self._open_work_dir)
        # Les QMessageBox ouverts depuis le dialogue modal sont parentés à
        # LUI : parentés à la fenêtre principale, ils risquent de s'empiler
        # SOUS le dialogue modal (fenêtre figée en apparence).
        dialog.modules_requested.connect(lambda: self._show_modules(dialog))
        dialog.about_requested.connect(lambda: self._show_about(dialog))

        def _start_demo() -> None:
            dialog.reject()
            self.load_demo()

        dialog.demo_requested.connect(_start_demo)
        if not dialog.exec():
            dialog.deleteLater()
            return
        self._settings = dialog.result_settings()
        try:
            save_settings(self._settings)
        except OSError as exc:
            self._bottom.show_toast(
                f"Réglages non enregistrés : {exc}", error=True, timeout=7000
            )
        self._apply_settings()
        dialog.deleteLater()

    def _apply_settings(self) -> None:
        """Réglages → workers (--lang/--dpi), indicateur toolbar, toast."""
        self._runner.set_worker_settings(
            self._settings.languages, self._settings.dpi
        )
        summary = settings_summary(self._settings)
        self._toolbar.set_settings_indicator(summary)
        self._bottom.show_toast(f"Réglages appliqués : {summary}.")

    def _open_work_dir(self) -> None:
        try:
            path = file_utils.work_root()
        except OSError as exc:
            self._bottom.show_toast(
                f"Dossier de travail inaccessible : {exc}", error=True
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_modules(self, parent: Optional[QWidget] = None) -> None:
        lines = []
        for module in detect_modules():
            state = "installé" if module.installed else "non installé"
            lines.append(f"- {module.label} : {state}")
        QMessageBox.information(
            parent or self,
            "Modules OCR",
            "Modules détectés sur cette machine :\n\n"
            + "\n".join(lines)
            + "\n\nPhase 5 : l'édition avant copie (R9) est branchée — "
            "double-clic sur un bloc de texte dans Rendu ou Texte brut.",
        )

    def _show_about(self, parent: Optional[QWidget] = None) -> None:
        from ocr_app import __version__

        QMessageBox.about(
            parent or self,
            "À propos d'AOCRI",
            "<b>AOCRI</b> — Advanced Optical Character Recognition for "
            "Itamba<br><br>"
            "Kriesty Solutions · application hors ligne<br>"
            f"Version {__version__} · {VERSION_LABEL}",
        )

    # --- Divers -----------------------------------------------------------
    def _on_mode_changed(self, mode: str) -> None:
        self._mode = mode
        self._bottom.show_toast(
            f"Mode {mode_label(mode)} sélectionné pour les prochains fichiers.",
            timeout=2200,
        )

    # --- Lien bloc ↔ texte + édition (phase 5, R9) ------------------------
    def _on_block_selected(self, index: int) -> None:
        """Sélection depuis l'aperçu : défilement + surlignage du texte."""
        self._results.set_active_block(index, reveal=True)
        self._preview.set_active_block(index)

    def _on_results_block_clicked(self, index: int) -> None:
        """Clic sur un texte (rendu ou brut) : surcouche mise en évidence."""
        self._preview.select_block(index)
        self._results.set_active_block(index)

    def _on_block_text_committed(self, index: int, new_text: str) -> None:
        """Édition inline validée : le modèle ``Page`` est mis à jour."""
        page = self._results.current_page()
        if page is None or not 0 <= index < len(page.blocks):
            return
        block = page.blocks[index]
        self._edit_session.update_block_text(
            page.number, block_identity(block, index), new_text
        )

    def _on_document_changed(self, page_number: int, block_id: str) -> None:
        """Le modèle a changé (édition, undo, redo) : vues + statut."""
        index = self._edit_session.block_index(page_number, block_id)
        changed_page = self._edit_session.page(page_number)
        displayed = self._selected_page()
        if changed_page is not None and changed_page is displayed:
            tab = self._results.current_tab_index()
            self._preview.show_page(changed_page)
            self._results.set_page(changed_page, active_block=index)
            self._results.show_tab(tab)
            if index is not None:
                self._preview.select_block(index)
        self._refresh_edit_status()

    def _on_dirty_changed(self, dirty: bool) -> None:
        self._refresh_edit_status()

    def _on_editing_changed(self, editing: bool) -> None:
        """Éditeur inline ouvert : Ctrl+Z/Ctrl+Y annulent le texte en cours."""
        for shortcut in (
            self._undo_shortcut,
            self._redo_shortcut,
            self._redo_alt_shortcut,
        ):
            shortcut.setEnabled(not editing)

    def _undo_edits(self) -> None:
        if not self._edit_session.undo():
            self._bottom.show_toast("Rien à annuler.", error=True)

    def _redo_edits(self) -> None:
        if not self._edit_session.redo():
            self._bottom.show_toast("Rien à rétablir.", error=True)

    def _reset_edits(self) -> None:
        """Bouton Réinitialiser : recharge le texte d'origine du document."""
        if not self._edit_session.is_dirty():
            self._bottom.show_toast(
                "Aucune modification à réinitialiser.", error=True
            )
            return
        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        pages = None
        result_dir = getattr(task, "result_dir", None) if task else None
        if result_dir:
            pages = load_pages_from_dir(Path(result_dir))
        self._edit_session.reset(pages)
        page = self._selected_page()
        if page is not None:
            self._preview.show_page(page)
            self._results.set_page(page)
        self._refresh_edit_status()
        self._bottom.show_toast(
            "Modifications réinitialisées — texte d'origine restauré."
        )

    def _refresh_edit_status(self) -> None:
        """Titre (« ● modifié » · v1.0) + statistiques de la barre inférieure."""
        dirty = self._edit_session.is_dirty()
        title = _full_window_title(dirty=dirty)
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        self._bottom.set_edit_stats(
            self._edit_session.modified_block_count(),
            self._edit_session.total_block_count(),
            self._processing_seconds(task),
        )

    @staticmethod
    def _processing_seconds(task: Optional[Task]) -> Optional[float]:
        """Temps de traitement mesuré sur les horodatages de la tâche."""
        if task is None or task.status == TaskStatus.PENDING:
            return None
        if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
            return max(0.0, time.time() - task.created_at)
        return max(0.0, task.updated_at - task.created_at)

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._runner.shutdown()
        super().closeEvent(event)
