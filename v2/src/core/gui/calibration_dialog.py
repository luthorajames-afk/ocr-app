"""Dialogue d'étalonnage qualité OCR (CER/WER vs vérité terrain)."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.calibration import CalibrationReport, calibrate


class CalibrationDialog(QDialog):
    """Sélectionne une vérité terrain, compare et affiche les indicateurs."""

    def __init__(self, work_dir: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Étalonnage qualité OCR")
        self.resize(560, 340)
        self._work_dir = Path(work_dir)
        self._reference_path: Path | None = None
        self.report: CalibrationReport | None = None

        self.reference_label = QLabel("Aucune référence sélectionnée")
        self.reference_label.setObjectName("Muted")
        pick = QPushButton("Choisir la vérité terrain…", self)
        pick.clicked.connect(self._pick_reference)

        self.threshold = QDoubleSpinBox(self)
        self.threshold.setRange(0.0, 1.0)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.60)
        self.threshold.setDecimals(2)

        self.cer_label = QLabel("—", self)
        self.wer_label = QLabel("—", self)
        self.confidence_label = QLabel("—", self)
        self.blocks_label = QLabel("—", self)
        self.verdict_label = QLabel("—", self)
        self.verdict_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        form = QFormLayout()
        form.addRow("Vérité terrain", self.reference_label)
        form.addRow("Seuil confiance", self.threshold)
        form.addRow("CER", self.cer_label)
        form.addRow("WER", self.wer_label)
        form.addRow("Confiance moyenne", self.confidence_label)
        form.addRow("Blocs sous seuil", self.blocks_label)
        form.addRow("Verdict", self.verdict_label)

        compute = QPushButton("Calculer l'étalonnage", self)
        compute.clicked.connect(self.compute)
        export = QPushButton("Exporter le rapport JSON…", self)
        export.clicked.connect(self.export_report)

        buttons = QHBoxLayout()
        buttons.addWidget(pick)
        buttons.addWidget(compute)
        buttons.addWidget(export)
        buttons.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addLayout(form)
        layout.addWidget(box)

    # ----------------------------------------------------------------- API
    def set_reference(self, path: str | Path) -> None:
        self._reference_path = Path(path)
        self.reference_label.setText(self._reference_path.name)

    def records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for path in sorted(self._work_dir.glob("page_*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return records

    def compute(self) -> CalibrationReport | None:
        if self._reference_path is None or not self._reference_path.is_file():
            self.verdict_label.setText("Référence manquante")
            return None
        reference = self._reference_path.read_text(encoding="utf-8")
        hypothesis = "\n".join(
            str(record.get("text", "")) for record in self.records()
        )
        self.report = calibrate(
            hypothesis,
            reference,
            self.records(),
            threshold=float(self.threshold.value()),
        )
        self.cer_label.setText(f"{self.report.cer:.2%}")
        self.wer_label.setText(f"{self.report.wer:.2%}")
        self.confidence_label.setText(f"{self.report.mean_confidence:.2%}")
        self.blocks_label.setText(
            f"{self.report.low_confidence_blocks} / {self.report.total_blocks}"
        )
        self.verdict_label.setText(self.report.verdict)
        return self.report

    def export_report(self) -> Path | None:
        if self.report is None:
            self.compute()
        if self.report is None:
            return None
        target, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport", "etalonnage.json", "JSON (*.json)"
        )
        if not target:
            return None
        path = Path(target)
        path.write_text(
            json.dumps(self.report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------- interne
    def _pick_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Vérité terrain", str(self._work_dir), "Texte (*.txt);;Tous (*)"
        )
        if path:
            self.set_reference(path)
