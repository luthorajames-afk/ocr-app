"""Thème clair/sombre et réglages — tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def test_dark_palette_and_stylesheet(qtbot) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui import theme

    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app, dark=True)
    assert theme.is_dark() is True
    assert theme.BG == "#17181c"
    assert theme.PANEL == "#24262c"
    assert theme.TEXT == "#e9ebf0"
    stylesheet = app.styleSheet()
    assert "#17181c" in stylesheet and "#24262c" in stylesheet

    theme.apply_theme(app, dark=False)
    assert theme.is_dark() is False
    assert theme.BG == "#eef0f4"
    assert theme.PANEL == "#ffffff"


def test_settings_dialog_exposes_values_and_previews_theme(qtbot) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui import theme
    from core.gui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app, dark=False)
    dialog = SettingsDialog(dpi=200, dark=True, languages="fra")
    qtbot.addWidget(dialog)
    assert dialog.dpi == 200
    assert dialog.dark is True
    assert dialog.languages == "fra"
    dialog.apply_theme_preview()
    assert theme.is_dark() is True
    dialog.close()
    theme.apply_theme(app, dark=False)
