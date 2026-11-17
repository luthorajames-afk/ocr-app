"""Fenêtre autonome de comparaison (résultat OCR + correction directe)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.gui import theme
from core.gui.block_editor import BlockEditor


class ComparisonWindow(QDialog):
    """Résultat OCR dans une fenêtre autonome (déplaçable, multi-écrans).

    Deux onglets : « Rendu » (lecture, surlignage) et « Corriger » (blocs
    éditables de la page courante). La correction est enregistrée dans le
    ``page_NNN.json`` puis signalée à la fenêtre principale.
    """

    closed = Signal()
    blocks_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AOCRI — Résultat (comparaison)")
        self.resize(760, 900)
        # Fenêtre de premier niveau : déplaçable librement (autre écran).
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        self.browser = QTextBrowser(self)
        self.browser.setStyleSheet(
            f"background: {theme.PANEL}; color: {theme.TEXT};"
            f"border: 1px solid {theme.BORDER}; border-radius: 7px;"
        )
        self.editor = BlockEditor(self)
        self.editor.saved.connect(self.blocks_saved)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.browser, "Rendu")
        self.tabs.addTab(self.editor, "Corriger")
        self.tabs.setTabToolTip(1, "Corriger directement les blocs de la page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)

    def set_html(self, html: str) -> None:
        self.browser.setHtml(html)

    def set_page(self, work_dir: str | Path, page: int) -> None:
        """Charge les blocs de la page dans l'onglet « Corriger »."""
        self.editor.load(work_dir, page)
        self.setWindowTitle(f"AOCRI — Résultat (comparaison) — page {page}")

    def show_correction_tab(self) -> None:
        self.tabs.setCurrentWidget(self.editor)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Ne pas perdre une correction en cours de saisie.
        self.editor.flush()
        super().closeEvent(event)
