"""T1.3 — Chargement des documents : pages rendues + texte natif PDF.

``load_document`` retourne une page par feuille, avec l'image rendue (300 DPI
max, R7) et le texte natif éventuel (PDF non scanné, pypdfium2).
"""

from __future__ import annotations

import unicodedata
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


def _native_blocks(page: pdfium.PdfPage, page_height: float) -> list[OcrBlock]:
    """Segmente la couche texte d'un PDF natif en blocs ordonnés (multi-colonnes).

    Les rectangles de ``PdfTextPage`` sont en coordonnées PDF (origine
    bas-gauche) ; la bbox retournée est en points, origine haut-gauche.
    """
    textpage = page.get_textpage()
    rects: list[tuple[float, float, float, float, str]] = []
    try:
        count = textpage.count_rects()
        for index in range(count):
            left, bottom, right, top = textpage.get_rect(index)
            text = (textpage.get_text_bounded(left, bottom, right, top) or "").strip()
            if text:
                rects.append((float(left), float(bottom), float(right), float(top), text))
    except Exception:  # noqa: BLE001 — PDF sans géométrie exploitable
        return []

    if not rects:
        return []

    # Colonnes : regroupement des bords gauches proches (< 40 pt).
    columns: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for rect in sorted(rects, key=lambda item: item[0]):
        left = rect[0]
        for index, (column_left, column_rects) in enumerate(columns):
            if abs(left - column_left) < 40.0:
                column_rects.append(rect)
                break
        else:
            columns.append((left, [rect]))
    columns.sort(key=lambda column: column[0])

    # Hauteur médiane des lignes pour repérer les titres.
    heights = sorted(rect[3] - rect[1] for rect in rects)
    median_height = heights[len(heights) // 2] if heights else 10.0

    blocks: list[OcrBlock] = []
    for _column_left, column_rects in columns:
        items = sorted(column_rects, key=lambda item: -item[3])
        for left, bottom, right, top, text in items:
            height = top - bottom
            is_title = height >= median_height * 1.3 and len(text) <= 90
            blocks.append(
                OcrBlock(
                    text=normalize_text(text).replace("\n", " "),
                    bbox=(left, page_height - top, right, page_height - bottom),
                    confidence=1.0,
                    kind="title" if is_title else "line",
                )
            )
    return blocks


def _render_pdf_page(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    bitmap = page.render(scale=dpi / 72.0)
    image: Image.Image = bitmap.to_pil().convert("RGB")
    return image


def load_document(path: str | Path, dpi: int = DEFAULT_DPI) -> list[DocumentPage]:
    """Charge un PDF, JPG ou PNG en pages prêtes pour l'OCR."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"document introuvable : {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"format non pris en charge : {source.suffix}")
    dpi = max(72, min(int(dpi), MAX_DPI))

    if source.suffix.lower() != ".pdf":
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        return [DocumentPage(number=1, image=image, native_text="")]

    pages: list[DocumentPage] = []
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
                pages.append(
                    DocumentPage(
                        number=index + 1,
                        image=image,
                        native_text=native,
                        blocks=blocks,
                    )
                )
            finally:
                page.close()
    finally:
        pdf.close()
    return pages
