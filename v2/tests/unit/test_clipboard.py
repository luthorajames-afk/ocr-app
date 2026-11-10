"""T6.1 — Tests du presse-papiers HTML riche."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.io.clipboard import document_payload, page_payload


def _record(page: int = 1, text: str = "Facture n° 041\nTotal : 1 450,90 €") -> dict:
    return {"page": page, "text": text}


def test_page_payload_escapes_html() -> None:
    payload = page_payload(_record(text="SARL <Dupont> & Fils"))
    assert "&lt;Dupont&gt;" in payload.html
    assert "&amp;" in payload.html
    assert "<Dupont>" not in payload.html


def test_page_payload_text_and_markdown() -> None:
    payload = page_payload(_record())
    assert "Facture n° 041" in payload.text
    assert payload.markdown.startswith("## Page 1")


def test_page_payload_renders_tables() -> None:
    record = {
        "page": 2,
        "text": "Désignation\nTotal",
        "tables": [{"grid": [["Désignation", "Montant"], ["Conseil", "450,00 €"]]}],
    }
    payload = page_payload(record)
    assert "<table" in payload.html
    assert "<th>Désignation</th>" in payload.html
    assert "| Désignation | Montant |" in payload.markdown


def test_document_payload_orders_pages() -> None:
    payload = document_payload([_record(2, "Deuxième"), _record(1, "Première")])
    assert payload.html.index("Page 1") < payload.html.index("Page 2")
    assert payload.text.index("Première") < payload.text.index("Deuxième")


def test_empty_document_payload() -> None:
    payload = document_payload([])
    assert "document vide" in payload.html
    assert payload.text == ""


def test_mime_data_formats() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui.clipboard_qt import MARKDOWN_MIME, build_mime_data

    QApplication.instance() or QApplication([])
    mime = build_mime_data(page_payload(_record()))
    assert mime.hasText()
    assert mime.hasHtml()
    assert mime.hasFormat(MARKDOWN_MIME)
    assert "Facture" in mime.text()
    assert mime.data(MARKDOWN_MIME).data().decode("utf-8").startswith("## Page 1")
