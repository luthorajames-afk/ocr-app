"""Éditeur de blocs : tri d'affichage par confiance, ordre de lecture persisté."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.gui.block_editor import BlockEditor
from core.gui.comparison_window import ComparisonWindow

pytest.importorskip("PySide6")


def make_page(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    data = {
        "page": 1,
        "blocks": [
            {"text": "Premier bloc", "confidence": 0.90, "kind": "line"},
            {"text": "Bloc faible", "confidence": 0.20, "kind": "line"},
            {"text": "Bloc moyen", "confidence": 0.70, "kind": "line"},
        ],
        "text": "Premier bloc\nBloc faible\nBloc moyen",
    }
    path = work / "page_001.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return work, path


def test_editor_sorts_display_but_saves_original_order(qtbot, tmp_path) -> None:
    work, path = make_page(tmp_path)
    editor = BlockEditor()
    qtbot.addWidget(editor)
    assert editor.load(work, 1)
    assert editor.table.item(0, 2).text() == "Bloc faible"
    assert editor.table.item(1, 2).text() == "Bloc moyen"
    assert editor.table.item(2, 2).text() == "Premier bloc"

    editor.table.item(0, 2).setText("Bloc faible corrigé")
    editor.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [block["text"] for block in saved["blocks"]] == [
        "Premier bloc",
        "Bloc faible corrigé",
        "Bloc moyen",
    ]
    assert saved["text"] == "Premier bloc\nBloc faible corrigé\nBloc moyen"


def test_editor_flush_saves_pending_edit(qtbot, tmp_path) -> None:
    work, path = make_page(tmp_path)
    editor = BlockEditor()
    qtbot.addWidget(editor)
    editor.load(work, 1)
    editor.table.item(2, 2).setText("Premier bloc corrigé")
    editor.flush()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["blocks"][0]["text"] == "Premier bloc corrigé"


def test_editor_without_page_is_noop(qtbot) -> None:
    editor = BlockEditor()
    qtbot.addWidget(editor)
    editor.save()
    assert not editor.has_page


def test_comparison_window_forwards_saved_signal(qtbot, tmp_path) -> None:
    work, _path = make_page(tmp_path)
    window = ComparisonWindow(None)
    qtbot.addWidget(window)
    window.set_page(work, 1)
    assert window.editor.has_page
    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == "Corriger"
    window.editor.table.item(1, 2).setText("Bloc moyen corrigé")
    with qtbot.waitSignal(window.blocks_saved, timeout=2000):
        window.editor.save()
    window.close()
