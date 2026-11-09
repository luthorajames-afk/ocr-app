"""Moteur d'export : génère HTML, Markdown, DOCX, XLSX, TXT, PDF
à partir du même modèle de données (Document / Page / Block).

Les cellules ``None`` (trous dans un tableau) et les tableaux vides sont gérés
sans produire de lignes Markdown/HTML vides. Les textes sont échappés pour
Markdown et HTML, et les écritures sont explicites en UTF-8 (R14).

Phase 6 :
- cellules fusionnées (``Cell.merged_from``) gérées dans DOCX (merge),
  XLSX (``merge_cells``), HTML (``colspan``/``rowspan``) et PDF (SPAN) —
  avec tolérance aux régions incohérentes (bornage, chevauchements ignorés) ;
- génération HTML « propre » (``clean_page_html`` / ``clean_document_html``)
  sans ancres ni liens d'édition, destinée au presse-papiers riche (R9 :
  le contenu reflète les éditions car les ``Page`` sont partagées) ;
- exports DOCX / XLSX / PDF réels (python-docx, openpyxl, reportlab).
"""

from __future__ import annotations

import html as html_module
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from ocr_app.core.data_model import BlockType, Cell, Document, Page, Table


# Caractères interdits dans un titre de feuille Excel.
_XLSX_INVALID_TITLE = re.compile(r"[\[\]:*?/\\]")
# Caractères de contrôle interdits dans une cellule XLSX.
_XLSX_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Budget de cellules d'un layout de tableau : au-delà, les dimensions
# « déclarées » (``Table.rows`` / ``Table.cols``) sont ignorées au profit de
# l'étendue réelle de la grille. Protège contre un JSON corrompu qui
# déclarerait des dimensions énormes (allocation géante → blocage / OOM).
_MAX_LAYOUT_CELLS = 1_000_000

# Largeur minimale d'une colonne PDF pour y placer un ``Paragraph`` :
# en dessous, les marges internes (12 pt) dépassent la largeur disponible
# et reportlab lève « negative availWidth ».
_MIN_PDF_PARAGRAPH_COL = 12.0


def _split_lines(text: str) -> List[str]:
    """Lignes d'un texte bloc : ``\\r\\n`` / ``\\r`` normalisés en ``\\n``.

    Un ``\\r`` résiduel (retours chariot d'OCR) part sinon tel quel dans le
    XML DOCX (sérialisé ``&#13;``), rendu aléatoirement par Word.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines if lines else [""]


# ---------------------------------------------------------------------------
# Layout d'un tableau (grille complète + fusions)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _TableLayout:
    """Grille normalisée d'un tableau, prête à dessiner.

    ``matrix`` contient les cellules (``None`` = trou) ; ``spans`` liste les
    régions fusionnées par leur ancre ``(r0, c0, r1, c1)`` et ``covered`` les
    positions recouvertes par une fusion (à ignorer au rendu). Le texte d'une
    cellule fusionnée est replacé sur l'ancre de sa région.
    """

    rows: int
    cols: int
    matrix: Tuple[Tuple[Optional[Cell], ...], ...]
    spans: Tuple[Tuple[int, int, int, int], ...]
    covered: FrozenSet[Tuple[int, int]]


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

    @classmethod
    def _layout(cls, table: Optional[Table]) -> _TableLayout:
        """Grille complète (bordée par ``rows``/``cols`` déclarés) + fusions.

        Tolérant aux données imparfaites : ``merged_from`` est borné à la
        grille, les régions dégénérées (une seule cellule) sont ignorées et
        les chevauchements sont rejetés sans exception.
        """
        grid = table.grid if table is not None else []
        declared_rows = table.rows if table is not None else 0
        declared_cols = table.cols if table is not None else 0
        n_rows = max(len(grid), declared_rows)
        n_cols = max(max((len(row) for row in grid), default=0), declared_cols)
        if n_rows * n_cols > _MAX_LAYOUT_CELLS:
            # Dimensions déclarées invraisemblables : bornage sur la grille
            # réellement présente (comportement dégradé mais sûr).
            n_rows = len(grid)
            n_cols = max((len(row) for row in grid), default=0)

        matrix: List[List[Optional[Cell]]] = [
            [None] * n_cols for _ in range(n_rows)
        ]
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if r < n_rows and c < n_cols:
                    matrix[r][c] = cell

        spans: List[Tuple[int, int, int, int]] = []
        covered: Set[Tuple[int, int]] = set()
        anchors: Set[Tuple[int, int]] = set()
        for r in range(n_rows):
            for c in range(n_cols):
                cell = matrix[r][c]
                if cell is None or cell.merged_from is None:
                    continue
                r0, c0, r1, c1 = cell.merged_from
                r0, r1 = sorted(
                    (max(0, min(r0, n_rows - 1)), max(0, min(r1, n_rows - 1)))
                )
                c0, c1 = sorted(
                    (max(0, min(c0, n_cols - 1)), max(0, min(c1, n_cols - 1)))
                )
                if (r1 - r0 + 1) * (c1 - c0 + 1) <= 1:
                    continue
                positions = [
                    (rr, cc)
                    for rr in range(r0, r1 + 1)
                    for cc in range(c0, c1 + 1)
                ]
                if any(
                    pos in covered or pos in anchors for pos in positions
                ):
                    # Chevauchement avec une fusion déjà acceptée : la fusion
                    # est ignorée (rendu en cellules séparées), sans crash.
                    continue
                # Le texte porté par la cellule fusionnée est affiché à l'ancre.
                matrix[r0][c0] = cell
                anchors.add((r0, c0))
                covered.update(pos for pos in positions if pos != (r0, c0))
                spans.append((r0, c0, r1, c1))

        return _TableLayout(
            rows=n_rows,
            cols=n_cols,
            matrix=tuple(tuple(row) for row in matrix),
            spans=tuple(spans),
            covered=frozenset(covered),
        )

    @staticmethod
    def _span_map(layout: _TableLayout) -> Dict[Tuple[int, int], Tuple[int, int]]:
        """Ancre → (rowspan, colspan) des régions fusionnées."""
        return {
            (r0, c0): (r1 - r0 + 1, c1 - c0 + 1)
            for r0, c0, r1, c1 in layout.spans
        }

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
    def _html_multiline(value: str) -> str:
        """Texte échappé, retours à la ligne rendus par ``<br>``."""
        text = Exporter._html_text(value)
        if "\n" not in text and "\r" not in text:
            return text
        return (
            text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        )

    @staticmethod
    def _xlsx_text(value: str) -> str:
        # ``\r`` résiduel normalisé (rendu aléatoire dans Excel), caractères
        # de contrôle interdits retirés.
        flat = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        return _XLSX_ILLEGAL_CHARS.sub("", flat)

    @staticmethod
    def _xlsx_sheet_title(title: str) -> str:
        cleaned = _XLSX_ILLEGAL_CHARS.sub("", title or "")
        cleaned = _XLSX_INVALID_TITLE.sub("_", cleaned).strip().strip("'")
        if not cleaned:
            return "Feuille1"
        return cleaned[:31].rstrip("'") or "Feuille1"

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
        """Tableau HTML complet : bordures, ``th`` en première ligne,
        ``rowspan``/``colspan`` pour les cellules fusionnées."""
        layout = cls._layout(table)
        if layout.rows == 0 or layout.cols == 0:
            return "<p>[tableau vide]</p>"

        span_map = cls._span_map(layout)
        html: List[str] = ["<table border='1'>"]
        for r in range(layout.rows):
            html.append("<tr>")
            for c in range(layout.cols):
                if (r, c) in layout.covered:
                    continue
                cell = layout.matrix[r][c]
                text = cls._html_text(cell.text) if cell is not None else ""
                attrs = ""
                rowspan, colspan = span_map.get((r, c), (1, 1))
                if rowspan > 1:
                    attrs += f" rowspan='{rowspan}'"
                if colspan > 1:
                    attrs += f" colspan='{colspan}'"
                tag = "th" if r == 0 else "td"
                html.append(f"<{tag}{attrs}>{text}</{tag}>")
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

    # ---------------------------------------------------------------- docx
    def save_docx(self, path: str | os.PathLike[str]) -> None:
        """Export DOCX réel : titres (Heading 1/2), paragraphes, tableaux
        « Table Grid », une section de titre par page (pagination page à page)."""
        try:
            from docx import Document as DocxDocument  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("python-docx n'est pas installé") from exc

        doc = DocxDocument()
        if self.document.title.strip():
            doc.add_heading(self.document.title, 0)
        for page in self.document.pages:
            doc.add_heading(f"Page {page.number}", level=1)
            for block in page.blocks:
                if block.type == BlockType.TABLE:
                    self._docx_table(doc, block.table)
                elif block.type == BlockType.TITLE:
                    doc.add_heading(block.text, level=2)
                elif block.type == BlockType.IMAGE:
                    doc.add_paragraph("[image]")
                else:
                    self._docx_lines(doc, block.text)
        self._ensure_parent(path)
        doc.save(str(path))

    @staticmethod
    def _docx_lines(doc, text: str) -> None:
        """Paragraphe(s) DOCX : chaque ligne du bloc devient un paragraphe."""
        lines = _split_lines(text)
        for line in lines:
            doc.add_paragraph(line)

    @classmethod
    def _docx_table(cls, doc, table: Optional[Table]) -> None:
        layout = cls._layout(table)
        if layout.rows == 0 or layout.cols == 0:
            doc.add_paragraph("[tableau vide]")
            return
        t = doc.add_table(rows=layout.rows, cols=layout.cols)
        try:
            t.style = "Table Grid"
        except KeyError:  # pragma: no cover - gabarit sans le style
            pass  # tableau nu : l'export reste utilisable

        n_cols = layout.cols
        # Une seule lecture de la grille python-docx : ``t.cell(r, c)``
        # reconstruit la liste complète des cellules à chaque appel
        # (O(lignes × colonnes) par accès), ce qui coûte des secondes sur un
        # gros document. Les objets capturés restent valides après fusion.
        try:
            cells = t._cells  # noqa: SLF001 - contournement de perf maîtrisé
        except AttributeError:  # pragma: no cover - autre version python-docx
            cells = [cell for row in t.rows for cell in row.cells]

        # Les fusions d'abord : les cellules recouvertes disparaissent du
        # gabarit, ensuite on ne remplit que les cellules visibles.
        for r0, c0, r1, c1 in layout.spans:
            cells[r0 * n_cols + c0].merge(cells[r1 * n_cols + c1])
        for r in range(layout.rows):
            for c in range(n_cols):
                if (r, c) in layout.covered:
                    continue
                cell = layout.matrix[r][c]
                cls._docx_fill_cell(
                    cells[r * n_cols + c], cell.text if cell else ""
                )

    @staticmethod
    def _docx_fill_cell(cell, text: str) -> None:
        """Remplit une cellule DOCX : une ligne du bloc = un paragraphe."""
        lines = _split_lines(text)
        cell.paragraphs[0].text = lines[0]
        for line in lines[1:]:
            cell.add_paragraph(line)

    # ---------------------------------------------------------------- xlsx
    def save_xlsx(self, path: str | os.PathLike[str]) -> None:
        """Export XLSX réel : une feuille par document, tableaux posés en
        vraies grilles de cellules (une cellule par valeur), fusions incluses."""
        try:
            import openpyxl  # type: ignore
            from openpyxl.styles import Font  # type: ignore
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("openpyxl n'est pas installé") from exc

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = self._xlsx_sheet_title(self.document.title)
        bold = Font(bold=True)

        def put(row: int, column: int, value: str, *, header: bool = False):
            """Écrit une cellule texte ; « =… » reste une chaîne (pas une
            formule : openpyxl classerait le texte OCR comme formule et
            Excel tenterait de l'évaluer, voire l'exécuter via DDE)."""
            cell = ws.cell(row=row, column=column, value=value)
            if cell.data_type == "f":
                cell.data_type = "s"
            if header:
                cell.font = bold
            return cell

        row = 1
        if self.document.title.strip():
            put(
                row, 1, self._xlsx_text(self.document.title), header=True
            )
            row += 2

        for page in self.document.pages:
            put(row, 1, f"Page {page.number}", header=True)
            row += 1
            for block in page.blocks:
                if block.type == BlockType.TABLE:
                    row = self._xlsx_table(ws, block.table, row, bold, put)
                elif block.type == BlockType.TITLE:
                    put(row, 1, self._xlsx_text(block.text), header=True)
                    row += 1
                elif block.type == BlockType.IMAGE:
                    put(row, 1, "[image]")
                    row += 1
                else:
                    for line in (block.text or "").split("\n") or [""]:
                        put(row, 1, self._xlsx_text(line))
                        row += 1
            row += 1  # ligne vide entre les pages

        self._ensure_parent(path)
        wb.save(str(path))

    @classmethod
    def _xlsx_table(cls, ws, table: Optional[Table], row: int, bold, put) -> int:
        """Écrit le tableau à partir de ``row`` ; retourne la ligne suivante.

        ``put`` est la fabrique de cellule fournie par ``save_xlsx``
        (force les textes « =… » en chaînes, applique la police d'en-tête).
        """
        layout = cls._layout(table)
        if layout.rows == 0 or layout.cols == 0:
            put(row, 1, "[tableau vide]")
            return row + 1

        for r in range(layout.rows):
            for c in range(layout.cols):
                if (r, c) in layout.covered:
                    continue
                cell = layout.matrix[r][c]
                value = cls._xlsx_text(cell.text) if cell is not None else ""
                put(row + r, 1 + c, value, header=(r == 0))
        for r0, c0, r1, c1 in layout.spans:
            ws.merge_cells(
                start_row=row + r0,
                start_column=1 + c0,
                end_row=row + r1,
                end_column=1 + c1,
            )
        return row + layout.rows + 1

    # ---------------------------------------------------------------- pdf
    def save_pdf(self, path: str | os.PathLike[str]) -> None:
        """Export PDF réel (reportlab) : couche de texte sélectionnable,
        pagination automatique avec numéros de page, titres, paragraphes et
        tableaux dessinés (grilles, fusions).

        Les polices standard (Helvetica) couvrent latin-1 / WinAnsi ; les
        caractères hors latin-1 sont remplacés par « ? » (repli sûr), le
        texte reste lisible et sélectionnable.
        """
        try:
            from reportlab.lib import colors  # type: ignore
            from reportlab.lib.pagesizes import A4  # type: ignore
            from reportlab.lib.styles import ParagraphStyle  # type: ignore
            from reportlab.pdfbase.pdfmetrics import (  # type: ignore
                stringWidth as string_width,
            )
            from reportlab.platypus import (  # type: ignore
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:  # pragma: no cover - dépendance optionnelle
            raise RuntimeError("reportlab n'est pas installé") from exc

        self._ensure_parent(path)
        margin = 56.0
        template = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=margin,
            title=self.document.title or "AOCRI",
            author="AOCRI — Kriesty Solutions",
        )
        available_width = A4[0] - 2 * margin
        styles = {
            "title": ParagraphStyle(
                "AOCRI-Title",
                fontName="Helvetica-Bold",
                fontSize=18,
                leading=22,
                spaceAfter=14,
            ),
            "page": ParagraphStyle(
                "AOCRI-Page",
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=16,
                spaceBefore=10,
                spaceAfter=8,
            ),
            "block_title": ParagraphStyle(
                "AOCRI-BlockTitle",
                fontName="Helvetica-Bold",
                fontSize=11.5,
                leading=14,
                spaceBefore=8,
                spaceAfter=4,
            ),
            "body": ParagraphStyle(
                "AOCRI-Body",
                fontName="Helvetica",
                fontSize=10.5,
                leading=14,
                spaceAfter=6,
            ),
            "cell": ParagraphStyle(
                "AOCRI-Cell",
                fontName="Helvetica",
                fontSize=9,
                leading=11,
            ),
            "muted": ParagraphStyle(
                "AOCRI-Muted",
                fontName="Helvetica-Oblique",
                fontSize=9.5,
                leading=12,
                spaceAfter=6,
            ),
        }

        def _footer(current_canvas, _template) -> None:
            current_canvas.saveState()
            current_canvas.setFont("Helvetica", 9)
            current_canvas.setFillColor(colors.grey)
            current_canvas.drawCentredString(
                A4[0] / 2.0, 32, f"Page {current_canvas.getPageNumber()}"
            )
            current_canvas.restoreState()

        story: List[object] = []
        title = self.document.title.strip()
        if title:
            story.append(Paragraph(_pdf_flowable_text(title), styles["title"]))

        for page in self.document.pages:
            story.append(
                Paragraph(_pdf_flowable_text(f"Page {page.number}"), styles["page"])
            )
            for block in page.blocks:
                if block.type == BlockType.TABLE:
                    story.append(
                        self._pdf_table(
                            block.table,
                            styles,
                            colors,
                            Paragraph,
                            Table,
                            TableStyle,
                            available_width,
                            string_width,
                        )
                    )
                    story.append(Spacer(1, 8))
                elif block.type == BlockType.TITLE:
                    story.append(
                        Paragraph(
                            _pdf_flowable_text(block.text), styles["block_title"]
                        )
                    )
                elif block.type == BlockType.IMAGE:
                    story.append(Paragraph("[image]", styles["muted"]))
                elif block.text.strip():
                    story.append(
                        Paragraph(_pdf_flowable_text(block.text), styles["body"])
                    )
            story.append(Spacer(1, 10))

        if not story:
            story.append(Paragraph("Document vide", styles["muted"]))

        template.build(story, onFirstPage=_footer, onLaterPages=_footer)

    @classmethod
    def _pdf_table(
        cls,
        table: Optional[Table],
        styles,
        colors,
        paragraph_cls,
        table_cls,
        style_cls,
        available_width: float,
        string_width,
    ):
        """Flowable reportlab du tableau : grille dessinée, fusions SPAN.

        Les cellules courtes sont posées en texte nu (beaucoup moins coûteux
        qu'un ``Paragraph`` : pas de mesure par mot ni d'objet par cellule) ;
        seules les cellules dont une ligne dépasse la colonne reçoivent un
        ``Paragraph`` (retour à la ligne correct).
        """
        layout = cls._layout(table)
        if layout.rows == 0 or layout.cols == 0:
            return paragraph_cls("[tableau vide]", styles["muted"])

        span_map = cls._span_map(layout)
        col_width = available_width / layout.cols
        # Sous ~12 pt de largeur, un ``Paragraph`` n'a plus la place de ses
        # marges internes : reportlab lève « negative availWidth ». On retombe
        # sur du texte nu (grille conservée, contenu possiblement serré).
        use_paragraphs = col_width >= _MIN_PDF_PARAGRAPH_COL
        cell_style = styles["cell"]
        font = cell_style.fontName
        font_size = cell_style.fontSize
        # Marges internes gauche + droite par défaut des cellules reportlab.
        inner = 12.0
        data: List[List[object]] = []
        for r in range(layout.rows):
            row: List[object] = []
            for c in range(layout.cols):
                if (r, c) in layout.covered:
                    row.append("")
                    continue
                cell = layout.matrix[r][c]
                raw = cell.text if cell is not None else ""
                if not raw or not raw.strip():
                    row.append("")
                    continue
                plain = _pdf_safe_text(raw)
                fits = False
                if use_paragraphs:
                    try:
                        fits = all(
                            string_width(line, font, font_size)
                            <= col_width - inner
                            for line in plain.split("\n")
                        )
                    except Exception:  # glyphe hors police : paragraphe sûr
                        fits = False
                if fits:
                    row.append(plain)
                elif use_paragraphs:
                    row.append(
                        paragraph_cls(_pdf_flowable_text(raw), cell_style)
                    )
                else:
                    row.append(plain)
            data.append(row)

        commands = [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa1ad")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2fb")),
        ]
        for r0, c0, r1, c1 in layout.spans:
            commands.append(("SPAN", (c0, r0), (c1, r1)))
        if not use_paragraphs:
            commands.append(("FONTSIZE", (0, 0), (-1, -1), 5))
            commands.append(("LEFTPADDING", (0, 0), (-1, -1), 1))
            commands.append(("RIGHTPADDING", (0, 0), (-1, -1), 1))

        widths = [col_width] * layout.cols
        flowable = table_cls(
            data, colWidths=widths, repeatRows=1, style=style_cls(commands)
        )
        flowable.spaceAfter = 8
        return flowable


# ---------------------------------------------------------------------------
# Génération « propre » pour le presse-papiers (sans ancres ni liens, R9)
# ---------------------------------------------------------------------------
def _pdf_safe_text(value: str) -> str:
    """Texte brut compatible WinAnsi, SANS échappement XML (cellules « texte
    nu » des tableaux très étroits, dessinées par reportlab sans ``Paragraph``
    — un texte échappé s'afficherait tel quel : « &amp; »)."""
    return "\n".join(
        _pdf_safe_line(line) for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )


def _pdf_safe_line(line: str) -> str:
    chars: List[str] = []
    for char in line:
        code = ord(char)
        if code < 0x20 and char != "\t":
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            chars.append("?")
            continue
        chars.append(char)
    return "".join(chars)


def _pdf_flowable_text(value: str) -> str:
    """Texte prêt pour un ``Paragraph`` reportlab : caractères hors WinAnsi
    remplacés par « ? », contrôle retiré, XML échappé, ``<br/>`` pour les
    retours à la ligne."""
    safe_lines: List[str] = []
    for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        chars: List[str] = []
        for char in line:
            code = ord(char)
            if code < 0x20 and char not in "\t":
                continue  # caractère de contrôle : retiré silencieusement
            try:
                char.encode("cp1252")
            except UnicodeEncodeError:
                chars.append("?")
                continue
            chars.append(char)
        safe_lines.append("".join(chars))
    return "<br/>".join(Exporter._html_text(line) for line in safe_lines)


def _table_plain_text(table: Optional[Table]) -> str:
    """Grille en texte brut : une ligne par rangée, cellules séparées par
    tabulations (collable dans Excel en colonnes). Les positions recouvertes
    par une fusion restent présentes (vides) pour préserver l'alignement."""
    layout = Exporter._layout(table)
    if layout.rows == 0 or layout.cols == 0:
        return "[tableau vide]"
    lines: List[str] = []
    for r in range(layout.rows):
        cells: List[str] = []
        for c in range(layout.cols):
            cell = None if (r, c) in layout.covered else layout.matrix[r][c]
            cells.append((cell.text if cell is not None else "").strip())
        lines.append("\t".join(cells))
    return "\n".join(lines)


def page_plain_text(page: Page) -> str:
    """Texte brut d'une page (contenu net, tableaux en colonnes)."""
    parts: List[str] = []
    for block in page.blocks:
        if block.type == BlockType.TABLE:
            parts.append(_table_plain_text(block.table))
        elif block.type == BlockType.IMAGE:
            parts.append("[image]")
        elif block.text.strip():
            parts.append(block.text.rstrip())
    return "\n\n".join(parts)


def document_plain_text(document: Document) -> str:
    """Texte brut du document entier, une section « === Page N === » par page."""
    parts: List[str] = []
    for page in document.pages:
        parts.append(f"=== Page {page.number} ===")
        body = page_plain_text(page)
        if body:
            parts.append(body)
    return "\n\n".join(parts)


def page_markdown(page: Page) -> str:
    """Markdown d'une page : titre ``#``, tableaux en pipelines."""
    parts: List[str] = []
    for block in page.blocks:
        if block.type == BlockType.TITLE:
            parts.append(f"# {block.text.strip()}")
        elif block.type == BlockType.TABLE:
            parts.append(
                Exporter._table_to_md(block.table)
                if block.table is not None
                else "[tableau]"
            )
        elif block.type == BlockType.IMAGE:
            parts.append("[image]")
        elif block.text.strip():
            parts.append(block.text.strip())
    return "\n\n".join(parts) + ("\n" if parts else "")


def document_markdown(document: Document) -> str:
    """Markdown du document entier (titre + une section par page)."""
    return Exporter(document).to_markdown()


def clean_page_html(page: Page, *, heading_level: int = 1) -> str:
    """HTML net d'une page : aucun lien ni ancre d'édition (R9).

    Les tableaux sont de vrais ``<table>`` (collables dans Word et Excel),
    avec ``colspan``/``rowspan`` pour les cellules fusionnées ; les retours
    à la ligne des paragraphes deviennent ``<br>``.
    """
    level = min(max(int(heading_level), 1), 6)
    parts: List[str] = []
    for block in page.blocks:
        if block.type == BlockType.TITLE:
            text = Exporter._html_text(block.text.strip())
            parts.append(f"<h{level}>{text}</h{level}>")
        elif block.type == BlockType.TABLE:
            parts.append(
                Exporter._table_to_html(block.table)
                if block.table is not None
                else "<p>[tableau]</p>"
            )
        elif block.type == BlockType.IMAGE:
            parts.append("<p>[image]</p>")
        elif block.text.strip():
            parts.append(f"<p>{Exporter._html_multiline(block.text.strip())}</p>")
    return "\n".join(parts)


def clean_document_html(document: Document) -> str:
    """HTML net du document entier : titre, puis une section par page."""
    parts: List[str] = []
    if document.title.strip():
        parts.append(f"<h1>{Exporter._html_text(document.title)}</h1>")
    for page in document.pages:
        parts.append(f"<h2>Page {page.number}</h2>")
        body = clean_page_html(page, heading_level=3)
        if body:
            parts.append(body)
    return "\n".join(parts)


def html_document(title: str, body: str) -> str:
    """Enveloppe HTML minimale pour un contenu de presse-papiers."""
    safe_title = Exporter._html_text(title or "AOCRI")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title></head><body>{body}</body></html>"
    )
