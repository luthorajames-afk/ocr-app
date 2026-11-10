"""T1.3 — Tests du chargement de documents et du texte natif."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pipeline.documents import DocumentPage, load_document, normalize_text

DATA = Path(__file__).resolve().parents[1] / "data"
MANIFEST = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
BY_ID = {entry["id"]: entry for entry in MANIFEST["documents"]}


def test_normalize_text_collapses_spaces_and_newlines() -> None:
    raw = "  Bonjour\r\n\r\n\r\nMonde\u00a0!\r\n"
    assert normalize_text(raw) == "Bonjour\n\nMonde !"


def test_native_pdf_exposes_native_text() -> None:
    entry = BY_ID["fact_001"]
    pages = load_document(DATA / entry["document"], dpi=150)
    assert pages and all(isinstance(page, DocumentPage) for page in pages)
    assert pages[0].has_native_text
    assert "Dupont" in pages[0].native_text
    assert pages[0].native_text.count("é") >= 1


def test_native_invoice_table_is_reconstructed() -> None:
    entry = BY_ID["fact_001"]
    pages = load_document(DATA / entry["document"], dpi=150)
    tables = [block for block in pages[0].blocks if block.kind == "table"]
    assert len(tables) == 1
    grid = tables[0].grid
    assert len(grid) >= 4 and len(grid[0]) >= 4
    assert grid[0][0].lower().startswith("désignation")


def test_native_blocks_are_ordered_by_columns() -> None:
    entry = BY_ID["fact_001"]
    pages = load_document(DATA / entry["document"], dpi=150)
    blocks = [block for block in pages[0].blocks if block.bbox[2] > block.bbox[0]]
    assert blocks
    # Ordre de lecture : la seconde colonne ne démarre jamais avant la première.
    xs = [block.bbox[0] for block in blocks]
    assert xs[0] <= 60


def test_image_document_has_no_native_text() -> None:
    entry = next(e for e in MANIFEST["documents"] if e["document"].endswith(".jpg"))
    pages = load_document(DATA / entry["document"])
    assert len(pages) == 1
    assert pages[0].has_native_text is False
    assert pages[0].number == 1


def test_missing_document_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_document(DATA / "absent.pdf")


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    bad = tmp_path / "note.docx"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        load_document(bad)
