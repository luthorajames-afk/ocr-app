"""Dialogue de correction : tri d'affichage sans propagation à l'enregistrement.

Régression : ``save_corrections`` réécrivait ``page_NNN.json`` dans l'ordre
d'affichage (confiance croissante), ce qui mélangeait le texte de la page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.gui.correction_dialog import CorrectionDialog

pytest.importorskip("PySide6")


def make_work_dir(tmp_path: Path) -> Path:
    """Page avec des blocs dans l'ordre de lecture, confiances en désordre."""
    blocks = [
        {"text": "ARTICLE 3.- Le présent arrêté prend effet.", "bbox": [27, 554, 522, 566], "confidence": 0.94, "kind": "line"},
        {"text": "garbage du tampon", "bbox": [336, 718, 377, 730], "confidence": 0.14, "kind": "line"},
        {"text": "Office National du Cacao et du Café (ONCC)", "bbox": [86, 467, 327, 479], "confidence": 0.96, "kind": "line"},
        {"text": "- Fonds Routier (FR)", "bbox": [82, 90, 201, 102], "confidence": 0.93, "kind": "line"},
    ]
    work = tmp_path / "work"
    work.mkdir()
    (work / "page_003.json").write_text(
        json.dumps({"page": 3, "blocks": blocks}, ensure_ascii=False), encoding="utf-8"
    )
    return work


def open_dialog(qtbot: pytest.fixture, work: Path) -> CorrectionDialog:  # type: ignore[type-arg]
    dialog = CorrectionDialog(work, 3)
    qtbot.addWidget(dialog)
    return dialog


def test_table_shows_weak_blocks_first(qtbot, tmp_path) -> None:
    dialog = open_dialog(qtbot, make_work_dir(tmp_path))
    confidences = [
        float(dialog.table.item(row, 0).text().rstrip("%")) / 100.0
        for row in range(dialog.table.rowCount())
    ]
    assert confidences == sorted(confidences)  # confiance croissante à l'écran
    assert dialog.table.item(0, 2).text() == "garbage du tampon"


def test_save_preserves_original_block_order(qtbot, tmp_path) -> None:
    work = make_work_dir(tmp_path)
    dialog = open_dialog(qtbot, work)
    # Édition d'une ligne (le garbage, affiché en premier).
    dialog.table.item(0, 2).setText("corrigé à la main")
    dialog.save_corrections()
    data = json.loads((work / "page_003.json").read_text(encoding="utf-8"))
    texts = [str(block["text"]) for block in data["blocks"]]
    assert texts == [
        "ARTICLE 3.- Le présent arrêté prend effet.",
        "corrigé à la main",
        "Office National du Cacao et du Café (ONCC)",
        "- Fonds Routier (FR)",
    ]


def test_save_rebuilds_text_in_reading_order(qtbot, tmp_path) -> None:
    work = make_work_dir(tmp_path)
    dialog = open_dialog(qtbot, work)
    dialog.save_corrections()
    data = json.loads((work / "page_003.json").read_text(encoding="utf-8"))
    expected = "\n".join(
        str(block["text"])
        for block in data["blocks"]
        if str(block.get("text", "")).strip()
    )
    assert data["text"] == expected
    assert data["text"].startswith("ARTICLE 3.-")  # pas le garbage de confiance 0.14
