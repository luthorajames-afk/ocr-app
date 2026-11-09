"""Tests des exports réels (phase 6) : DOCX, XLSX, PDF, HTML, MD, TXT.

Chaque export binaire est relu avec la bibliothèque qui le lit en production :

- DOCX via python-docx : titre, niveaux de titres, paragraphes, grille de
  tableau « Table Grid », fusions (``merged_from``) sans crash ;
- XLSX via openpyxl : cellules, en-têtes en gras, ``merge_cells``, et
  ANTI-INJECTION : un texte « =cmd|'/c calc'!A0 » est relu comme CHAÎNE
  (``data_type == 's'``), jamais comme formule ;
- PDF via reportlab : en-tête ``%PDF``, taille > 0, accents éèàç et « € »
  présents à l'extraction (pdftotext si disponible, sinon skip), caractères
  hors WinAnsi (emoji / CJK) remplacés par « ? » sans crash ;
- HTML / Markdown / TXT : comportement inchangé (compatibilité phase 5) ;
- chemins accentués / espaces (R14), document vide, blocs dégénérés,
  document synthétique de 30 blocs exporté en moins de 10 s.

Le module est SKIPPED si une des dépendances optionnelles manque.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("docx", reason="python-docx n'est pas installé")
pytest.importorskip("openpyxl", reason="openpyxl n'est pas installé")
pytest.importorskip("reportlab", reason="reportlab n'est pas installé")

import docx as docx_module
import openpyxl
from openpyxl.utils import get_column_letter

from ocr_app.core.data_model import Block, BlockType, Cell, Document, Page, Table
from ocr_app.core.exports import Exporter


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _cell(text: str, row: int, col: int, merged_from=None) -> Cell:
    return Cell(
        text=text, row_index=row, col_index=col, merged_from=merged_from
    )


def _simple_table() -> Table:
    rows = [
        ["Produit", "Quantité", "Prix"],
        ["Café", "2", "3,50 €"],
        ["Thé", "1", "2,80 €"],
    ]
    return Table(
        rows=3,
        cols=3,
        grid=[
            [_cell(v, r, c) for c, v in enumerate(row)]
            for r, row in enumerate(rows)
        ],
    )


def _merged_table() -> Table:
    """3×3 : fusion horizontale (0,0)-(0,2) et fusion 2×2 (1,1)-(2,2)."""
    return Table(
        rows=3,
        cols=3,
        grid=[
            [
                _cell("En-tête fusionnée", 0, 0, merged_from=(0, 0, 0, 2)),
                None,
                None,
            ],
            [
                _cell("A", 1, 0),
                _cell("Bloc 2x2", 1, 1, merged_from=(1, 1, 2, 2)),
                None,
            ],
            [_cell("D", 2, 0), None, None],
        ],
    )


def make_document() -> Document:
    page1 = Page(
        number=1,
        blocks=[
            Block(type=BlockType.TITLE, text="Rapport éàç — Résumé"),
            Block(
                type=BlockType.PARAGRAPH,
                text="Premier paragraphe éèàç.\nSeconde ligne du bloc.",
            ),
            Block(type=BlockType.TABLE, table=_simple_table()),
        ],
    )
    page2 = Page(
        number=2,
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text="=cmd|'/c calc'!A0\n=SUM(A1:A2)",
            ),
            Block(type=BlockType.TABLE, table=_merged_table()),
        ],
    )
    return Document(title="Rapport annuel éàç", pages=[page1, page2])


# ---------------------------------------------------------------------------
# Helpers de relecture
# ---------------------------------------------------------------------------
def _extract_pdf_text(path: Path) -> str:
    """Texte du PDF via pdftotext (skip si l'outil est absent)."""
    binary = shutil.which("pdftotext")
    if binary is None:
        pytest.skip("pdftotext n'est pas disponible sur cette machine")
    result = subprocess.run(
        [binary, "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    raw = result.stdout
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:  # build pdftotext sans -enc effectif
        return raw.decode("cp1252", "replace")


def _sheet_values(ws) -> list:
    values = []
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                values.append(cell.value)
    return values


def _find_cell(ws, value: str):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == value:
                return cell
    raise AssertionError(f"cellule introuvable dans la feuille : {value!r}")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def test_export_docx_roundtrip(tmp_path):
    document = make_document()
    target = tmp_path / "rapport.docx"

    Exporter(document).save_docx(target)

    reloaded = docx_module.Document(str(target))
    # Titre du document en Heading 0 (style « Title »).
    first = reloaded.paragraphs[0]
    assert first.text == "Rapport annuel éàç"
    assert first.style.name == "Title"
    # Pagination page à page : une section « Page N » en Heading 1.
    styles = {p.text: p.style.name for p in reloaded.paragraphs}
    assert styles.get("Page 1") == "Heading 1"
    assert styles.get("Page 2") == "Heading 1"
    assert styles.get("Rapport éàç — Résumé") == "Heading 2"
    # Chaque ligne du bloc devient un paragraphe.
    texts = [p.text for p in reloaded.paragraphs]
    assert "Premier paragraphe éèàç." in texts
    assert "Seconde ligne du bloc." in texts
    # Grille de tableau correcte.
    table = reloaded.tables[0]
    assert len(table.rows) == 3
    assert len(table.columns) == 3
    assert table.cell(0, 0).text == "Produit"
    assert table.cell(0, 2).text == "Prix"
    assert table.cell(1, 2).text == "3,50 €"
    assert table.cell(2, 0).text == "Thé"


def test_export_docx_merged_cells(tmp_path):
    document = make_document()
    target = tmp_path / "fusions.docx"

    Exporter(document).save_docx(target)  # ne doit pas lever

    table = docx_module.Document(str(target)).tables[1]
    # Fusion horizontale : la rangée 0 porte un seul <tc> (gridSpan) et les
    # trois positions relisent le texte de l'ancre.
    assert len(table.rows[0]._tr.tc_lst) == 1
    assert [table.cell(0, c).text for c in range(3)] == [
        "En-tête fusionnée"
    ] * 3
    # Fusion 2×2 : l'ancre porte le texte, la continuation y est résolue.
    assert table.cell(1, 1).text == "Bloc 2x2"
    assert table.cell(2, 1).text == "Bloc 2x2"
    # XML canonique python-docx : la rangée 2 = 1 tc « D » + 1 tc de
    # continuation (vMerge + gridSpan) pour la zone 2×2.
    assert len(table.rows[2]._tr.tc_lst) == 2
    # Les cellules hors fusions restent à leur place.
    assert table.cell(1, 0).text == "A"
    assert table.cell(2, 0).text == "D"


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def test_export_xlsx_roundtrip(tmp_path):
    document = make_document()
    target = tmp_path / "rapport.xlsx"

    Exporter(document).save_xlsx(target)

    wb = openpyxl.load_workbook(str(target))
    ws = wb.active
    assert ws.title == "Rapport annuel éàç"
    values = _sheet_values(ws)
    # Titre du document, sections de page, blocs et grille présents.
    assert "Rapport annuel éàç" in values
    assert "Page 1" in values
    assert "Page 2" in values
    for expected in (
        "Rapport éàç — Résumé",
        "Premier paragraphe éèàç.",
        "Seconde ligne du bloc.",
        "Produit",
        "Quantité",
        "Café",
        "3,50 €",
        "En-tête fusionnée",
        "Bloc 2x2",
    ):
        assert expected in values, expected

    # En-têtes du premier tableau en gras (r == 0 → header=True).
    header_cell = _find_cell(ws, "Produit")
    header_row = header_cell.row
    for col in (1, 2, 3):
        assert ws.cell(row=header_row, column=col).font.bold is True
    assert ws.cell(row=header_row + 1, column=1).value == "Café"

    # Fusions posées aux bonnes coordonnées (relatives aux ancres).
    merges = {str(rng) for rng in ws.merged_cells.ranges}
    anchor = _find_cell(ws, "En-tête fusionnée")
    anchor_col = get_column_letter(anchor.column)
    assert f"{anchor_col}{anchor.row}:C{anchor.row}" in merges
    block = _find_cell(ws, "Bloc 2x2")
    block_col = get_column_letter(block.column)
    assert f"{block_col}{block.row}:C{block.row + 1}" in merges


def test_export_xlsx_anti_injection_formulas_stay_strings(tmp_path):
    """Un texte OCR « =… » doit rester une CHAÎNE (data_type 's') : openpyxl
    classerait sinon la cellule comme formule et Excel tenterait de
    l'évaluer, voire de l'exécuter via DDE."""
    document = make_document()
    target = tmp_path / "injection.xlsx"

    Exporter(document).save_xlsx(target)

    ws = openpyxl.load_workbook(str(target)).active
    formulas = {
        cell.value: cell
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    }
    assert "=cmd|'/c calc'!A0" in formulas
    assert "=SUM(A1:A2)" in formulas
    for value, cell in formulas.items():
        assert cell.data_type == "s", (value, cell.data_type)
    # Aucune cellule de la feuille n'est une formule.
    assert all(
        cell.data_type != "f"
        for row in ws.iter_rows()
        for cell in row
    )


def test_export_xlsx_normalises_control_chars(tmp_path):
    document = Document(
        title="Contrôles",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(
                        type=BlockType.PARAGRAPH,
                        text="ligne\x00avec\x1fctrl",
                    ),
                    Block(
                        type=BlockType.PARAGRAPH,
                        text="a\rb",
                    ),
                ],
            )
        ],
    )
    target = tmp_path / "controles.xlsx"

    Exporter(document).save_xlsx(target)

    ws = openpyxl.load_workbook(str(target)).active
    values = _sheet_values(ws)
    assert "ligneavecctrl" in values  # caractères de contrôle retirés
    assert "a\nb" in values  # \r normalisé en \n
    assert not any("\r" in v for v in values if isinstance(v, str))


def test_export_xlsx_sheet_title_sanitised(tmp_path):
    document = Document(
        title="A/B:C*D?E[F]G\\H",
        pages=[Page(number=1, blocks=[Block(type=BlockType.PARAGRAPH, text="x")])],
    )
    target = tmp_path / "titre.xlsx"

    Exporter(document).save_xlsx(target)

    ws = openpyxl.load_workbook(str(target)).active
    assert ws.title == "A_B_C_D_E_F_G_H"


def test_export_xlsx_sheet_title_truncated_to_31(tmp_path):
    document = Document(
        title="Titre de document vraiment trop long pour une feuille Excel",
        pages=[Page(number=1, blocks=[Block(type=BlockType.PARAGRAPH, text="x")])],
    )
    target = tmp_path / "long.xlsx"

    Exporter(document).save_xlsx(target)

    ws = openpyxl.load_workbook(str(target)).active
    assert ws.title == document.title[:31]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def test_export_pdf_header_and_accents(tmp_path):
    document = make_document()
    target = tmp_path / "rapport.pdf"

    Exporter(document).save_pdf(target)

    raw = target.read_bytes()
    assert raw[:5] == b"%PDF-"
    assert len(raw) > 0

    text = _extract_pdf_text(target)
    assert "Rapport annuel éàç" in text
    assert "Café" in text
    assert "é" in text and "à" in text and "ç" in text
    assert "3,50 €" in text


def test_export_pdf_non_winansi_becomes_question_marks(tmp_path):
    document = Document(
        title="Symboles",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(
                        type=BlockType.PARAGRAPH,
                        text="SYM: \U0001F600 中文 café",
                    )
                ],
            )
        ],
    )
    target = tmp_path / "symboles.pdf"

    Exporter(document).save_pdf(target)  # ne doit pas lever

    raw = target.read_bytes()
    assert raw[:5] == b"%PDF-"
    text = _extract_pdf_text(target)
    assert "?" in text  # repli WinAnsi « ? »
    assert "café" in text  # les accents restent lisibles
    assert "\U0001F600" not in text
    assert "中" not in text


def test_export_pdf_paginates(tmp_path):
    # Assez de texte pour déborder largement sur plusieurs feuilles A4.
    filler = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 60
    document = Document(
        title="Long document",
        pages=[
            Page(
                number=n,
                blocks=[
                    Block(
                        type=BlockType.PARAGRAPH,
                        text=f"Contenu de la page {n}. {filler}",
                    )
                ],
            )
            for n in range(1, 4)
        ],
    )
    target = tmp_path / "long.pdf"

    Exporter(document).save_pdf(target)

    pdfium = pytest.importorskip(
        "pypdfium2", reason="pypdfium2 n'est pas installé"
    )
    with pdfium.PdfDocument(str(target)) as pdf:
        assert len(pdf) >= 3
    text = _extract_pdf_text(target)
    assert "Page 2" in text


# ---------------------------------------------------------------------------
# HTML / Markdown / TXT : comportement inchangé (compatibilité phase 5)
# ---------------------------------------------------------------------------
def test_plain_text_legacy_format(tmp_path):
    document = make_document()
    target = tmp_path / "doc.txt"

    Exporter(document).save_plain_text(target)

    text = target.read_text(encoding="utf-8")
    assert text.index("=== Page 1 ===") < text.index("=== Page 2 ===")
    # Le TXT historique marque les tableaux, il ne les reconstruit pas.
    assert text.count("[tableau]") == 2
    assert "Premier paragraphe éèàç." in text


def test_markdown_legacy_format(tmp_path):
    document = make_document()
    target = tmp_path / "doc.md"

    Exporter(document).save_markdown(target)

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Rapport annuel éàç")
    assert "## Page 1" in text
    assert "| Produit | Quantité | Prix |" in text
    assert "|---|---|---|" in text
    assert "| Café | 2 | 3,50 € |" in text


def test_html_legacy_format(tmp_path):
    document = make_document()
    target = tmp_path / "doc.html"

    Exporter(document).save_html(target)

    text = target.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in text
    assert "<h1>Rapport annuel éàç</h1>" in text
    assert "data-page='1'" in text
    assert "<table border='1'>" in text
    assert "<th>Produit</th>" in text
    assert "<td>Café</td>" in text


# ---------------------------------------------------------------------------
# R14 : chemins accentués / avec espaces
# ---------------------------------------------------------------------------
def test_export_paths_with_accents_and_spaces(tmp_path):
    document = make_document()
    cases = [
        ("docx", "save_docx"),
        ("xlsx", "save_xlsx"),
        ("pdf", "save_pdf"),
        ("html", "save_html"),
        ("md", "save_markdown"),
        ("txt", "save_plain_text"),
    ]
    exporter = Exporter(document)
    for suffix, method in cases:
        target = tmp_path / f"export éàç Été v1.{suffix}"
        getattr(exporter, method)(target)
        assert target.exists(), method
        assert target.stat().st_size > 0, method

    # Les écritures texte sont explicites en UTF-8 (R14).
    assert "éàç" in (tmp_path / "export éàç Été v1.txt").read_text(
        encoding="utf-8"
    )
    assert "éàç" in (tmp_path / "export éàç Été v1.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Robustesse et performances
# ---------------------------------------------------------------------------
def test_export_empty_document_all_formats(tmp_path):
    document = Document(title="", pages=[])

    exporter = Exporter(document)
    exporter.save_docx(tmp_path / "vide.docx")
    exporter.save_xlsx(tmp_path / "vide.xlsx")
    exporter.save_pdf(tmp_path / "vide.pdf")
    exporter.save_html(tmp_path / "vide.html")
    exporter.save_markdown(tmp_path / "vide.md")
    exporter.save_plain_text(tmp_path / "vide.txt")

    assert (tmp_path / "vide.pdf").read_bytes()[:5] == b"%PDF-"
    reloaded = docx_module.Document(str(tmp_path / "vide.docx"))
    assert len(reloaded.tables) == 0
    assert not any(p.text.strip() for p in reloaded.paragraphs)
    ws = openpyxl.load_workbook(str(tmp_path / "vide.xlsx")).active
    assert ws.title == "Feuille1"
    assert (tmp_path / "vide.txt").read_text(encoding="utf-8") == ""
    # Le Markdown d'un document vide garde l'en-tête du titre (vide ici).
    assert (tmp_path / "vide.md").read_text(encoding="utf-8") == "# \n"
    assert "<!DOCTYPE html>" in (tmp_path / "vide.html").read_text(
        encoding="utf-8"
    )


def test_export_degenerate_blocks_all_formats(tmp_path):
    """Blocs sans texte, tableau absent, tableau vide, cellules None."""
    document = Document(
        title="Dégénéré",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type=BlockType.PARAGRAPH, text=""),
                    Block(type=BlockType.TABLE, table=None),
                    Block(
                        type=BlockType.TABLE,
                        table=Table(rows=0, cols=0, grid=[]),
                    ),
                    Block(
                        type=BlockType.TABLE,
                        table=Table(rows=1, cols=2, grid=[[None, None]]),
                    ),
                    Block(type=BlockType.IMAGE, text=""),
                ],
            )
        ],
    )

    exporter = Exporter(document)
    exporter.save_docx(tmp_path / "deg.docx")
    exporter.save_xlsx(tmp_path / "deg.xlsx")
    exporter.save_pdf(tmp_path / "deg.pdf")
    exporter.save_markdown(tmp_path / "deg.md")
    exporter.save_plain_text(tmp_path / "deg.txt")
    exporter.save_html(tmp_path / "deg.html")

    reloaded = docx_module.Document(str(tmp_path / "deg.docx"))
    # Deux tableaux dégénérés → texte « [tableau vide] », un seul vrai tableau.
    assert len(reloaded.tables) == 1
    assert len(reloaded.tables[0].rows) == 1
    assert len(reloaded.tables[0].columns) == 2
    texts = [p.text for p in reloaded.paragraphs]
    assert texts.count("[tableau vide]") == 2

    ws = openpyxl.load_workbook(str(tmp_path / "deg.xlsx")).active
    values = _sheet_values(ws)
    assert values.count("[tableau vide]") == 2

    text = _extract_pdf_text(tmp_path / "deg.pdf")
    assert "[tableau vide]" in text

    markdown = (tmp_path / "deg.md").read_text(encoding="utf-8")
    assert "[tableau]" in markdown
    assert "[tableau vide]" in markdown
    assert "[image]" in markdown

    plain = (tmp_path / "deg.txt").read_text(encoding="utf-8")
    assert plain.count("[tableau]") == 3

    html = (tmp_path / "deg.html").read_text(encoding="utf-8")
    assert "<p>[tableau]</p>" in html
    assert "<p>[tableau vide]</p>" in html
    assert "<th></th>" in html  # cellules None : rangée d'en-tête vide


def test_export_large_synthetic_document_under_10s(tmp_path):
    """Document synthétique de 30 blocs (3 pages × 10 blocs avec tableaux) :
    les six exports restent complets et sous 10 secondes."""
    pages = []
    for number in range(1, 4):
        blocks: list = []
        for index in range(10):
            if index % 3 == 0:
                blocks.append(
                    Block(
                        type=BlockType.TITLE,
                        text=f"Titre {number}-{index} éàç",
                    )
                )
            elif index % 3 == 1:
                blocks.append(
                    Block(
                        type=BlockType.PARAGRAPH,
                        text=f"Paragraphe {number}-{index}.\nSeconde ligne.",
                    )
                )
            else:
                rows = [
                    ["Col A", "Col B", "Col C"],
                    ["a1", "b1", "c1"],
                    ["a2", "b2", "c2"],
                    ["a3", "b3", "c3"],
                ]
                blocks.append(
                    Block(
                        type=BlockType.TABLE,
                        table=Table(
                            rows=4,
                            cols=3,
                            grid=[
                                [_cell(v, r, c) for c, v in enumerate(row)]
                                for r, row in enumerate(rows)
                            ],
                        ),
                    )
                )
        pages.append(Page(number=number, blocks=blocks))
    document = Document(title="Document synthétique", pages=pages)

    exporter = Exporter(document)
    targets = [
        ("save_docx", tmp_path / "gros.docx"),
        ("save_xlsx", tmp_path / "gros.xlsx"),
        ("save_pdf", tmp_path / "gros.pdf"),
        ("save_html", tmp_path / "gros.html"),
        ("save_markdown", tmp_path / "gros.md"),
        ("save_plain_text", tmp_path / "gros.txt"),
    ]
    started = time.perf_counter()
    for method, target in targets:
        getattr(exporter, method)(target)
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0, f"exports trop lents : {elapsed:.2f} s"
    for _method, target in targets:
        assert target.exists() and target.stat().st_size > 0, target.name
