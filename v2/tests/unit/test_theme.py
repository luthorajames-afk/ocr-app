"""Thème clair/sombre — tests."""

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


def test_main_window_toggle_switches_theme(qtbot) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui import theme
    from core.gui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    theme.apply_theme(QApplication.instance(), dark=False)
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.theme_btn.text() == "Sombre"
    window.theme_btn.click()
    assert theme.is_dark() is True
    assert window.theme_btn.text() == "Clair"
    window.close()
    theme.apply_theme(QApplication.instance(), dark=False)
