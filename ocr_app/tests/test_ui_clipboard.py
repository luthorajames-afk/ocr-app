"""Tests du presse-papiers Qt (phase 6), headless offscreen.

Vérifie, hors écran (``QT_QPA_PLATFORM=offscreen`` posé AVANT tout import
PySide6) :

- ``build_mime_data`` : un ``QMimeData`` portant ``text/plain`` +
  ``text/html`` + ``text/markdown`` (``hasFormat`` / contenus exacts) ;
- ``put_on_clipboard`` : chemin complet (``setMimeData`` avec les trois
  formats) vérifié via un presse-papiers factice, et repli documenté sur
  texte brut quand la plateforme est ``offscreen`` (le presse-papiers Qt
  hors écran plante à la destruction si on lui confie un ``QMimeData``) ;
- ``MainWindow`` (mode démo) : ``_copy_page`` / ``_copy_document`` posent le
  texte de la page/du document sur le presse-papiers de l'application ;
  ``_export_document`` écrit le fichier à l'emplacement fourni par la
  boîte de dialogue (simulée par monkeypatch de ``ask_export_path``),
  n'écrit rien si l'utilisateur annule et affiche l'avertissement en cas
  d'échec d'écriture ;
- cycle de vie : QApplication unique, aucune fenêtre zombie.

Les modules UI manquants ou PySide6 absent → module SKIPPED, pas FAILED.
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
    reason="ocr_app.ui.main_window indisponible",
)
pytest.importorskip(
    "ocr_app.ui.clipboard_qt",
    reason="ocr_app.ui.clipboard_qt indisponible (phase 6)",
)

from ocr_app.core.clipboard import document_payload, page_payload
from ocr_app.core.exports import page_plain_text
from ocr_app.ui import clipboard_qt, main_window as main_window_module
from ocr_app.ui.clipboard_qt import MARKDOWN_MIME_TYPE, build_mime_data
from ocr_app.ui.clipboard_qt import put_on_clipboard
from ocr_app.ui.demo_data import invoice_page
from ocr_app.ui.export_dialog import format_by_key

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


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


@pytest.fixture()
def demo_window(qapp):
    """MainWindow en mode démo, fermée et détruite proprement."""
    from ocr_app.ui.main_window import MainWindow

    window = MainWindow(demo=True)
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        _drain_events(qapp)


@pytest.fixture()
def demo_document(demo_window):
    """Document courant de la fenêtre démo (page facture)."""
    document = demo_window._current_document()
    assert document is not None and document.pages
    return document


# ---------------------------------------------------------------------------
# build_mime_data : les trois formats
# ---------------------------------------------------------------------------
def test_build_mime_data_carries_three_formats(qapp):
    payload = page_payload(invoice_page())
    mime = build_mime_data(payload)
    try:
        assert mime.hasFormat("text/plain")
        assert mime.hasFormat("text/html")
        assert mime.hasFormat(MARKDOWN_MIME_TYPE)
        assert set(mime.formats()) >= {
            "text/plain",
            "text/html",
            MARKDOWN_MIME_TYPE,
        }
        # Contenus exacts : le HTML ne porte aucune ancre d'édition (R9).
        assert mime.text() == payload.text
        assert mime.html() == payload.html
        assert bytes(mime.data(MARKDOWN_MIME_TYPE)) == payload.markdown.encode(
            "utf-8"
        )
        assert "block-" not in mime.html()
        assert "<table border='1'>" in mime.html()
    finally:
        mime.deleteLater()
        _drain_events(qapp)


def test_build_mime_data_empty_payload_only_plain(qapp):
    """Chargeur vide (document None) : seul text/plain est posé."""
    mime = build_mime_data(document_payload(None))
    try:
        assert mime.hasFormat("text/plain")
        assert not mime.hasFormat("text/html")
        assert not mime.hasFormat(MARKDOWN_MIME_TYPE)
    finally:
        mime.deleteLater()
        _drain_events(qapp)


# ---------------------------------------------------------------------------
# put_on_clipboard : chemin complet (presse-papiers factice) + repli offscreen
# ---------------------------------------------------------------------------
class _RecordingClipboard:
    """Presse-papiers factice : enregistre le QMimeData sans le conserver."""

    def __init__(self) -> None:
        self.mime_data = None

    def setMimeData(self, mime) -> None:
        self.mime_data = mime


class _FakeQGuiApplication:
    """Remplaçant de QGuiApplication : plateforme non-offscreen factice."""

    _clipboard = _RecordingClipboard()

    @staticmethod
    def platformName() -> str:
        return "windows"

    @staticmethod
    def clipboard() -> _RecordingClipboard:
        return _FakeQGuiApplication._clipboard


def test_put_on_clipboard_full_path_sets_three_formats(monkeypatch):
    """Plateforme non-offscreen simulée : le QMimeData complet est confié au
    presse-papiers (texte + HTML + Markdown)."""
    recorder = _RecordingClipboard()
    _FakeQGuiApplication._clipboard = recorder
    monkeypatch.setattr(clipboard_qt, "QGuiApplication", _FakeQGuiApplication)

    payload = page_payload(invoice_page())
    try:
        result = put_on_clipboard(payload)
        assert result == "texte + HTML + Markdown"
        mime = recorder.mime_data
        assert mime is not None
        assert mime.hasFormat("text/plain")
        assert mime.hasFormat("text/html")
        assert mime.hasFormat(MARKDOWN_MIME_TYPE)
        assert mime.html() == payload.html
    finally:
        recorder.mime_data = None  # ne traîne pas à la destruction
        gc.collect()


def test_put_on_clipboard_empty_payload_only_plain(monkeypatch):
    recorder = _RecordingClipboard()
    _FakeQGuiApplication._clipboard = recorder
    monkeypatch.setattr(clipboard_qt, "QGuiApplication", _FakeQGuiApplication)

    try:
        result = put_on_clipboard(document_payload(None))
        assert result == "texte + HTML + Markdown"
        mime = recorder.mime_data
        assert mime.hasFormat("text/plain")
        assert not mime.hasFormat("text/html")
        assert not mime.hasFormat(MARKDOWN_MIME_TYPE)
    finally:
        recorder.mime_data = None
        gc.collect()


def test_put_on_clipboard_offscreen_falls_back_to_text(qapp):
    """Plateforme réellement offscreen : repli documenté sur le texte brut
    (le presse-papiers Qt hors écran plante à la destruction d'un
    QMimeData confié)."""
    payload = page_payload(invoice_page())

    result = put_on_clipboard(payload)

    assert result == "texte"
    assert qapp.clipboard().text() == payload.text


# ---------------------------------------------------------------------------
# MainWindow : copie de page / document
# ---------------------------------------------------------------------------
def test_copy_page_puts_page_text_on_clipboard(demo_window, qapp):
    page = demo_window._results.current_page()
    assert page is not None

    demo_window._copy_page()

    clipboard = qapp.clipboard()
    assert clipboard.text() == page_plain_text(page)
    assert "Facture n° 2026-041" in clipboard.text()
    assert "Désignation\tQté" in clipboard.text()
    assert "copiée" in demo_window._bottom._toast.toolTip()


def test_copy_page_reflects_edited_text(demo_window, qapp):
    """R9 : édition avant copie — le presse-papiers porte le texte édité."""
    session = demo_window._edit_session
    page = demo_window._results.current_page()
    assert page is not None

    assert session.update_block_text(page.number, "demo-title", "FACTURE ÉDITÉE")
    demo_window._copy_page()

    clipboard_text = qapp.clipboard().text()
    assert "FACTURE ÉDITÉE" in clipboard_text
    assert "Facture n° 2026-041" not in clipboard_text


def test_copy_document_puts_document_text_on_clipboard(demo_window, qapp):
    demo_window._copy_document()

    clipboard_text = qapp.clipboard().text()
    assert "=== Page 4 ===" in clipboard_text
    assert "Facture n° 2026-041" in clipboard_text
    assert "copié" in demo_window._bottom._toast.toolTip()


def test_copy_and_export_guards_without_document(qapp, monkeypatch, tmp_path):
    """Sans document : messages d'erreur, aucun dialogue, aucune écriture."""
    from ocr_app.ui.main_window import MainWindow

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("ne doit pas être appelé sans document")

    monkeypatch.setattr(main_window_module, "ask_export_path", _forbidden)

    window = MainWindow()  # pas de mode démo : aucun document
    try:
        window._copy_page()
        assert "Aucune page à copier." in window._bottom._toast.toolTip()

        window._copy_document()
        assert "Aucun document à copier." in window._bottom._toast.toolTip()

        window._export_document()
        assert "Aucune page à exporter." in window._bottom._toast.toolTip()
    finally:
        window.close()
        window.deleteLater()
        _drain_events(qapp)


# ---------------------------------------------------------------------------
# MainWindow : export réel via dialogue simulé
# ---------------------------------------------------------------------------
class _FakeMessageBox:
    """Remplaçant de QMessageBox.warning (non modal, enregistre les appels)."""

    calls: list = []

    @classmethod
    def warning(cls, *_args, **_kwargs) -> int:
        cls.calls.append(_args)
        return 0


@pytest.fixture()
def message_box_spy(monkeypatch):
    _FakeMessageBox.calls = []
    monkeypatch.setattr(main_window_module, "QMessageBox", _FakeMessageBox)
    return _FakeMessageBox


def test_export_document_writes_docx(
    demo_window, tmp_path, monkeypatch, message_box_spy, qapp
):
    docx_module = pytest.importorskip(
        "docx", reason="python-docx n'est pas installé"
    )
    target = tmp_path / "export éàç Été.docx"
    fmt = format_by_key("docx")
    monkeypatch.setattr(
        main_window_module,
        "ask_export_path",
        lambda *args, **kwargs: (fmt, target),
    )

    demo_window._export_document()

    assert target.exists()
    assert target.stat().st_size > 0
    assert message_box_spy.calls == []  # aucun avertissement d'échec
    assert "Exporté :" in demo_window._bottom._toast.toolTip()

    reloaded = docx_module.Document(str(target))
    texts = [paragraph.text for paragraph in reloaded.paragraphs]
    assert "Facture n° 2026-041" in texts
    assert "Désignation" in reloaded.tables[0].cell(0, 0).text


def test_export_document_cancel_writes_nothing(
    demo_window, tmp_path, monkeypatch, message_box_spy
):
    from ocr_app.core.exports import Exporter

    created: list = []

    class _SpyExporter(Exporter):
        def __init__(self, document):
            created.append(document)
            super().__init__(document)

    monkeypatch.setattr(main_window_module, "Exporter", _SpyExporter)
    monkeypatch.setattr(
        main_window_module, "ask_export_path", lambda *a, **k: None
    )

    demo_window._export_document()

    assert created == []  # annulé : aucun export lancé
    assert message_box_spy.calls == []
    assert not any(tmp_path.iterdir())


def test_export_document_failure_shows_warning(
    demo_window, tmp_path, monkeypatch, message_box_spy
):
    """Échec d'écriture (chemin = répertoire existant) → avertissement
    « Export impossible », sans planter la fenêtre."""
    fmt = format_by_key("xlsx")
    monkeypatch.setattr(
        main_window_module,
        "ask_export_path",
        lambda *a, **k: (fmt, tmp_path),  # un répertoire : l'écriture échoue
    )

    demo_window._export_document()

    assert len(message_box_spy.calls) == 1
    assert message_box_spy.calls[0][1] == "Export impossible"


# ---------------------------------------------------------------------------
# Cycle de vie : QApplication unique, aucune fenêtre zombie
# ---------------------------------------------------------------------------
def test_single_qapplication_shared(qapp):
    assert QApplication.instance() is qapp
    assert QGuiApplication.platformName() == "offscreen"


def test_window_cleanup_leaves_no_python_zombie(qapp):
    from ocr_app.ui.main_window import MainWindow

    window = MainWindow(demo=True)
    window.show()
    qapp.processEvents()
    reference = weakref.ref(window)

    window.close()
    window.deleteLater()
    _drain_events(qapp)

    assert not shiboken_is_valid(window)
    del window
    gc.collect()
    assert reference() is None


def shiboken_is_valid(obj) -> bool:
    """Disponibilité shiboken6 tolérée : faux si l'objet C++ est détruit."""
    import shiboken6

    return shiboken6.isValid(obj)
