"""Composants de l'interface AOCRI (PySide6)."""

from ocr_app.ui.widgets.file_sidebar import FileSidebar
from ocr_app.ui.widgets.preview import PreviewPanel
from ocr_app.ui.widgets.results_panel import ResultsPanel
from ocr_app.ui.widgets.status_bar import BottomBar
from ocr_app.ui.widgets.titlebar import TitleBar
from ocr_app.ui.widgets.toolbar import AppToolbar, ModeSelector

__all__ = [
    "AppToolbar",
    "BottomBar",
    "FileSidebar",
    "ModeSelector",
    "PreviewPanel",
    "ResultsPanel",
    "TitleBar",
]
