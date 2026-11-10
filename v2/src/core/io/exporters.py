"""T6.2/T6.3/T6.4 — Exports DOCX, Markdown, XLSX et PDF.

- DOCX/Markdown : ordre de lecture multi-colonnes (blocs déjà ordonnés par
  colonnes) et numérotation des articles préservée telle quelle ;
- XLSX : neutralisation stricte des injections de formules (préfixes = + - @) ;
- PDF : image de la page + couche de texte invisible et sélectionnable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True, slots=True)
class ExportPage:
    """Contenu d'une page pour l'export (ordre de lecture déjà appliqué)."""

    number: int
    blocks: list[dict[str, Any]]
    image_path: str | None = None


def _safe_cell(value: object) -> str:
    """Neutralise une cellule texte destinée à Excel/Calc."""
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def _block_text(block: dict[str, Any]) -> str:
    return str(block.get("text", ""))


def export_markdown(pages: list[ExportPage], title: str = "Document") -> str:
    """Markdown multi-colonnes : titres, paragraphes et tableaux."""
    parts: list[str] = [f"# {title}", ""]
    for page in pages:
        parts.append(f"## Page {page.number}")
        parts.append("")
        for block in page.blocks:
            kind = str(block.get("kind", "line"))
            text = _block_text(block)
            if kind == "title":
                parts.extend([f"### {text}", ""])
            elif kind == "table":
                grid = block.get("grid") or []
                if grid:
                    header = "| " + " | ".join(str(c) for c in grid[0]) + " |"
                    separator = "|" + "---|" * len(grid[0])
                    parts.extend([header, separator])
                    for row in grid[1:]:
                        parts.append("| " + " | ".join(str(c) for c in row) + " |")
                    parts.append("")
            else:
                parts.extend([text, ""])
    return "\n".join(parts).strip() + "\n"


def export_docx(pages: list[ExportPage], path: str | Path, title: str = "Document") -> Path:
    """DOCX : titres, paragraphes, tableaux, numérotation d'articles intacte."""
    from docx import Document

    document = Document()
    document.add_heading(title, level=0)
    for page in pages:
        document.add_heading(f"Page {page.number}", level=1)
        for block in page.blocks:
            kind = str(block.get("kind", "line"))
            text = _block_text(block)
            if kind == "title":
                document.add_heading(text, level=2)
            elif kind == "table":
                grid = block.get("grid") or []
                if grid:
                    table = document.add_table(rows=len(grid), cols=len(grid[0]))
                    table.style = "Table Grid"
                    for row_index, row in enumerate(grid):
                        for col_index, cell in enumerate(row):
                            table.cell(row_index, col_index).text = str(cell)
            else:
                document.add_paragraph(text)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))
    return target


def export_xlsx(pages: list[ExportPage], path: str | Path, title: str = "Document") -> Path:
    """XLSX : une feuille, cellules texte, formules neutralisées."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = title[:31] or "Document"
    row_index = 1
    for page in pages:
        sheet.cell(row=row_index, column=1, value=f"Page {page.number}")
        row_index += 1
        for block in page.blocks:
            kind = str(block.get("kind", "line"))
            if kind == "table":
                grid = block.get("grid") or []
                for row in grid:
                    for col_index, value in enumerate(row, start=1):
                        cell = sheet.cell(row=row_index, column=col_index, value=_safe_cell(value))
                        cell.data_type = "s"
                    row_index += 1
            else:
                cell = sheet.cell(row=row_index, column=1, value=_safe_cell(_block_text(block)))
                cell.data_type = "s"
                row_index += 1
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(target))
    return target


def export_pdf(
    pages: list[ExportPage],
    path: str | Path,
    dpi: int = 300,
) -> Path:
    """PDF : image de page + couche de texte invisible mais sélectionnable."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(target), pagesize=A4)
    page_width, page_height = A4
    scale = 72.0 / max(1, dpi)

    for page in pages:
        if page.image_path and Path(page.image_path).is_file():
            pdf.drawImage(
                page.image_path, 0, 0, width=page_width, height=page_height, mask="auto"
            )
        pdf.setFillColorRGB(0, 0, 0)
        for block in page.blocks:
            text = _block_text(block).replace("\u00ad", "")
            if not text:
                continue
            bbox = block.get("bbox") or [0, 0, 0, 0]
            try:
                x0, y0 = float(bbox[0]) * scale, float(bbox[1]) * scale
            except (TypeError, ValueError, IndexError):
                x0, y0 = 36.0, page_height - 36.0
            font_size = max(6.0, min(12.0, (dpi / 300.0) * 9.0))
            for line_index, line in enumerate(text.splitlines() or [text]):
                baseline = page_height - y0 - (line_index + 1) * (font_size + 1.5)
                text_object = pdf.beginText()
                text_object.setTextRenderMode(3)  # invisible, recherchable
                text_object.setFont("Helvetica", font_size)
                text_object.setTextOrigin(x0, baseline)
                text_object.textLine(line[:180])
                pdf.drawText(text_object)
        pdf.showPage()
    pdf.save()
    return target
