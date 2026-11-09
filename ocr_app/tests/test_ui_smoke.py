"""Smoke test de l'interface PySide6, en mode ``offscreen``.

Le module entier est SKIPPED (et non FAILED) tant que :

- PySide6 n'est pas installé (extra ``[ui]``) ;
- ``ocr_app.ui.main_window`` n'existe pas encore (branchement GUI phase 4).

Aucun calcul OCR n'est effectué : on vérifie seulement que la fenêtre
principale s'instancie et expose un titre contenant « AOCRI ».
"""

from __future__ import annotations

import os

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 n'est pas installé (extra [ui])",
)
pytest.importorskip(
    "ocr_app.ui.main_window",
    reason="ocr_app.ui.main_window n'existe pas encore (phase 4)",
)


@pytest.fixture(scope="module")
def qapp():
    """QApplication unique pour tout le module (politique Qt)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def main_window(qapp):
    """Instancie MainWindow et garantit un nettoyage propre."""
    from ocr_app.ui.main_window import MainWindow

    window = MainWindow()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_main_window_instantiates(main_window):
    assert main_window is not None
    assert isinstance(main_window.windowTitle(), str)


def test_main_window_title_contains_aocri(main_window):
    assert "AOCRI" in main_window.windowTitle()
