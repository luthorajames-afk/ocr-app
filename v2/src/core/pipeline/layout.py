"""Lecture multi-colonnes : regroupement et ordre des blocs OCR."""

from __future__ import annotations

from core.pipeline.interfaces import OcrBlock

COLUMN_TOLERANCE = 40.0


def order_blocks_in_columns(blocks: tuple[OcrBlock, ...] | list[OcrBlock]) -> list[OcrBlock]:
    """Ordonne les blocs par colonne (x croissant) puis de haut en bas.

    Les blocs dont la bbox est vide (0,0,0,0) sont conservés en fin de liste,
    dans leur ordre d'origine.
    """
    positioned = [block for block in blocks if block.bbox[2] > block.bbox[0]]
    unpositioned = [block for block in blocks if block.bbox[2] <= block.bbox[0]]

    columns: list[tuple[float, list[OcrBlock]]] = []
    for block in sorted(positioned, key=lambda item: item.bbox[0]):
        for _left, column_blocks in columns:
            if abs(block.bbox[0] - _left) < COLUMN_TOLERANCE:
                column_blocks.append(block)
                break
        else:
            columns.append((block.bbox[0], [block]))
    columns.sort(key=lambda column: column[0])

    ordered: list[OcrBlock] = []
    for _left, column_blocks in columns:
        ordered.extend(sorted(column_blocks, key=lambda item: (item.bbox[1], item.bbox[0])))
    return ordered + unpositioned
