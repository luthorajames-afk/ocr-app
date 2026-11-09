"""Boîte de dialogue des réglages OCR (phase 8).

Langues OCR (cases à cocher, au moins une) et résolution de rendu des PDF
(DPI, 300 maximum — R7). Les réglages sont persistés par
``ocr_app.ui.app_settings`` (``%LOCALAPPDATA%\\AOCRI\\settings.json``) puis
passés aux workers (``--lang fra+eng``, ``--dpi N``) par le ``TaskRunner``.

La boîte reste testable hors écran (offscreen) : aucune opération bloquante,
les boutons annexes (dossier de travail, modules, à propos) émettent des
signaux traités par la fenêtre principale.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ocr_app import __version__
from ocr_app.ui import theme
from ocr_app.ui.app_settings import (
    AppSettings,
    AVAILABLE_LANGUAGES,
    DEFAULT_DPI,
    DPI_CHOICES,
    LANGUAGE_LABELS,
)


class SettingsDialog(QDialog):
    """Réglages OCR : langues Tesseract + DPI de rendu (défaut : 300)."""

    # Boutons annexes : la fenêtre principale fournit les actions réelles.
    work_dir_requested = Signal()
    modules_requested = Signal()
    about_requested = Signal()
    demo_requested = Signal()

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        parent: Optional[QWidget] = None,
        *,
        show_demo_entry: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Réglages OCR")
        self.setModal(True)
        self.setMinimumWidth(380)

        current = settings or AppSettings()
        selected_languages = set(current.clean_languages())
        if not selected_languages:
            selected_languages = {"fra", "eng"}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        # --- Langues OCR -------------------------------------------------
        language_box = QGroupBox("Langues OCR (au moins une)", self)
        language_layout = QVBoxLayout(language_box)
        language_layout.setContentsMargins(12, 6, 12, 8)
        language_layout.setSpacing(6)
        self._language_checks: dict[str, QCheckBox] = {}
        for code in AVAILABLE_LANGUAGES:
            check = QCheckBox(LANGUAGE_LABELS.get(code, code), language_box)
            check.setChecked(code in selected_languages)
            check.toggled.connect(self._on_language_toggled)
            language_layout.addWidget(check)
            self._language_checks[code] = check
        layout.addWidget(language_box)

        # --- DPI ----------------------------------------------------------
        dpi_box = QGroupBox("Résolution des PDF (DPI)", self)
        dpi_layout = QHBoxLayout(dpi_box)
        dpi_layout.setContentsMargins(12, 6, 12, 8)
        dpi_layout.setSpacing(14)
        self._dpi_buttons: dict[int, QRadioButton] = {}
        effective_dpi = current.effective_dpi()
        if effective_dpi not in DPI_CHOICES:
            effective_dpi = DEFAULT_DPI
        for choice in DPI_CHOICES:
            radio = QRadioButton(str(choice), dpi_box)
            radio.setChecked(choice == effective_dpi)
            dpi_layout.addWidget(radio)
            self._dpi_buttons[choice] = radio
        dpi_layout.addStretch(1)
        layout.addWidget(dpi_box)

        hint = QLabel("R7 : la résolution est limitée à 300 DPI.", self)
        hint.setObjectName("SettingsHint")
        hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch(1)

        # --- Boutons annexes (dossier, modules, à propos, démo) -----------
        extras = QWidget(self)
        extras_layout = QHBoxLayout(extras)
        extras_layout.setContentsMargins(0, 0, 0, 0)
        extras_layout.setSpacing(8)
        self._add_extra_button(extras_layout, "Dossier de travail", self.work_dir_requested)
        self._add_extra_button(extras_layout, "Modules OCR…", self.modules_requested)
        if show_demo_entry:
            self._add_extra_button(
                extras_layout, "Aperçu de démonstration", self.demo_requested
            )
        self._add_extra_button(extras_layout, "À propos", self.about_requested)
        extras_layout.addStretch(1)
        layout.addWidget(extras)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {theme.BORDER};")
        layout.addWidget(line)

        # --- OK / Annuler -------------------------------------------------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._ok_button = QPushButton("OK", self)
        self._ok_button.setObjectName("StatusPrimary")
        self._ok_button.setDefault(True)
        self._ok_button.clicked.connect(self.accept)
        cancel = QPushButton("Annuler", self)
        cancel.setObjectName("StatusButton")
        cancel.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self._ok_button)
        layout.addLayout(buttons)

        version = QLabel(f"AOCRI v1.0 ({__version__}) — Kriesty Solutions", self)
        version.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(version)

        self._refresh_ok_enabled()

    # --- Construction -----------------------------------------------------
    @staticmethod
    def _add_extra_button(layout: QHBoxLayout, text: str, signal: Signal) -> None:
        button = QPushButton(text)
        button.setObjectName("StatusButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(signal.emit)
        layout.addWidget(button)

    # --- Validation -------------------------------------------------------
    def _selected_languages(self) -> List[str]:
        return [
            code
            for code, check in self._language_checks.items()
            if check.isChecked()
        ]

    def _selected_dpi(self) -> int:
        for choice, radio in self._dpi_buttons.items():
            if radio.isChecked():
                return choice
        return DEFAULT_DPI

    def _on_language_toggled(self) -> None:
        self._refresh_ok_enabled()

    def _refresh_ok_enabled(self) -> None:
        # « au moins une » langue OCR : OK reste désactivé sinon.
        self._ok_button.setEnabled(bool(self._selected_languages()))

    # --- Résultat ---------------------------------------------------------
    def result_settings(self) -> AppSettings:
        """Réglages choisis (validés : au moins une langue, DPI ≤ 300)."""
        return AppSettings(
            languages=self._selected_languages(),
            dpi=self._selected_dpi(),
        )
