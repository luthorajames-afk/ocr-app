"""T4.1/T4.2/T5 — Fenêtre principale AOCRI v2 (maquette T4.1 validée).

L'IHM ne fait aucun OCR : elle pilote `TaskRunner`/`WorkerClient` et affiche
la progression, l'aperçu (image de page + surcouches) et les résultats.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.gui import theme
from core.gui.task_runner import Task, TaskRunner, TaskStatus
from core.metadata import APP_VERSION

MODES = (
    ("simple", "Simple"),
    ("medium", "Moyen"),
    ("advanced", "Avancé"),
    ("approfondi", "Approfondi"),
)
SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png"}


STATUS_STYLE = {
    "running": (theme.ACCENT_SOFT, theme.ACCENT, "En cours"),
    "paused": (theme.AMBER_SOFT, theme.AMBER, "Pause"),
    "done": (theme.GREEN_SOFT, theme.GREEN, "Terminé"),
    "pending": (theme.AMBER_SOFT, theme.AMBER, "En attente"),
    "cancelled": (theme.PANEL_2, theme.MUTED, "Annulé"),
    "error": (theme.RED_SOFT, theme.RED, "Erreur"),
}


class TaskItemWidget(QWidget):
    """Carte de tâche : nom, badge de statut, barre de progression, étape."""

    def __init__(self, task: Task, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self.name = QLabel(parent=self)
        self.name.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        self.badge = QLabel(parent=self)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.stage = QLabel(parent=self)
        self.percent = QLabel(parent=self)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.name, 1)
        header.addWidget(self.badge)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.stage, 1)
        footer.addWidget(self.percent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)
        layout.addLayout(header)
        layout.addWidget(self.progress)
        layout.addLayout(footer)
        self._apply(task)

    def update_task(self, task: Task) -> None:
        self._apply(task)

    def _apply(self, task: Task) -> None:
        self.name.setText(Path(task.source_path).name)
        background, foreground, label = STATUS_STYLE.get(
            task.status.value, (theme.PANEL_2, theme.MUTED, task.status.value)
        )
        self.badge.setText(label)
        self.badge.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 9px;"
            "padding: 1px 8px; font-size: 10.5px; font-weight: 500;"
        )
        total = task.total_pages or 0
        if total:
            percent = int(task.pages_done / total * 100)
        else:
            percent = 100 if task.status.value == "done" else 0
        self.progress.setValue(percent)
        chunk = theme.GREEN if task.status.value == "done" else theme.ACCENT
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.PANEL_2}; border: 0; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}"
        )
        muted = f"color: {theme.MUTED}; font-size: 10.5px;"
        self.stage.setStyleSheet(muted)
        self.percent.setStyleSheet(muted)
        self.stage.setText(
            f"page {task.current_page} / {total or '?'} · {task.stage or task.status.value}"
        )
        self.percent.setText(
            f"{task.pages_done}/{total}" if total else ("100 %" if percent == 100 else "—")
        )


class TaskSidebar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.WINDOW};")
        self.list = QListWidget(self)
        self.list.setObjectName("TaskList")
        self.list.setSpacing(2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel("TÂCHES")
        header.setObjectName("Muted")
        header.setStyleSheet(
            f"padding: 11px 13px 8px; color: {theme.MUTED}; font-size: 11px;"
            "font-weight: 600; letter-spacing: 1px;"
        )
        layout.addWidget(header)
        layout.addWidget(self.list)
        self._widgets: dict[str, TaskItemWidget] = {}

    def upsert(self, task: Task) -> None:
        widget = self._widgets.get(task.id)
        if widget is not None:
            widget.update_task(task)
            return
        item = QListWidgetItem(self.list)
        item.setData(Qt.ItemDataRole.UserRole, task.id)
        widget = TaskItemWidget(task, self.list)
        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._widgets[task.id] = widget


class PreviewPanel(QWidget):
    """Aperçu : page entière ajustée à la vue + navigation entre pages."""

    page_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setObjectName("Preview")
        self.title = QLabel("Aucun document")
        self.title.setObjectName("Title")
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.prev_btn = QPushButton("‹", self)
        self.next_btn = QPushButton("›", self)
        self.compare_btn = QPushButton("Comparer", self)
        self.prev_btn.setFixedWidth(30)
        self.next_btn.setFixedWidth(30)
        self.page_label = QLabel("", self)
        self.page_label.setObjectName("Muted")
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.info)
        head.addStretch(1)
        head.addWidget(self.prev_btn)
        head.addWidget(self.page_label)
        head.addWidget(self.next_btn)
        head.addWidget(self.compare_btn)
        self.compare = QTextBrowser(self)
        self.compare.setOpenExternalLinks(False)
        body = QSplitter(Qt.Orientation.Horizontal, self)
        body.addWidget(self.view)
        body.addWidget(self.compare)
        body.setSizes([600, 400])
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(body, 1)
        self.compare_btn.setCheckable(True)
        self.compare_btn.setChecked(True)
        self.compare_btn.toggled.connect(self.compare.setVisible)
        self._page = 1
        self._total = 1
        self.prev_btn.clicked.connect(lambda: self.page_requested.emit(self._page - 1))
        self.next_btn.clicked.connect(lambda: self.page_requested.emit(self._page + 1))
        self.show_placeholder()

    def set_result_html(self, html: str) -> None:
        self.compare.setHtml(html)

    def show_placeholder(self) -> None:
        self.scene.clear()
        self.scene.addRect(0, 0, 430, 608, brush=Qt.GlobalColor.white)
        self.compare.setHtml("<p style='color:#68707d'>Résultat en attente…</p>")
        self.info.setText("aperçu en attente")

    def show_page(self, task: Task, page_number: int) -> None:
        self.scene.clear()
        self._page = page_number
        self._total = max(1, task.total_pages or 1)
        self.page_label.setText(f"page {self._page} / {self._total}")
        self.prev_btn.setEnabled(self._page > 1)
        self.next_btn.setEnabled(self._page < self._total)
        png = Path(task.work_dir) / f"page_{page_number:03d}.png"
        if png.is_file():
            pixmap = QPixmap(str(png))
            if not pixmap.isNull():
                self.scene.addPixmap(pixmap)
                self.scene.setSceneRect(self.scene.itemsBoundingRect())
                self.view.fitInView(
                    self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
                )
        else:
            data = Path(task.work_dir) / f"page_{page_number:03d}.json"
            text = ""
            if data.is_file():
                try:
                    text = json.loads(data.read_text(encoding="utf-8")).get("text", "")
                except (OSError, ValueError):
                    text = ""
            self.scene.addRect(0, 0, 430, 608, brush=Qt.GlobalColor.white)
            self.scene.addText((text or "[page en cours]")[:1500])
        self.title.setText(Path(task.source_path).name)
        self.info.setText(f"{task.total_pages or '?'} page(s)")


class ResultsPanel(QTabWidget):
    text_edited = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rendered = QTextBrowser(self)
        self.markup = QPlainTextEdit(self)
        self.markup.setReadOnly(True)
        self.plain = QPlainTextEdit(self)
        self.plain.setPlaceholderText(
            "Texte OCR — éditable avant copie (Ctrl+Z / Ctrl+Y), enregistré par page."
        )
        self._loading = False
        self.plain.textChanged.connect(self._on_plain_changed)
        self.addTab(self.rendered, "Rendu")
        self.addTab(self.markup, "Markdown / HTML")
        self.addTab(self.plain, "Texte brut")

    def show_record(self, record: dict[str, object]) -> None:
        self._loading = True
        text = str(record.get("text", ""))
        self.plain.setPlainText(text)
        self.markup.setPlainText(text)
        html_parts: list[str] = []
        blocks = record.get("blocks")
        if isinstance(blocks, list) and blocks:
            html_parts = self._render_blocks(blocks)
        else:
            html_parts = [
                f"<p>{escape(line)}</p>" for line in text.splitlines() if line.strip()
            ]
        self.rendered.setHtml("".join(html_parts) or "<p>[page vide]</p>")
        self._loading = False

    @staticmethod
    def _block_html(block: dict[str, object]) -> str:
        content = escape(str(block.get("text", "")))
        kind = str(block.get("kind", "line"))
        if kind == "table":
            grid = block.get("grid") or []
            rows_html = []
            for row_index, row in enumerate(grid if isinstance(grid, list) else []):
                tag = "th" if row_index == 0 else "td"
                cells = "".join(
                    f"<{tag} style='border:1px solid #c3c8d2;padding:3px 6px'>"
                    f"{escape(str(cell))}</{tag}>"
                    for cell in row
                )
                rows_html.append(f"<tr>{cells}</tr>")
            return (
                "<table style='border-collapse:collapse;margin:6px 0'>"
                + "".join(rows_html)
                + "</table>"
            )
        if kind == "title":
            return f"<h2 style='margin:6px 0'>{content}</h2>"
        return f"<p style='margin:2px 0'>{content}</p>"

    def _render_blocks(self, blocks: list[object]) -> list[str]:
        """Rendu : colonnes côte à côte quand la page en contient plusieurs."""
        groups: list[list[dict[str, object]]] = []
        buckets: list[int] = []
        rights_by_group: list[list[float]] = []
        group_lefts: list[float] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            bbox = block.get("bbox") or [0, 0, 0, 0]
            try:
                x0 = float(bbox[0])
                x1 = float(bbox[2])
            except (TypeError, ValueError, IndexError):
                x0, x1 = 0.0, 0.0
            bucket = round(x0 / 40)
            if groups and buckets[-1] == bucket:
                groups[-1].append(block)
                rights_by_group[-1].append(x1)
            else:
                groups.append([block])
                buckets.append(bucket)
                rights_by_group.append([x1])
                group_lefts.append(x0)

        lefts = [
            float((block.get("bbox") or [0])[0])
            for block in blocks
            if isinstance(block, dict)
        ]
        rights = [
            float((block.get("bbox") or [0, 0, 0])[2])
            for block in blocks
            if isinstance(block, dict)
        ]
        page_left = min(lefts, default=0.0)
        page_right = max(rights, default=0.0)
        page_width = max(page_right - page_left, 1.0)
        def median(values: list[float]) -> float:
            ordered = sorted(values)
            return ordered[len(ordered) // 2] if ordered else 0.0

        narrow = all(
            (median(rights) - left) < page_width * 0.8
            for rights, left in zip(rights_by_group, group_lefts)
        )
        use_columns = 2 <= len(groups) <= 4 and narrow

        parts: list[str] = []
        if use_columns:
            # QTextBrowser ne gère pas flexbox : colonnes en tableau HTML.
            parts.append(
                "<table width='100%' cellspacing='0' cellpadding='8' "
                "style='border-collapse:collapse'><tr>"
            )
            for group in groups:
                parts.append("<td valign='top' style='vertical-align:top'>")
                for block in group:
                    parts.append(self._block_html(block))
                parts.append("</td>")
            parts.append("</tr></table>")
        else:
            for group in groups:
                for block in group:
                    parts.append(self._block_html(block))
        return parts
        self._loading = False

    def _on_plain_changed(self) -> None:
        if not self._loading:
            self.text_edited.emit(self.plain.toPlainText())


class BottomBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.label = QLabel("En attente")
        self.label.setObjectName("Muted")
        self.pause_btn = QPushButton("Pause", self)
        self.cancel_btn = QPushButton("Annuler", self)
        self.copy_btn = QPushButton("Copier", self)
        self.export_btn = QPushButton("Exporter", self)
        self.export_btn.setObjectName("Primary")
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.label, 1)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.export_btn)
        layout.addWidget(self.progress)
        layout.addLayout(row)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AOCRI — Advanced Optical Character Recognition for Itamba — v2.0")
        self.resize(1280, 800)
        self._mode = "simple"
        self._runner = TaskRunner(self)
        self._current_task: Task | None = None
        self._last_page = 0
        self._active_page = 0
        self._record_text: dict[int, str] = {}
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(400)
        self._edit_timer.timeout.connect(self._commit_edit)
        self._pending_edit: str | None = None

        self._modes: dict[str, QPushButton] = {}
        self._toolbar = self._build_toolbar()

        self.sidebar = TaskSidebar(self)
        self.preview = PreviewPanel(self)
        self.results = ResultsPanel(self)
        center = QWidget(self)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addWidget(self.preview, 3)
        center_layout.addWidget(self.results, 2)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(center)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 980])
        self.setCentralWidget(splitter)

        self.bottom = BottomBar(self)
        status = QWidget(self)
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(8, 4, 8, 4)
        status_layout.addWidget(self.bottom)
        self.statusBar().addPermanentWidget(status, 1)

        self.bottom.pause_btn.clicked.connect(self._toggle_pause)
        self.bottom.cancel_btn.clicked.connect(self._runner.cancel)
        self.bottom.copy_btn.clicked.connect(self._copy)
        self.bottom.export_btn.clicked.connect(self._export)
        self.sidebar.list.itemClicked.connect(self._on_task_clicked)

        self._runner.task_changed.connect(self._on_task_changed)
        self._runner.page_ready.connect(self._on_page_ready)
        self.results.text_edited.connect(self._on_text_edited)
        self._runner.all_done.connect(self._on_all_done)
        QTimer.singleShot(0, self._update_buttons)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> QWidget:
        bar = QWidget(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        group = QFrame(bar)
        group.setObjectName("ModeGroup")
        group_layout = QHBoxLayout(group)
        group_layout.setContentsMargins(3, 3, 3, 3)
        for key, label in MODES:
            button = QPushButton(label, group)
            button.setObjectName("Mode")
            button.setCheckable(True)
            button.setChecked(key == self._mode)
            button.clicked.connect(lambda _checked, k=key: self._set_mode(k))
            group_layout.addWidget(button)
            self._modes[key] = button
        self._update_mode_availability()
        add = QPushButton("Fichiers", bar)
        self.theme_btn = QPushButton("Sombre", bar)
        self.theme_btn.setCheckable(True)
        self.theme_btn.clicked.connect(self._toggle_theme)
        calibrate = QPushButton("Étalonnage…", bar)
        calibrate.clicked.connect(self._open_calibration)
        add.clicked.connect(self._pick_files)
        scan = QPushButton("Scanner…", bar)
        scan.setEnabled(False)
        scan.setToolTip("Aucun scanner détecté (WIA/TWAIN/eSCL)")
        info = QLabel(f"Tesseract · DPI 300 · v{APP_VERSION}", bar)
        info.setObjectName("Muted")
        layout.addWidget(group)
        layout.addWidget(add)
        layout.addWidget(self.theme_btn)
        self.calibrate_btn = calibrate
        layout.addWidget(calibrate)
        layout.addWidget(scan)
        layout.addStretch(1)
        layout.addWidget(info)
        self.setMenuWidget(bar)
        return bar

    def _update_mode_availability(self) -> None:
        """Modes optionnels grisés si leur environnement n'est pas installé."""
        from core.gui.worker_client import advanced_available, medium_available

        medium = self._modes.get("medium")
        if medium is not None:
            available = medium_available()
            medium.setEnabled(available)
            medium.setToolTip(
                "RapidOCR dans son environnement dédié"
                if available
                else "Module Moyen non installé (venv .venv-eval-medium absent)"
            )
        advanced_ok = advanced_available()
        for key, label in (("advanced", "Docling"), ("approfondi", "Docling + filtres")):
            button = self._modes.get(key)
            if button is not None:
                button.setEnabled(advanced_ok)
                button.setToolTip(
                    f"Module Avancé ({label}) dans son environnement dédié"
                    if advanced_ok
                    else "Module Avancé non installé (venv .venv-eval-advanced absent)"
                )

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        for key, button in self._modes.items():
            button.setChecked(key == mode)

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des documents", "", "Documents (*.pdf *.jpg *.jpeg *.png)"
        )
        for path in paths:
            self.add_file(path)

    def add_file(self, path: str) -> bool:
        if Path(path).suffix.lower() not in SUPPORTED:
            return False
        task = self._runner.add(path, self._mode)
        self.sidebar.upsert(task)
        self._current_task = task
        self._runner.start(task.id)
        return True

    # ------------------------------------------------------------- signaux
    def _on_task_changed(self, task: Task) -> None:
        self.sidebar.upsert(task)
        if self._current_task is None or task.id == self._current_task.id:
            self._current_task = task
            total = task.total_pages or 0
            if total:
                percent = int(task.pages_done / total * 100)
            else:
                percent = 0
            self.bottom.progress.setValue(percent)
            self.bottom.label.setText(
                f"Page {task.current_page}/{total or '?'} — {task.stage or task.status.value}"
                if task.status == TaskStatus.RUNNING
                else task.status.value
            )
            self._last_page = task.pages_done or task.current_page
            if task.status == TaskStatus.RUNNING and task.stage:
                self.preview.info.setText(f"page {task.current_page} / {total or '?'}")
        self._update_buttons()

    def _on_page_ready(self, task_id: str, page: int) -> None:
        task = self._runner.task(task_id)
        if task is None:
            return
        self.preview.show_page(task, page)
        self._active_page = page
        data = Path(task.work_dir) / f"page_{page:03d}.json"
        if data.is_file():
            try:
                record = json.loads(data.read_text(encoding="utf-8"))
                self._record_text[page] = str(record.get("text", ""))
                self.results.show_record(record)
                self.preview.set_result_html(self.results.rendered.toHtml())
            except (OSError, ValueError):
                pass

    def _on_text_edited(self, text: str) -> None:
        """R9 — édition avant copie : sauvegarde débouncée de la page active."""
        if not self._active_page or self._current_task is None:
            return
        if text == self._record_text.get(self._active_page, ""):
            return
        self._pending_edit = text
        self._edit_timer.start()

    def _commit_edit(self) -> None:
        if self._pending_edit is None or self._current_task is None or not self._active_page:
            return
        text = self._pending_edit
        page = self._active_page
        if text == self._record_text.get(page, ""):
            return
        from dataclasses import replace as dc_replace

        from core.pipeline.job_store import load_page, save_page

        record = load_page(self._current_task.work_dir, page)
        if record is not None:
            save_page(dc_replace(record, text=text), self._current_task.work_dir)
        self._record_text[page] = text
        self._pending_edit = None
        title = self.windowTitle().rstrip(" ●")
        self.setWindowTitle(title + " ●")

    def _on_all_done(self) -> None:
        # Enchaînement des tâches en attente (ajout multi-fichiers) : différé
        # pour laisser le processus précédent se terminer proprement.
        QTimer.singleShot(150, self._start_next_pending)
        self._update_buttons()

    def _start_next_pending(self) -> None:
        for task in self._runner.tasks():
            if task.status == TaskStatus.PENDING:
                self._runner.start(task.id)
                break

    def _on_task_clicked(self, item: QListWidgetItem) -> None:
        task = self._runner.task(str(item.data(Qt.ItemDataRole.UserRole)))
        if task is not None:
            self._current_task = task
            if task.pages_done:
                self._on_page_ready(task.id, task.pages_done)

    # ------------------------------------------------------------ actions
    def _toggle_pause(self) -> None:
        task = self._current_task
        if task is None:
            return
        if task.status == TaskStatus.PAUSED:
            self._runner.resume()
        elif task.status == TaskStatus.RUNNING:
            self._runner.pause()

    def _update_buttons(self) -> None:
        task = self._current_task
        running = task is not None and task.status == TaskStatus.RUNNING
        paused = task is not None and task.status == TaskStatus.PAUSED
        self.bottom.pause_btn.setText("Reprendre" if paused else "Pause")
        self.bottom.pause_btn.setEnabled(running or paused)
        self.bottom.cancel_btn.setEnabled(running or paused)
        self.bottom.copy_btn.setEnabled(task is not None and task.pages_done > 0)
        self.bottom.export_btn.setEnabled(task is not None and task.pages_done > 0)

    def _copy(self) -> None:
        """T6.1 — copie riche (HTML tableaux + texte + markdown)."""
        task = self._current_task
        if task is None:
            return
        from core.gui.clipboard_qt import put_on_clipboard
        from core.io.clipboard import document_payload

        records: list[dict[str, object]] = []
        for path in sorted(Path(task.work_dir).glob("page_*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if records:
            put_on_clipboard(document_payload(records))

    def _toggle_theme(self) -> None:
        from PySide6.QtWidgets import QApplication

        dark = self.theme_btn.isChecked()
        self.theme_btn.setText("Clair" if dark else "Sombre")
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply_theme(app, dark=dark)
        # Rafraîchir les cartes (styles inline recalculés avec la palette).
        for task in self._runner.tasks():
            self.sidebar.upsert(task)
        self.update()

    def _open_calibration(self) -> None:
        from core.gui.calibration_dialog import CalibrationDialog

        task = self._current_task
        if task is None or task.pages_done == 0:
            self.bottom.label.setText("Étalonnage : traitez d'abord un document")
            return
        CalibrationDialog(task.work_dir, self).exec()

    def _export(self) -> None:
        task = self._current_task
        if task is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Exporter le texte", f"{Path(task.source_path).stem}.txt", "Texte (*.txt)"
        )
        if not target:
            return
        parts: list[str] = []
        for page in sorted(Path(task.work_dir).glob("page_*.json")):
            try:
                parts.append(str(json.loads(page.read_text(encoding="utf-8")).get("text", "")))
            except (OSError, ValueError):
                continue
        Path(target).write_text("\n".join(parts), encoding="utf-8")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._runner.shutdown()
        super().closeEvent(event)
