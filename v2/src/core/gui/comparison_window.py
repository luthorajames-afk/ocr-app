"""Fenêtre autonome de comparaison (texte OCR détaché de l'aperçu)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget

from core.gui import theme


class ComparisonWindow(QDialog):
    """Résultat OCR dans une fenêtre séparée (lecture confortable)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AOCRI — Résultat (comparaison)")
        self.resize(640, 900)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.browser = QTextBrowser(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.browser)
        self.browser.setStyleSheet(
            f"background: {theme.PANEL}; color: {theme.TEXT};"
            f"border: 1px solid {theme.BORDER}; border-radius: 7px;"
        )

    def set_html(self, html: str) -> None:
        self.browser.setHtml(html)

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
