"""T4.1/T4.2/T5 — Fenêtre principale AOCRI v2 (maquette T4.1 validée).

L'IHM ne fait aucun OCR : elle pilote `TaskRunner`/`WorkerClient` et affiche
la progression, l'aperçu (image de page + surcouches) et les résultats.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsRectItem,
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
from core.gui.comparison_window import ComparisonWindow
from core.gui.correction_dialog import CorrectionDialog
from core.gui.outline_panel import OutlinePanel
from core.gui.search_service import SearchService
from core.gui.search_window import SearchWindow, _Job
from core.gui.task_runner import Task, TaskRunner, TaskStatus
from core.gui.worker_client import WorkerClient
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
        if task.status == TaskStatus.ERROR and task.error:
            self.stage.setText(f"Erreur : {task.error[:90]}")
            self.setToolTip(task.error)
        else:
            self.stage.setText(
                f"page {task.current_page} / {total or '?'} · {task.stage or task.status.value}"
            )
            self.setToolTip("")
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


class PreviewView(QGraphicsView):
    """Vue de l'aperçu : double-clic et zoom à la molette sous le curseur."""

    double_clicked = Signal(QPointF)
    zoomed = Signal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.double_clicked.emit(self.mapToScene(event.position().toPoint()))
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)
        self.setTransformationAnchor(anchor)
        self.zoomed.emit()
        event.accept()


class PreviewPanel(QWidget):
    """Aperçu : navigation, zoom, comparaison et correction sur la page."""

    page_requested = Signal(int)
    page_corrected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view = PreviewView(self.scene, self)
        self.view.setObjectName("Preview")
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.double_clicked.connect(self._on_preview_double_click)
        self.view.zoomed.connect(self._on_wheel_zoom)
        self.title = QLabel("Aucun document")
        self.title.setObjectName("Title")
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.prev_btn = QPushButton("‹", self)
        self.next_btn = QPushButton("›", self)
        self.zoom_out_btn = QPushButton("−", self)
        self.zoom_in_btn = QPushButton("+", self)
        self.fit_btn = QPushButton("Ajuster", self)
        self.compare_btn = QPushButton("Comparer", self)
        self.detach_btn = QPushButton("Détacher", self)
        self.edit_btn = QPushButton("✎ Corriger", self)
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip(
            "Corriger l'OCR sur la page : double-clic sur un bloc, Ctrl+Entrée pour enregistrer"
        )
        self.edit_btn.toggled.connect(self._set_edit_mode)
        for button in (self.prev_btn, self.next_btn, self.zoom_out_btn, self.zoom_in_btn):
            button.setFixedWidth(30)
        self.page_label = QLabel("", self)
        self.page_label.setObjectName("Muted")
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.info)
        head.addStretch(1)
        head.addWidget(self.prev_btn)
        head.addWidget(self.page_label)
        head.addWidget(self.next_btn)
        head.addSpacing(10)
        head.addWidget(self.zoom_out_btn)
        head.addWidget(self.zoom_in_btn)
        head.addWidget(self.fit_btn)
        head.addSpacing(10)
        head.addWidget(self.edit_btn)
        head.addWidget(self.compare_btn)
        head.addWidget(self.detach_btn)
        self.compare = QTextBrowser(self)
        self.compare.setOpenExternalLinks(False)
        self.body = QSplitter(Qt.Orientation.Horizontal, self)
        self.body.addWidget(self.view)
        self.body.addWidget(self.compare)
        self.body.setSizes([600, 400])
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self.body, 1)
        self.compare_btn.setCheckable(True)
        self.compare_btn.setChecked(True)
        self.compare_btn.toggled.connect(self.compare.setVisible)
        self._page = 1
        self._total = 1
        self._dpi = 300
        self._user_zoomed = False
        self._task: Task | None = None
        self._flash_item: QGraphicsRectItem | None = None
        self._blocks: list[dict[str, object]] = []
        self._page_path: Path | None = None
        self._overlay_items: list[QGraphicsRectItem] = []
        self._inline_widget: QWidget | None = None
        self._inline_editor: QPlainTextEdit | None = None
        self._inline_index = -1
        self._inline_handle: QLabel | None = None
        self._inline_drag_offset: QPoint | None = None
        self._html = "<p style='color:#68707d'>Résultat en attente…</p>"
        self._detached: ComparisonWindow | None = None
        self._pinning = False
        self.prev_btn.clicked.connect(lambda: self.page_requested.emit(self._page - 1))
        self.next_btn.clicked.connect(lambda: self.page_requested.emit(self._page + 1))
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.fit_btn.clicked.connect(self.fit_page)
        self.detach_btn.clicked.connect(self.detach_comparison)
        self.show_placeholder()

    # ------------------------------------------------------------- contenu
    def set_result_html(self, html: str) -> None:
        self._html = html
        self.compare.setHtml(html)
        if self._detached is not None:
            self._detached.set_html(html)

    def show_placeholder(self) -> None:
        self.scene.clear()
        self._flash_item = None
        self.scene.addRect(0, 0, 430, 608, brush=Qt.GlobalColor.white)
        self.compare.setHtml("<p style='color:#68707d'>Résultat en attente…</p>")
        self.info.setText("aperçu en attente")

    def flash_block(
        self, bbox: tuple[float, float, float, float], dpi: int = 300
    ) -> None:
        """Surligne un bloc (points, haut-gauche) sur la page affichée ~2,5 s."""
        scale = dpi / 72.0
        x0, y0, x1, y1 = bbox
        pen = QPen(QColor(125, 152, 255), max(2.0, scale * 0.9))
        brush = QColor(125, 152, 255, 60)
        item = QGraphicsRectItem(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)
        item.setPen(pen)
        item.setBrush(brush)
        self.scene.addItem(item)
        self._flash_item = item
        QTimer.singleShot(2500, self._clear_flash)

    def _clear_flash(self) -> None:
        item = getattr(self, "_flash_item", None)
        if item is not None:
            self.scene.removeItem(item)
        self._flash_item = None

    def show_page(self, task: Task, page_number: int) -> None:
        self._close_inline_editor()
        previous_page = self._page
        scroll_h = self.view.horizontalScrollBar().value()
        scroll_v = self.view.verticalScrollBar().value()
        self.scene.clear()
        self._flash_item = None
        self._overlay_items = []
        self._page = page_number
        self._total = max(1, task.total_pages or 1)
        self._dpi = max(72, int(task.dpi or 300))
        self.page_label.setText(f"page {self._page} / {self._total}")
        self.prev_btn.setEnabled(self._page > 1)
        self.next_btn.setEnabled(self._page < self._total)
        png = Path(task.work_dir) / f"page_{page_number:03d}.png"
        if png.is_file():
            pixmap = QPixmap(str(png))
            if not pixmap.isNull():
                self.scene.addPixmap(pixmap)
                self.scene.setSceneRect(self.scene.itemsBoundingRect())
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
        # Zoom persistant : on ne réajuste que si l'utilisateur n'a pas zoomé.
        if self._user_zoomed:
            self.view.horizontalScrollBar().setValue(
                scroll_h if previous_page == page_number else 0
            )
            self.view.verticalScrollBar().setValue(
                scroll_v if previous_page == page_number else 0
            )
        else:
            self.fit_page()
        self._task = task
        self.title.setText(Path(task.source_path).name)
        self.info.setText(f"{task.total_pages or '?'} page(s)")
        self._load_page_blocks(task, page_number)
        self._draw_block_overlays()
        if self._detached is not None:
            self._detached.set_page(Path(task.work_dir), page_number)

    # -------------------------------------------------- correction sur la page
    def _load_page_blocks(self, task: Task, page_number: int) -> None:
        """Charge les blocs de la page pour la correction directe."""
        self._blocks = []
        self._page_path = Path(task.work_dir) / f"page_{page_number:03d}.json"
        if not self._page_path.is_file():
            return
        try:
            record = json.loads(self._page_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        blocks = record.get("blocks")
        if isinstance(blocks, list):
            self._blocks = [block for block in blocks if isinstance(block, dict)]

    def _draw_block_overlays(self) -> None:
        """Surligne les blocs corrigeables quand le mode correction est actif."""
        self._clear_overlays()
        if not self.edit_btn.isChecked() or not self._blocks:
            return
        scale = self._dpi / 72.0
        pen = QPen(QColor(125, 152, 255, 170), 1.4, Qt.PenStyle.DashLine)
        for block in self._blocks:
            if str(block.get("kind", "line")) == "table":
                continue
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            bbox = block.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(value) for value in bbox)
            except (TypeError, ValueError):
                continue
            item = QGraphicsRectItem(
                x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale
            )
            item.setPen(pen)
            item.setBrush(QColor(125, 152, 255, 22))
            item.setToolTip("Double-clic pour corriger ce bloc")
            self.scene.addItem(item)
            self._overlay_items.append(item)

    def _clear_overlays(self) -> None:
        for item in self._overlay_items:
            self.scene.removeItem(item)
        self._overlay_items = []

    def _set_edit_mode(self, enabled: bool) -> None:
        """Active/désactive les surcouches de correction de l'aperçu."""
        self._close_inline_editor()
        self.view.setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        self._draw_block_overlays()
        if enabled:
            self.info.setText("double-clic sur un bloc pour corriger")
        else:
            task = self._task
            self.info.setText(f"{task.total_pages or '?'} page(s)" if task else "")

    def _on_preview_double_click(self, position: QPointF) -> None:
        if not self.edit_btn.isChecked() or not self._blocks:
            return
        scale = self._dpi / 72.0
        point = (position.x() / scale, position.y() / scale)
        index = self._block_at(point)
        if index >= 0:
            self._open_inline_editor(index)

    def _block_at(self, point: tuple[float, float]) -> int:
        """Index du dernier bloc contenant le point (points page)."""
        x, y = point
        for index in range(len(self._blocks) - 1, -1, -1):
            block = self._blocks[index]
            if str(block.get("kind", "line")) == "table":
                continue
            bbox = block.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(value) for value in bbox)
            except (TypeError, ValueError):
                continue
            if x0 <= x <= x1 and y0 <= y <= y1:
                return index
        return -1

    def _open_inline_editor(self, index: int) -> None:
        """Éditeur intégré à la page, positionné sur le bloc double-cliqué."""
        self._close_inline_editor()
        block = self._blocks[index]
        bbox = block.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return
        scale = self._dpi / 72.0
        text = str(block.get("text", ""))
        container = QWidget()
        container.setObjectName("InlineCorrector")
        muted = theme.PANEL_2
        container.setStyleSheet(
            f"#InlineCorrector {{ background: {theme.PANEL};"
            f" border: 1.5px solid {theme.ACCENT}; border-radius: 6px; }}"
            f" QPlainTextEdit {{ background: {theme.PANEL_2}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 4px; }}"
            f" QLabel {{ color: {muted}; font-size: 10px; }}"
        )
        handle = QLabel("✎ Correction — glisser pour déplacer", container)
        handle.setObjectName("InlineHandle")
        handle.setCursor(Qt.CursorShape.OpenHandCursor)
        handle.setStyleSheet(
            f"#InlineHandle {{ color: {theme.MUTED}; font-size: 10.5px;"
            f" padding: 2px 4px; background: transparent; }}"
        )
        handle.installEventFilter(self)
        editor = QPlainTextEdit(text, container)
        editor.setTabChangesFocus(True)
        editor.installEventFilter(self)
        hint = QLabel("Ctrl+Entrée : enregistrer · Échap : annuler", container)
        save = QPushButton("Enregistrer", container)
        save.setObjectName("Primary")
        cancel = QPushButton("Annuler", container)
        save.clicked.connect(lambda: self._save_inline_editor())
        cancel.clicked.connect(self._close_inline_editor)
        buttons = QHBoxLayout()
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.addWidget(handle)
        layout.addWidget(editor, 1)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        zoom = max(0.1, self.view.transform().m11())
        width = max(280.0, min(680.0, (x1 - x0) * scale * zoom + 24.0))
        height = max(120.0, min(300.0, (y1 - y0) * scale * zoom + 96.0))
        container.resize(int(width), int(height))
        container.setParent(self.view.viewport())
        top_left = self.view.mapFromScene(QPointF(x0 * scale, y0 * scale))
        container.move(top_left)
        container.show()
        container.raise_()
        self._inline_widget = container
        self._inline_editor = editor
        self._inline_handle = handle
        self._inline_index = index
        editor.setFocus()

    def _close_inline_editor(self) -> None:
        if self._inline_widget is not None:
            self._inline_widget.hide()
            self._inline_widget.deleteLater()
            self._inline_widget = None
        self._inline_editor = None
        self._inline_handle = None
        self._inline_drag_offset = None
        self._inline_index = -1

    def _save_inline_editor(self) -> None:
        """Écrit le bloc corrigé dans page_NNN.json (ordre de lecture préservé)."""
        if self._inline_editor is None or self._page_path is None or self._inline_index < 0:
            return
        text = self._inline_editor.toPlainText().strip()
        try:
            data = json.loads(self._page_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._close_inline_editor()
            return
        blocks = data.get("blocks")
        if not isinstance(blocks, list) or self._inline_index >= len(blocks):
            self._close_inline_editor()
            return
        block = blocks[self._inline_index]
        if not isinstance(block, dict):
            self._close_inline_editor()
            return
        if text and text != str(block.get("text", "")):
            block["text"] = text
            data["text"] = chr(10).join(
                str(item.get("text", ""))
                for item in blocks
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            )
            self._atomic_write_json(self._page_path, data)
            self._blocks = [item for item in blocks if isinstance(item, dict)]
        self._close_inline_editor()
        self._draw_block_overlays()
        self.page_corrected.emit()

    @staticmethod
    def _atomic_write_json(path: Path, data: dict[str, object]) -> None:
        import os
        import tempfile

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self._inline_handle
            and self._inline_widget is not None
            and isinstance(event, QMouseEvent)
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._inline_drag_offset = (
                        event.globalPosition().toPoint() - self._inline_widget.pos()
                    )
                    self._inline_handle.setCursor(
                        Qt.CursorShape.ClosedHandCursor
                    )
                    return True
            elif (
                event.type() == QEvent.Type.MouseMove
                and self._inline_drag_offset is not None
            ):
                target = event.globalPosition().toPoint() - self._inline_drag_offset
                self._inline_widget.move(target)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._inline_drag_offset = None
                if self._inline_handle is not None:
                    self._inline_handle.setCursor(
                        Qt.CursorShape.OpenHandCursor
                    )
                return True
        if (
            watched is self._inline_editor
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self._save_inline_editor()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self._close_inline_editor()
                return True
        return super().eventFilter(watched, event)

    # --------------------------------------------------------------- zoom
    def _on_wheel_zoom(self) -> None:
        self._user_zoomed = True
        self.info.setText("zoom molette · Ajuster pour recadrer")

    def zoom_in(self) -> None:
        self.view.scale(1.2, 1.2)
        self._user_zoomed = True

    def zoom_out(self) -> None:
        self.view.scale(1 / 1.2, 1 / 1.2)
        self._user_zoomed = True

    def fit_page(self) -> None:
        if not self.scene.items():
            return
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._user_zoomed = False

    # ---------------------------------------------------- comparaison détachée
    def detach_comparison(self) -> None:
        if self._detached is None:
            window = ComparisonWindow(None)  # fenêtre autonome (multi-écrans)
            window.finished.connect(lambda _code: self._on_detached_closed())
            window.blocks_saved.connect(self.page_corrected.emit)
            window.set_html(self._html)
            if self._task is not None:
                window.set_page(Path(self._task.work_dir), self._page)
            window.show()
            self._detached = window
            # Le preview original occupe tout l'espace laissé vacant.
            self.compare.setVisible(False)
            self.compare_btn.setEnabled(False)
            self.detach_btn.setText("Épingler")
        else:
            self._pinning = True
            self._detached.close()
            self._pinning = False
            self._detached = None
            self._restore_inline_compare()

    def _on_detached_closed(self) -> None:
        """Fermeture de la fenêtre détachée (croix) : on ré-épingle."""
        if self._pinning or self._detached is None:
            return
        self._detached = None
        self._restore_inline_compare()

    def _restore_inline_compare(self) -> None:
        self.compare_btn.setEnabled(True)
        self.compare.setVisible(self.compare_btn.isChecked())
        self.detach_btn.setText("Détacher")

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
        declared = block.get("font_size")
        size = (
            float(declared)
            if isinstance(declared, (int, float, str))
            else 0.0
        )
        name = str(block.get("font_name") or "")
        css: list[str] = []
        if 0.0 < size < 40.0:
            css.append(f"font-size:{size:.1f}pt")
        if name:
            css.append(f"font-family:{escape(name, quote=True)}")
        if block.get("bold"):
            css.append("font-weight:bold")
        if block.get("italic"):
            css.append("font-style:italic")
        style = f" style='{';'.join(css)}'" if css else ""
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
                f"<table style='border-collapse:collapse;margin:6px 0'{style}>"
                + "".join(rows_html)
                + "</table>"
            )
        if kind == "title":
            return f"<h2 style='margin:6px 0'{style}>{content}</h2>"
        return f"<p style='margin:2px 0'{style}>{content}</p>"

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
        self._settings = QSettings("Kriesty Solutions", "AOCRI")
        self._mode = "simple"
        dpi_value = self._settings.value("dpi", 300)
        self._dpi = int(dpi_value) if isinstance(dpi_value, (int, float, str)) else 300
        self._languages = str(self._settings.value("languages", "fra+eng"))
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
        self._improve_client: WorkerClient | None = None
        self._search_service = SearchService()
        self._auto_indexed = False

        self._modes: dict[str, QPushButton] = {}
        self._toolbar = self._build_toolbar()

        self.sidebar = TaskSidebar(self)
        self.preview = PreviewPanel(self)
        self.results = ResultsPanel(self)
        center = QWidget(self)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_split = QSplitter(Qt.Orientation.Vertical, center)
        self.center_split.addWidget(self.preview)
        self.center_split.addWidget(self.results)
        self.center_split.setSizes([520, 260])
        self.center_split.setStretchFactor(0, 3)
        self.center_split.setStretchFactor(1, 2)
        self._results_sizes = [520, 260]
        center_layout.addWidget(self.center_split)
        self.outline = OutlinePanel(self)
        self.outline.setVisible(False)
        self.outline.jump_requested.connect(self._on_outline_jump)
        self.main_split = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_split.addWidget(self.sidebar)
        self.main_split.addWidget(self.outline)
        self.main_split.addWidget(center)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 0)
        self.main_split.setStretchFactor(2, 1)
        self.main_split.setSizes([300, 300, 680])
        self._sidebar_sizes = [300, 300, 680]
        self.setCentralWidget(self.main_split)

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

        self.preview.page_requested.connect(self._on_page_requested)
        self.preview.page_corrected.connect(self._on_page_corrected)
        self._runner.task_changed.connect(self._on_task_changed)
        self._runner.page_ready.connect(self._on_page_ready)
        self.results.text_edited.connect(self._on_text_edited)
        self._runner.all_done.connect(self._on_all_done)
        QTimer.singleShot(0, self._update_buttons)
        QTimer.singleShot(4000, self._auto_index)

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
        self.sidebar_btn = QPushButton("◀", bar)
        self.sidebar_btn.setFixedWidth(30)
        self.sidebar_btn.setToolTip("Replier / déplier le bloc des tâches")
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)
        add = QPushButton("Fichiers", bar)
        self.results_btn = QPushButton("Résultat ▾", bar)
        self.results_btn.setToolTip("Replier / déplier le bloc Rendu / HTML / Texte brut")
        self.results_btn.clicked.connect(self._toggle_results)
        settings = QPushButton("⚙", bar)
        settings.setFixedWidth(34)
        settings.setToolTip("Réglages : échantillonnage, thème, langue")
        settings.clicked.connect(self._open_settings)
        self.correct_btn = QPushButton("Corriger…", bar)
        self.correct_btn.setToolTip("Corriger les blocs OCR / relancer la page")
        self.correct_btn.clicked.connect(self._open_correction)
        calibrate = QPushButton("Étalonnage…", bar)
        calibrate.clicked.connect(self._open_calibration)
        add.clicked.connect(self._pick_files)
        scan = QPushButton("Scanner…", bar)
        scan.setEnabled(False)
        scan.setToolTip("Aucun scanner détecté (WIA/TWAIN/eSCL)")
        info = QLabel(f"Tesseract · DPI 300 · v{APP_VERSION}", bar)
        info.setObjectName("Muted")
        self.plan_btn = QPushButton("☰ Plan", bar)
        self.plan_btn.setToolTip("Plan du document : articles, titres, sections")
        self.plan_btn.setCheckable(True)
        self.plan_btn.setChecked(False)
        self.plan_btn.clicked.connect(self._toggle_plan)
        search_btn = QPushButton("Rechercher", bar)
        search_btn.setToolTip("Rechercher dans les documents traités, voir les similaires")
        search_btn.clicked.connect(self._open_search)
        layout.addWidget(self.sidebar_btn)
        layout.addWidget(group)
        layout.addWidget(add)
        layout.addWidget(self.plan_btn)
        layout.addWidget(search_btn)
        layout.addWidget(self.results_btn)
        layout.addWidget(self.correct_btn)
        self.calibrate_btn = calibrate
        layout.addWidget(calibrate)
        layout.addWidget(scan)
        layout.addStretch(1)
        layout.addWidget(info)
        layout.addWidget(settings)  # icône à l'extrême droite
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
        task = self._runner.add(
            path, self._mode, dpi=self._dpi, languages=self._languages
        )
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
            if task.status == TaskStatus.ERROR and task.error:
                self.bottom.label.setText(f"Erreur : {task.error}")
            else:
                self.bottom.label.setText(
                    f"Page {task.current_page}/{total or '?'} — {task.stage or task.status.value}"
                    if task.status == TaskStatus.RUNNING
                    else task.status.value
                )
            self._last_page = task.pages_done or task.current_page
            if task.status == TaskStatus.RUNNING and task.stage:
                self.preview.info.setText(f"page {task.current_page} / {total or '?'}")
            if task.status == TaskStatus.DONE and self.outline.isVisible():
                self._refresh_outline()
        self._update_buttons()

    def _on_page_ready(self, task_id: str, page: int) -> None:
        task = self._runner.task(task_id)
        if task is None:
            return
        self._show_page(task, page)

    def _show_page(self, task: Task, page: int) -> None:
        self.preview.show_page(task, page)
        self._active_page = page
        if self.outline.isVisible():
            self.outline.set_current_page(page)
        data = Path(task.work_dir) / f"page_{page:03d}.json"
        if data.is_file():
            try:
                record = json.loads(data.read_text(encoding="utf-8"))
                self._record_text[page] = str(record.get("text", ""))
                self.results.show_record(record)
                self.preview.set_result_html(self.results.rendered.toHtml())
            except (OSError, ValueError):
                pass

    def _on_page_requested(self, page: int) -> None:
        task = self._current_task
        if task is None:
            return
        total = max(1, task.total_pages or 1)
        page = max(1, min(page, total))
        if (Path(task.work_dir) / f"page_{page:03d}.json").is_file():
            self._show_page(task, page)
        else:
            self.bottom.label.setText(f"Page {page} non encore traitée")

    def _on_page_corrected(self) -> None:
        """Corrections enregistrées depuis la fenêtre de comparaison."""
        task = self._current_task
        if task is None or not self._active_page:
            return
        self._show_page(task, self._active_page)
        if self.outline.isVisible():
            self.outline.set_current_page(self._active_page)
        self.bottom.label.setText(
            f"Corrections enregistrées — page {self._active_page}"
        )

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
        self._auto_indexed = False
        QTimer.singleShot(2000, self._auto_index)
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
            if self.outline.isVisible():
                self._refresh_outline()

    # ------------------------------------------------------------ recherche
    def _open_search(self) -> None:
        """Ouvre la fenêtre Recherche & documents similaires."""
        window = getattr(self, "_search_window", None)
        if window is None:
            source = self._current_task.source_path if self._current_task else None
            window = SearchWindow(
                service=self._search_service, current_source=source, parent=None
            )
            window.open_requested.connect(self._open_search_hit)
            self._search_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_search_hit(self, source: str, page: int) -> None:
        """Ouvre une page indexée dans l'aperçu principal."""
        wanted = str(Path(source).resolve())
        for task in self._runner.tasks():
            if str(Path(task.source_path).resolve()) == wanted:
                self._current_task = task
                self.sidebar.select_task(task.id) if hasattr(self.sidebar, "select_task") else None
                self._show_page(task, page)
                return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "Document non chargé",
            "Ce document n'est pas dans la liste des tâches. L'ajouter "
            "(il sera retraité) ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.add_file(source)
            self.bottom.label.setText(
                f"Document ajouté ; la page {page} sera disponible après traitement"
            )

    def _auto_index(self) -> None:
        """Indexe en arrière-plan les nouveaux documents traités (une fois)."""
        if self._auto_indexed:
            return
        self._auto_indexed = True
        try:
            # Précharger onnxruntime/transformers dans le thread principal :
            # le faire depuis un QThread gèle le chargeur DLL de Windows.
            _ = self._search_service.embedder
        except Exception:  # noqa: BLE001 — modèle absent : indexation ignorée
            return
        job = _Job(lambda: self._search_service.index_new())
        job.done.connect(self._on_auto_index_done)
        job.failed.connect(
            lambda _message: self.bottom.label.setText(
                "Index de recherche indisponible (modèle e5 absent ?)"
            )
        )
        job.finished.connect(job.deleteLater)
        job.start()

    def _on_auto_index_done(self, report: object) -> None:
        from core.indexing.search import IndexReport

        if isinstance(report, IndexReport) and report.indexed:
            self.bottom.label.setText(
                f"Recherche : {len(report.indexed)} document(s) indexé(s) en arrière-plan"
            )

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

    def _toggle_sidebar(self) -> None:
        """Replie/déplie le bloc des tâches (flèche ◀ / ▶)."""
        if self.sidebar.isVisible():
            self._sidebar_sizes = self.main_split.sizes()
            self.sidebar.setVisible(False)
            self.sidebar_btn.setText("▶")
            self.preview.fit_page()
        else:
            self.sidebar.setVisible(True)
            self.sidebar_btn.setText("◀")
            self.main_split.setSizes(self._sidebar_sizes)
            self.preview.fit_page()

    def _toggle_plan(self) -> None:
        """Ouvre/ferme le panneau plan du document (bouton ☰ Plan)."""
        if self.outline.isVisible():
            self._sidebar_sizes = self.main_split.sizes()
            self.outline.setVisible(False)
            self.main_split.setSizes(self._sidebar_sizes)
        else:
            self.outline.setVisible(True)
            if self._sidebar_sizes[-1] >= self._sidebar_sizes[0]:
                total = sum(self._sidebar_sizes)
                self._sidebar_sizes = [300, 300, max(400, total - 600)]
            self.main_split.setSizes(self._sidebar_sizes)
            self._refresh_outline()
        self.preview.fit_page()

    def _refresh_outline(self) -> None:
        """Recharge le plan du document courant (tâche sélectionnée)."""
        task = self._current_task
        if task is None:
            self.outline.clear_outline()
            return
        count = self.outline.load_outline(Path(task.work_dir))
        self.outline.set_current_page(self._active_page)
        if count == 0:
            self.bottom.label.setText(
                "Plan : aucune entrée (attendez la fin du traitement)"
            )

    def _on_outline_jump(
        self,
        page: int,
        _block_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Clic dans le plan : saut à la page + surlignage du bloc."""
        self._on_page_requested(page)
        task = self._current_task
        if task is not None:
            self.preview.flash_block((x0, y0, x1, y1), dpi=task.dpi)

    def _toggle_results(self) -> None:
        """Replie/déplie le bloc du bas (Rendu / Markdown-HTML / Texte brut)."""
        if self.results.isVisible():
            self._results_sizes = self.center_split.sizes()
            self.results.setVisible(False)
            self.results_btn.setText("Résultat ▴")
            self.preview.fit_page()
        else:
            self.results.setVisible(True)
            self.results_btn.setText("Résultat ▾")
            self.center_split.setSizes(self._results_sizes)
            self.preview.fit_page()

    def _open_settings(self) -> None:
        from PySide6.QtWidgets import QApplication

        from core.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            dpi=self._dpi, dark=theme.is_dark(), languages=self._languages, parent=self
        )
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        previous_language = self._languages
        self._dpi = dialog.dpi
        self._languages = dialog.languages
        self._settings.setValue("dpi", self._dpi)
        self._settings.setValue("languages", self._languages)
        self._settings.setValue("dark", dialog.dark)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply_theme(app, dark=dialog.dark)
        for task in self._runner.tasks():
            self.sidebar.upsert(task)
        self.preview.set_result_html(self.results.rendered.toHtml())
        # Changement de langue : relance l'OCR du document courant.
        if self._languages != previous_language and self._current_task is not None:
            self.add_file(self._current_task.source_path)

    def _open_correction(self) -> None:
        """Correction OCR directe : blocs faibles, édition, relance ciblée."""
        task = self._current_task
        if task is None or not self._active_page:
            self.bottom.label.setText("Correction : aucune page traitée")
            return
        dialog = CorrectionDialog(task.work_dir, self._active_page, self)
        dialog.saved.connect(lambda: self._reload_active_page())
        dialog.reprocess_requested.connect(
            lambda second, best: self._reprocess_page(task, self._active_page, second, best)
        )
        dialog.exec()

    def _reload_active_page(self) -> None:
        task = self._current_task
        if task is not None and self._active_page:
            self._show_page(task, self._active_page)

    def _reprocess_page(
        self, task: Task, page: int, second_pass: bool, best_variant: bool
    ) -> None:
        """Relance l'OCR d'UNE page avec les options choisies (sous-processus, R2)."""
        if self._improve_client is not None and self._improve_client.is_running:
            self.bottom.label.setText("Une amélioration est déjà en cours")
            return
        client = WorkerClient(self)
        self._improve_client = client
        self.correct_btn.setEnabled(False)
        self.bottom.label.setText(f"Amélioration de la page {page}…")

        def on_done(done_page: int, _path: str) -> None:
            self.bottom.label.setText(f"Page {done_page} améliorée")
            self._show_page(task, done_page)
            self.correct_btn.setEnabled(True)

        def on_error(message: str) -> None:
            self.bottom.label.setText(f"Erreur : {message}")
            self.correct_btn.setEnabled(True)

        def on_finished(_code: int) -> None:
            self._improve_client = None
            self.correct_btn.setEnabled(True)

        client.page_done.connect(on_done)
        client.error.connect(on_error)
        client.finished.connect(on_finished)
        client.start(
            task.source_path,
            task.work_dir,
            dpi=self._dpi,
            mode=task.mode,
            languages=self._languages,
            page=page,
            second_pass=second_pass,
            best_variant=best_variant,
        )

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
