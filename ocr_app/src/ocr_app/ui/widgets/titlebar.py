"""Barre de titre / branding de la fenêtre AOCRI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ocr_app.ui.icons import app_icon_pixmap


class TitleBar(QWidget):
    """En-tête « AOCRI — Advanced Optical Character Recognition for Itamba »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(58)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        icon = QLabel(self)
        icon.setPixmap(app_icon_pixmap(28))
        icon.setFixedSize(28, 28)
        icon.setScaledContents(True)
        icon.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(0)

        title = QLabel(self)
        title.setObjectName("AppTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setText(
            '<span style="font-weight:600;">AOCRI</span> '
            '<span style="color:#68707d;">— Advanced Optical Character '
            "Recognition for Itamba</span>"
        )
        text_column.addWidget(title)

        subtitle = QLabel("Kriesty Solutions · hors ligne", self)
        subtitle.setObjectName("AppSubtitle")
        text_column.addWidget(subtitle)

        layout.addLayout(text_column)
        layout.addStretch(1)
