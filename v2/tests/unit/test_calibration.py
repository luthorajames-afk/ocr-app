"""Étalonnage qualité OCR — tests unitaires et dialogue."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path

import pytest

from core.pipeline.calibration import CalibrationReport, calibrate, cer, wer


def test_cer_and_wer_perfect_match() -> None:
    text = "Facture n° 041, total 1 450,90 €"
    assert cer(text, text) == 0.0
    assert wer(text, text) == 0.0


def test_cer_and_wer_detect_errors() -> None:
    reference = "le décret est publié"
    hypothesis = "le decret est publie"
    assert cer(hypothesis, reference) > 0.0
    assert wer(hypothesis, reference) > 0.0


def test_calibration_report_verdict_and_confidence() -> None:
    records = [
        {
            "text": "bonjour",
            "blocks": [
                {"text": "bonjour", "confidence": 0.95},
                {"text": "monde", "confidence": 0.30},
            ],
        }
    ]
    report = calibrate("bonjour", "bonjour", records, threshold=0.6)
    assert isinstance(report, CalibrationReport)
    assert report.cer == 0.0
    assert report.verdict == "excellent"
    assert report.total_blocks == 2
    assert report.low_confidence_blocks == 1
    assert 0.0 < report.mean_confidence < 1.0


def test_calibration_handles_missing_reference() -> None:
    report = calibrate("texte", "")
    assert report.cer == 1.0


def test_dialog_computes_report(tmp_path: Path, qtbot) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui.calibration_dialog import CalibrationDialog

    QApplication.instance() or QApplication([])
    work = tmp_path / "travail éàç"
    work.mkdir()
    (work / "page_001.json").write_text(
        json.dumps(
            {
                "page": 1,
                "text": "SARL Dupont & Fils\nFACTURE",
                "blocks": [{"text": "SARL Dupont & Fils", "confidence": 0.99}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    truth = tmp_path / "truth.txt"
    truth.write_text("SARL Dupont & Fils\nFACTURE", encoding="utf-8")

    dialog = CalibrationDialog(work)
    qtbot.addWidget(dialog)
    dialog.set_reference(truth)
    report = dialog.compute()
    assert report is not None
    assert report.cer == 0.0
    assert dialog.cer_label.text() == "0.00%"
    assert dialog.verdict_label.text() == "excellent"
    dialog.close()
