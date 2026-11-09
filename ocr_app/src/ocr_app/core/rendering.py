"""Rendu des documents en images et extraction du texte natif (R7, R8).

- PDF : pypdfium2, une page à la fois, rendu à 300 DPI maximum.
- JPG/PNG (et JPEG) : ouverture Pillow directe, une seule page.
- R8 : si une page PDF possède déjà une couche de texte significative,
  ``extract_native_text`` la retourne et aucun rendu/OCR n'est nécessaire.

Le rendu et le texte natif restent dans ce module : le pipeline ne connaît que
``DocumentRenderer`` et les workers ne reçoivent qu'une image PIL.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

MAX_DPI = 300
DEFAULT_DPI = 300

PDF_SUFFIXES = frozenset({".pdf"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
SUPPORTED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES

# Seuil de « texte significatif » : une couche de texte plus courte que cela
# (ex. seulement « Page 1 ») ne justifie pas de sauter l'OCR.
MIN_NATIVE_CHARS = 20


class RenderingError(RuntimeError):
    """Erreur de lecture, de rendu ou de format d'un document."""


class UnsupportedFormatError(ValueError):
    """Extension de fichier non prise en charge."""


def normalise_native_text(text: str) -> str:
    """Nettoie le texte extrait d'un PDF sans altérer son contenu.

    - NFKC : espaces insécables, ligatures et caractères de compatibilité ;
    - fins de ligne Windows -> ``\\n`` ;
    - artefacts de polices embarquées : trait d'union conditionnel (U+00AD)
      écrit comme un vrai « - », point médian encodé U+A78F -> « · » ;
    - lignes vides multiples compactées.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xad", "-")
    text = text.replace("\ua78f", "·")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def is_significant_native_text(text: str, minimum: int = MIN_NATIVE_CHARS) -> bool:
    """Vrai si le texte natif justifie de sauter l'OCR (R8)."""
    compact = "".join(character for character in text if not character.isspace())
    return len(compact) >= minimum


def normalise_dpi(value: object, default: int = DEFAULT_DPI) -> int:
    """DPI borné à 1..300 (R7), avec repli sur ``default`` si invalide."""
    try:
        dpi = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    if dpi <= 0:
        return default
    return min(dpi, MAX_DPI)


class DocumentRenderer:
    """Source de pages d'un document : PDF multi-pages ou image unique."""

    def __init__(self, source_path: str | Path, dpi: int = DEFAULT_DPI) -> None:
        self.source_path = Path(source_path)
        self.dpi = normalise_dpi(dpi)
        try:
            requested_dpi = int(dpi)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            requested_dpi = self.dpi
        if requested_dpi > MAX_DPI:
            logger.warning(
                "DPI %s limité à %s (R7) pour %s", dpi, MAX_DPI, self.source_path
            )

        self._pdf: Optional[pdfium.PdfDocument] = None
        self._image: Optional[Image.Image] = None
        self._image_dpi: Optional[int] = None
        self._kind: str = ""
        self._page_count: int = 0

        self._validate_source()
        self._open()

    # ------------------------------------------------------------ validation
    def _validate_source(self) -> None:
        path = self.source_path
        if not path.exists():
            raise FileNotFoundError(f"document introuvable : {path}")
        if not path.is_file():
            raise RenderingError(f"chemin qui n'est pas un fichier : {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            accepted = ", ".join(sorted(SUPPORTED_SUFFIXES))
            raise UnsupportedFormatError(
                f"format non supporté : {suffix or '(sans extension)'} "
                f"(formats acceptés : {accepted})"
            )

    def _open(self) -> None:
        suffix = self.source_path.suffix.lower()
        if suffix in PDF_SUFFIXES:
            try:
                self._pdf = pdfium.PdfDocument(str(self.source_path))
                self._page_count = len(self._pdf)
            except Exception as exc:  # noqa: BLE001 - erreur PDF contextualisée
                raise RenderingError(
                    f"PDF illisible ou corrompu : {self.source_path} ({exc})"
                ) from exc
            self._kind = "pdf"
            return

        try:
            opened = Image.open(self.source_path)
            try:
                opened.load()
                self._image_dpi = _image_dpi(opened, fallback=self.dpi)
                image = ImageOps.exif_transpose(opened)
                image.load()
                if image is opened:
                    image = opened.copy()
                self._image = _flatten_image(image)
            finally:
                opened.close()
        except (OSError, ValueError) as exc:
            raise RenderingError(
                f"image illisible ou corrompue : {self.source_path} ({exc})"
            ) from exc
        self._kind = "image"
        self._page_count = 1

    # ------------------------------------------------------------- propriétés
    @property
    def page_count(self) -> int:
        return self._page_count

    @property
    def is_pdf(self) -> bool:
        return self._kind == "pdf"

    def page_dpi(self, index: int) -> int:
        """DPI réel de la page (métadonnées de l'image ou DPI de rendu PDF)."""
        self._check_index(index)
        if self._kind == "image" and self._image_dpi:
            return self._image_dpi
        return self.dpi

    def page_size_points(self, index: int) -> Tuple[float, float]:
        """Taille de la page en points PDF (1 pt = 1/72 po)."""
        self._check_index(index)
        if self._kind == "image":
            assert self._image is not None
            dpi = float(self.page_dpi(index))
            return (
                self._image.width * 72.0 / dpi,
                self._image.height * 72.0 / dpi,
            )

        assert self._pdf is not None
        page = self._pdf[index]
        try:
            width, height = page.get_size()
            return float(width), float(height)
        finally:
            page.close()

    # ---------------------------------------------------------------- contenu
    def extract_native_text(self, index: int) -> str:
        """Texte natif de la page (R8), normalisé ; vide pour une image."""
        self._check_index(index)
        if self._kind != "pdf":
            return ""
        assert self._pdf is not None
        page = self._pdf[index]
        try:
            textpage = page.get_textpage()
            try:
                raw = textpage.get_text_range() or ""
            finally:
                textpage.close()
        except Exception as exc:  # noqa: BLE001 - extraction best effort
            raise RenderingError(
                f"extraction du texte natif impossible "
                f"(page {index + 1} de {self.source_path}) : {exc}"
            ) from exc
        finally:
            page.close()
        return normalise_native_text(raw)

    def render_page(self, index: int) -> Image.Image:
        """Rend une page en image PIL à ``self.dpi`` (PDF) ou la retourne telle quelle."""
        self._check_index(index)
        if self._kind == "image":
            assert self._image is not None
            return self._image.copy()

        assert self._pdf is not None
        page = self._pdf[index]
        try:
            bitmap = page.render(scale=self.dpi / 72.0)
            try:
                image = bitmap.to_pil()
            finally:
                bitmap.close()
        except Exception as exc:  # noqa: BLE001 - rendu contextualisé
            raise RenderingError(
                f"rendu impossible (page {index + 1} de {self.source_path}) : {exc}"
            ) from exc
        finally:
            page.close()

        if image.mode not in ("RGB", "L"):
            image = _flatten_image(image)
        return image

    # ------------------------------------------------------------- cycle de vie
    def close(self) -> None:
        if self._image is not None:
            try:
                self._image.close()
            except Exception:  # pragma: no cover - libération best effort
                pass
            self._image = None
        if self._pdf is not None:
            try:
                self._pdf.close()
            except Exception:  # pragma: no cover - libération best effort
                pass
            self._pdf = None

    def __enter__(self) -> "DocumentRenderer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _check_index(self, index: int) -> None:
        if not isinstance(index, int) or index < 0 or index >= self._page_count:
            raise IndexError(
                f"index de page {index} hors limites "
                f"(0..{max(0, self._page_count - 1)}) pour {self.source_path}"
            )


def _flatten_image(image: Image.Image) -> Image.Image:
    """Convertit une image en RGB, avec fond blanc si elle a de la transparence."""
    if image.mode in ("RGB", "L"):
        return image
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _image_dpi(image: Image.Image, fallback: int) -> int:
    """DPI déclaré par l'image (JPEG/PNG), sinon repli."""
    value = image.info.get("dpi")
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    try:
        dpi = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if 30.0 <= dpi <= 2400.0:
        return int(round(dpi))
    return fallback
