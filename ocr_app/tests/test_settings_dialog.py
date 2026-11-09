"""Tests du dialogue de réglages OCR (phase 8) — interface hors écran.

Couvre ``ocr_app.ui.settings_dialog.SettingsDialog`` :

- « au moins une » langue : bouton OK désactivé sans langue, réactivé dès
  qu'une langue est cochée ;
- DPI par boutons radio bornés aux paliers 150/200/300 (R7) — une valeur
  hors palier (600) recharge la borne 300, jamais 600 ;
- ``result_settings()`` reflète la sélection (langues dans l'ordre canonique) ;
- intégration fenêtre : au OK, les réglages sont persistés dans
  ``%LOCALAPPDATA%\\AOCRI\\settings.json`` (surchargé en %TEMP% pour les
  tests), appliqués aux workers et affichés dans l'indicateur de la barre
  d'outils ; à Annuler, rien n'est persisté.

Tous les tests sont headless (``QT_QPA_PLATFORM=offscreen``), une seule
QApplication par module.
"""

from __future__ import annotations

import os
from pathlib import Path

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 n'est pas installé (extra [ui])")
pytest.importorskip(
    "ocr_app.ui.settings_dialog", reason="settings_dialog absent (phase 8)"
)

from ocr_app.ui.app_settings import (
    AppSettings,
    load_settings,
    settings_summary,
)
from ocr_app.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def qapp():
    """QApplication unique pour tout le module (politique Qt)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch):
    """Réglages et dossiers de travail redirigés vers %TEMP% du test."""
    monkeypatch.setenv("AOCRI_SETTINGS_DIR", str(tmp_path / "réglages éàç"))
    monkeypatch.setenv("AOCRI_WORK_DIR", str(tmp_path / "ocr_work éàç"))
    return tmp_path


def _click_ok(dialog: SettingsDialog) -> None:
    dialog._ok_button.click()


# --- Langues : « au moins une » ------------------------------------------------

def test_ok_disabled_without_any_language(qapp) -> None:
    dialog = SettingsDialog(AppSettings(languages=["fra"], dpi=300))
    try:
        assert dialog._ok_button.isEnabled(), "une langue cochée : OK actif"

        dialog._language_checks["fra"].setChecked(False)
        assert not dialog._ok_button.isEnabled(), (
            "OK doit être désactivé sans aucune langue"
        )

        dialog._language_checks["eng"].setChecked(True)
        assert dialog._ok_button.isEnabled(), "une langue suffit pour OK"

        dialog._language_checks["eng"].setChecked(False)
        assert not dialog._ok_button.isEnabled()
        assert dialog.result_settings().languages == []
    finally:
        dialog.deleteLater()


def test_default_settings_leave_ok_enabled(qapp) -> None:
    dialog = SettingsDialog()  # défauts : fra+eng cochés
    try:
        assert dialog._language_checks["fra"].isChecked()
        assert dialog._language_checks["eng"].isChecked()
        assert dialog._ok_button.isEnabled()
        result = dialog.result_settings()
        assert result.languages == ["fra", "eng"]
        assert result.dpi == 300
    finally:
        dialog.deleteLater()


def test_empty_incoming_settings_fall_back_to_both_boxes(qapp) -> None:
    """Des réglages entrants sans langue ne verrouillent pas le dialogue."""
    dialog = SettingsDialog(AppSettings(languages=[], dpi=150))
    try:
        assert dialog._language_checks["fra"].isChecked()
        assert dialog._language_checks["eng"].isChecked()
        assert dialog._ok_button.isEnabled()
    finally:
        dialog.deleteLater()


# --- DPI : radios bornées (R7) --------------------------------------------------

def test_dpi_radios_bounded_to_choices(qapp) -> None:
    from PySide6.QtWidgets import QRadioButton

    dialog = SettingsDialog(AppSettings(languages=["fra"], dpi=600))
    try:
        radios = dialog.findChildren(QRadioButton)
        assert sorted(int(radio.text()) for radio in radios) == [150, 200, 300], (
            "seuls les paliers 150/200/300 sont proposés (R7)"
        )
        # 600 est hors palier : le dialogue recharge la borne 300.
        assert dialog._dpi_buttons[300].isChecked()
        assert dialog.result_settings().dpi == 300

        dialog._dpi_buttons[150].setChecked(True)
        assert dialog.result_settings().dpi == 150
    finally:
        dialog.deleteLater()


def test_dpi_radio_single_choice_at_a_time(qapp) -> None:
    dialog = SettingsDialog(AppSettings(languages=["eng"], dpi=200))
    try:
        assert dialog._dpi_buttons[200].isChecked()
        dialog._dpi_buttons[300].setChecked(True)
        assert not dialog._dpi_buttons[200].isChecked()
        assert dialog.result_settings().dpi == 300
    finally:
        dialog.deleteLater()


# --- Sélection complète ---------------------------------------------------------

def test_selection_reflected_in_result_settings(qapp) -> None:
    dialog = SettingsDialog(AppSettings(languages=["eng"], dpi=150))
    try:
        dialog._language_checks["fra"].setChecked(True)  # eng déjà cochée
        dialog._dpi_buttons[200].setChecked(True)
        result = dialog.result_settings()
        assert result.languages == ["fra", "eng"], "ordre canonique"
        assert result.dpi == 200
        assert result.language_string() == "fra+eng"
    finally:
        dialog.deleteLater()


def test_ok_button_accepts_dialog(qapp) -> None:
    from PySide6.QtWidgets import QDialog

    dialog = SettingsDialog(AppSettings(languages=["eng"], dpi=300))
    try:
        _click_ok(dialog)
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


# --- Intégration fenêtre : persistance et application au OK ---------------------

def test_ok_persists_and_applies_settings(qapp, isolated_env, monkeypatch) -> None:
    """Au OK : réglages sauvés dans le JSON, appliqués aux workers + indicateur."""
    from PySide6.QtWidgets import QDialog

    from ocr_app.ui.main_window import MainWindow

    def fake_exec(dialog_self) -> int:
        # Simule l'utilisateur : Anglais décoché, 150 DPI choisi.
        dialog_self._language_checks["eng"].setChecked(False)
        dialog_self._dpi_buttons[150].setChecked(True)
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    window = MainWindow()
    try:
        qapp.processEvents()
        window._open_settings()
        qapp.processEvents()

        # Réglages mis à jour en mémoire…
        assert window._settings.languages == ["fra"]
        assert window._settings.dpi == 150

        # …persistés dans le dossier surchargé…
        saved = load_settings()
        assert saved.languages == ["fra"] and saved.dpi == 150

        # …appliqués aux workers (prochain --lang/--dpi)…
        assert window._runner._settings_args == ("--lang", "fra", "--dpi", "150")

        # …et affichés dans l'indicateur de la barre d'outils.
        summary = settings_summary(window._settings)
        assert summary == "FR · 150 DPI"
        assert window._toolbar._language_text.text() == summary
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_cancel_keeps_previous_settings(qapp, isolated_env, monkeypatch) -> None:
    """À Annuler : réglages inchangés, aucun fichier écrit."""
    from PySide6.QtWidgets import QDialog

    from ocr_app.ui.main_window import MainWindow

    def fake_exec(dialog_self) -> int:
        # L'utilisateur modifie puis abandonne.
        dialog_self._language_checks["eng"].setChecked(False)
        dialog_self._dpi_buttons[150].setChecked(True)
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    window = MainWindow()
    try:
        qapp.processEvents()
        window._open_settings()
        qapp.processEvents()

        assert window._settings.languages == ["fra", "eng"]
        assert window._settings.dpi == 300
        settings_target = isolated_env / "réglages éàç"
        assert not (settings_target / "settings.json").exists(), (
            "Annuler ne doit rien persister"
        )
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
