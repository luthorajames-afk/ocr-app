"""Panneau plan du document : chargement, filtrage, saut, page courante."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.gui.outline_panel import OutlinePanel

pytest.importorskip("PySide6")


def make_work_dir(tmp_path: Path) -> Path:
    page1 = {
        "page": 1,
        "blocks": [
            {"text": "VU la Constitution du 02 juin 1972 ;", "bbox": [40, 100, 400, 112], "kind": "line"},
            {"text": "ARRETE", "bbox": [250, 200, 300, 212], "kind": "line"},
            {"text": "ARTICLE 1er.- Le présent arrêté fixe la composition.", "bbox": [40, 240, 520, 252], "kind": "line"},
        ],
    }
    page2 = {
        "page": 2,
        "blocks": [
            {"text": "TITRE I : DISPOSITIONS GENERALES", "bbox": [180, 100, 400, 116], "kind": "title"},
            {"text": "ANNEXE : grille de notation", "bbox": [40, 500, 300, 512], "kind": "line"},
        ],
    }
    work = tmp_path / "work"
    work.mkdir()
    (work / "page_001.json").write_text(json.dumps(page1, ensure_ascii=False), encoding="utf-8")
    (work / "page_002.json").write_text(json.dumps(page2, ensure_ascii=False), encoding="utf-8")
    return work


def test_outline_lists_titles_articles_annexes(qtbot, tmp_path) -> None:
    panel = OutlinePanel()
    qtbot.addWidget(panel)
    count = panel.load_outline(make_work_dir(tmp_path))
    assert count == 4
    assert panel.count_label.text() == "4"
    texts = [panel.list.item(i).text() for i in range(panel.list.count())]
    assert any("ARTICLE" in text for text in texts)
    assert any("TITRE" in text for text in texts)
    assert any("ANNEXE" in text for text in texts)


def test_outline_filter(qtbot, tmp_path) -> None:
    panel = OutlinePanel()
    qtbot.addWidget(panel)
    panel.load_outline(make_work_dir(tmp_path))
    panel.search.setText("annexe")
    visible = [
        panel.list.item(i) for i in range(panel.list.count()) if not panel.list.item(i).isHidden()
    ]
    assert len(visible) == 1
    assert "ANNEXE" in visible[0].text()
    panel.search.setText("")
    visible = [
        panel.list.item(i) for i in range(panel.list.count()) if not panel.list.item(i).isHidden()
    ]
    assert len(visible) == 4


def test_outline_jump_signal(qtbot, tmp_path) -> None:
    panel = OutlinePanel()
    qtbot.addWidget(panel)
    panel.load_outline(make_work_dir(tmp_path))
    with qtbot.waitSignal(panel.jump_requested, timeout=2000) as blocker:
        panel.list.item(0).setSelected(True)
        panel._on_clicked(panel.list.item(0))
    page, _index, x0, y0, x1, y1 = blocker.args
    assert page == 1
    assert (x1 - x0) > 0 and (y1 - y0) > 0


def test_outline_current_page_marked(qtbot, tmp_path) -> None:
    panel = OutlinePanel()
    qtbot.addWidget(panel)
    panel.load_outline(make_work_dir(tmp_path))
    panel.set_current_page(2)
    marked = [
        i for i in range(panel.list.count())
        if panel.list.item(i).background().color().alpha() > 0
    ]
    assert len(marked) == 2  # TITRE et ANNEXE de la page 2
