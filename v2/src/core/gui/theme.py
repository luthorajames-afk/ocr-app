"""Palettes AOCRI v2 — thème clair (planche validée) et thème sombre."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

LIGHT = {
    "BG": "#eef0f4",
    "WINDOW": "#f7f8fa",
    "PANEL": "#ffffff",
    "PANEL_2": "#f2f4f7",
    "BORDER": "#dfe3ea",
    "TEXT": "#1a1d24",
    "MUTED": "#68707d",
    "ACCENT": "#2f5bd7",
    "ACCENT_SOFT": "#eef2fb",
    "GREEN": "#1f7a4d",
    "GREEN_SOFT": "#e6f2eb",
    "AMBER": "#946200",
    "AMBER_SOFT": "#f7f0e0",
    "RED": "#b3261e",
    "RED_SOFT": "#fbecea",
    "CANCELLED": "#6b7280",
    "CANCELLED_SOFT": "#eceef2",
}

DARK = {
    "BG": "#17181c",
    "WINDOW": "#1d1e23",
    "PANEL": "#24262c",
    "PANEL_2": "#2a2c33",
    "BORDER": "#33363e",
    "TEXT": "#e9ebf0",
    "MUTED": "#9aa1ad",
    "ACCENT": "#7d98ff",
    "ACCENT_SOFT": "#26314f",
    "GREEN": "#57c98a",
    "GREEN_SOFT": "#1c3a2a",
    "AMBER": "#e3b34d",
    "AMBER_SOFT": "#3a2f14",
    "RED": "#e8908a",
    "RED_SOFT": "#3d1f1c",
    "CANCELLED": "#8b93a1",
    "CANCELLED_SOFT": "#2b2d33",
}

FONT_FAMILY = "Segoe UI"
PAPER = "#ffffff"
PAPER_INK = "#22252b"
_current_dark = False

# Valeurs courantes (mises à jour par apply_theme) — lues par les widgets.
BG = LIGHT["BG"]
WINDOW = LIGHT["WINDOW"]
PANEL = LIGHT["PANEL"]
PANEL_2 = LIGHT["PANEL_2"]
BORDER = LIGHT["BORDER"]
TEXT = LIGHT["TEXT"]
MUTED = LIGHT["MUTED"]
ACCENT = LIGHT["ACCENT"]
ACCENT_SOFT = LIGHT["ACCENT_SOFT"]
GREEN = LIGHT["GREEN"]
GREEN_SOFT = LIGHT["GREEN_SOFT"]
AMBER = LIGHT["AMBER"]
AMBER_SOFT = LIGHT["AMBER_SOFT"]
RED = LIGHT["RED"]
RED_SOFT = LIGHT["RED_SOFT"]
CANCELLED = LIGHT["CANCELLED"]
CANCELLED_SOFT = LIGHT["CANCELLED_SOFT"]


def is_dark() -> bool:
    return _current_dark


def build_stylesheet(p: dict[str, str]) -> str:
    return f"""
QWidget {{ font-family: "{FONT_FAMILY}"; font-size: 13px; color: {p['TEXT']}; }}
QMainWindow, QWidget#Root {{ background: {p['BG']}; }}
QFrame#Panel {{ background: {p['PANEL']}; border: 1px solid {p['BORDER']}; border-radius: 8px; }}
QLabel#Muted {{ color: {p['MUTED']}; font-size: 11.5px; }}
QLabel#Title {{ font-weight: 600; }}
QToolTip {{ background: {p['PANEL']}; color: {p['TEXT']}; border: 1px solid {p['BORDER']}; }}
QPushButton {{
    background: {p['PANEL']}; border: 1px solid {p['BORDER']}; border-radius: 7px;
    padding: 5px 12px; font-size: 12.5px; font-weight: 500; color: {p['TEXT']};
}}
QPushButton:hover {{ background: {p['PANEL_2']}; }}
QPushButton:disabled {{ color: {p['MUTED']}; }}
QPushButton#Primary {{ background: {p['ACCENT']}; border-color: {p['ACCENT']}; color: white; }}
QPushButton#Mode {{ border: 0; background: transparent; color: {p['MUTED']}; padding: 5px 12px; }}
QPushButton#Mode:checked {{ background: {p['PANEL']}; color: {p['TEXT']}; border-radius: 6px; }}
QFrame#ModeGroup {{ background: {p['PANEL_2']}; border: 1px solid {p['BORDER']}; border-radius: 9px; }}
QListWidget {{ background: transparent; border: 0; }}
QListWidget::item {{ border-radius: 8px; margin: 2px 0; }}
QListWidget::item:selected {{ background: {p['PANEL']}; border: 1px solid {p['BORDER']}; }}
QProgressBar {{
    background: {p['PANEL_2']}; border: 0; border-radius: 3px; height: 6px; text-align: center;
}}
QProgressBar::chunk {{ background: {p['ACCENT']}; border-radius: 3px; }}
QTabWidget::pane {{ border: 1px solid {p['BORDER']}; background: {p['PANEL']}; }}
QTabBar::tab {{ background: {p['PANEL']}; color: {p['MUTED']}; padding: 8px 12px; border: 0; }}
QTabBar::tab:selected {{ color: {p['ACCENT']}; border-bottom: 2px solid {p['ACCENT']}; }}
QTextBrowser, QPlainTextEdit {{
    background: {p['PANEL']}; border: 1px solid {p['BORDER']}; border-radius: 7px;
    color: {p['TEXT']};
}}
QGraphicsView {{ background: {p['PANEL_2']}; border: 0; }}
QWidget#TaskCard {{ background: transparent; }}
QListWidget#TaskList {{ background: {p['WINDOW']}; border: 0; }}
QListWidget#TaskList::item {{ border-radius: 8px; margin: 2px 6px; }}
QListWidget#TaskList::item:selected {{ background: {p['PANEL']}; border: 1px solid {p['BORDER']}; }}
QListWidget#TaskList::item:hover {{ background: {p['PANEL']}; }}
QSplitter::handle {{ background: {p['BORDER']}; width: 1px; }}
QStatusBar {{ background: {p['PANEL']}; border-top: 1px solid {p['BORDER']}; }}
QToolBar {{ background: {p['PANEL']}; border-bottom: 1px solid {p['BORDER']}; }}
QMenu {{ background: {p['PANEL']}; color: {p['TEXT']}; border: 1px solid {p['BORDER']}; }}
QMenu::item:selected {{ background: {p['ACCENT_SOFT']}; }}
QScrollBar:vertical {{ background: {p['PANEL']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p['BORDER']}; border-radius: 5px; min-height: 24px; }}
QDialog {{ background: {p['BG']}; }}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
    background: {p['PANEL']}; color: {p['TEXT']}; border: 1px solid {p['BORDER']};
    border-radius: 6px; padding: 3px 6px;
}}
"""


def apply_theme(app: QApplication, dark: bool = False) -> None:
    """Applique la palette (clair/sombre) et met à jour les constantes."""
    global _current_dark, BG, WINDOW, PANEL, PANEL_2, BORDER, TEXT, MUTED
    global ACCENT, ACCENT_SOFT, GREEN, GREEN_SOFT, AMBER, AMBER_SOFT
    global RED, RED_SOFT, CANCELLED, CANCELLED_SOFT

    palette = DARK if dark else LIGHT
    _current_dark = dark
    BG = palette["BG"]
    WINDOW = palette["WINDOW"]
    PANEL = palette["PANEL"]
    PANEL_2 = palette["PANEL_2"]
    BORDER = palette["BORDER"]
    TEXT = palette["TEXT"]
    MUTED = palette["MUTED"]
    ACCENT = palette["ACCENT"]
    ACCENT_SOFT = palette["ACCENT_SOFT"]
    GREEN = palette["GREEN"]
    GREEN_SOFT = palette["GREEN_SOFT"]
    AMBER = palette["AMBER"]
    AMBER_SOFT = palette["AMBER_SOFT"]
    RED = palette["RED"]
    RED_SOFT = palette["RED_SOFT"]
    CANCELLED = palette["CANCELLED"]
    CANCELLED_SOFT = palette["CANCELLED_SOFT"]
    app.setStyleSheet(build_stylesheet(palette))
