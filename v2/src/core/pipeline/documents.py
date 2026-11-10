"""T1.3 — Chargement des documents : pages rendues + texte natif PDF.

``load_document`` retourne une page par feuille, avec l'image rendue (300 DPI
max, R7) et le texte natif éventuel (PDF non scanné, pypdfium2).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

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
                try:
                    textpage = page.get_textpage()
                    native = normalize_text(textpage.get_text_range())
                except Exception:  # noqa: BLE001 — PDF sans couche texte
                    native = ""
                pages.append(
                    DocumentPage(number=index + 1, image=image, native_text=native)
                )
            finally:
                page.close()
    finally:
        pdf.close()
    return pages
