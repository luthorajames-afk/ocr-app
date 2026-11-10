"""Correction OCR dans l'application : revue des blocs faibles et relance ciblée."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

LOW_CONFIDENCE = 0.60


class CorrectionDialog(QDialog):
    """Liste les blocs de la page, permet de les corriger et de relancer l'OCR."""

    reprocess_requested = Signal(bool, bool)  # second_pass, best_variant
    saved = Signal()

    def __init__(
        self,
        work_dir: str | Path,
        page: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Correction OCR — page {page}")
        self.resize(760, 520)
        self._work_dir = Path(work_dir)
        self._page = page
        self._data = self._load()

        raw_blocks = self._data.get("blocks")
        blocks = raw_blocks if isinstance(raw_blocks, list) else []
        self._blocks: list[dict[str, object]] = [
            block
            for block in blocks
            if isinstance(block, dict) and str(block.get("text", "")).strip()
        ]

        def confidence_of(block: dict[str, object]) -> float:
            value = block.get("confidence", 0.0)
            return float(value) if isinstance(value, (int, float, str)) else 0.0

        self._blocks.sort(key=confidence_of)

        self.table = QTableWidget(len(self._blocks), 3, self)
        self.table.setHorizontalHeaderLabels(["Confiance", "Type", "Texte (éditable)"])
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, block in enumerate(self._blocks):
            confidence = confidence_of(block)
            confidence_item = QTableWidgetItem(f"{confidence:.0%}")
            if confidence < LOW_CONFIDENCE:
                confidence_item.setForeground(Qt.GlobalColor.red)
            confidence_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, confidence_item)
            kind_item = QTableWidgetItem(str(block.get("kind", "line")))
            kind_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, kind_item)
            self.table.setItem(row, 2, QTableWidgetItem(str(block.get("text", ""))))

        self.threshold_note = QLabel(
            f"Blocs sous {LOW_CONFIDENCE:.0%} affichés en rouge — corrigez le texte "
            "puis « Enregistrer les corrections ».",
            self,
        )
        self.threshold_note.setObjectName("Muted")

        self.second_pass = QCheckBox("Seconde passe sur les lignes faibles", self)
        self.second_pass.setChecked(True)
        self.best_variant = QCheckBox("Choisir le meilleur prétraitement", self)
        self.best_variant.setChecked(True)

        save = QPushButton("Enregistrer les corrections", self)
        save.clicked.connect(self.save_corrections)
        reprocess = QPushButton("Relancer l'OCR de la page", self)
        reprocess.setObjectName("Primary")
        reprocess.clicked.connect(self._request_reprocess)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)

        options = QHBoxLayout()
        options.addWidget(self.second_pass)
        options.addWidget(self.best_variant)
        options.addStretch(1)

        actions = QHBoxLayout()
        actions.addWidget(save)
        actions.addWidget(reprocess)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.threshold_note)
        layout.addWidget(self.table, 1)
        layout.addLayout(options)
        layout.addLayout(actions)
        layout.addWidget(box)

    # ------------------------------------------------------------------ IO
    def _path(self) -> Path:
        return self._work_dir / f"page_{self._page:03d}.json"

    def _load(self) -> dict[str, object]:
        path = self._path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_corrections(self) -> None:
        """Écrit les textes corrigés dans page_NNN.json (atomique)."""
        for row, block in enumerate(self._blocks):
            item = self.table.item(row, 2)
            if item is not None:
                block["text"] = item.text()
        self._data["blocks"] = self._blocks
        self._data["text"] = "\n".join(
            str(block.get("text", "")) for block in self._blocks if str(block.get("text", "")).strip()
        )
        target = self._path()
        target.parent.mkdir(parents=True, exist_ok=True)
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
        self.saved.emit()

    def _request_reprocess(self) -> None:
        self.save_corrections()
        self.reprocess_requested.emit(
            self.second_pass.isChecked(), self.best_variant.isChecked()
        )
