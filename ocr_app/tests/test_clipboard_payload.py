"""Tests du presse-papiers riche (phase 6) — chargeurs, sans Qt.

``ocr_app.core.clipboard`` assemble, depuis les ``Page`` / ``Document`` du
modèle, un ``ClipboardPayload`` (``{html, text, markdown}``) destiné au
presse-papiers. Ces tests valident la construction des chargeurs SANS Qt :

- HTML propre : vrais ``<table>`` (``<th>``/``<td>``), zéro ancre ou lien
  d'édition (R9) ;
- texte brut : tableaux en colonnes tabulées ;
- Markdown : tableaux en pipelines ``|`` ;
- édition via ``EditSession`` (``Page`` partagées) → le chargeur reflète le
  texte édité (R9 : édition avant copie) ;
- document multi-pages : pages ordonnées par numéro croissant, sans muter le
  document ; chargeur d'une page seule correct ;
- blocs sans texte, tableau vide, cellules ``None`` : aucun crash.

Le module reste importable sans PySide6 : le test qui utilise
``EditSession`` (dépendant de Qt) est skippé individuellement si l'extra
``[ui]`` n'est pas installé.
"""

from __future__ import annotations

import os

# Inoffensif sans interface ; cohérent avec les autres modules de tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from ocr_app.core.clipboard import ClipboardPayload, document_payload, page_payload
from ocr_app.core.data_model import Block, BlockType, Cell, Document, Page, Table


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _cell(text: str, row: int, col: int) -> Cell:
    return Cell(text=text, row_index=row, col_index=col)


def _table() -> Table:
    rows = [
        ["Désignation", "Qté", "Prix"],
        ["Café", "2", "3,50 €"],
        ["Thé", "1", "2,80 €"],
    ]
    grid = [
        [_cell(value, row_index, col_index)
         for col_index, value in enumerate(row)]
        for row_index, row in enumerate(rows)
    ]
    return Table(rows=len(rows), cols=3, grid=grid)


def _rich_page(number: int = 4) -> Page:
    """Page complète : titre + paragraphe multi-lignes + tableau."""
    return Page(
        number=number,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text="Note de frais éàç",
                block_id="t1",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text="Client : Société Exemple SAS\nDate : 9 novembre 2026",
                block_id="p1",
            ),
            Block(type=BlockType.TABLE, table=_table(), block_id="tab1"),
        ],
    )


def _simple_page(number: int, marker: str) -> Page:
    """Page minimale : un paragraphe identifiable."""
    return Page(
        number=number,
        blocks=[Block(type=BlockType.PARAGRAPH, text=marker, block_id="m")],
    )


# ---------------------------------------------------------------------------
# page_payload : structure HTML
# ---------------------------------------------------------------------------
def test_page_payload_html_has_table_structure():
    payload = page_payload(_rich_page())

    assert isinstance(payload, ClipboardPayload)
    assert "<!DOCTYPE html>" in payload.html
    assert "<table border='1'>" in payload.html
    assert "<th>Désignation</th>" in payload.html
    assert "<td>Café</td>" in payload.html
    # Le titre du premier bloc titre devient <h1> et <title>.
    assert "<h1>Note de frais éàç</h1>" in payload.html
    assert "<title>Note de frais éàç</title>" in payload.html
    # Paragraphe multi-lignes rendu avec <br>.
    assert "Client : Société Exemple SAS<br>Date : 9 novembre 2026" in (
        payload.html
    )


def test_page_payload_html_has_no_block_anchors():
    """R9 : aucun identifiant de bloc ni lien d'édition dans le HTML."""
    payload = page_payload(_rich_page())

    assert "block-" not in payload.html
    assert "block_id" not in payload.html
    assert "<a " not in payload.html
    assert "href" not in payload.html


# ---------------------------------------------------------------------------
# page_payload : texte brut et Markdown
# ---------------------------------------------------------------------------
def test_page_payload_text_has_tabulated_table():
    payload = page_payload(_rich_page())

    assert "Désignation\tQté\tPrix" in payload.text
    assert "Café\t2\t3,50 €" in payload.text
    # Paragraphes et tableau séparés par une ligne vide.
    assert "Date : 9 novembre 2026\n\nDésignation" in payload.text


def test_page_payload_markdown_has_pipelines():
    payload = page_payload(_rich_page())

    assert "# Note de frais éàç" in payload.markdown
    assert "| Désignation | Qté | Prix |" in payload.markdown
    assert "|---|---|---|" in payload.markdown
    assert "| Café | 2 | 3,50 € |" in payload.markdown


def test_page_payload_triple_format_same_content():
    """Les trois formats portent le même contenu net, sans bandeau de page."""
    payload = page_payload(_rich_page())

    for body in (payload.text, payload.markdown, payload.html):
        assert "Note de frais éàç" in body
        assert "3,50 €" in body
    # Pas d'en-tête « === Page N === » (c'est le document qui en porte).
    assert "=== Page" not in payload.text


# ---------------------------------------------------------------------------
# document_payload : ordre des pages, titre, page seule
# ---------------------------------------------------------------------------
def test_document_payload_orders_pages_ascending():
    document = Document(
        title="Dossier éàç",
        pages=[
            _simple_page(3, "Contenu page 3"),
            _simple_page(1, "Contenu page 1"),
            _simple_page(2, "Contenu page 2"),
        ],
    )

    payload = document_payload(document)
    text = payload.text
    assert text.index("=== Page 1 ===") < text.index("=== Page 2 ===")
    assert text.index("=== Page 2 ===") < text.index("=== Page 3 ===")
    assert text.index("Contenu page 1") < text.index("Contenu page 2")

    # Le document source n'est pas muté (vue triée uniquement).
    assert [page.number for page in document.pages] == [3, 1, 2]


def test_document_payload_html_and_markdown_sections():
    document = Document(
        title="Dossier éàç",
        pages=[_simple_page(1, "Contenu 1"), _simple_page(2, "Contenu 2")],
    )

    payload = document_payload(document)
    assert "<h1>Dossier éàç</h1>" in payload.html
    assert "<h2>Page 1</h2>" in payload.html
    assert "<h2>Page 2</h2>" in payload.html
    assert payload.html.index("<h2>Page 1</h2>") < payload.html.index(
        "<h2>Page 2</h2>"
    )
    assert "# Dossier éàç" in payload.markdown
    assert "## Page 1" in payload.markdown
    assert "## Page 2" in payload.markdown


def test_page_payload_of_single_page_in_document():
    """page_payload sur une page d'un document : contenu de cette page seule."""
    document = Document(
        title="Dossier",
        pages=[
            _simple_page(1, "Contenu page 1"),
            _simple_page(2, "Contenu page 2"),
        ],
    )

    payload = page_payload(document.pages[1])
    assert "Contenu page 2" in payload.text
    assert "Contenu page 1" not in payload.text
    assert "=== Page" not in payload.text


def test_document_payload_none_is_empty():
    payload = document_payload(None)

    assert payload.html == ""
    assert payload.text == ""
    assert payload.markdown == ""


# ---------------------------------------------------------------------------
# R9 : édition via EditSession (Page partagées) → chargeurs à jour
# ---------------------------------------------------------------------------
def test_payload_reflects_edit_session_edits():
    """Les Page sont partagées : une édition via EditSession est visible
    immédiatement dans le chargeur (édition avant copie, R9)."""
    pytest.importorskip(
        "PySide6", reason="EditSession dépend de PySide6 (extra [ui])"
    )
    from ocr_app.ui.edit_session import EditSession

    page = _rich_page()
    document = Document(title="Note de frais éàç", pages=[page])
    session = EditSession()
    try:
        session.load_document(document, task_id="task-test")

        edited = "Note ÉDITÉE — montant 9 999,00 €"
        assert session.update_block_text(page.number, "p1", edited) is True

        single = page_payload(page)
        assert edited in single.text
        assert edited in single.markdown
        assert "ÉDITÉE" in single.html
        assert "Client : Société Exemple SAS" not in single.text

        whole = document_payload(document)
        assert edited in whole.text
        assert edited in whole.html
    finally:
        session.deleteLater()


# ---------------------------------------------------------------------------
# Robustesse : blocs sans texte, tableau vide, cellules None
# ---------------------------------------------------------------------------
def _degenerate_page() -> Page:
    return Page(
        number=7,
        blocks=[
            Block(type=BlockType.PARAGRAPH, text=""),
            Block(type=BlockType.TABLE, table=None),
            Block(type=BlockType.TABLE, table=Table(rows=0, cols=0, grid=[])),
            Block(
                type=BlockType.TABLE,
                table=Table(rows=1, cols=2, grid=[[None, None]]),
            ),
            Block(type=BlockType.IMAGE, text=""),
            Block(type=BlockType.FOOTER, text="   "),
        ],
    )


def test_page_payload_with_empty_blocks_does_not_crash():
    payload = page_payload(_degenerate_page())

    # Texte brut : table absente et table vide portent le même marqueur ;
    # la grille de cellules None reste une ligne tabulée (alignement).
    assert payload.text.count("[tableau vide]") == 2
    assert "\t" in payload.text
    assert "[image]" in payload.text
    assert "<th></th>" in payload.html  # cellules None rendues vides (rangée 0)


def test_document_payload_with_empty_blocks_does_not_crash():
    document = Document(title="Dossier", pages=[_degenerate_page()])

    payload = document_payload(document)
    assert "=== Page 7 ===" in payload.text
    assert "[tableau]" in payload.markdown  # table absente
    assert "[tableau vide]" in payload.markdown  # Table() sans grille


def test_page_payload_title_fallback_without_title_block():
    page = _simple_page(12, "Seul paragraphe")

    payload = page_payload(page)
    assert "<title>Page 12</title>" in payload.html


def test_page_payload_escapes_html_and_markdown_pipes():
    page = Page(
        number=1,
        blocks=[
            Block(type=BlockType.PARAGRAPH, text="a<b> & c"),
            Block(
                type=BlockType.TABLE,
                table=Table(rows=1, cols=1, grid=[[_cell("a|b", 0, 0)]]),
            ),
        ],
    )

    payload = page_payload(page)
    assert "a&lt;b&gt; &amp; c" in payload.html
    assert "a\\|b" in payload.markdown
