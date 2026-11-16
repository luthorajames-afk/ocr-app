"""Plan du document : navigation par articles, titres et sections juridiques.

Panneau alimenté par les enregistrements de pages du worker (rôles issus de
``core.pipeline.legal_structure``). Clic = saut à la page + surlignage du
bloc ; recherche instantanée ; entrées de la page courante marquées.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.legal_structure import (
    _OUTLINE_ROLES,
    OutlineEntry,
    classify,
)

_ROLE_LABELS = {
    "titre": "TITRE",
    "section": "SECTION",
    "article": "ARTICLE",
    "annexe": "ANNEXE",
    "dispositif": "DISPOSITIF",
}


class OutlinePanel(QWidget):
    """Liste cliquable des titres/articles du document courant."""

    jump_requested = Signal(int, int, float, float, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        header = QHBoxLayout()
        title = QLabel("Plan du document")
        title.setObjectName("Title")
        self.count_label = QLabel("0")
        self.count_label.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.count_label)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filtrer : article, titre, annexe…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        self.list = QListWidget(self)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemActivated.connect(self._on_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(header)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        self._entries: list[OutlineEntry] = []

    # ------------------------------------------------------------- données
    def load_outline(self, work_dir: str | Path) -> int:
        """Construit le plan depuis les ``page_NNN.json`` du dossier."""
        self.list.clear()
        self._entries = []
        directory = Path(work_dir)
        pages: list[tuple[int, list[object]]] = []
        for path in sorted(directory.glob("page_*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            page_number = int(record.get("page", 0) or 0)
            blocks = record.get("blocks")
            if isinstance(blocks, list):
                pages.append((page_number, blocks))
        for page_number, blocks in pages:
            for index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                text = str(block.get("text", "")).strip()
                if not text:
                    continue
                role = str(block.get("role", "")) or classify(text)
                bbox = block.get("bbox")
                box: tuple[float, float, float, float] | None = None
                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    values = [float(value) for value in bbox]
                    box = (values[0], values[1], values[2], values[3])
                if role in _OUTLINE_ROLES:
                    self._entries.append(
                        OutlineEntry(role, text, page_number, index, box)
                    )
        self._populate()
        return len(self._entries)

    def _populate(self) -> None:
        self.list.clear()
        shown = 0
        current_page = 0
        for entry in self._entries:
            item = QListWidgetItem()
            label = _ROLE_LABELS.get(entry.role)
            text = entry.text if len(entry.text) <= 96 else entry.text[:95] + "…"
            if label:
                item.setText(f"{label}  ·  {text}      [p.{entry.page}]")
            else:
                continue
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list.addItem(item)
            shown += 1
            current_page = max(current_page, entry.page)
        self.count_label.setText(str(shown))
        self._mark_current(current_page)
        self._apply_filter(self.search.text())

    def clear_outline(self) -> None:
        self._entries = []
        self.list.clear()
        self.count_label.setText("0")

    def set_current_page(self, page: int) -> None:
        self._mark_current(page)
        self._apply_filter(self.search.text())

    def _mark_current(self, page: int) -> None:
        current = QColor(125, 152, 255, 46)
        transparent = QColor(0, 0, 0, 0)
        for index in range(self.list.count()):
            item = self.list.item(index)
            entry = item.data(Qt.ItemDataRole.UserRole)
            item.setBackground(current if entry is not None and entry.page == page else transparent)

    # ------------------------------------------------------------ filtrage
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            entry = item.data(Qt.ItemDataRole.UserRole)
            visible = (
                not needle
                or needle in item.text().lower()
                or (entry is not None and needle in entry.role.lower())
            )
            item.setHidden(not visible)

    # ------------------------------------------------------------ interaction
    def _on_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        bbox = entry.bbox or (0.0, 0.0, 0.0, 0.0)
        self.jump_requested.emit(
            entry.page,
            entry.block_index,
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
