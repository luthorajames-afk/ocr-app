"""Éditeur de blocs réutilisable (dialogue Correction, fenêtre de comparaison).

Affiche les blocs de la page triés par confiance croissante (les plus faibles
d'abord) ; l'enregistrement réécrit ``page_NNN.json`` dans l'ordre d'origine
du pipeline, jamais dans l'ordre d'affichage.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.gui import theme

LOW_CONFIDENCE = 0.60


class BlockEditor(QWidget):
    """Table éditable des blocs d'une page + sauvegarde atomique."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Confiance", "Type", "Texte (éditable)"])
        self.table.setColumnWidth(0, 88)
        self.table.setColumnWidth(1, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.note = QLabel(
            f"Blocs sous {LOW_CONFIDENCE:.0%} en rouge — corrigez puis enregistrez.",
            self,
        )
        self.note.setObjectName("Muted")
        self.save_btn = QPushButton("Enregistrer les corrections", self)
        self.save_btn.clicked.connect(self.save)
        self.status = QLabel("", self)
        self.status.setObjectName("Muted")

        actions = QHBoxLayout()
        actions.addWidget(self.save_btn)
        actions.addWidget(self.status)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.note)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)

        self._work_dir: Path | None = None
        self._page = 0
        self._data: dict[str, object] = {}
        self._blocks: list[dict[str, object]] = []
        self._rows: list[dict[str, object]] = []
        self._dirty = False
        self.table.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------ état
    @property
    def has_page(self) -> bool:
        return self._work_dir is not None and self._page > 0

    # --------------------------------------------------------------- chargement
    def load(self, work_dir: str | Path, page: int) -> bool:
        """Charge page_NNN.json ; sauvegarde d'abord une édition en attente."""
        self.flush()
        directory = Path(work_dir)
        path = directory / f"page_{page:03d}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.clear()
            return False
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            blocks = []
        self._work_dir = directory
        self._page = page
        self._data = data if isinstance(data, dict) else {}
        self._blocks = [block for block in blocks if isinstance(block, dict)]
        self._dirty = False
        self._populate()
        self.status.setText("")
        return True

    def clear(self) -> None:
        self._work_dir = None
        self._page = 0
        self._data = {}
        self._blocks = []
        self._rows = []
        self._dirty = False
        self.table.setRowCount(0)
        self.status.setText("Aucune page à corriger")

    def _confidence(self, block: dict[str, object]) -> float:
        value = block.get("confidence", 0.0)
        return float(value) if isinstance(value, (int, float, str)) else 0.0

    def _populate(self) -> None:
        self._rows = sorted(
            [block for block in self._blocks if str(block.get("text", "")).strip()],
            key=self._confidence,
        )
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        for row, block in enumerate(self._rows):
            confidence = self._confidence(block)
            confidence_item = QTableWidgetItem(f"{confidence:.0%}")
            if confidence < LOW_CONFIDENCE:
                confidence_item.setForeground(QColor(theme.RED))
            confidence_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, confidence_item)
            kind_item = QTableWidgetItem(str(block.get("kind", "line")))
            kind_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, kind_item)
            self.table.setItem(row, 2, QTableWidgetItem(str(block.get("text", ""))))
        self.table.blockSignals(False)

    # ---------------------------------------------------------------- édition
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self._dirty = True
            self.status.setText("Modifications non enregistrées")

    def flush(self) -> None:
        """Sauvegarde silencieuse si des modifications sont en attente."""
        if self._dirty and self.has_page:
            self.save()

    # ------------------------------------------------------------- sauvegarde
    def save(self) -> None:
        if not self.has_page:
            return
        assert self._work_dir is not None
        for row, block in enumerate(self._rows):
            item = self.table.item(row, 2)
            if item is not None:
                block["text"] = item.text()
        self._data["blocks"] = self._blocks
        self._data["text"] = "\n".join(
            str(block.get("text", ""))
            for block in self._blocks
            if str(block.get("text", "")).strip()
        )
        target = self._work_dir / f"page_{self._page:03d}.json"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        self._dirty = False
        self.status.setText("Corrections enregistrées ✓")
        self.saved.emit()
