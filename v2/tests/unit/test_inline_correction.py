"""Correction directe dans l'aperçu : surcouches, éditeur intégré, sauvegarde."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap

from core.gui.main_window import PreviewPanel

pytest.importorskip("PySide6")


def make_work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    data = {
        "page": 1,
        "blocks": [
            {"text": "Premier bloc", "bbox": [20, 40, 300, 55], "confidence": 0.9, "kind": "line"},
            {"text": "Deuxieme bloc", "bbox": [20, 80, 300, 95], "confidence": 0.5, "kind": "line"},
        ],
        "text": "Premier bloc\nDeuxieme bloc",
    }
    (work / "page_001.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pixmap = QPixmap(1200, 1600)
    pixmap.fill()
    pixmap.save(str(work / "page_001.png"), "PNG")
    return work


def task_for(work: Path) -> SimpleNamespace:
    return SimpleNamespace(
        work_dir=str(work), source_path="doc.pdf", total_pages=1, dpi=300
    )


def test_edit_mode_draws_overlays(qtbot, tmp_path) -> None:
    work = make_work(tmp_path)
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show_page(task_for(work), 1)
    panel.edit_btn.setChecked(True)
    assert len(panel._overlay_items) == 2


def test_double_click_opens_inline_editor_and_saves(qtbot, tmp_path) -> None:
    work = make_work(tmp_path)
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show_page(task_for(work), 1)
    panel.edit_btn.setChecked(True)
    scale = 300 / 72.0
    with qtbot.waitSignal(panel.page_corrected, timeout=2000):
        panel._on_preview_double_click(QPointF(100 * scale, 47 * scale))
        assert panel._inline_editor is not None
        panel._inline_editor.setPlainText("Premier bloc corrigé")
        panel._save_inline_editor()
    saved = json.loads((work / "page_001.json").read_text(encoding="utf-8"))
    assert saved["blocks"][0]["text"] == "Premier bloc corrigé"
    assert saved["blocks"][1]["text"] == "Deuxieme bloc"
    assert saved["text"].startswith("Premier bloc corrigé")


def test_second_block_hit_test_and_escape(qtbot, tmp_path) -> None:
    work = make_work(tmp_path)
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show_page(task_for(work), 1)
    panel.edit_btn.setChecked(True)
    scale = 300 / 72.0
    panel._on_preview_double_click(QPointF(150 * scale, 90 * scale))
    assert panel._inline_index == 1
    panel._close_inline_editor()
    assert panel._inline_editor is None


def test_zoom_persists_across_page_refresh(qtbot, tmp_path) -> None:
    work = make_work(tmp_path)
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 700)
    panel.show()
    panel.show_page(task_for(work), 1)
    panel.zoom_in()
    zoomed = panel.view.transform().m11()
    panel.show_page(task_for(work), 1)  # rafraîchissement après correction
    assert abs(panel.view.transform().m11() - zoomed) < 1e-6
    assert panel._user_zoomed is True
    panel.fit_page()
    assert panel._user_zoomed is False


def test_mouse_wheel_zooms_under_cursor(qtbot, tmp_path) -> None:
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    work = make_work(tmp_path)
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 700)
    panel.show()
    panel.show_page(task_for(work), 1)
    before = panel.view.transform().m11()
    event = QWheelEvent(
        QPointF(300.0, 300.0),
        QPointF(300.0, 300.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(panel.view.viewport(), event)
    assert panel.view.transform().m11() > before
    assert panel._user_zoomed is True
