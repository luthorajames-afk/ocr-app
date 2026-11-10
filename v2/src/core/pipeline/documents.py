"""T1.3 — Chargement des documents : pages rendues + texte natif PDF.

``load_document`` retourne une page par feuille, avec l'image rendue (300 DPI
max, R7) et le texte natif éventuel (PDF non scanné, pypdfium2).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from core.pipeline.interfaces import OcrBlock

MAX_DPI = 300
DEFAULT_DPI = 300
MIN_NATIVE_CHARS = 20
SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Une page chargée : image RGB + texte natif normalisé (vide si scan)."""

    number: int
    image: Image.Image
    native_text: str
    # Blocs natifs segmentés (colonnes, ordre de lecture) — vides pour un scan.
    blocks: tuple[OcrBlock, ...] = field(default_factory=tuple)

    @property
    def has_native_text(self) -> bool:
        return len(self.native_text.strip()) >= MIN_NATIVE_CHARS


def normalize_text(raw: str) -> str:
    """NFKC, espaces unifiés, lignes vides compressées."""
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    compressed: list[str] = []
    for line in lines:
        if not line and compressed and not compressed[-1]:
            continue
        compressed.append(line)
    return "\n".join(compressed).strip()


def _text_layer_words(
    textpage: pdfium.PdfTextPage,
) -> list[tuple[float, float, float, float, str]]:
    """Reconstruit les mots depuis les caractères (texte + géométrie exacte).

    La couche texte de certains PDF scannés corrompt ``get_text_bounded`` avec
    des marqueurs parasites (« achieving t the ») et des rectangles
    multi-lignes. Les caractères, eux, sont fiables : on regroupe par ligne
    (baseline) puis en mots (écart horizontal).
    """
    chars: list[tuple[float, float, float, float, str]] = []
    count = textpage.count_chars()
    for index in range(count):
        left, bottom, right, top = textpage.get_charbox(index)
        char = textpage.get_text_range(index, 1)
        if not char or ord(char[0]) < 32 or char == chr(0xFFFD):
            continue
        chars.append((float(left), float(bottom), float(right), float(top), char))
    if not chars:
        return []

    heights = sorted(char[3] - char[1] for char in chars)
    median_height = heights[len(heights) // 2] if heights else 0.0
    if median_height > 0:
        # Filigranes pivotés : glyphes énormes (57-90 pt), jamais du texte.
        limit = max(30.0, median_height * 3.0)
        chars = [char for char in chars if (char[3] - char[1]) <= limit]
    if not chars:
        return []

    # Lignes : la baseline (bottom) est stable, contrairement au centre.
    # Ancrer sur la baseline MAX du groupe (les jambages descendent plus bas)
    # évite qu'une ligne se fragmente en pseudo-lignes.
    chars.sort(key=lambda char: (-char[1], char[0]))
    lines: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for char in chars:
        if lines and lines[-1][0] - char[1] <= 6.5:
            baseline, items = lines[-1]
            lines[-1] = (max(baseline, char[1]), items + [char])
        else:
            lines.append((char[1], [char]))

    words: list[tuple[float, float, float, float, str]] = []
    for _baseline, items in lines:
        if len(items) == 1 and median_height > 0 and items[0][4].strip():
            height = items[0][3] - items[0][1]
            # Marqueur superscript isolé : hors flux.
            if height < median_height * 0.7:
                continue
        items.sort(key=lambda char: char[0])
        # Seuil de coupure de mot propre à la ligne : le texte en petites
        # capitales espacées (titres) a des écarts inter-lettres plus larges
        # que les espaces de mots d'un paragraphe.
        gaps = [
            items[index + 1][0] - items[index][2]
            for index in range(len(items) - 1)
            if not items[index][4].isspace() and not items[index + 1][4].isspace()
        ]
        # Ne garder que les écarts intra-mot : les grands blancs de colonnes
        # ne doivent pas gonfler le seuil.
        gaps = sorted(gap for gap in gaps if 0.0 < gap <= 3.0)
        if gaps:
            word_gap = min(6.0, max(2.2, gaps[len(gaps) // 2] * 2.2))
        else:
            word_gap = 2.2
        groups: list[list[tuple[float, float, float, float, str]]] = []
        group: list[tuple[float, float, float, float, str]] = []
        previous_right: float | None = None
        for char in items:
            if char[4].isspace():
                if group:
                    groups.append(group)
                    group = []
                previous_right = None
                continue
            if group and previous_right is not None and char[0] - previous_right > word_gap:
                groups.append(group)
                group = []
            group.append(char)
            previous_right = char[2]
        if group:
            groups.append(group)
        for group in groups:
            text = "".join(char[4] for char in group).strip()
            if text:
                words.append(
                    (
                        min(char[0] for char in group),
                        min(char[1] for char in group),
                        max(char[2] for char in group),
                        max(char[3] for char in group),
                        text,
                    )
                )
    return words


def _native_blocks(page: pdfium.PdfPage, page_height: float) -> list[OcrBlock]:
    """Segmente la couche texte d'un PDF natif en blocs ordonnés (multi-colonnes).

    Les rectangles de ``PdfTextPage`` sont en coordonnées PDF (origine
    bas-gauche) ; la bbox retournée est en points, origine haut-gauche.
    """
    textpage = page.get_textpage()
    try:
        rects = _text_layer_words(textpage)
    except Exception:  # noqa: BLE001 — PDF sans géométrie exploitable
        return []

    if not rects:
        return []

    # Hauteur médiane des lignes pour repérer les titres.
    heights = sorted(rect[3] - rect[1] for rect in rects)
    median_height = heights[len(heights) // 2] if heights else 10.0

    return _native_tables_and_columns(rects, page_height, median_height)


def _cluster_columns(
    rects: list[tuple[float, float, float, float, str]],
) -> list[tuple[float, list[tuple[float, float, float, float, str]]]]:
    columns: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for rect in sorted(rects, key=lambda item: item[0]):
        for column_left, column_rects in columns:
            if abs(rect[0] - column_left) < 40.0:
                column_rects.append(rect)
                break
        else:
            columns.append((rect[0], [rect]))
    columns.sort(key=lambda column: column[0])
    return columns


def _native_tables_and_columns(
    rects: list[tuple[float, float, float, float, str]],
    page_height: float,
    median_height: float,
) -> list[OcrBlock]:
    """Blocs ordonnés ; tableaux reconstruits avant le découpage en colonnes."""
    rows = _group_rows(rects)
    word_gap = _median_word_gap(rows)
    cell_gap = max(10.0, word_gap * 2.5)
    table_groups: list[tuple[list[list[tuple[float, float, float, float, str]]], list[list[str]]]] = []
    line_rects: list[tuple[float, float, float, float, str]] = []
    pending_rows: list[list[tuple[float, float, float, float, str]]] = []
    pending_cells: list[list[str]] = []
    for row in rows:
        merged = _merge_row_cells(row, cell_gap)
        if _is_tabular_row(merged, cell_gap):
            pending_rows.append(row)
            pending_cells.append([cell[0] for cell in merged])
        else:
            if pending_rows:
                _flush_table(table_groups, pending_rows, pending_cells, line_rects)
                pending_rows, pending_cells = [], []
            line_rects.extend(row)
    if pending_rows:
        _flush_table(table_groups, pending_rows, pending_cells, line_rects)

    # Lignes physiques : regroupement global par y, AVANT tout découpage en
    # colonnes. Sinon une même ligne visuelle (« REPUBLIC OF … ») est éclatée
    # en un bloc par colonne x.
    all_rows = _group_rows(line_rects)
    word_gap = _median_word_gap(all_rows)
    cell_gap = max(10.0, word_gap * 2.5)
    columns = _cluster_columns(line_rects)
    # Multi-colonnes seulement si au moins 2 colonnes substantielles ; sinon
    # ordre ligne par ligne (documents juridiques à une colonne + tableaux).
    substantial = [rects for _left, rects in columns if len(rects) >= 5]
    use_columns = (
        2 <= len(substantial) <= 4
        and len(line_rects) >= 60
        and any(len(column_rects) >= 15 for _left, column_rects in columns)
    )
    if use_columns:
        blocks = []
        for _column_left, column_rects in columns:
            for row in _group_rows(column_rects):
                row_block = _row_block(row, page_height, median_height, cell_gap)
                if row_block is not None:
                    blocks.append(row_block)
    else:
        blocks = []
        for row in all_rows:
            row_block = _row_block(row, page_height, median_height, cell_gap)
            if row_block is not None:
                blocks.append(row_block)
        blocks.sort(key=lambda block: (block.bbox[1], block.bbox[0]))

    for table_rows, table_cells in table_groups:
        table = _table_block(table_rows, table_cells, page_height)
        position = next(
            (index for index, block in enumerate(blocks) if block.bbox[1] > table.bbox[1]),
            len(blocks),
        )
        blocks.insert(position, table)
    return blocks


def _row_block(
    row: list[tuple[float, float, float, float, str]],
    page_height: float,
    median_height: float,
    cell_gap: float,
) -> OcrBlock | None:
    """Une ligne de texte = un bloc (et non un bloc par mot)."""
    merged = _merge_row_cells(row, cell_gap)
    text = " ".join(cell[0] for cell in merged if cell[0].strip()).strip()
    if not text or not any(char.isalnum() for char in text):
        return None
    left = min(rect[0] for rect in row)
    right = max(rect[2] for rect in row)
    bottom = min(rect[1] for rect in row)
    top = max(rect[3] for rect in row)
    height = top - bottom
    is_title = height >= median_height * 1.3 and len(text) <= 90
    return OcrBlock(
        text=text,
        bbox=(left, page_height - top, right, page_height - bottom),
        confidence=1.0,
        kind="title" if is_title else "line",
    )


def _flush_table(
    table_groups: list[
        tuple[list[list[tuple[float, float, float, float, str]]], list[list[str]]]
    ],
    rows: list[list[tuple[float, float, float, float, str]]],
    cells: list[list[str]],
    line_rects: list[tuple[float, float, float, float, str]],
) -> None:
    """Valide un groupe de lignes tabulaires, sinon le rend en lignes simples.

    Un vrai tableau a au moins 2 lignes et une majorité de lignes à >= 3
    cellules ; un paragraphe justifié isolé est rejeté (faux positif).
    """
    tabular = sum(1 for row in cells if len(row) >= 3)
    if len(rows) >= 2 and tabular >= 2:
        table_groups.append((rows, _merge_continuations(cells)))
    else:
        for row in rows:
            line_rects.extend(row)


def _merge_continuations(cells: list[list[str]]) -> list[list[str]]:
    """Fusionne une ligne de continuation (texte seul) dans la ligne précédente."""
    merged: list[list[str]] = []
    for row in cells:
        non_empty = [index for index, value in enumerate(row) if value.strip()]
        if (
            merged
            and len(non_empty) == 1
            and non_empty[0] == 0
            and len(merged[-1]) >= 3
        ):
            merged[-1][0] = f"{merged[-1][0]} {row[0]}".strip()
            continue
        merged.append(row)
    return merged


def _group_rows(
    rects: list[tuple[float, float, float, float, str]],
) -> list[list[tuple[float, float, float, float, str]]]:
    """Regroupe les rectangles par ligne (tolérance verticale ~4 pt)."""
    rows: list[list[tuple[float, float, float, float, str]]] = []
    for rect in sorted(rects, key=lambda item: -(item[1] + item[3]) / 2.0):
        center = (rect[1] + rect[3]) / 2.0
        for row in rows:
            row_center = sum((item[1] + item[3]) / 2.0 for item in row) / len(row)
            if abs(center - row_center) <= 4.0:
                row.append(rect)
                break
        else:
            rows.append([rect])
    for row in rows:
        row.sort(key=lambda item: item[0])
    return rows


def _median_word_gap(
    rows: list[list[tuple[float, float, float, float, str]]],
) -> float:
    """Espacement médian entre mots d'une ligne (sert de référence)."""
    gaps: list[float] = []
    for row in rows:
        for index in range(len(row) - 1):
            gap = row[index + 1][0] - row[index][2]
            if 0.0 < gap < 40.0:
                gaps.append(gap)
    if not gaps:
        return 4.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def _merge_row_cells(
    row: list[tuple[float, float, float, float, str]],
    cell_gap: float,
) -> list[tuple[str, float, float]]:
    """Fusionne les mots proches en cellules ; sépare sur un grand espace.

    Retourne des tuples (texte, x0, x1). C'est ce qui distingue une phrase
    (mots espacés de ~3-6 pt) d'un tableau (colonnes séparées de 15-60 pt).
    """
    cells: list[tuple[str, float, float]] = []
    heights = sorted(max(0.0, rect[3] - rect[1]) for rect in row)
    median_height = heights[len(heights) // 2] if heights else 0.0
    for left, _bottom, right, _top, text in row:
        clean = normalize_text(text).replace(chr(10), " ").strip()
        if not clean:
            continue
        # Marqueur superscript isolé (a, p, s, c, m) : bruit de couche texte.
        if (
            len(clean) == 1
            and clean.isalpha()
            and median_height > 0
            and (_top - _bottom) < median_height * 0.7
        ):
            continue
        gap = left - cells[-1][2] if cells else 0.0
        numeric_break = (
            bool(cells)
            and gap >= 6.0
            and cells[-1][0][-1:].isdigit()
            and clean[:1].isdigit()
        )
        if cells and gap <= cell_gap and not numeric_break:
            previous_text, previous_left, _previous_right = cells[-1]
            joined = _normalise_cell(f"{previous_text} {clean}".strip())
            cells[-1] = (joined, previous_left, right)
        else:
            cells.append((_normalise_cell(clean), left, right))
    return cells


def _normalise_cell(text: str) -> str:
    """Recolle et regroupe les nombres coupés (ex. « 15 4 » -> « 154 »)."""
    compact = text.replace(" ", "")
    if compact.isdigit():
        if len(compact) <= 6:
            return compact
        if len(compact) <= 15:
            # Regroupement canonique par milliers : la couche texte coupe
            # parfois « 1 431 018 100 » en « 143101 8 1 00 ».
            grouped = f"{int(compact):,}".replace(",", " ")
            return grouped
    return " ".join(text.split())


def _is_tabular_row(cells: list[tuple[str, float, float]], cell_gap: float) -> bool:
    """Ligne tabulaire : au moins 3 cellules et 2 espaces de colonne nets."""
    if len(cells) < 3:
        return False
    gaps = [
        cells[index + 1][1] - cells[index][2]
        for index in range(len(cells) - 1)
        if cells[index + 1][1] > cells[index][2]
    ]
    return sum(1 for gap in gaps if gap >= cell_gap) >= 2


def _table_block(
    rows: list[list[tuple[float, float, float, float, str]]],
    cells: list[list[str]],
    page_height: float,
) -> OcrBlock:
    width = max(len(row) for row in cells)
    grid = tuple(tuple(row + [""] * (width - len(row))) for row in cells)
    left = min(rect[0] for row in rows for rect in row)
    right = max(rect[2] for row in rows for rect in row)
    bottom = min(rect[1] for row in rows for rect in row)
    top = max(rect[3] for row in rows for rect in row)
    text = chr(10).join(chr(9).join(cell for cell in row) for row in grid)
    return OcrBlock(
        text=text,
        bbox=(left, page_height - top, right, page_height - bottom),
        confidence=1.0,
        kind="table",
        grid=grid,
    )


def _render_pdf_page(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    bitmap = page.render(scale=dpi / 72.0)
    image: Image.Image = bitmap.to_pil().convert("RGB")
    return image


def page_count(path: str | Path) -> int:
    """Nombre de pages sans rendu (PDF : len ; image : 1)."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"document introuvable : {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"format non pris en charge : {source.suffix}")
    if source.suffix.lower() != ".pdf":
        return 1
    document = pdfium.PdfDocument(str(source))
    try:
        return len(document)
    finally:
        document.close()


def iter_document_pages(
    path: str | Path, dpi: int = DEFAULT_DPI
) -> Iterator[DocumentPage]:
    """Itère les pages UNE PAR UNE (R7 : une seule page en mémoire).

    Le générateur rend la page, la remet à l'appelant, puis libère son image
    avant de passer à la suivante : un PDF de plusieurs centaines de pages ne
    sature plus la mémoire.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"document introuvable : {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"format non pris en charge : {source.suffix}")
    dpi = max(72, min(int(dpi), MAX_DPI))

    if source.suffix.lower() != ".pdf":
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        yield DocumentPage(number=1, image=image, native_text="")
        return

    pdf = pdfium.PdfDocument(str(source))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                image = _render_pdf_page(page, dpi)
                native = ""
                blocks: tuple[OcrBlock, ...] = ()
                try:
                    _width, page_height = page.get_size()
                    textpage = page.get_textpage()
                    native = normalize_text(textpage.get_text_range())
                    blocks = tuple(_native_blocks(page, page_height))
                except Exception:  # noqa: BLE001 — PDF sans couche texte
                    native = ""
                yield DocumentPage(
                    number=index + 1,
                    image=image,
                    native_text=native,
                    blocks=blocks,
                )
            finally:
                page.close()
    finally:
        pdf.close()


def load_document(path: str | Path, dpi: int = DEFAULT_DPI) -> list[DocumentPage]:
    """Compatibilité (tests, outils) : matérialise toutes les pages."""
    return list(iter_document_pages(path, dpi=dpi))
