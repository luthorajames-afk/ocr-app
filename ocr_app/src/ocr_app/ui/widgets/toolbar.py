"""Barre d'outils : modes Simple / Moyen / Avancé, ajout de fichiers, réglages."""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ocr_app.ui import theme
from ocr_app.ui.icons import make_icon

MODES = (
    ("simple", "Simple"),
    ("medium", "Moyen"),
    ("advanced", "Avancé"),
    ("approfondi", "Approfondi"),
)

# Tooltips par mode (disponibilité gérée par set_mode_enabled).
_MODE_TOOLTIPS = {
    "advanced": "Mode Avancé — module Docling non installé dans cette version",
    "approfondi": "Analyse approfondie — filtres et zones d'exclusion",
}


class ModeSelector(QWidget):
    """Sélecteur de mode segmenté (Simple / Moyen / Avancé)."""

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModeSelector")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[int, QPushButton] = {}
        self._ids: Dict[str, int] = {}

        for index, (mode, label) in enumerate(MODES):
            button = QPushButton(label, self)
            button.setObjectName("ModeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if mode in _MODE_TOOLTIPS:
                button.setToolTip(_MODE_TOOLTIPS[mode])
            self._group.addButton(button, index)
            self._buttons[index] = button
            self._ids[mode] = index
            layout.addWidget(button)

        self._buttons[self._ids["simple"]].setChecked(True)
        self._group.idClicked.connect(self._on_clicked)

    def _on_clicked(self, button_id: int) -> None:
        for mode, index in self._ids.items():
            if index == button_id:
                self.mode_changed.emit(mode)
                return

    def current_mode(self) -> str:
        for mode, index in self._ids.items():
            if self._buttons[index].isChecked():
                return mode
        return "simple"

    def set_mode(self, mode: str) -> None:
        index = self._ids.get(mode)
        if index is not None:
            self._buttons[index].setChecked(True)

    def set_mode_enabled(
        self, mode: str, enabled: bool, tooltip: str | None = None
    ) -> None:
        """Active/grise un mode (ex. Approfondi sans backend Avancé).

        Un tooltip est obligatoire avec l'état grisé : sans lui, Qt
        n'explique pas pourquoi le bouton est inactif.
        """
        button = self._buttons.get(self._ids.get(mode, -1))
        if button is None:
            return
        button.setEnabled(bool(enabled))
        if tooltip is not None:
            button.setToolTip(tooltip)
        elif enabled:
            default = _MODE_TOOLTIPS.get(mode)
            if default is not None:
                button.setToolTip(default)


class AppToolbar(QWidget):
    """Barre d'outils principale de l'application."""

    add_files_requested = Signal()
    add_folder_requested = Signal()
    settings_requested = Signal()
    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(10)

        self.mode_selector = ModeSelector(self)
        self.mode_selector.mode_changed.connect(self.mode_changed)
        layout.addWidget(self.mode_selector, 0, Qt.AlignmentFlag.AlignVCenter)

        add_button = QPushButton("Ajouter des fichiers", self)
        add_button.setObjectName("ToolButton")
        add_button.setIcon(make_icon("plus", theme.TEXT, 16))
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.setToolTip("Ajouter des PDF, JPG ou PNG (Ctrl+O)")
        add_button.setAccessibleName("Ajouter des fichiers")
        add_button.clicked.connect(self.add_files_requested)
        layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # Phase 8 : traitement par lots — tous les PDF/JPG/PNG d'un dossier.
        add_folder_button = QPushButton("Ajouter un dossier", self)
        add_folder_button.setObjectName("ToolButton")
        add_folder_button.setIcon(make_icon("folder", theme.TEXT, 16))
        add_folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_folder_button.setToolTip(
            "Ajouter tous les PDF, JPG et PNG d'un dossier (lot séquentiel)"
        )
        add_folder_button.setAccessibleName("Ajouter un dossier")
        add_folder_button.clicked.connect(self.add_folder_requested)
        layout.addWidget(add_folder_button, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch(1)

        language = QWidget(self)
        language_layout = QHBoxLayout(language)
        language_layout.setContentsMargins(0, 0, 0, 0)
        language_layout.setSpacing(6)
        globe = QLabel(language)
        globe.setPixmap(make_icon("globe", theme.MUTED, 14).pixmap(14, 14))
        globe.setFixedSize(14, 14)
        language_layout.addWidget(globe)
        self._language_text = QLabel("FR + EN · 300 DPI", language)
        self._language_text.setObjectName("LangLabel")
        language_layout.addWidget(self._language_text)
        layout.addWidget(language, 0, Qt.AlignmentFlag.AlignVCenter)

        settings_button = QPushButton(self)
        settings_button.setObjectName("IconButton")
        settings_button.setIcon(make_icon("settings", theme.MUTED, 17))
        settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_button.setToolTip("Réglages OCR (langues, DPI)")
        settings_button.setAccessibleName("Réglages")
        settings_button.clicked.connect(self.settings_requested)
        layout.addWidget(settings_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def current_mode(self) -> str:
        return self.mode_selector.current_mode()

    def set_mode(self, mode: str) -> None:
        self.mode_selector.set_mode(mode)

    def set_mode_enabled(
        self, mode: str, enabled: bool, tooltip: str | None = None
    ) -> None:
        """Active/grise un mode (ex. Approfondi sans backend Avancé)."""
        self.mode_selector.set_mode_enabled(mode, enabled, tooltip=tooltip)

    def set_settings_indicator(self, text: str) -> None:
        """Indicateur dynamique « FR + EN · 300 DPI » (réglages, phase 8)."""
        cleaned = (text or "").strip()
        if cleaned:
            self._language_text.setText(cleaned)
