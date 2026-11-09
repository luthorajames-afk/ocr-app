"""Moteur d'export : génère HTML, Markdown, DOCX, XLSX, TXT, PDF
à partir du même modèle de données (Document / Page / Block).

Les cellules ``None`` (trous dans un tableau) et les tableaux vides sont gérés
sans produire de lignes Markdown/HTML vides. Les textes sont échappés pour
Markdown et HTML, et les écritures sont explicites en UTF-8 (R14).
"""

from __future__ import annotations

import html as html_module
import os
import re
from pathlib import Path
from typing import List, Optional

from ocr_app.core.data_model import BlockType, Cell, Document, Table


# Caractères interdits dans un titre de feuille Excel.
_XLSX_INVALID_TITLE = re.compile(r"[\[\]:*?/\\]")
# Caractères de contrôle interdits dans une cellule XLSX.
_XLSX_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Largeur maximale d'une ligne PDF (stub) et hauteur de ligne.
_PDF_MAX_CHARS = 100
_PDF_LINE_HEIGHT = 20
_PDF_TOP = 800
_PDF_BOTTOM = 72


class Exporter:
    """Exporte un document vers plusieurs formats."""

    def __init__(self, document: Document) -> None:
        self.document = document

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _table_rows(table: Optional[Table]) -> List[List[Optional[Cell]]]:
        """Lignes non vides du tableau (copie superficielle, sans trous de ligne)."""
        if table is None or not table.grid:
            return []
        return [list(row) for row in table.grid if row]

    @staticmethod
    def _md_cell(cell: Optional[Cell]) -> str:
        if cell is None or not cell.text:
            return ""
        text = cell.text
        # Chemin rapide : la majorité des cellules ne contient aucun caractère
        # à échapper (évite six remplacements par cellule).
        if (
            "|" not in text
            and "\\" not in text
            and "\n" not in text
            and "\r" not in text
        ):
            return text
        text = text.replace("\\", "\\\\").replace("|", "\\|")
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        return text

    @staticmethod
    def _html_text(value: str) -> str:
        if "&" not in value and "<" not in value and ">" not in value:
            return value
        return html_module.escape(value, quote=False)

    @staticmethod
    def _xlsx_text(value: str) -> str:
        return _XLSX_ILLEGAL_CHARS.sub("", value or "")

    @staticmethod
    def _xlsx_sheet_title(title: str) -> str:
        cleaned = _XLSX_ILLEGAL_CHARS.sub("", title or "")
        cleaned = _XLSX_INVALID_TITLE.sub("_", cleaned).strip().strip("'")
        if not cleaned:
            return "Feuille1"
        return cleaned[:31].rstrip("'") or "Feuille1"

    @staticmethod
    def _pdf_text(value: str) -> str:
        """Texte compatible avec la police standard PDF (WinAnsi)."""
        if not value:
            return ""
        flat = " ".join(value.split())
        safe = flat.encode("cp1252", errors="replace").decode("cp1252")
        return safe[:_PDF_MAX_CHARS]

    # ---------------------------------------------------------------- text
    def to_plain_text(self) -> str:
        parts: List[str] = []
        for page in self.document.pages:
            parts.append(f"=== Page {page.number} ===")
            for block in page.blocks:
                if block.type == BlockType.TABLE:
                    # Le texte brut ne reconstruit pas la grille : marqueur
                    # explicite, y compris pour un tableau vide ou absent.
                    parts.append("[tableau]")
                elif block.text:
                    parts.append(block.text)
        return "\n".join(parts)

    # ---------------------------------------------------------------- markdown
    def to_markdown(self) -> str:
        parts: List[str] = [f"# {self.document.title}"]
        for page in self.document.pages:
            parts.append(f"## Page {page.number}")
            for block in page.blocks:
                if block.type == BlockType.TITLE:
                    parts.append(f"# {block.text}")
                elif block.type == BlockType.TABLE:
                    parts.append(
                        self._table_to_md(block.table)
                        if block.table is not None
                        else "[tableau]"
                    )
                elif block.type == BlockType.IMAGE:
                    parts.append("[image]")
                elif block.text:
                    parts.append(block.text)
        # Une seule ligne vide entre les sections : plus de lignes vides
        # accumulées autour des tableaux vides.
        return "\n\n".join(part for part in parts if part) + "\n"

    @classmethod
    def _table_to_md(cls, table: Optional[Table]) -> str:
        rows = cls._table_rows(table)
        if not rows:
            return "[tableau vide]"

        width = max(len(row) for row in rows)
        if width == 0:
            return "[tableau vide]"

        lines: List[str] = []
        header = list(rows[0]) + [None] * (width - len(rows[0]))
        lines.append("| " + " | ".join(cls._md_cell(c) for c in header) + " |")
        lines.append("|" + "---|" * width)
        for row in rows[1:]:
            padded = list(row) + [None] * (width - len(row))
            lines.append("| " + " | ".join(cls._md_cell(c) for c in padded) + " |")
        return "\n".join(lines)

    # ---------------------------------------------------------------- html
    def to_html(self) -> str:
        title = self._html_text(self.document.title)
        parts: List[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>{title}</title></head><body>",
            f"<h1>{title}</h1>",
        ]
        for page in self.document.pages:
            parts.append(f"<div class='page' data-page='{page.number}'>")
            parts.append(f"<h2>Page {page.number}</h2>")
            for block in page.blocks:
                if block.type == BlockType.TITLE:
                    parts.append(f"<h1>{self._html_text(block.text)}</h1>")
                elif block.type == BlockType.TABLE:
                    parts.append(
                        self._table_to_html(block.table)
                        if block.table is not None
                        else "<p>[tableau]</p>"
                    )
                elif block.type == BlockType.IMAGE:
                    parts.append("<div class='image'>[image]</div>")
                else:
                    parts.append(f"<p>{self._html_text(block.text)}</p>")
            parts.append("</div>")
        parts.append("</body></html>")
        return "\n".join(parts)

    @classmethod
    def _table_to_html(cls, table: Optional[Table]) -> str:
        rows = cls._table_rows(table)
        if not rows:
            return "<p>[tableau vide]</p>"
        width = max(len(row) for row in rows)
        if width == 0:
            return "<p>[tableau vide]</p>"

        html: List[str] = ["<table border='1'>"]
        for i, row in enumerate(rows):
            tag = "th" if i == 0 else "td"
            padded = list(row) + [None] * (width - len(row))
            html.append("<tr>")
            for cell in padded:
                text = cls._html_text(cell.text) if cell is not None else ""
                html.append(f"<{tag}>{text}</{tag}>")
            html.append("</tr>")
        html.append("</table>")
        return "\n".join(html)

    # ---------------------------------------------------------------- files
    @staticmethod
    def _ensure_parent(path: str | os.PathLike[str]) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _write_text(cls, path: str | os.PathLike[str], text: str) -> None:
        cls._ensure_parent(path)
        Path(path).write_text(text, encoding="utf-8", newline="\n")

    def save_plain_text(self, path: str | os.PathLike[str]) -> None:
        self._write_text(path, self.to_plain_text())

    def save_markdown(self, path: str | os.PathLike[str]) -> None:
        self._write_text(path, self.to_markdown())

    def save_html(self, path: str | os.PathLike[str]) -> None:
        self._write_text(path, self.to_html())

    def save_docx(self, path: str | os.PathLike[str]) -> None:
        """Export DOCX (stub de squelette)."""
        try:
            from docx import Document as DocxDocument  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("python-docx n'est pas installé") from exc
        doc = DocxDocument()
        doc.add_heading(self.document.title, 0)
        for page in self.document.pages:
            doc.add_heading(f"Page {page.number}", level=1)
            for block in page.blocks:
                if block.type == BlockType.TABLE and block.table is not None:
                    self._docx_table(doc, block.table)
                else:
                    doc.add_paragraph(block.text)
        self._ensure_parent(path)
        doc.save(str(path))

    @classmethod
    def _docx_table(cls, doc, table: Table) -> None:
        rows = cls._table_rows(table)
        if not rows:
            doc.add_paragraph("[tableau vide]")
            return
        n_rows = len(rows)
        n_cols = max(1, max(len(row) for row in rows))
        t = doc.add_table(rows=n_rows, cols=n_cols)
        t.style = "Table Grid"
        for r_idx, row in enumerate(rows):
            for c_idx, cell in enumerate(row):
                if cell is not None:
                    t.cell(r_idx, c_idx).text = cell.text

    def save_xlsx(self, path: str | os.PathLike[str]) -> None:
        """Export XLSX (stub de squelette)."""
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("openpyxl n'est pas installé") from exc
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = self._xlsx_sheet_title(self.document.title)
        r = 1
        for page in self.document.pages:
            ws.cell(row=r, column=1, value=f"Page {page.number}")
            r += 1
            for block in page.blocks:
                if block.type == BlockType.TABLE and block.table is not None:
                    rows = self._table_rows(block.table)
                    if not rows:
                        ws.cell(row=r, column=1, value="[tableau vide]")
                        r += 1
                        continue
                    for row in rows:
                        for c_idx, cell in enumerate(row, start=1):
                            value = self._xlsx_text(cell.text) if cell else ""
                            ws.cell(row=r, column=c_idx, value=value)
                        r += 1
                else:
                    ws.cell(row=r, column=1, value=self._xlsx_text(block.text))
                    r += 1
        self._ensure_parent(path)
        wb.save(str(path))

    def save_pdf(self, path: str | os.PathLike[str]) -> None:
        """Export PDF avec couche de texte (stub de squelette)."""
        try:
            from reportlab.lib.pagesizes import A4  # type: ignore
            from reportlab.pdfgen import canvas  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("reportlab n'est pas installé") from exc
        self._ensure_parent(path)
        c = canvas.Canvas(str(path), pagesize=A4)
        c.setTitle(self.document.title)
        for page in self.document.pages:
            c.drawString(72, _PDF_TOP, f"Page {page.number}")
            y = _PDF_TOP - 40
            for block in page.blocks:
                text = self._pdf_text(block.text)
                if not text:
                    continue
                if y < _PDF_BOTTOM:
                    c.showPage()
                    y = _PDF_TOP
                c.drawString(72, y, text)
                y -= _PDF_LINE_HEIGHT
            c.showPage()
        c.save()
