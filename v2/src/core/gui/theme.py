"""Palette de l'interface AOCRI v2 (identique à la maquette validée)."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

BG = "#eef0f4"
WINDOW = "#f7f8fa"
PANEL = "#ffffff"
PANEL_2 = "#f2f4f7"
BORDER = "#dfe3ea"
TEXT = "#1a1d24"
MUTED = "#68707d"
ACCENT = "#2f5bd7"
ACCENT_SOFT = "#eef2fb"
GREEN = "#1f7a4d"
GREEN_SOFT = "#e6f2eb"
AMBER = "#946200"
AMBER_SOFT = "#f7f0e0"
RED = "#b3261e"
RED_SOFT = "#fbecea"
PAPER = "#ffffff"
PAPER_INK = "#22252b"
FONT_FAMILY = "Segoe UI"

STYLESHEET = f"""
QWidget {{ font-family: "{FONT_FAMILY}"; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QFrame#Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#Muted {{ color: {MUTED}; font-size: 11.5px; }}
QLabel#Title {{ font-weight: 600; }}
QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 5px 12px; font-size: 12.5px; font-weight: 500;
}}
QPushButton:hover {{ background: {PANEL_2}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton#Mode {{ border: 0; background: transparent; color: {MUTED}; padding: 5px 12px; }}
QPushButton#Mode:checked {{ background: {PANEL}; color: {TEXT}; border-radius: 6px; }}
QFrame#ModeGroup {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 9px; }}
QListWidget {{ background: transparent; border: 0; }}
QListWidget::item {{ border-radius: 8px; margin: 2px 0; }}
QListWidget::item:selected {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QProgressBar {{
    background: {PANEL_2}; border: 0; border-radius: 3px; height: 6px; text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; }}
QTabBar::tab {{ background: {PANEL}; color: {MUTED}; padding: 8px 12px; border: 0; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTextBrowser, QPlainTextEdit {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 7px; }}
QGraphicsView {{ background: {PANEL_2}; border: 0; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyleSheet(STYLESHEET)
