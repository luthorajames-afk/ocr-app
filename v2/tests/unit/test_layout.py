"""Ordre de lecture OCR : gouttières, indentation, en-têtes pleine largeur."""

from __future__ import annotations

from core.pipeline.interfaces import OcrBlock
from core.pipeline.layout import order_blocks_in_columns


def block(text: str, x0: float, y0: float, x1: float, y1: float) -> OcrBlock:
    return OcrBlock(text=text, bbox=(x0, y0, x1, y1), confidence=0.9, kind="line")


def test_indented_single_column_keeps_y_order() -> None:
    blocks = [
        block("premier", 83, 100, 590, 112),
        block("suite", 143, 116, 590, 128),
        block("fin.", 83, 132, 122, 144),
        block("deuxieme", 143, 150, 590, 162),
    ]
    ordered = order_blocks_in_columns(blocks)
    assert [item.text for item in ordered] == ["premier", "suite", "fin.", "deuxieme"]


def test_two_real_columns_are_read_column_by_column() -> None:
    blocks = [
        block("gauche 1", 45, 100, 260, 112),
        block("droite 1", 300, 104, 550, 116),
        block("gauche 2", 45, 130, 260, 142),
        block("droite 2", 300, 134, 550, 146),
    ]
    ordered = order_blocks_in_columns(blocks)
    assert [item.text for item in ordered] == [
        "gauche 1",
        "gauche 2",
        "droite 1",
        "droite 2",
    ]


def test_full_width_header_separates_columns() -> None:
    blocks = [
        block("en-tete", 40, 20, 560, 32),
        block("gauche", 45, 100, 260, 112),
        block("droite", 300, 104, 550, 116),
        block("pied", 40, 780, 560, 792),
    ]
    ordered = order_blocks_in_columns(blocks)
    assert [item.text for item in ordered] == ["en-tete", "gauche", "droite", "pied"]


def test_empty_geometry_blocks_stay_last() -> None:
    blocks = [
        block("texte", 45, 100, 550, 112),
        OcrBlock(text="sans position", bbox=(0, 0, 0, 0), confidence=0.0, kind="line"),
    ]
    ordered = order_blocks_in_columns(blocks)
    assert [item.text for item in ordered] == ["texte", "sans position"]


def test_invoice_header_is_not_two_columns() -> None:
    blocks = [
        block("SARL Dupont", 45, 20, 260, 32),
        block("FACTURE", 600, 20, 700, 34),
        block("adresse", 45, 36, 250, 48),
        block("N° 2026-003", 600, 40, 720, 52),
        block("Corps de la facture sur toute la largeur", 45, 200, 760, 212),
        block("Total TTC", 600, 240, 720, 252),
        block("Pied de page", 45, 780, 760, 792),
    ]
    ordered = order_blocks_in_columns(blocks)
    assert [item.text for item in ordered] == [
        "SARL Dupont",
        "FACTURE",
        "adresse",
        "N° 2026-003",
        "Corps de la facture sur toute la largeur",
        "Total TTC",
        "Pied de page",
    ]
