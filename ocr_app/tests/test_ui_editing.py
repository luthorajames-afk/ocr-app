"""Tests d'édition dans la fenêtre principale (phase 5 — R9), headless.

Le mode ``--demo`` charge la page facture en mémoire : on édite via
l'``EditSession`` de la fenêtre et on vérifie que les onglets « Markdown /
HTML » et « Texte brut » reflètent le nouveau texte, que le titre de la
fenêtre porte « ● » tant que la session est sale et qu'il disparaît après
réinitialisation, et que les fenêtres sont détruites proprement (aucun objet
Python zombie à la fin).

Tous les tests tournent hors écran : ``QT_QPA_PLATFORM=offscreen`` est posé
AVANT tout import de PySide6. Si les modules UI sont momentanément déplacés
(par un travail parallèle sur l'interface), le module entier est SKIPPED et
non FAILED.
"""

from __future__ import annotations

import gc
import os
import weakref

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 n'est pas installé (extra [ui])",
)
pytest.importorskip(
    "ocr_app.ui.main_window",
    reason="ocr_app.ui.main_window indisponible (phase 4/5)",
)

shiboken = pytest.importorskip(
    "shiboken6",
    reason="shiboken6 indisponible (livré avec PySide6)",
)

from ocr_app.core.data_model import Page
from ocr_app.ui.demo_data import DEMO_FILES

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit

# Page facture du mode démonstration (ocr_app.ui.demo_data.DEMO_FILES[0]).
DEMO_PAGE_NUMBER = 4
DEMO_TITLE_ID = "demo-title"
DEMO_TITLE_TEXT = "Facture n° 2026-041"
EDITED_TEXT = "Facture n° 9999-ÉDITION TEST"


@pytest.fixture(scope="module")
def qapp():
    """QApplication unique pour tout le module (politique Qt)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _drain_events(qapp) -> None:
    """Traite les événements en attente, y compris les ``deleteLater``."""
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _code_view(window):
    """Onglet « Markdown / HTML » de la fenêtre (QPlainTextEdit « CodeView »)."""
    return window._results.findChild(QPlainTextEdit, "CodeView")


def _plain_view(window):
    """Onglet « Texte brut » de la fenêtre (QPlainTextEdit « PlainView »)."""
    return window._results.findChild(QPlainTextEdit, "PlainView")


def _save_demo_pages() -> dict:
    """Copie profonde des pages de démo (objets partagés au niveau module)."""
    return {
        index: Page.from_dict(spec.page.to_dict())
        for index, spec in enumerate(DEMO_FILES)
        if spec.page is not None
    }


def _restore_demo_pages(saved: dict) -> None:
    """Restaure les pages de démo partagées (les éditions testées ne fuient
    pas vers la fenêtre suivante : les objets de ``DEMO_FILES`` sont des
    singletons de module mutés en place par ``load_demo``)."""
    for index, snapshot in saved.items():
        shared = DEMO_FILES[index].page
        shared.blocks = list(snapshot.blocks)
        shared.confidence = snapshot.confidence
        shared.source = snapshot.source


@pytest.fixture()
def demo_window(qapp):
    """MainWindow en mode --demo, fermée et détruite proprement.

    Les pages de démo sont des objets partagés entre fenêtres : elles sont
    sauvegardées avant le test et restaurées après, pour une isolation totale.
    """
    from ocr_app.ui.main_window import MainWindow

    saved = _save_demo_pages()
    window = MainWindow(demo=True)
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        _drain_events(qapp)
        _restore_demo_pages(saved)


# ---------------------------------------------------------------------------
# État initial du mode démo
# ---------------------------------------------------------------------------
def test_demo_window_loads_invoice_page(demo_window, qapp):
    session = demo_window._edit_session

    assert session.is_dirty() is False
    assert session.document is not None
    page = session.page(DEMO_PAGE_NUMBER)
    assert page is not None
    assert len(page.blocks) == 3
    assert page.blocks[0].block_id == DEMO_TITLE_ID
    assert page.blocks[0].text == DEMO_TITLE_TEXT

    qapp.processEvents()
    assert demo_window._results.has_page()
    assert DEMO_TITLE_TEXT in _code_view(demo_window).toPlainText()
    assert DEMO_TITLE_TEXT in _plain_view(demo_window).toPlainText()
    assert "●" not in demo_window.windowTitle()


# ---------------------------------------------------------------------------
# Édition via EditSession → rafraîchissement des panneaux
# ---------------------------------------------------------------------------
def test_edit_via_session_updates_markdown_and_text_panels(demo_window, qapp):
    session = demo_window._edit_session

    assert (
        session.update_block_text(
            DEMO_PAGE_NUMBER, DEMO_TITLE_ID, EDITED_TEXT
        )
        is True
    )
    qapp.processEvents()

    markdown = _code_view(demo_window).toPlainText()
    plain = _plain_view(demo_window).toPlainText()
    assert EDITED_TEXT in markdown
    assert EDITED_TEXT in plain
    assert DEMO_TITLE_TEXT not in markdown
    assert DEMO_TITLE_TEXT not in plain

    # Le modèle partagé est bien la source de vérité des vues.
    assert session.page(DEMO_PAGE_NUMBER).blocks[0].text == EDITED_TEXT
    assert demo_window._results.current_page().blocks[0].text == EDITED_TEXT


def test_edit_via_commit_signal_updates_panels(demo_window, qapp):
    """Le signal « block_text_committed » du panneau passe par la session."""
    window = demo_window
    # Bloc 0 = titre (block_identity → « demo-title »).
    window._results.block_text_committed.emit(0, "Titre édité via commit")
    qapp.processEvents()

    session = window._edit_session
    assert session.is_dirty() is True
    assert session.page(DEMO_PAGE_NUMBER).blocks[0].text == (
        "Titre édité via commit"
    )
    assert "Titre édité via commit" in _code_view(window).toPlainText()
    assert "Titre édité via commit" in _plain_view(window).toPlainText()
    assert "●" in window.windowTitle()


def test_undo_redo_through_window_restores_panels(demo_window, qapp):
    window = demo_window
    session = window._edit_session

    session.update_block_text(DEMO_PAGE_NUMBER, DEMO_TITLE_ID, EDITED_TEXT)
    assert "●" in window.windowTitle()

    window._undo_edits()
    qapp.processEvents()
    assert session.page(DEMO_PAGE_NUMBER).blocks[0].text == DEMO_TITLE_TEXT
    assert session.is_dirty() is False
    assert "●" not in window.windowTitle()
    assert EDITED_TEXT not in _code_view(window).toPlainText()
    assert DEMO_TITLE_TEXT in _plain_view(window).toPlainText()

    window._redo_edits()
    qapp.processEvents()
    assert session.page(DEMO_PAGE_NUMBER).blocks[0].text == EDITED_TEXT
    assert session.is_dirty() is True
    assert "●" in window.windowTitle()
    assert EDITED_TEXT in _plain_view(window).toPlainText()


# ---------------------------------------------------------------------------
# Titre « ● » et réinitialisation
# ---------------------------------------------------------------------------
def test_window_title_dirty_dot_cleared_after_reset(demo_window, qapp):
    window = demo_window
    session = window._edit_session

    assert "●" not in window.windowTitle()
    assert "AOCRI" in window.windowTitle()

    session.update_block_text(DEMO_PAGE_NUMBER, DEMO_TITLE_ID, EDITED_TEXT)
    assert session.is_dirty() is True
    assert "●" in window.windowTitle()
    assert "AOCRI" in window.windowTitle()

    # Réinitialisation via le bouton de la barre inférieure (mode démo :
    # restauration instantanée depuis l'instantané mémoire).
    window._reset_edits()
    qapp.processEvents()

    assert session.is_dirty() is False
    assert "●" not in window.windowTitle()
    assert session.page(DEMO_PAGE_NUMBER).blocks[0].text == DEMO_TITLE_TEXT
    assert DEMO_TITLE_TEXT in _code_view(window).toPlainText()
    assert EDITED_TEXT not in _plain_view(window).toPlainText()


def test_reset_without_edits_does_not_touch_title(demo_window):
    session = demo_window._edit_session
    assert session.is_dirty() is False
    demo_window._reset_edits()
    assert "●" not in demo_window.windowTitle()
    assert demo_window._results.has_page()  # la vue est intacte


# ---------------------------------------------------------------------------
# Statistiques de la barre inférieure
# ---------------------------------------------------------------------------
def test_bottom_bar_edit_stats_follow_edits(demo_window, qapp):
    session = demo_window._edit_session

    session.update_block_text(DEMO_PAGE_NUMBER, DEMO_TITLE_ID, EDITED_TEXT)
    session.update_block_text(
        DEMO_PAGE_NUMBER, "demo-body", "Bloc de corps modifié."
    )
    qapp.processEvents()

    stats = demo_window._bottom._edit_stats.text()
    assert "2 / 3" in stats  # 2 blocs modifiés sur 3

    session.reset()
    qapp.processEvents()
    assert not demo_window._bottom._edit_stats.isVisible()


# ---------------------------------------------------------------------------
# Cycle de vie : QApplication unique, aucune fenêtre zombie
# ---------------------------------------------------------------------------
def test_single_qapplication_shared(qapp):
    assert QApplication.instance() is qapp


def test_window_cleanup_leaves_no_python_zombie(qapp):
    """close + deleteLater + pompe d'événements → objet C++ détruit et
    wrapper Python collecté."""
    from ocr_app.ui.main_window import MainWindow

    window = MainWindow(demo=True)
    window.show()
    qapp.processEvents()
    reference = weakref.ref(window)

    window.close()
    window.deleteLater()
    _drain_events(qapp)

    assert not shiboken.isValid(window)
    del window
    gc.collect()
    assert reference() is None


def test_demo_windows_do_not_leak_between_instantiations(qapp):
    """Deux fenêtres successives coexistent, puis sont toutes deux nettoyées."""
    from ocr_app.ui.main_window import MainWindow

    first = MainWindow(demo=True)
    second = MainWindow(demo=True)
    qapp.processEvents()

    assert first._edit_session.document is not None
    assert second._edit_session.document is not None

    first.close()
    first.deleteLater()
    second.close()
    second.deleteLater()
    _drain_events(qapp)

    assert not shiboken.isValid(first)
    assert not shiboken.isValid(second)
