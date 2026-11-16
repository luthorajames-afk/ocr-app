"""T1.3 — Chargement des documents : pages rendues + texte natif PDF.

``load_document`` retourne une page par feuille, avec l'image rendue (300 DPI
max, R7) et le texte natif éventuel (PDF non scanné, pypdfium2).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
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

    # 1) Regrouper les glyphes pleins par baseline réelle (les jambages et
    # exposants ne doivent pas créer de fausses lignes).
    solid = (
        [char for char in chars if (char[3] - char[1]) > median_height * 0.4]
        if median_height > 0
        else list(chars)
    )
    solid.sort(key=lambda char: (char[1], char[0]))
    tolerance = max(2.5, median_height * 0.3)
    clusters: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for char in solid:
        if clusters and abs(char[1] - clusters[-1][0]) <= tolerance:
            items = clusters[-1][1]
            items.append(char)
            clusters[-1] = (sum(item[1] for item in items) / len(items), items)
        else:
            clusters.append((char[1], [char]))

    # 2) Fusionner apostrophes/exposants proches qui recouvrent la ligne en x.
    merge_tolerance = max(7.0, median_height)
    merged = True
    while merged:
        merged = False
        for first in range(len(clusters)):
            for second in range(first + 1, len(clusters)):
                first_baseline, first_items = clusters[first]
                second_baseline, second_items = clusters[second]
                if abs(first_baseline - second_baseline) > merge_tolerance:
                    continue
                first_left = min(item[0] for item in first_items)
                first_right = max(item[2] for item in first_items)
                second_left = min(item[0] for item in second_items)
                second_right = max(item[2] for item in second_items)
                if min(first_right, second_right) - max(first_left, second_left) <= 0:
                    continue
                combined = first_items + second_items
                clusters[first] = (
                    sum(item[1] for item in combined) / len(combined),
                    combined,
                )
                del clusters[second]
                merged = True
                break
            if merged:
                break

    # 3) Assigner les caractères restants (espaces, petits glyphes) à la
    # baseline la plus proche ; sinon, nouvelle ligne.
    assigned = {id(char) for _baseline, items in clusters for char in items}
    assign_tolerance = max(8.0, median_height * 1.1)
    for char in chars:
        if id(char) in assigned:
            continue
        if not clusters:
            clusters.append((char[1], [char]))
            assigned.add(id(char))
            continue
        nearest = min(
            range(len(clusters)),
            key=lambda index: abs(char[1] - clusters[index][0]),
        )
        if abs(char[1] - clusters[nearest][0]) <= assign_tolerance:
            clusters[nearest][1].append(char)
            assigned.add(id(char))
        else:
            clusters.append((char[1], [char]))
            assigned.add(id(char))
    lines = clusters

    words: list[tuple[float, float, float, float, str]] = []
    for _baseline, items in lines:
        if len(items) == 1 and median_height > 0 and items[0][4].strip():
            height = items[0][3] - items[0][1]
            # Marqueur superscript isolé : hors flux.
            if height < median_height * 0.7:
                continue
        items.sort(key=lambda char: char[0])
        # Quand la couche texte contient de vrais espaces, ce sont eux qui
        # délimitent les mots : ne couper sur un écart géométrique que pour
        # les colonnes (grand blanc sans caractère espace). Sinon, seuil
        # déduit des petits écarts intra-mot de la ligne.
        has_spaces = any(char[4].isspace() for char in items)
        gaps = [
            items[index + 1][0] - items[index][2]
            for index in range(len(items) - 1)
            if not items[index][4].isspace() and not items[index + 1][4].isspace()
        ]
        gaps = sorted(gap for gap in gaps if 0.0 < gap <= 3.0)
        if has_spaces:
            word_gap = max(6.0, median_height * 0.8)
        elif gaps:
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


def _text_object_styles(
    textpage: pdfium.PdfTextPage,
) -> list[tuple[tuple[float, float, float, float], str, float, bool, bool]]:
    """Polices des objets texte de la page (famille, taille, gras, italique).

    ``get_textobj`` renvoie l'objet texte contenant le caractère demandé ;
    on déduplique par bornes + nom de base. La taille vient de la matrice
    quand elle est exploitable (> 1,5 pt), sinon elle est estimée à partir
    de la hauteur de l'objet, calibrée sur les autres objets de la page.
    """
    seen: set[tuple[tuple[float, ...], str]] = set()
    entries: list[tuple[tuple[float, float, float, float], str, float, bool, bool]] = []
    for index in range(textpage.count_chars()):
        try:
            obj = textpage.get_textobj(index)
        except Exception:  # noqa: BLE001, S112 — objet texte absent
            continue
        if obj is None:
            continue
        try:
            font = obj.get_font()
            base = font.get_base_name()
            bounds = tuple(round(value, 2) for value in obj.get_bounds())
            matrix = obj.get_matrix()
            size = abs(matrix.a) or abs(matrix.d)
        except Exception:  # noqa: BLE001, S112 — objet illisible
            continue
        key = (bounds, base)
        if key in seen:
            continue
        seen.add(key)
        lowered = base.lower()
        entries.append(
            (
                (bounds[0], bounds[1], bounds[2], bounds[3]),
                font.get_family_name(),
                size,
                "bold" in lowered or font.get_weight() >= 600,
                "italic" in lowered or "oblique" in lowered,
            )
        )
    ratios = [
        size / (bounds[3] - bounds[1])
        for bounds, _family, size, _bold, _italic in entries
        if size > 1.5 and (bounds[3] - bounds[1]) > 2.0
    ]
    if ratios:
        ratios.sort()
        factor = ratios[len(ratios) // 2]
    else:
        factor = 0.85
    styled: list[tuple[tuple[float, float, float, float], str, float, bool, bool]] = []
    for bounds, family, size, bold, italic in entries:
        if size <= 1.5:
            size = max(5.0, (bounds[3] - bounds[1]) * factor)
        styled.append((bounds, family, round(size, 1), bold, italic))
    return styled


def _apply_styles(blocks: list[OcrBlock], textpage: pdfium.PdfTextPage) -> list[OcrBlock]:
    """Attribue à chaque bloc la police dominante des objets texte qui le recouvrent."""
    try:
        styles = _text_object_styles(textpage)
    except Exception:  # noqa: BLE001 — page sans objets texte
        return blocks
    if not styles:
        return blocks
    matches: list[tuple[OcrBlock, tuple[float, float, float, float], str, float, bool, bool, float]] = []
    for block in blocks:
        left, top, right, bottom = block.bbox
        best_area = 0.0
        best: tuple[tuple[float, float, float, float], str, float, bool, bool] | None = None
        for style in styles:
            sl, sb, sr, st = style[0]
            overlap_width = max(0.0, min(right, sr) - max(left, sl))
            overlap_height = max(0.0, min(bottom, st) - max(top, sb))
            area = overlap_width * overlap_height
            if area > best_area:
                best_area = area
                best = style
        coverage = (
            best_area / max(1e-6, (right - left) * (bottom - top)) if best else 0.0
        )
        matches.append(
            (
                block,
                best[0] if best else (0.0, 0.0, 0.0, 0.0),
                best[1] if best else "",
                best[2] if best else 0.0,
                best[3] if best else False,
                best[4] if best else False,
                coverage,
            )
        )

    ratios = [
        size / max(1e-6, match[0].bbox[3] - match[0].bbox[1])
        for match in matches
        for size in (match[3],)
        if match[6] >= 0.2 and size > 0 and (match[0].bbox[3] - match[0].bbox[1]) > 2.0
    ]
    if ratios:
        ratios.sort()
        factor = ratios[len(ratios) // 2]
    else:
        factor = 0.85
    families = [match[2] for match in matches if match[2]]
    fallback_family = max(set(families), key=families.count) if families else ""

    styled: list[OcrBlock] = []
    for block, _bounds, family, size, bold, italic, coverage in matches:
        height = block.bbox[3] - block.bbox[1]
        if size <= 0:
            size = max(5.0, height * factor)
            bold = italic = False
        styled.append(
            replace(
                block,
                font_size=round(size, 1),
                font_name=family if coverage >= 0.2 else fallback_family,
                bold=bold,
                italic=italic,
            )
        )
    return styled


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

    blocks = _native_tables_and_columns(rects, page_height, median_height)
    return _apply_styles(blocks, textpage)


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
    text = ""
    for cell in merged:
        if cell[0].strip():
            text = _join_text(text, cell[0].strip())
    text = text.strip()
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
    bottoms = sorted(rect[1] for rect in row)
    baseline = bottoms[len(bottoms) // 2] if bottoms else 0.0
    for left, bottom, right, top, text in row:
        clean = normalize_text(text).replace(chr(10), " ").strip()
        if not clean:
            continue
        # Marqueur superscript isolé (a, p, s, c, m) : lettre seule, petite ET
        # surélevée par rapport à la baseline. Un « n » de « n° » reste.
        if (
            len(clean) == 1
            and clean.isalpha()
            and median_height > 0
            and (top - bottom) < median_height * 0.7
            and bottom > baseline + median_height * 0.25
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
            joined = _normalise_cell(_join_text(previous_text, clean))
            cells[-1] = (joined, previous_left, right)
        else:
            cells.append((_normalise_cell(clean), left, right))
    return cells


_NO_SPACE_BEFORE = ",.)]}»…"
_NO_SPACE_AFTER = "([{«"
_APOSTROPHES = "'’"


def _join_text(left: str, right: str) -> str:
    """Assemble deux fragments en respectant la ponctuation française.

    Pas d'espace avant ``, . ) ] } »`` ni après ``( [ { «`` ; le trait
    d'union et l'apostrophe ne se collent qu'entre deux lettres
    (``Nord-Quest``, ``d’organisation``). En revanche ``; : ! ?`` gardent
    l'espace française qui les précède.
    """
    if not left:
        return right
    if not right:
        return left
    if right[0] in _NO_SPACE_BEFORE:
        return left + right
    if left[-1] in _NO_SPACE_AFTER:
        return left + right
    if right[0] == "-" and left[-1].isalpha():
        return left + right
    if left[-1] == "-" and len(left) > 1 and left[-2].isalpha() and right[0].isalpha():
        return left + right
    if right[0] in _APOSTROPHES and left[-1].isalnum():
        return left + right
    if left[-1] in _APOSTROPHES and right[0].isalnum():
        return left + right
    return f"{left} {right}"


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
