"""Moteur d'export : génère HTML, Markdown, DOCX, XLSX, TXT, PDF
à partir du même modèle de données (Document / Page / Block).
"""

from __future__ import annotations

from typing import List
from pathlib import Path

from ocr_app.core.data_model import Document, Page, Block, BlockType


class Exporter:
    """Exporte un document vers plusieurs formats."""

    def __init__(self, document: Document) -> None:
        self.document = document

    # ---------------------------------------------------------------- text
    def to_plain_text(self) -> str:
        parts: list[str] = []
        for page in self.document.pages:
            parts.append(f"=== Page {page.number} ===")
            for block in page.blocks:
                if block.type == BlockType.TABLE:
                    parts.append("[tableau]")
                else:
                    parts.append(block.text)
        return "\n".join(parts)

    # ---------------------------------------------------------------- markdown
    def to_markdown(self) -> str:
        parts: list[str] = [f"# {self.document.title}\n"]
        for page in self.document.pages:
            parts.append(f"\n## Page {page.number}\n")
            for block in page.blocks:
                if block.type == BlockType.TITLE:
                    parts.append(f"# {block.text}\n")
                elif block.type == BlockType.TABLE:
                    parts.append(self._table_to_md(block.table) if block.table else "[tableau]\n")
                elif block.type == BlockType.IMAGE:
                    parts.append("[image]\n")
                else:
                    parts.append(f"{block.text}\n")
        return "\n".join(parts)

    @staticmethod
    def _table_to_md(table) -> str:
        if not table or not table.grid:
            return "[tableau vide]\n"
        rows = table.grid
        if not rows:
            return "[tableau vide]\n"
        header = rows[0]
        md = "| " + " | ".join((c.text if c else "") for c in header) + " |\n"
        md += "|" + "---|" * len(header) + "\n"
        for row in rows[1:]:
            md += "| " + " | ".join((c.text if c else "") for c in row) + " |\n"
        return md + "\n"

    # ---------------------------------------------------------------- html
    def to_html(self) -> str:
        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>{self.document.title}</title></head><body>",
            f"<h1>{self.document.title}</h1>",
        ]
        for page in self.document.pages:
            parts.append(f"<div class='page' data-page='{page.number}'>")
            parts.append(f"<h2>Page {page.number}</h2>")
            for block in page.blocks:
                if block.type == BlockType.TITLE:
                    parts.append(f"<h1>{block.text}</h1>")
                elif block.type == BlockType.TABLE:
                    parts.append(self._table_to_html(block.table) if block.table else "<p>[tableau]</p>")
                elif block.type == BlockType.IMAGE:
                    parts.append("<div class='image'>[image]</div>")
                else:
                    parts.append(f"<p>{block.text}</p>")
            parts.append("</div>")
        parts.append("</body></html>")
        return "\n".join(parts)

    @staticmethod
    def _table_to_html(table) -> str:
        if not table or not table.grid:
            return "<p>[tableau vide]</p>"
        html = ["<table border='1'>"]
        for i, row in enumerate(table.grid):
            tag = "th" if i == 0 else "td"
            html.append("<tr>")
            for cell in row:
                if cell is None:
                    html.append(f"<{tag}></{tag}>")
                else:
                    html.append(f"<{tag}>{cell.text}</{tag}>")
            html.append("</tr>")
        html.append("</table>")
        return "\n".join(html)

    # ---------------------------------------------------------------- files
    def save_plain_text(self, path: str) -> None:
        Path(path).write_text(self.to_plain_text(), encoding="utf-8")

    def save_markdown(self, path: str) -> None:
        Path(path).write_text(self.to_markdown(), encoding="utf-8")

    def save_html(self, path: str) -> None:
        Path(path).write_text(self.to_html(), encoding="utf-8")

    def save_docx(self, path: str) -> None:
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
                if block.type == BlockType.TABLE and block.table:
                    self._docx_table(doc, block.table)
                else:
                    doc.add_paragraph(block.text)
        doc.save(path)

    @staticmethod
    def _docx_table(doc, table) -> None:
        rows = max(len(table.grid), 1)
        cols = max((len(r) for r in table.grid), default=1)
        t = doc.add_table(rows=rows, cols=cols)
        t.style = "Table Grid"
        for r_idx, row in enumerate(table.grid):
            for c_idx, cell in enumerate(row):
                if cell is not None:
                    t.cell(r_idx, c_idx).text = cell.text

    def save_xlsx(self, path: str) -> None:
        """Export XLSX (stub de squelette)."""
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("openpyxl n'est pas installé") from exc
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = self.document.title[:31]
        r = 1
        for page in self.document.pages:
            ws.cell(row=r, column=1, value=f"Page {page.number}")
            r += 1
            for block in page.blocks:
                if block.type == BlockType.TABLE and block.table:
                    for row in block.table.grid:
                        ws.append([c.text if c else "" for c in row])
                        r += 1
                else:
                    ws.cell(row=r, column=1, value=block.text)
                    r += 1
        wb.save(path)

    def save_pdf(self, path: str) -> None:
        """Export PDF avec couche de texte (stub de squelette)."""
        try:
            from reportlab.lib.pagesizes import A4  # type: ignore
            from reportlab.pdfgen import canvas  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("reportlab n'est pas installé") from exc
        c = canvas.Canvas(path, pagesize=A4)
        c.setTitle(self.document.title)
        for page in self.document.pages:
            c.drawString(72, 800, f"Page {page.number}")
            y = 760
            for block in page.blocks:
                c.drawString(72, y, block.text[:100])
                y -= 20
            c.showPage()
        c.save()