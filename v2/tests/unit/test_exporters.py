"""T6.2/T6.3/T6.4 — Tests des exports DOCX, Markdown, XLSX et PDF."""

from __future__ import annotations

import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V2_ROOT / "src"))

from core.io.exporters import ExportPage, export_docx, export_markdown, export_pdf, export_xlsx

PAGE = ExportPage(
    number=1,
    blocks=[
        {"kind": "title", "text": "DÉCRET N° 2026-041"},
        {"kind": "line", "text": "Article 1er — Le présent décret entre en vigueur."},
        {"kind": "line", "text": "Article 2 — Les modalités sont fixées en annexe."},
        {"kind": "table", "grid": [["Désignation", "Montant"], ["Conseil", "450,00 €"]]},
    ],
)


def test_markdown_preserves_order_and_article_numbering() -> None:
    markdown = export_markdown([PAGE], title="Journal officiel")
    assert "# Journal officiel" in markdown
    assert markdown.index("Article 1er") < markdown.index("Article 2")
    assert "### DÉCRET N° 2026-041" in markdown
    assert "| Désignation | Montant |" in markdown


def test_docx_exports_headings_and_table(tmp_path: Path) -> None:
    path = export_docx([PAGE], tmp_path / "export éàç.docx", title="Journal")
    from docx import Document

    document = Document(str(path))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert "DÉCRET N° 2026-041" in texts
    assert any("Article 1er" in text for text in texts)
    assert len(document.tables) == 1
    assert document.tables[0].cell(1, 1).text == "450,00 €"


def test_xlsx_neutralizes_formula_injection(tmp_path: Path) -> None:
    hostile = ExportPage(
        number=1,
        blocks=[
            {"kind": "line", "text": "=cmd|'/c calc'!A0"},
            {"kind": "line", "text": "+SUM(A1:A2)"},
            {"kind": "line", "text": "-2+3"},
            {"kind": "line", "text": "@HYPERLINK(\"http://x\")"},
        ],
    )
    path = export_xlsx([hostile], tmp_path / "hostile.xlsx")
    import openpyxl

    sheet = openpyxl.load_workbook(path).active
    values = [
        cell
        for row in sheet.iter_rows()
        for cell in row
        if isinstance(cell.value, str)
    ]
    assert all(cell.data_type == "s" for cell in values)
    assert not any(str(cell.value).startswith("=") for cell in values)


def test_pdf_contains_invisible_text_layer(tmp_path: Path) -> None:
    path = export_pdf([PAGE], tmp_path / "rendu.pdf")
    assert path.is_file() and path.stat().st_size > 0
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        page = document[0]
        try:
            text = page.get_textpage().get_text_range()
        finally:
            page.close()
    finally:
        document.close()
    assert "Article 1er" in text.replace("\xad", "")


def test_pdf_includes_page_image_when_available(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (200, 280), "white").save(image_path)
    page = ExportPage(number=1, blocks=[{"kind": "line", "text": "bonjour"}], image_path=str(image_path))
    path = export_pdf([page], tmp_path / "avec_image.pdf")
    assert path.stat().st_size > 0


def test_markdown_uses_page_anchor_instead_of_heading() -> None:
    markdown = export_markdown([PAGE], title="Journal")
    assert "<!-- page 1 -->" in markdown
    assert "## Page 1" not in markdown


def test_docx_preserves_font_and_page_break(tmp_path: Path) -> None:
    pages = [
        ExportPage(
            number=1,
            blocks=[
                {
                    "kind": "line",
                    "text": "Ligne de style",
                    "bbox": [40, 40, 500, 52],
                    "font_size": 9.0,
                    "font_name": "Times New Roman",
                    "bold": True,
                }
            ],
        ),
        ExportPage(
            number=2,
            blocks=[{"kind": "line", "text": "Deuxième page", "bbox": [40, 40, 500, 52]}],
        ),
    ]
    path = export_docx(pages, tmp_path / "styles.docx", title="Essai")
    from docx import Document

    document = Document(str(path))
    run = document.paragraphs[1].runs[0]
    assert run.font.size is not None and abs(run.font.size.pt - 9.0) < 0.01
    assert run.font.name == "Times New Roman"
    assert run.bold is True
    breaks = [run for paragraph in document.paragraphs for run in paragraph.runs]
    assert any('w:type="page"' in run._r.xml for run in breaks)


def test_docx_merges_wrapped_paragraph_lines(tmp_path: Path) -> None:
    page = ExportPage(
        number=1,
        blocks=[
            {"kind": "line", "text": "Le présent décret fixe les modalités", "bbox": [40, 100, 520, 112]},
            {"kind": "line", "text": "d'application du texte.", "bbox": [40, 112, 300, 124]},
            {"kind": "line", "text": "Fin de phrase.", "bbox": [40, 140, 520, 152]},
            {"kind": "line", "text": "Nouvelle phrase.", "bbox": [40, 152, 520, 164]},
        ],
    )
    path = export_docx([page], tmp_path / "paragraphes.docx", title="Essai")
    from docx import Document

    texts = [paragraph.text for paragraph in Document(str(path)).paragraphs]
    assert "Le présent décret fixe les modalités d'application du texte." in texts
    assert "Fin de phrase." in texts and "Nouvelle phrase." in texts
