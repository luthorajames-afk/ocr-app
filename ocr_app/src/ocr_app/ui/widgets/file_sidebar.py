"""Panneau latéral : liste des fichiers et état des modules OCR."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.task_queue import Task
from ocr_app.ui import theme
from ocr_app.ui.icons import make_icon
from ocr_app.ui.modules_info import ModuleInfo
from ocr_app.ui.task_format import format_pages, status_badge


class Thumbnail(QWidget):
    """Vignette de document dessinée (aucune dépendance à un rendu PDF)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(34, 44)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.setBrush(QColor(theme.PANEL))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 33, 43), 4, 4)

            widths = (16, 11, 22, 18, 23)
            for index, width in enumerate(widths):
                y = 8 + index * 5
                color = theme.PAPER_TABLE_BORDER if index == 0 else "#c9ced8"
                height = 3 if index == 0 else 2
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(
                    QRectF(6, y, min(width, 22), height), 1, 1
                )
        finally:
            painter.end()


class ElidedLabel(QLabel):
    """QLabel dont le texte est élidé au milieu quand la place manque."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - API Qt
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(0, self.width())
        if width <= 0:
            super().setText(self._full_text)
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, width
        )
        super().setText(elided)


class StatusBadge(QLabel):
    """Badge coloré : En cours / Terminé / En attente…"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._kind = ""
        self._text = ""

    def set_status(self, kind: str, text: str) -> None:
        changed = kind != self._kind
        self._kind = kind
        if text != self._text:
            self._text = text
            self.setText(text)
        if changed:
            self.setProperty("kind", kind)
            theme.repolish(self)


class FileRow(QWidget):
    """Ligne de fichier : vignette, nom, nombre de pages et badge."""

    clicked = Signal(str)

    def __init__(self, task: Task, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FileRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.task_id = task.id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(60)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(9)

        self._thumbnail = Thumbnail(self)
        self._thumbnail.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        layout.addWidget(self._thumbnail, 0, Qt.AlignmentFlag.AlignVCenter)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(3)

        self._filename = ElidedLabel("", self)
        self._filename.setObjectName("FileName")
        meta.addWidget(self._filename)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(6)
        self._pages = QLabel("", self)
        self._pages.setObjectName("FileMeta")
        sub_row.addWidget(self._pages)
        self._badge = StatusBadge(self)
        sub_row.addWidget(self._badge)
        sub_row.addStretch(1)
        meta.addLayout(sub_row)

        layout.addLayout(meta, 1)
        for child in (self._filename, self._pages, self._badge):
            child.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
        self._selected = False
        self.update_from_task(task)

    def update_from_task(self, task: Task) -> None:
        name = Path(task.source_path).name if task.source_path else task.id
        self._filename.setText(name)
        self._pages.setText(format_pages(task.total_pages))
        kind, label = status_badge(task)
        self._badge.set_status(kind, label)

        tooltip = f"{name}\n{format_pages(task.total_pages)}"
        if task.error_message:
            tooltip += f"\nErreur : {task.error_message}"
        self.setToolTip(tooltip)
        self._filename.setToolTip(task.source_path or name)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        theme.repolish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit(self.task_id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.task_id)
            event.accept()
            return
        super().keyPressEvent(event)


class FileSidebar(QWidget):
    """Liste des documents et section « Modules »."""

    file_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self.setMaximumWidth(380)

        self._rows: Dict[str, FileRow] = {}
        self._selected_id: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        files_head, self._count = self._make_section_header("FICHIERS")
        layout.addWidget(files_head)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("FileScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._container = QWidget(self._scroll)
        self._container.setObjectName("FileListContainer")
        self._container.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self._file_layout = QVBoxLayout(self._container)
        self._file_layout.setContentsMargins(8, 0, 8, 8)
        self._file_layout.setSpacing(2)

        self._empty_hint = QLabel(
            "Aucun fichier\nAjoutez des fichiers pour lancer l'OCR.", self._container
        )
        self._empty_hint.setObjectName("FileEmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._file_layout.addWidget(self._empty_hint)
        self._file_layout.addStretch(1)

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        modules_head, modules_count = self._make_section_header("MODULES")
        modules_count.hide()
        layout.addWidget(modules_head)

        self._modules_box = QWidget(self)
        self._modules_layout = QVBoxLayout(self._modules_box)
        self._modules_layout.setContentsMargins(13, 0, 13, 13)
        self._modules_layout.setSpacing(7)
        layout.addWidget(self._modules_box)

        self._empty_hint.setVisible(True)
        self._update_count()

    @staticmethod
    def _make_section_header(title: str) -> tuple[QWidget, QLabel]:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(13, 11, 13, 8)
        layout.setSpacing(6)
        label = QLabel(title, header)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        count = QLabel("0", header)
        count.setObjectName("SectionCount")
        layout.addStretch(1)
        layout.addWidget(count)
        return header, count

    # --- Fichiers ---------------------------------------------------------
    def add_task(self, task: Task) -> FileRow:
        existing = self._rows.get(task.id)
        if existing is not None:
            existing.update_from_task(task)
            return existing
        row = FileRow(task, self._container)
        row.clicked.connect(self._on_row_clicked)
        self._rows[task.id] = row
        self._file_layout.insertWidget(self._file_layout.count() - 1, row)
        self._empty_hint.setVisible(False)
        self._update_count()
        return row

    def update_task(self, task: Task) -> None:
        row = self._rows.get(task.id)
        if row is not None:
            row.update_from_task(task)

    def select_task(self, task_id: str, emit_signal: bool = False) -> None:
        row = self._rows.get(task_id)
        if row is None:
            return
        for other_id, other in self._rows.items():
            other.set_selected(other_id == task_id)
        self._selected_id = task_id
        if emit_signal:
            self.file_selected.emit(task_id)

    def selected_task_id(self) -> Optional[str]:
        return self._selected_id

    def row_count(self) -> int:
        return len(self._rows)

    def clear(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._selected_id = None
        self._empty_hint.setVisible(True)
        self._update_count()

    def _on_row_clicked(self, task_id: str) -> None:
        self.select_task(task_id, emit_signal=True)

    def _update_count(self) -> None:
        self._count.setText(str(len(self._rows)))

    # --- Modules ----------------------------------------------------------
    def set_modules(self, modules: List[ModuleInfo]) -> None:
        while self._modules_layout.count():
            item = self._modules_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for module in modules:
            row = QWidget(self._modules_box)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(7)

            icon = QLabel(row)
            if module.installed:
                icon.setPixmap(
                    make_icon("check-circle", theme.GREEN, 14).pixmap(14, 14)
                )
                icon.setToolTip("Module installé")
            else:
                icon.setPixmap(
                    make_icon("circle-dashed", theme.MUTED, 14).pixmap(14, 14)
                )
                icon.setToolTip("Module non installé")
            icon.setFixedSize(14, 14)
            layout.addWidget(icon)

            text = module.label
            if module.key == "advanced" and module.detail:
                text = f"{module.label} · {module.detail}"
            label = QLabel(text, row)
            label.setObjectName("ModuleLabel")
            if module.detail and module.key != "advanced":
                label.setToolTip(f"{module.label} : {module.detail}")
            layout.addWidget(label)
            layout.addStretch(1)
            self._modules_layout.addWidget(row)

        self._modules_layout.addStretch(1)
