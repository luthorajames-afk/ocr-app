"""Comparaison côte à côte de deux pages (paires similaires/doublons).

Affiche les images ``page_NNN.png`` produites par les workers, côte à côte,
avec les extraits texte correspondants. Lecture seule : aucune ré-OCR, la
fenêtre fonctionne même si les documents ne sont pas chargés dans l'application.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.gui import theme


def _page_text(work_dir: str | Path, page: int) -> str:
    path = Path(work_dir) / f"page_{page:03d}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(record.get("text", ""))


def _page_png(work_dir: str | Path, page: int) -> QPixmap | None:
    path = Path(work_dir) / f"page_{page:03d}.png"
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    return pixmap if not pixmap.isNull() else None


class PairComparisonWindow(QDialog):
    """Deux pages côte à côte : image + texte, pour juger une paire."""

    def __init__(
        self,
        doc_a: str,
        page_a: int,
        work_a: str,
        doc_b: str,
        page_b: int,
        work_b: str,
        score: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AOCRI — Comparaison côte à côte")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.resize(1280, 860)

        head = QLabel(
            f"Similarité <b>{score:.3f}</b> · {Path(doc_a).name} p.{page_a}"
            f" &nbsp;↔&nbsp; {Path(doc_b).name} p.{page_b}",
            self,
        )
        head.setObjectName("Muted")

        self.view_a = self._build_pane(doc_a, page_a, work_a)
        self.view_b = self._build_pane(doc_b, page_b, work_b)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.addWidget(self.view_a)
        split.addWidget(self.view_b)
        split.setSizes([640, 640])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(head)
        layout.addWidget(split, 1)

    def _build_pane(self, source: str, page: int, work_dir: str) -> QScrollArea:
        pane = QWidget(self)
        label = QLabel(
            f"<b>{html.escape(Path(source).name)}</b> — page {page}", pane
        )
        label.setTextFormat(Qt.TextFormat.RichText)
        image = QLabel(pane)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = _page_png(work_dir, page)
        if pixmap is not None:
            scaled = pixmap.scaled(
                620,
                820,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            image.setPixmap(scaled)
        else:
            image.setText("[image de page indisponible]")
        text = QTextBrowser(pane)
        text.setOpenExternalLinks(False)
        text.setHtml(
            f"<div style='white-space:pre-wrap'>{html.escape(_page_text(work_dir, page))}</div>"
        )
        text.setMaximumHeight(220)
        text.setStyleSheet(
            f"QTextBrowser {{ background: {theme.PANEL_2}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        inner = QVBoxLayout(pane)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.addWidget(label)
        inner.addWidget(image, 1)
        inner.addWidget(text)
        scroll = QScrollArea(self)
        scroll.setWidget(pane)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; }}"
        )
        return scroll
