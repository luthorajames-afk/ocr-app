"""Rendu des documents en images et extraction du texte natif (R7, R8).

- PDF : pypdfium2, une page à la fois, rendu à 300 DPI maximum.
- JPG/PNG (et JPEG) : ouverture Pillow directe, une seule page.
- R8 : si une page PDF possède déjà une couche de texte significative,
  ``extract_native_text``/``extract_native_blocks`` la retourne et aucun
  rendu/OCR n'est nécessaire.
- Segmentation native : ``extract_native_blocks`` découpe la couche de texte
  en blocs réels (paragraphes et titres) avec leur boîte englobante, au lieu
  d'un unique bloc pleine page (liens bloc ↔ texte précis, R9).

Le rendu et le texte natif restent dans ce module : le pipeline ne connaît que
``DocumentRenderer`` et les workers ne reçoivent qu'une image PIL.

Choix d'API pour la segmentation (documenté, cf. ``extract_native_blocks``) :
le textpage de pypdfium2 (``count_rects``/``get_rect``/``get_text_bounded``)
est utilisé de préférence à l'itération des objets texte
(``page.get_objects()``) car il couvre toute la couche de texte aplatie
(XObjects inclus), renvoie déjà des segments « une ligne d'un coup » dans
l'ordre du texte, et donne des rectangles en coordonnées page directement ;
les objets PDF bruts (``PdfTextObj``) sont plus hétérogènes (un objet peut
couvrir plusieurs lignes, ou un fragment) et leur position n'y est pas
exposée par une API stable selon les versions.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

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

# ---------------------------------------------------------------------------
# Paramètres de la segmentation native (valeurs en points PDF ou ratios).
# Deux segments appartiennent à la même ligne s'ils se chevauchent
# verticalement (ratio ``_LINE_OVERLAP_RATIO`` de la plus petite hauteur) et
# si leur écart horizontal reste petit (``_LINE_GAP_FACTOR`` × hauteur).
# Deux lignes appartiennent au même paragraphe si leurs portées horizontales
# se recouvrent (``_PARA_OVERLAP_RATIO`` de la plus étroite) et si l'écart
# vertical reste borné (``_PARA_GAP_FACTOR`` × hauteur de ligne médiane).
# Une ligne courte en police nettement plus grande que le corps
# (``_TITLE_SIZE_FACTOR`` × médiane) est isolée et typée titre.
# ---------------------------------------------------------------------------
_LINE_OVERLAP_RATIO = 0.5
_LINE_GAP_FACTOR = 1.5
_LINE_GAP_MIN_PT = 6.0
_PARA_OVERLAP_RATIO = 0.5
_PARA_GAP_FACTOR = 0.75
_TITLE_SIZE_FACTOR = 2.2
_TITLE_MAX_CHARS = 60
_MIN_LINE_HEIGHT_PT = 4.0
# Tolérance du regroupement en bandes de lecture : deux lignes dont le haut
# diffère de moins que cela (× hauteur médiane) partagent une bande.
_BAND_TOLERANCE_FACTOR = 0.9


@dataclass(frozen=True)
class NativeBlock:
    """Bloc de texte natif d'une page PDF, avant conversion en ``Block``.

    ``bbox`` est en points PDF avec l'origine en haut à gauche et y vers le
    bas — la même convention que les blocs OCR (ce qui permet à l'aperçu de
    superposer les deux sans transformation). ``is_title`` marque les lignes
    isolées en grande police (titre probable).
    """

    text: str
    bbox: Tuple[float, float, float, float]
    is_title: bool = False


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


# ---------------------------------------------------------------------------
# Segmentation du texte natif (blocs réels au lieu d'une page entière)
# ---------------------------------------------------------------------------
# Un segment brut du textpage : (texte, left, bottom, right, top) en
# coordonnées PDF (origine bas-gauche, y vers le haut).
_Segment = Tuple[str, float, float, float, float]
# Une ligne reconstruite en coordonnées « haut-gauche » (y vers le bas).
_Line = Tuple[str, float, float, float, float]  # (texte, x0, y0, x1, y1)


def _group_segments_into_lines(segments: List[_Segment]) -> List[_Line]:
    """Réunit les segments partageant une ligne, en coordonnées haut-gauche.

    Le textpage livre des segments contigus d'une même ligne (un « rect » par
    suite de texte de même style) : on fusionne les segments consécutifs qui
    se chevauchent verticalement et dont l'écart horizontal est petit. Les
    colonnes d'un PDF natif étant des objets séparés, leur grand écart
    horizontal les laisse dans des lignes distinctes : l'ordre des objets
    plus le tri final restituent l'ordre de lecture.
    """
    lines: List[_Line] = []
    current: List[_Segment] = []
    current_left = current_right = current_top = current_bottom = 0.0

    def close_line() -> None:
        nonlocal current
        if not current:
            return
        ordered = sorted(current, key=lambda seg: seg[1])
        text = " ".join(seg[0] for seg in ordered)
        left = min(seg[1] for seg in current)
        bottom = min(seg[2] for seg in current)
        right = max(seg[3] for seg in current)
        top = max(seg[4] for seg in current)
        lines.append((text, left, top, right, bottom))
        current = []

    for text, left, bottom, right, top in segments:
        if not current:
            current = [(text, left, bottom, right, top)]
            current_left, current_bottom = left, bottom
            current_right, current_top = right, top
            continue
        overlap = min(current_top, top) - max(current_bottom, bottom)
        smallest = max(1e-6, min(current_top - current_bottom, top - bottom))
        gap = left - current_right
        gap_limit = max(
            _LINE_GAP_MIN_PT,
            _LINE_GAP_FACTOR * max(current_top - current_bottom, top - bottom),
        )
        if overlap >= _LINE_OVERLAP_RATIO * smallest and gap <= gap_limit:
            current.append((text, left, bottom, right, top))
            current_left = min(current_left, left)
            current_bottom = min(current_bottom, bottom)
            current_right = max(current_right, right)
            current_top = max(current_top, top)
        else:
            close_line()
            current = [(text, left, bottom, right, top)]
            current_left, current_bottom = left, bottom
            current_right, current_top = right, top
    close_line()
    return lines


def _assign_reading_bands(lines: List[_Line], reference: float) -> List[int]:
    """Numéro de bande de lecture de chaque ligne (haut -> bas).

    Les lignes doivent être triées par ``y0`` croissant au préalable. Deux
    lignes dont le haut diffère de moins que ``_BAND_TOLERANCE_FACTOR ×
    reference`` partagent une bande (tolérance de ligne) ; la comparaison se
    fait avec la moyenne courante de la bande pour éviter les bascules
    d'arrondi entre deux lignes quasi alignées (ex. un titre et la ligne
    d'en-tête qui lui fait face).
    """
    bands: List[int] = []
    band_index = -1
    band_mean = 0.0
    tolerance = _BAND_TOLERANCE_FACTOR * reference
    for _text, _x0, y0, _x1, _y1 in lines:
        if band_index < 0 or (y0 - band_mean) > tolerance:
            band_index += 1
            band_mean = y0
        else:
            band_mean = (band_mean * band_index + y0) / (band_index + 1)
        bands.append(band_index)
    return bands


def _build_native_blocks(
    segments: List[_Segment],
    page_width: float,
    page_height: float,
    fallback_text: str = "",
) -> List[NativeBlock]:
    """Construit les blocs natifs (paragraphes, titres) d'une page.

    Entrée : segments du textpage en coordonnées bas-gauche. Sortie : blocs
    en coordonnées haut-gauche, triés en ordre de lecture (bandes de haut en
    bas, de gauche à droite dans une bande). Les lignes rapprochées et
    alignées horizontalement sont réunies en un paragraphe ; une ligne courte
    en police nettement plus grande que le corps devient un titre isolé.
    """
    if not segments:
        text = (fallback_text or "").strip()
        if text:
            # Repli : couche de texte présente mais aucun rectangle
            # exploitable — comportement historique, un bloc pleine page.
            return [
                NativeBlock(
                    text=text, bbox=(0.0, 0.0, page_width, page_height)
                )
            ]
        return []

    lines = _group_segments_into_lines(segments)
    # Conversion d'origine : y PDF (bas-gauche) -> y affichage (haut-gauche).
    lines = [
        (text, left, page_height - top, right, page_height - bottom)
        for text, left, top, right, bottom in lines
    ]

    heights = [max(1e-6, y1 - y0) for _text, _x0, y0, _x1, y1 in lines]
    reference = max(_MIN_LINE_HEIGHT_PT, float(statistics.median(heights)))

    # Ordre de lecture : bandes verticales de haut en bas, gauche à droite.
    by_y = sorted(lines, key=lambda line: (line[2], line[1]))
    bands = _assign_reading_bands(by_y, reference)
    ordered = sorted(zip(by_y, bands), key=lambda item: (item[1], item[0][1]))
    ordered_lines = [(line, band) for line, band in ordered]

    blocks: List[dict] = []  # blocs ouverts : {parts, x0, y0, x1, y1, title, band}
    built: List[Tuple[Tuple[int, float, float], NativeBlock]] = []

    def close_block(block: dict) -> None:
        text = "\n".join(block["parts"]).strip()
        if not text:
            return
        key = (block["band"], block["x0"], block["y0"])
        built.append(
            (
                key,
                NativeBlock(
                    text=text,
                    bbox=(block["x0"], block["y0"], block["x1"], block["y1"]),
                    is_title=block["title"],
                ),
            )
        )

    for (text, x0, y0, x1, y1), line_band in ordered_lines:
        if (y1 - y0) >= _TITLE_SIZE_FACTOR * reference and len(text) <= _TITLE_MAX_CHARS:
            # Une ligne « display » (grande police, courte) forme toujours un
            # bloc isolé : ni rattachée au paragraphe précédent, ni suivie.
            close_block(
                {
                    "parts": [text],
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "title": True,
                    "band": line_band,
                }
            )
            continue
        attached = False
        for block in reversed(blocks):
            overlap = min(block["x1"], x1) - max(block["x0"], x0)
            narrower = max(1e-6, min(block["x1"] - block["x0"], x1 - x0))
            if overlap < _PARA_OVERLAP_RATIO * narrower:
                continue
            gap = y0 - block["y1"]
            if gap > _PARA_GAP_FACTOR * reference or gap < -2.0 * reference:
                continue
            block["parts"].append(text)
            block["x0"] = min(block["x0"], x0)
            block["y0"] = min(block["y0"], y0)
            block["x1"] = max(block["x1"], x1)
            block["y1"] = max(block["y1"], y1)
            attached = True
            break
        if not attached:
            blocks.append(
                {
                    "parts": [text],
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "title": False,
                    "band": line_band,
                }
            )
    for block in blocks:
        close_block(block)
    # Les blocs ont été fermés dans des ordres différents (titre immédiat,
    # paragraphes en fin) : on rétablit l'ordre de lecture.
    built.sort(key=lambda item: item[0])
    return [native for _key, native in built]


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

    def extract_native_blocks(self, index: int) -> List[NativeBlock]:
        """Texte natif de la page découpé en blocs réels (R8, R9).

        Via le textpage pypdfium2 : ``count_rects`` segmente la couche de
        texte en rectangles « une suite de texte d'une ligne », lus avec
        ``get_text_bounded`` ; ces segments sont réunis en lignes puis en
        paragraphes (voir ``_build_native_blocks``). Ce choix d'API plutôt
        que l'itération des objets texte (``page.get_objects()``) est
        documenté dans le docstring du module : le textpage couvre toute la
        couche aplatie et donne des rectangles page directement exploitables.

        Retour : blocs en points PDF, origine haut-gauche (même convention
        que les blocs OCR), triés en ordre de lecture. Une page sans couche
        de texte significative retourne une liste vide ; une couche présente
        mais sans rectangle exploitable retombe sur un bloc pleine page.
        """
        self._check_index(index)
        if self._kind != "pdf":
            return []
        assert self._pdf is not None
        page = self._pdf[index]
        segments: List[_Segment] = []
        try:
            try:
                page_width, page_height = (float(v) for v in page.get_size())
                textpage = page.get_textpage()
                try:
                    rect_count = textpage.count_rects()
                    for rect_index in range(rect_count):
                        left, bottom, right, top = textpage.get_rect(rect_index)
                        raw = textpage.get_text_bounded(
                            left=left, bottom=bottom, right=right, top=top
                        )
                        text = normalise_native_text(raw)
                        if text:
                            segments.append(
                                (text, float(left), float(bottom),
                                 float(right), float(top))
                            )
                finally:
                    textpage.close()
            except Exception as exc:  # noqa: BLE001 - extraction contextualisée
                raise RenderingError(
                    f"extraction du texte natif impossible "
                    f"(page {index + 1} de {self.source_path}) : {exc}"
                ) from exc
        finally:
            page.close()

        fallback = ""
        if not segments:
            # Couche de texte présente mais illisible en rectangles : repli
            # sur l'extraction brute (bloc pleine page historique).
            fallback = self.extract_native_text(index)
        return _build_native_blocks(
            segments,
            page_width=page_width,
            page_height=page_height,
            fallback_text=fallback,
        )

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
