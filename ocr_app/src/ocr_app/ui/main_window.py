"""Fenêtre principale AOCRI (phase 5) — fidèle au mockup validé.

La fenêtre ne fait aucun OCR : elle affiche les résultats produits par les
workers en sous-processus et pilote pause / reprise / annulation (R2).
Phase 5 : édition avant copie (R9) — l'``EditSession`` porte le document
courant, l'annulation (Ctrl+Z) / le rétablissement (Ctrl+Y) et le lien
bidirectionnel bloc ↔ texte ; l'aperçu et les trois onglets se rafraîchissent
depuis le modèle édité.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QMimeData, QPoint, QStandardPaths, Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.data_model import Page
from ocr_app.core.exports import Exporter
from ocr_app.core.task_queue import Task, TaskStatus
from ocr_app.ui import file_utils
from ocr_app.ui.demo_data import DEMO_FILES
from ocr_app.ui.edit_session import (
    EditSession,
    block_identity,
    load_pages_from_dir,
)
from ocr_app.ui.icons import app_icon
from ocr_app.ui.modules_info import demo_modules, detect_modules
from ocr_app.ui.task_format import mode_label
from ocr_app.ui.task_runner import TaskRunner
from ocr_app.ui.widgets.file_sidebar import FileSidebar
from ocr_app.ui.widgets.preview import PreviewPanel
from ocr_app.ui.widgets.results_panel import (
    ResultsPanel,
    page_to_html_document,
    page_to_text,
)
from ocr_app.ui.widgets.status_bar import BottomBar
from ocr_app.ui.widgets.titlebar import TitleBar
from ocr_app.ui.widgets.toolbar import AppToolbar

WINDOW_TITLE = "AOCRI — Advanced Optical Character Recognition for Itamba"

EXPORT_FORMATS = (
    ("txt", "Texte brut (*.txt)", ".txt", "save_plain_text"),
    ("md", "Markdown (*.md)", ".md", "save_markdown"),
    ("html", "HTML (*.html)", ".html", "save_html"),
    ("docx", "Word (*.docx)", ".docx", "save_docx"),
    ("xlsx", "Excel (*.xlsx)", ".xlsx", "save_xlsx"),
    ("pdf", "PDF (*.pdf)", ".pdf", "save_pdf"),
)


class MainWindow(QMainWindow):
    """Fenêtre principale : branding, barre d'outils, trois panneaux, statut."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        demo: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1000, 700)

        self._mode = "simple"
        self._selected_id: Optional[str] = None
        self._demo_active = False

        self._runner = TaskRunner(self)
        self._edit_session = EditSession(self)
        self._build_ui()
        self._connect_signals()

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
        self._toolbar.settings_requested.connect(self._show_settings_menu)
        self._toolbar.mode_changed.connect(self._on_mode_changed)

        self._sidebar.file_selected.connect(self._on_file_selected)
        self._preview.block_selected.connect(self._on_block_selected)
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
        self._bottom.copy_clicked.connect(self._copy_page)
        self._bottom.export_clicked.connect(self._show_export_menu)

        self._runner.task_changed.connect(self._on_task_changed)
        self._runner.page_loaded.connect(self._on_page_loaded)
        self._runner.task_started.connect(self._on_task_started)
        self._runner.error_occurred.connect(self._on_error)
        self._runner.notice.connect(self._on_notice)

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

        first_id: Optional[str] = None
        for path in valid:
            total = file_utils.probe_page_count(path)
            task = self._runner.create_task(path, self._mode, total)
            self._sidebar.add_task(task)
            self._runner.enqueue(task.id)
            if first_id is None:
                first_id = task.id

        if first_id is not None:
            self._select_task(first_id)
        self._bottom.show_toast(
            f"{len(valid)} fichier(s) ajouté(s) — traitement lancé."
        )
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

    def _exit_demo(self) -> None:
        if not self._demo_active and self._sidebar.row_count() == 0:
            return
        self._runner.clear()
        self._sidebar.clear()
        self._sidebar.set_modules(detect_modules())
        self._demo_active = False
        self._reset_view()

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

        self._bottom.set_task(task)
        self._refresh_edit_status()

    @staticmethod
    def _task_info(task: Task) -> str:
        total = task.total_pages
        if total <= 0:
            return "300 DPI"
        if task.status == TaskStatus.PENDING:
            return f"{total} pages · 300 DPI"
        if task.current_page > 0:
            return f"Page {task.current_page} / {total} · 300 DPI"
        return f"{total} pages · 300 DPI"

    # --- Signaux tâches ---------------------------------------------------
    def _on_task_changed(self, task: Task) -> None:
        self._sidebar.update_task(task)
        if task.id != self._selected_id:
            return

        self._bottom.set_task(task)
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
            self._task_info(task) if task else "300 DPI",
        )
        self._preview.show_page(page)
        self._results.set_page(page)
        self._bottom.set_content_available(True)
        if task is not None:
            self._bottom.set_task(task)
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
                self._bottom.set_task(task)

    def _on_notice(self, message: str) -> None:
        self._bottom.show_toast(message)

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

    def _copy_page(self) -> None:
        page = self._selected_page()
        if page is None:
            self._bottom.show_toast("Aucune page à copier.", error=True)
            return
        clipboard = QGuiApplication.clipboard()
        if QGuiApplication.platformName() == "offscreen":
            # Le presse-papiers offscreen de Qt plante à la destruction si un
            # QMimeData lui est confié ; on reste en texte brut en mode headless.
            clipboard.setText(page_to_text(page))
            copied_as = "texte"
        else:
            mime = QMimeData()
            mime.setHtml(page_to_html_document(page))
            mime.setText(page_to_text(page))
            clipboard.setMimeData(mime)
            copied_as = "texte + HTML"
        self._bottom.show_toast(
            f"Page {page.number} copiée ({copied_as})."
        )

    def _build_export_menu(self) -> QMenu:
        menu = QMenu(self)
        for key, label, _suffix, _method in EXPORT_FORMATS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, fmt=key: self._export_task(fmt)
            )
        return menu

    def _show_export_menu(self) -> None:
        task = (
            self._runner.task(self._selected_id) if self._selected_id else None
        )
        if task is None or self._runner.latest_page(task.id) is None:
            self._bottom.show_toast("Aucune page à exporter.", error=True)
            return

        menu = self._build_export_menu()
        button = self._bottom.export_button
        anchor = button.mapToGlobal(QPoint(0, -menu.sizeHint().height()))
        menu.exec(anchor)
        # Un menu parenté à la fenêtre s'accumule à chaque ouverture : libéré
        # après la fermeture (exec est bloquant, deleteLater est donc sûr ici).
        menu.deleteLater()

    def _export_task(self, fmt: str) -> None:
        if self._selected_id is None:
            return
        task = self._runner.task(self._selected_id)
        document = self._runner.document_for(self._selected_id)
        if task is None or document is None or not document.pages:
            self._bottom.show_toast("Aucune page à exporter.", error=True)
            return

        spec = next((item for item in EXPORT_FORMATS if item[0] == fmt), None)
        if spec is None:
            return
        _key, label, suffix, method_name = spec

        source = Path(task.source_path) if task.source_path else Path("document")
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
        default_path = directory / f"{source.stem or 'resultat_ocr'}{suffix}"

        selected, _ = QFileDialog.getSaveFileName(
            self,
            f"Exporter — {label}",
            str(default_path),
            label,
        )
        if not selected:
            return
        output = Path(selected)
        if output.suffix.lower() != suffix:
            output = output.with_suffix(suffix)

        try:
            exporter = Exporter(document)
            getattr(exporter, method_name)(str(output))
        except Exception as exc:  # dépendances optionnelles ou disque
            QMessageBox.warning(
                self,
                "Export impossible",
                f"Le format {label} n'a pas pu être généré.\n\n{exc}",
            )
            return

        self._bottom.show_toast(f"Exporté : {output.name}", timeout=5000)

    # --- Réglages ---------------------------------------------------------
    def _build_settings_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Dossier de travail", self._open_work_dir)
        menu.addAction("Modules OCR…", self._show_modules)
        if self._sidebar.row_count() == 0 and not self._demo_active:
            menu.addAction(
                "Charger l'aperçu de démonstration", self.load_demo
            )
        menu.addSeparator()
        menu.addAction("À propos d'AOCRI", self._show_about)
        return menu

    def _show_settings_menu(self) -> None:
        from PySide6.QtGui import QCursor

        menu = self._build_settings_menu()
        menu.exec(QCursor.pos())
        menu.deleteLater()  # même raison que le menu d'export

    def _open_work_dir(self) -> None:
        try:
            path = file_utils.work_root()
        except OSError as exc:
            self._bottom.show_toast(
                f"Dossier de travail inaccessible : {exc}", error=True
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_modules(self) -> None:
        lines = []
        for module in detect_modules():
            state = "installé" if module.installed else "non installé"
            lines.append(f"- {module.label} : {state}")
        QMessageBox.information(
            self,
            "Modules OCR",
            "Modules détectés sur cette machine :\n\n"
            + "\n".join(lines)
            + "\n\nPhase 5 : l'édition avant copie (R9) est branchée — "
            "double-clic sur un bloc de texte dans Rendu ou Texte brut.",
        )

    def _show_about(self) -> None:
        from ocr_app import __version__

        QMessageBox.about(
            self,
            "À propos d'AOCRI",
            "<b>AOCRI</b> — Advanced Optical Character Recognition for "
            "Itamba<br><br>"
            "Kriesty Solutions · application hors ligne<br>"
            f"Version {__version__}",
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
        """Titre (« ● modifié ») + statistiques de la barre inférieure."""
        dirty = self._edit_session.is_dirty()
        title = f"● {WINDOW_TITLE}" if dirty else WINDOW_TITLE
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
