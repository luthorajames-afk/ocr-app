"""Lecture multi-colonnes : gouttières réelles et ordre des blocs OCR."""

from __future__ import annotations

from core.pipeline.interfaces import OcrBlock

GUTTER_MIN_WIDTH = 20.0
WIDE_BLOCK_RATIO = 0.6
MAX_COLUMNS = 4


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _column_ranges(
    blocks: list[OcrBlock], page_width: float, page_height: float
) -> list[tuple[float, float]]:
    """Colonnes = segments séparés par une gouttière libre de tout bloc.

    Les blocs pleine largeur (en-têtes, titres, pieds de page) sont exclus :
    ce sont eux qui relient visuellement les colonnes. Deux colonnes ne sont
    retenues que si elles se recouvrent verticalement de façon significative :
    un en-tête de facture (société à gauche, « FACTURE » à droite) n'est pas
    un texte en deux colonnes.
    """
    if page_width <= 0:
        return []
    narrow = [
        block
        for block in blocks
        if (block.bbox[2] - block.bbox[0]) < page_width * WIDE_BLOCK_RATIO
    ]
    if not narrow:
        return []
    merged = _merge_intervals([(block.bbox[0], block.bbox[2]) for block in narrow])
    if not 2 <= len(merged) <= MAX_COLUMNS:
        return []
    gutters = [
        merged[index + 1][0] - merged[index][1]
        for index in range(len(merged) - 1)
    ]
    if any(gutter < GUTTER_MIN_WIDTH for gutter in gutters):
        return []
    spans: list[list[float]] = []
    for left, right in merged:
        members = [
            block
            for block in narrow
            if min(block.bbox[2], right) - max(block.bbox[0], left) > 0
        ]
        if members:
            spans.append(
                [
                    min(block.bbox[1] for block in members),
                    max(block.bbox[3] for block in members),
                ]
            )
    overlap_floor = page_height * 0.2
    for first in range(len(spans)):
        for second in range(first + 1, len(spans)):
            overlap = min(spans[first][1], spans[second][1]) - max(
                spans[first][0], spans[second][0]
            )
            if overlap >= overlap_floor:
                return merged
    return []


def _column_of(block: OcrBlock, columns: list[tuple[float, float]]) -> int:
    best_index = 0
    best_overlap = float("-inf")
    for index, (left, right) in enumerate(columns):
        overlap = min(block.bbox[2], right) - max(block.bbox[0], left)
        if overlap > best_overlap:
            best_index = index
            best_overlap = overlap
    return best_index


def order_blocks_in_columns(
    blocks: tuple[OcrBlock, ...] | list[OcrBlock],
) -> list[OcrBlock]:
    """Ordonne les blocs en respectant les colonnes, si elles existent.

    Les blocs sans géométrie sont conservés en fin de liste. Les blocs
    pleine largeur découpent la page en bandes : les colonnes ne s'appliquent
    qu'à l'intérieur d'une bande (en-tête, contenu, pied de page).
    """
    positioned = [block for block in blocks if block.bbox[2] > block.bbox[0]]
    unpositioned = [block for block in blocks if block.bbox[2] <= block.bbox[0]]
    if not positioned:
        return unpositioned

    page_width = max(block.bbox[2] for block in positioned)
    wide = [
        block
        for block in positioned
        if (block.bbox[2] - block.bbox[0]) >= page_width * WIDE_BLOCK_RATIO
    ]
    narrow = [block for block in positioned if block not in wide]
    page_height = max(block.bbox[3] for block in positioned)
    columns = _column_ranges(narrow, page_width, page_height)
    if not columns:
        return sorted(positioned, key=lambda block: (block.bbox[1], block.bbox[0])) + unpositioned

    def sort_key(block: OcrBlock) -> tuple[int, float, float]:
        return (_column_of(block, columns), block.bbox[1], block.bbox[0])

    wide.sort(key=lambda block: (block.bbox[1], block.bbox[0]))
    ordered: list[OcrBlock] = []
    cursor = 0.0
    remaining = list(narrow)
    for separator in wide:
        band = [
            block
            for block in remaining
            if cursor <= (block.bbox[1] + block.bbox[3]) / 2.0 < separator.bbox[1]
        ]
        ordered.extend(sorted(band, key=sort_key))
        remaining = [block for block in remaining if block not in band]
        ordered.append(separator)
        cursor = max(cursor, separator.bbox[3])
    tail = [
        block
        for block in remaining
        if (block.bbox[1] + block.bbox[3]) / 2.0 >= cursor
    ]
    ordered.extend(sorted(tail, key=sort_key))
    ordered.extend(
        block for block in remaining if block not in tail
    )
    return ordered + unpositioned
