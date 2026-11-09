"""Thème visuel de l'interface AOCRI (reprise du mockup validé).

Les couleurs et les métriques proviennent du mockup HTML validé par le client.
Le thème est appliqué via une feuille de style Qt (QSS) ; aucun calcul d'OCR
n'est effectué ici.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - uniquement pour les type hints
    from PySide6.QtWidgets import QApplication, QWidget


# --- Palette (mode clair du mockup) -----------------------------------------
BG = "#eef0f4"
WINDOW = "#f7f8fa"
PANEL = "#ffffff"
PANEL_2 = "#f2f4f7"
BORDER = "#dfe3ea"
TEXT = "#1a1d24"
MUTED = "#68707d"
ACCENT = "#2f5bd7"
ACCENT_2 = "#7aa2ff"
ACCENT_SOFT = "#eef2fb"
GREEN = "#1f7a4d"
GREEN_SOFT = "#e6f2eb"
AMBER = "#946200"
AMBER_SOFT = "#f7f0e0"
BLUE = "#1d6fb8"
BLUE_SOFT = "#e6eff9"
RED = "#b3261e"
RED_SOFT = "#fbecea"
CANCELLED = "#6b7280"
CANCELLED_SOFT = "#eceef2"

# Couleurs utilisées par les vues graphiques (QPainter).
PAPER = "#ffffff"
PAPER_INK = "#22252b"
PAPER_LINE = "#d7dbe2"
PAPER_TABLE_BORDER = "#c3c8d2"
PAPER_TABLE_HEAD = "#eceef2"

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Cascadia Mono, Consolas, monospace"


STYLESHEET = """
QWidget {
    color: #1a1d24;
}
QMainWindow, QWidget#Root {
    background: #eef0f4;
}

/* --- Barre de titre / branding --------------------------------------- */
QWidget#TitleBar {
    background: #ffffff;
    border-bottom: 1px solid #dfe3ea;
}
QLabel#AppTitle {
    font-size: 13px;
}
QLabel#AppSubtitle {
    color: #68707d;
    font-size: 11.5px;
}

/* --- Barre d'outils --------------------------------------------------- */
QWidget#Toolbar {
    background: #ffffff;
    border-bottom: 1px solid #dfe3ea;
}
QPushButton#ToolButton, QPushButton#IconButton {
    background: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 7px;
    color: #1a1d24;
    font-size: 12.5px;
    font-weight: 500;
    padding: 0 12px;
    min-height: 30px;
}
QPushButton#ToolButton:hover, QPushButton#IconButton:hover {
    background: #f2f4f7;
}
QPushButton#ToolButton:pressed, QPushButton#IconButton:pressed {
    background: #e9ecf1;
}
QPushButton#ToolButton:disabled, QPushButton#IconButton:disabled {
    color: #a4aab4;
}
QPushButton#IconButton {
    padding: 0;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
}
QWidget#ModeSelector {
    background: #f2f4f7;
    border: 1px solid #dfe3ea;
    border-radius: 9px;
}
QPushButton#ModeButton {
    background: transparent;
    border: 0;
    border-radius: 6px;
    color: #68707d;
    font-size: 12.5px;
    font-weight: 500;
    padding: 5px 13px;
}
QPushButton#ModeButton:hover {
    color: #1a1d24;
}
QPushButton#ModeButton:checked {
    background: #ffffff;
    border: 1px solid #e2e6ee;
    color: #1a1d24;
}
QLabel#LangLabel {
    color: #68707d;
    font-size: 12px;
}

/* --- Panneau latéral -------------------------------------------------- */
QWidget#Sidebar {
    background: #f7f8fa;
    border-right: 1px solid #dfe3ea;
}
QLabel#SectionTitle {
    color: #68707d;
    font-size: 11px;
    font-weight: 600;
}
QLabel#SectionCount {
    color: #68707d;
    font-size: 11px;
}
QScrollArea#FileScroll, QWidget#FileListContainer {
    background: transparent;
    border: none;
}
QWidget#FileRow {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
}
QWidget#FileRow:hover {
    background: #ffffff;
}
QWidget#FileRow[selected="true"] {
    background: #ffffff;
    border: 1px solid #dfe3ea;
}
QLabel#FileName {
    font-size: 12.5px;
    font-weight: 500;
}
QLabel#FileMeta {
    color: #68707d;
    font-size: 11px;
}
QLabel#StatusBadge {
    border-radius: 9px;
    padding: 1px 7px;
    font-size: 10.5px;
    font-weight: 500;
}
QLabel#StatusBadge[kind="run"] {
    background: #e6eff9;
    color: #1d6fb8;
}
QLabel#StatusBadge[kind="done"] {
    background: #e6f2eb;
    color: #1f7a4d;
}
QLabel#StatusBadge[kind="wait"] {
    background: #f7f0e0;
    color: #946200;
}
QLabel#StatusBadge[kind="error"] {
    background: #fbecea;
    color: #b3261e;
}
QLabel#StatusBadge[kind="cancelled"] {
    background: #eceef2;
    color: #6b7280;
}
QLabel#ModuleLabel {
    color: #68707d;
    font-size: 11.5px;
}
QLabel#FileEmptyHint {
    color: #9aa1ad;
    font-size: 11.5px;
}

/* --- Aperçu de la page ------------------------------------------------ */
QWidget#PreviewPanel {
    background: #f2f4f7;
}
QWidget#PreviewHead {
    background: #f7f8fa;
    border-bottom: 1px solid #dfe3ea;
}
QLabel#PreviewTitle {
    font-size: 13px;
    font-weight: 600;
}
QLabel#PreviewInfo {
    color: #68707d;
    font-size: 11.5px;
}
QPushButton#ZoomButton {
    background: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 7px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
}
QPushButton#ZoomButton:hover {
    background: #f2f4f7;
}
QLabel#ZoomLabel {
    color: #68707d;
    font-size: 11.5px;
}
QGraphicsView#PreviewView {
    background: #f2f4f7;
    border: none;
}

/* --- Panneau de résultats --------------------------------------------- */
QWidget#ResultsPanel {
    background: #f7f8fa;
    border-left: 1px solid #dfe3ea;
}
QTabWidget#ResultsTabs {
    background: #ffffff;
}
QTabWidget#ResultsTabs::pane {
    border: none;
    background: #ffffff;
}
QTabWidget#ResultsTabs QTabBar {
    background: #ffffff;
}
QTabWidget#ResultsTabs QTabBar::tab {
    background: #ffffff;
    border: 0;
    border-bottom: 2px solid transparent;
    color: #68707d;
    font-size: 12px;
    font-weight: 500;
    padding: 8px 11px 9px;
}
QTabWidget#ResultsTabs QTabBar::tab:hover {
    color: #1a1d24;
}
QTabWidget#ResultsTabs QTabBar::tab:selected {
    color: #2f5bd7;
    border-bottom: 2px solid #2f5bd7;
}
QWidget#ResultsPane, QWidget#CodePane {
    background: #ffffff;
}
QTextBrowser#RenderedView {
    background: #ffffff;
    border: none;
}
QPlainTextEdit#CodeView, QPlainTextEdit#PlainView {
    background: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 7px;
    selection-background-color: #dbe5fb;
}
QPlainTextEdit#CodeView {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11.5px;
}
QPlainTextEdit#PlainView {
    font-size: 12px;
}
QPushButton#SubModeButton {
    background: transparent;
    border: 0;
    border-radius: 5px;
    color: #68707d;
    font-size: 11.5px;
    font-weight: 500;
    padding: 3px 9px;
}
QPushButton#SubModeButton:hover {
    color: #1a1d24;
}
QPushButton#SubModeButton:checked {
    background: #eef2fb;
    color: #2f5bd7;
}

/* --- Barre inférieure -------------------------------------------------- */
QWidget#BottomBar {
    background: #ffffff;
    border-top: 1px solid #dfe3ea;
}
QLabel#ProgressText {
    color: #68707d;
    font-size: 11px;
}
QLabel#ProgressPercent {
    color: #68707d;
    font-size: 11px;
}
QProgressBar#Progress {
    background: #f2f4f7;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
}
QProgressBar#Progress::chunk {
    background: #2f5bd7;
    border-radius: 3px;
}
QPushButton#StatusButton, QPushButton#StatusPrimary {
    background: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 7px;
    color: #1a1d24;
    font-size: 12.5px;
    font-weight: 500;
    padding: 0 12px;
    min-height: 30px;
}
QPushButton#StatusButton:hover {
    background: #f2f4f7;
}
QPushButton#StatusButton:disabled, QPushButton#StatusPrimary:disabled {
    color: #a4aab4;
}
QPushButton#StatusButton[iconOnly="true"] {
    min-width: 34px;
    max-width: 34px;
    padding: 0;
}
QPushButton#StatusPrimary {
    background: #2f5bd7;
    border: 1px solid #2f5bd7;
    color: #ffffff;
}
QPushButton#StatusPrimary:hover {
    background: #3a66df;
}
QPushButton#StatusPrimary:disabled {
    background: #f2f4f7;
    border: 1px solid #dfe3ea;
    color: #a4aab4;
}
QLabel#Toast {
    color: #1f7a4d;
    font-size: 11.5px;
    padding: 0 4px;
}
QLabel#Toast[error="true"] {
    color: #b3261e;
}

/* --- Divers ----------------------------------------------------------- */
QMenu {
    background: #ffffff;
    border: 1px solid #dfe3ea;
    padding: 5px;
}
QMenu::item {
    background: transparent;
    border-radius: 5px;
    font-size: 12.5px;
    padding: 6px 22px 6px 12px;
}
QMenu::item:selected {
    background: #eef2fb;
    color: #1a1d24;
}
QMenu::separator {
    background: #dfe3ea;
    height: 1px;
    margin: 4px 6px;
}
QToolTip {
    background: #1f2430;
    color: #ffffff;
    border: none;
    padding: 5px 7px;
    font-size: 11.5px;
}
QSplitter::handle {
    background: #dfe3ea;
}
QSplitter::handle:horizontal {
    width: 1px;
}
"""


def apply_theme(app: "QApplication") -> None:
    """Applique la police, la palette et la feuille de style à l'application."""
    from PySide6.QtGui import QColor, QFont, QPalette

    app.setStyle("Fusion")

    font = QFont(FONT_FAMILY, 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    palette = app.palette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(PANEL))
    palette.setColor(QPalette.AlternateBase, QColor(PANEL_2))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(PANEL))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipBase, QColor("#1f2430"))
    palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)


def repolish(widget: "QWidget") -> None:
    """Force le recalcul du style après un changement de propriété dynamique."""
    try:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
    except RuntimeError:  # widget déjà détruit
        pass
