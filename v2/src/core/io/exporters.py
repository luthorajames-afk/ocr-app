"""T6.2/T6.3/T6.4 — Exports DOCX, Markdown, XLSX et PDF.

- DOCX/Markdown : ordre de lecture multi-colonnes (blocs déjà ordonnés par
  colonnes) et numérotation des articles préservée telle quelle ;
- XLSX : neutralisation stricte des injections de formules (préfixes = + - @) ;
- PDF : image de la page + couche de texte invisible et sélectionnable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
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


def _margin_key(block: dict[str, Any], page_height: float) -> tuple[str, str] | None:
    """Clé d'un bloc de marge (en-tête/pied), chiffres normalisés."""
    if page_height <= 0:
        return None
    text = " ".join(unicodedata.normalize("NFKC", _block_text(block)).split())
    if not text or len(text) > 100:
        return None
    bbox = block.get("bbox") or [0, 0, 0, 0]
    try:
        top, bottom = float(bbox[1]), float(bbox[3])
    except (TypeError, ValueError, IndexError):
        return None
    if bottom <= page_height * 0.12:
        zone = "top"
    elif top >= page_height * 0.90:
        zone = "bottom"
    else:
        return None
    return zone, re.sub(r"\d+", "#", text.lower())


def _strip_running_margins(pages: list[ExportPage]) -> list[ExportPage]:
    """Retire les en-têtes/pieds répétés, même fragmentés.

    Les blocs de marge (12 % haut / 10 % bas) sont regroupés par similarité
    (0,72) et par zone ; un groupe présent sur assez de pages est considéré
    comme un en-tête/pied courant. Les tampons irréguliers (fragments
    différents selon les pages) sont ainsi attrapés, sans toucher au corps.
    """
    if len(pages) < 4:
        return pages
    entries: list[tuple[int, int, str, str]] = []
    for page in pages:
        height = max(
            (
                float((block.get("bbox") or [0, 0, 0, 0])[3])
                for block in page.blocks
            ),
            default=0.0,
        )
        for index, block in enumerate(page.blocks):
            if str(block.get("kind", "line")) == "table":
                continue
            key = _margin_key(block, height)
            if key is not None:
                entries.append((page.number, index, key[0], key[1]))
    if not entries:
        return pages
    entries.sort(key=lambda entry: -len(entry[3]))
    clusters: list[tuple[str, str, set[int], list[tuple[int, int]]]] = []
    for page_number, index, zone, text in entries:
        for cluster_zone, representative, cluster_pages, members in clusters:
            if cluster_zone != zone:
                continue
            if SequenceMatcher(None, text, representative).ratio() >= 0.72:
                cluster_pages.add(page_number)
                members.append((page_number, index))
                break
        else:
            clusters.append((zone, text, {page_number}, [(page_number, index)]))
    top_threshold = max(3, (len(pages) + 1) // 2)
    bottom_threshold = (
        max(3, int(len(pages) * 0.1)) if len(pages) >= 10 else top_threshold
    )
    skip: dict[int, set[int]] = {}
    for zone, _text, cluster_pages, members in clusters:
        threshold = bottom_threshold if zone == "bottom" else top_threshold
        if len(cluster_pages) < threshold:
            continue
        for page_number, index in members:
            skip.setdefault(page_number, set()).add(index)
    if not skip:
        return pages
    return [
        replace(
            page,
            blocks=[
                block
                for index, block in enumerate(page.blocks)
                if index not in skip.get(page.number, set())
            ],
        )
        for page in pages
    ]


def _block_text(block: dict[str, Any]) -> str:
    return str(block.get("text", ""))


def _block_style(block: dict[str, Any]) -> tuple[float, str, bool, bool]:
    try:
        size = float(block.get("font_size") or 0.0)
    except (TypeError, ValueError):
        size = 0.0
    return (
        size,
        str(block.get("font_name") or ""),
        bool(block.get("bold")),
        bool(block.get("italic")),
    )


def _markdown_styled(text: str, block: dict[str, Any]) -> str:
    _size, _name, bold, italic = _block_style(block)
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


_ENUMERATION = re.compile(r"^(?:\d+[°.)]|[a-zA-Z]\)|[-–—•])")
_CONTINUATION_WORDS = {
    "à", "au", "aux", "ce", "ces", "cette", "d", "dans", "de", "des", "du",
    "en", "et", "l", "la", "le", "les", "leur", "leurs", "ou", "par", "pour",
    "sa", "ses", "son", "sur", "un", "une",
}


def _can_join(previous: dict[str, Any], following: dict[str, Any], page_width: float) -> bool:
    """Deux lignes consécutives forment-elles un même paragraphe ?"""
    prev_kind = str(previous.get("kind", "line"))
    next_kind = str(following.get("kind", "line"))
    if prev_kind != next_kind or prev_kind not in ("line", "title"):
        return False
    prev_text = _block_text(previous).strip()
    next_text = _block_text(following).strip()
    if not prev_text or not next_text:
        return False
    if prev_kind == "title":
        try:
            prev_bbox = previous.get("bbox") or [0, 0, 0, 0]
            next_bbox = following.get("bbox") or [0, 0, 0, 0]
            prev_height = max(1.0, float(prev_bbox[3]) - float(prev_bbox[1]))
            gap = float(next_bbox[1]) - float(prev_bbox[3])
        except (TypeError, ValueError, IndexError):
            return False
        return gap <= prev_height * 2.5
    if prev_text.endswith((".", "!", "?", ":", ";", "…")) and not _ENUMERATION.match(prev_text):
        return False
    if next_text[0] in "-•*–—":
        return False
    if next_text[0].isupper():
        last_word = prev_text.split()[-1].strip(".,;:—–-").lower() if prev_text.split() else ""
        if last_word not in _CONTINUATION_WORDS:
            return False
    try:
        prev_bbox = previous.get("bbox") or [0, 0, 0, 0]
        next_bbox = following.get("bbox") or [0, 0, 0, 0]
        prev_right = float(prev_bbox[2])
        prev_height = max(1.0, float(prev_bbox[3]) - float(prev_bbox[1]))
        gap = float(next_bbox[1]) - float(prev_bbox[3])
    except (TypeError, ValueError, IndexError):
        return False
    if page_width > 0 and prev_right < page_width * 0.8:
        return False
    return gap <= prev_height * 1.8


def _merge_paragraphs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fusionne les lignes de continuation d'un paragraphe (export lisible)."""
    if not blocks:
        return []
    try:
        page_width = max(float((block.get("bbox") or [0, 0, 0, 0])[2]) for block in blocks)
    except (TypeError, ValueError, IndexError):
        page_width = 0.0
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if merged and _can_join(merged[-1], block, page_width):
            previous = merged[-1]
            left = _block_text(previous).rstrip()
            right = _block_text(block).strip()
            if left.endswith("-") and right[:1].islower():
                text = left[:-1] + right
            else:
                text = f"{left} {right}"
            combined = dict(previous)
            combined["text"] = text
            prev_bbox = previous.get("bbox") or [0, 0, 0, 0]
            next_bbox = block.get("bbox") or [0, 0, 0, 0]
            combined["bbox"] = [
                min(float(prev_bbox[0]), float(next_bbox[0])),
                min(float(prev_bbox[1]), float(next_bbox[1])),
                max(float(prev_bbox[2]), float(next_bbox[2])),
                max(float(prev_bbox[3]), float(next_bbox[3])),
            ]
            merged[-1] = combined
        else:
            merged.append(block)
    return merged


def export_markdown(pages: list[ExportPage], title: str = "Document") -> str:
    """Markdown multi-colonnes : titres, paragraphes et tableaux."""
    parts: list[str] = [f"# {title}", ""]
    for page in _strip_running_margins(pages):
        parts.extend([f"<!-- page {page.number} -->", ""])
        for block in _merge_paragraphs(page.blocks):
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
                parts.extend([_markdown_styled(text, block), ""])
    return "\n".join(parts).strip() + "\n"


def export_docx(pages: list[ExportPage], path: str | Path, title: str = "Document") -> Path:
    """DOCX : titres, paragraphes, tableaux, numérotation d'articles intacte."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    document = Document()
    document.add_heading(title, level=0)
    for page_index, page in enumerate(_strip_running_margins(pages)):
        if page_index > 0:
            document.add_page_break()  # type: ignore[no-untyped-call]
        blocks = _merge_paragraphs(page.blocks)
        positions = [
            (float((block.get("bbox") or [0, 0, 0, 0])[0]),
             float((block.get("bbox") or [0, 0, 0, 0])[2]))
            for block in blocks
        ]
        page_left = min((left for left, _right in positions), default=0.0)
        page_right = max((right for _left, right in positions), default=0.0)
        page_center = (page_left + page_right) / 2.0
        for block in blocks:
            kind = str(block.get("kind", "line"))
            text = _block_text(block)
            size, name, bold, italic = _block_style(block)
            if kind == "title":
                paragraph = document.add_heading(text, level=2)
            elif kind == "table":
                grid = block.get("grid") or []
                if grid:
                    table = document.add_table(rows=len(grid), cols=len(grid[0]))
                    table.style = "Table Grid"
                    for row_index, row in enumerate(grid):
                        for col_index, cell in enumerate(row):
                            table.cell(row_index, col_index).text = str(cell)
                continue
            else:
                paragraph = document.add_paragraph(text)
            bbox = block.get("bbox") or [0, 0, 0, 0]
            try:
                x0 = float(bbox[0])
                x1 = float(bbox[2])
            except (TypeError, ValueError, IndexError):
                x0, x1 = page_left, page_right
            if kind == "title" and abs((x0 + x1) / 2.0 - page_center) < 20.0:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif kind != "title":
                indent = x0 - page_left
                if 8.0 < indent < 220.0:
                    paragraph.paragraph_format.left_indent = Pt(indent)
            for run in paragraph.runs:
                if size > 0:
                    run.font.size = Pt(min(36.0, size))
                if name:
                    run.font.name = name
                run.bold = bold
                run.italic = italic
                if kind == "title":
                    run.font.color.rgb = RGBColor(0, 0, 0)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))
    return target


def export_xlsx(pages: list[ExportPage], path: str | Path, title: str = "Document") -> Path:
    """XLSX : une feuille, cellules texte, formules neutralisées."""
    import openpyxl
    from openpyxl.styles import Font

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = title[:31] or "Document"
    row_index = 1
    for page in _strip_running_margins(pages):
        sheet.cell(row=row_index, column=1, value=f"Page {page.number}")
        row_index += 1
        for block in page.blocks:
            kind = str(block.get("kind", "line"))
            size, name, bold, italic = _block_style(block)
            font = Font(
                name=name or None,
                size=min(36.0, size) if size > 0 else None,
                bold=bold,
                italic=italic,
            )
            if kind == "table":
                grid = block.get("grid") or []
                for row in grid:
                    for col_index, value in enumerate(row, start=1):
                        cell = sheet.cell(row=row_index, column=col_index, value=_safe_cell(value))
                        cell.data_type = "s"
                        cell.font = font
                    row_index += 1
            else:
                cell = sheet.cell(row=row_index, column=1, value=_safe_cell(_block_text(block)))
                cell.data_type = "s"
                cell.font = font
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

    def pdf_font(name: str, bold: bool, italic: bool) -> str:
        lowered = name.lower()
        if "times" in lowered:
            base = "Times"
        elif "courier" in lowered:
            base = "Courier"
        else:
            base = "Helvetica"
        if bold and italic:
            return f"{base}-BoldItalic"
        if bold:
            return f"{base}-Bold"
        if italic:
            return f"{base}-Italic"
        return base

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
            style_size, style_name, bold, italic = _block_style(block)
            font_size = (
                max(5.0, min(16.0, style_size))
                if style_size > 0
                else max(6.0, min(12.0, (dpi / 300.0) * 9.0))
            )
            font = pdf_font(style_name, bold, italic)
            for line_index, line in enumerate(text.splitlines() or [text]):
                baseline = page_height - y0 - (line_index + 1) * (font_size + 1.5)
                text_object = pdf.beginText()
                text_object.setTextRenderMode(3)  # invisible, recherchable
                text_object.setFont(font, font_size)
                text_object.setTextOrigin(x0, baseline)
                text_object.textLine(line[:180])
                pdf.drawText(text_object)
        pdf.showPage()
    pdf.save()
    return target
