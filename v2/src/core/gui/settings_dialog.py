"""Réglages AOCRI v2 : échantillonnage (DPI), thème, langue, à propos."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.gui import theme
from core.metadata import APP_EDITOR, APP_NAME, APP_VERSION

DPI_CHOICES = (150, 200, 300)
LANGUAGES = (
    ("fra+eng", "Français + Anglais"),
    ("fra", "Français"),
    ("eng", "Anglais"),
)


class SettingsDialog(QDialog):
    """Regroupe l'échantillonnage, le thème, la langue et le « à propos »."""

    def __init__(
        self,
        *,
        dpi: int = 300,
        dark: bool = False,
        languages: str = "fra+eng",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Réglages — AOCRI")
        self.resize(460, 320)

        self.dpi_combo = QComboBox(self)
        for value in DPI_CHOICES:
            self.dpi_combo.addItem(f"{value} DPI", value)
        index = self.dpi_combo.findData(dpi)
        self.dpi_combo.setCurrentIndex(index if index >= 0 else len(DPI_CHOICES) - 1)

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem("Clair", False)
        self.theme_combo.addItem("Sombre", True)
        self.theme_combo.setCurrentIndex(1 if dark else 0)

        self.language_combo = QComboBox(self)
        for lang_value, lang_label in LANGUAGES:
            self.language_combo.addItem(lang_label, lang_value)
        language_index = int(self.language_combo.findData(languages))
        self.language_combo.setCurrentIndex(max(language_index, 0))

        about = QLabel(
            f"<b>{APP_NAME}</b> v{APP_VERSION} — {APP_EDITOR}<br>"
            "OCR hors ligne, conservation de la mise en page.<br>"
            "Noyau Tesseract · modules Moyen (RapidOCR) et Avancé (Docling) "
            "dans leurs environnements dédiés.",
            self,
        )
        about.setWordWrap(True)
        about.setObjectName("Muted")

        form = QFormLayout()
        form.addRow("Échantillonnage", self.dpi_combo)
        form.addRow("Thème", self.theme_combo)
        form.addRow("Langue du document", self.language_combo)
        form.addRow("À propos", about)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ---------------------------------------------------------------- valeurs
    @property
    def dpi(self) -> int:
        return int(self.dpi_combo.currentData())

    @property
    def dark(self) -> bool:
        return bool(self.theme_combo.currentData())

    @property
    def languages(self) -> str:
        return str(self.language_combo.currentData())

    def apply_theme_preview(self) -> None:
        """Aperçu immédiat du thème choisi (sans valider le dialogue)."""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply_theme(app, dark=self.dark)
