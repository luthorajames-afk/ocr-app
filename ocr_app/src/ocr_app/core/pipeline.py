"""Pipeline d'OCR par mode.

Stages d'une page (dépendent du profil du mode choisi) :
    rendu 300 DPI (ou texte natif) → prétraitement → analyse de mise en page
    → OCR par bloc → structure des tableaux → reconstruction → sauvegarde
    → signal done.

Phase 1 :
- mode ``simple`` : OCR réel Tesseract (via ``SimpleWorker``/``OcrEngine``) ;
- R8 : le texte natif d'un PDF est utilisé sans OCR ; il est segmenté en
  blocs réels (``DocumentRenderer.extract_native_blocks``) au lieu d'un
  unique bloc pleine page ;
- plan v1 : chaque page terminée est écrite dans ``work_dir`` sous forme
  d'une image ``page_NNN.png`` (rendu 300 DPI) ET d'un ``page_NNN.json`` ;
- R7 : le PDF est traité page par page, à 300 DPI maximum, une seule page
  en mémoire à la fois (l'image est écrite puis libérée) ;
- R6 : chaque page terminée est sauvegardée immédiatement dans ``work_dir``
  avant de passer à la suivante ;
- mode ``medium`` : stub explicite (placeholder), sans plantage ;
- mode ``advanced`` : moteur Docling réel (``AdvancedWorker`` + ``DoclingEngine``),
  exécuté dans le venv d'évaluation dédié (R1/R13 : jamais dans l'interface) ;
- mode ``approfondi`` : même backend que ``advanced`` (Docling) + prétraitement
  interactif — filtres d'image (``core/preprocessing.py``) et zones d'exclusion
  dessinées par l'utilisateur, passées via ``PipelineConfig``.

Prétraitement Approfondi (``filter_params`` / ``excluded_zones``) :

- ``filter_params``  : l'image rendue 300 DPI passe par ``apply_filters`` AVANT
  l'OCR/le moteur ; dimensions inchangées, donc les bboxes restent comparables
  aux points PDF du document d'origine ;
- ``excluded_zones`` : rectangles en points PDF (origine haut-gauche) peints
  en blanc papier (couleur fond, jamais noir) sur l'image envoyée au moteur,
  et blocs dont au moins 50 % de la surface intersecte une zone retirés du
  résultat (natifs comme Docling) ;
- sans filtre ni zone, le document ORIGINAL est remis au moteur (chemin
  historique, qualité native) — l'image préparée n'est construite que si
  nécessaire.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from PIL import Image, ImageDraw

from ocr_app.core.data_model import Block, BlockType, Document, Page
from ocr_app.core.rendering import (
    DocumentRenderer,
    is_significant_native_text,
    normalise_dpi,
)

logger = logging.getLogger(__name__)

MAX_DPI = 300
_MODES = ("simple", "medium", "advanced", "approfondi")

#: Part de la surface d'un bloc qui doit intersecter une zone d'exclusion
#: pour que le bloc soit retiré du résultat.
ZONE_PRUNE_RATIO = 0.5

#: Zones d'exclusion : rectangles (x0, y0, x1, y1) en points PDF.
Zone = Tuple[float, float, float, float]


class PipelineStage(str, Enum):
    RENDER = "render"
    PREPROCESS = "preprocess"
    LAYOUT = "layout"
    OCR = "ocr"
    TABLES = "tables"
    RECONSTRUCT = "reconstruct"
    SAVE = "save"
    DONE = "done"


class PipelineError(RuntimeError):
    """Erreur du pipeline (document, moteur, sauvegarde)."""


@dataclass
class PipelineConfig:
    """Configuration d'un traitement.

    ``models_dir`` désigne le dossier tessdata (ou son parent) pour le mode
    Simple ; ``None`` utilise ``models/tessdata`` du projet (R4).

    Mode Approfondi : ``filter_params`` (dict de filtres, cf.
    ``core/preprocessing.py`` ; ``None`` = aucun filtre) et
    ``excluded_zones`` (rectangles points PDF, cf. :data:`Zone`).
    """

    mode: str = "simple"  # "simple", "medium", "advanced", "approfondi"
    dpi: int = MAX_DPI
    use_native_text: bool = True
    work_dir: Optional[str] = None
    models_dir: Optional[str] = None
    excluded_zones: Tuple[Zone, ...] = ()
    filter_params: Optional[dict] = None

    def __post_init__(self) -> None:
        mode = str(self.mode or "simple").strip().lower()
        if mode not in _MODES:
            raise ValueError(
                f"mode inconnu : {self.mode!r} "
                f"(modes acceptés : {', '.join(_MODES)})"
            )
        self.mode = mode

        try:
            requested_dpi = int(self.dpi)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"--dpi invalide : {self.dpi!r}") from None
        if requested_dpi <= 0:
            raise ValueError("--dpi doit être un entier strictement positif")
        self.dpi = normalise_dpi(requested_dpi)

        self.use_native_text = bool(self.use_native_text)
        if self.work_dir is not None:
            work_dir = str(self.work_dir).strip()
            self.work_dir = work_dir or None
        if self.models_dir is not None:
            self.models_dir = str(self.models_dir)

        self.excluded_zones = _normalize_zones(self.excluded_zones)
        if self.filter_params is not None and not isinstance(
            self.filter_params, dict
        ):
            raise ValueError(
                "filter_params invalide : dict attendu, "
                f"{type(self.filter_params).__name__} reçu"
            )
        if self.filter_params:
            # Import paresseux : numpy n'est chargé que si des filtres sont
            # réellement demandés (les autres modes n'en ont jamais besoin).
            from ocr_app.core.preprocessing import normalize_params

            self.filter_params = normalize_params(self.filter_params)


def _normalize_zones(zones: Any) -> Tuple[Zone, ...]:
    """Normalise les zones d'exclusion en tuples de 4 floats (x0 ≤ x1, y0 ≤ y1).

    Tolérant en entrée (listes, tuples, entiers) ; une zone mal formée ou
    non finie (NaN/inf) lève ``ValueError`` (erreur d'usage claire, comme
    ``--dpi``). Les zones dégénérées (surface nulle) sont ignorées.
    """
    if not zones:
        return ()
    if isinstance(zones, (str, bytes)) or not isinstance(zones, (list, tuple)):
        raise ValueError(
            f"excluded_zones invalide : séquence de rectangles attendue, "
            f"{type(zones).__name__} reçu"
        )
    normalized: List[Zone] = []
    for zone in zones:
        if isinstance(zone, (str, bytes)) or not isinstance(
            zone, (list, tuple)
        ) or len(zone) != 4:
            raise ValueError(
                f"zone d'exclusion invalide : (x0, y0, x1, y1) attendu, "
                f"{zone!r} reçu"
            )
        try:
            x0, y0, x1, y1 = (float(value) for value in zone)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                f"zone d'exclusion invalide : coordonnées numériques "
                f"attendues, {zone!r} reçu"
            ) from None
        # NaN/inf (JSON hostile) : les comparaisons d'ordre sont toutes
        # fausses, les gardes suivantes laisseraient passer la zone — un
        # filtre de finitude explicite est nécessaire.
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            raise ValueError(
                f"zone d'exclusion invalide : coordonnées finies attendues, "
                f"{zone!r} reçu"
            )
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        if x1 <= x0 or y1 <= y0:
            continue  # zone dégénérée : sans effet, ignorée
        normalized.append((x0, y0, x1, y1))
    return tuple(normalized)


def _zone_overlap_ratio(
    bbox: Tuple[float, float, float, float],
    zones: Tuple[Zone, ...],
) -> float:
    """Part maximale de la surface d'un bloc couverte par UNE zone."""
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return 0.0
    area = width * height
    best = 0.0
    for zx0, zy0, zx1, zy1 in zones:
        overlap_w = min(x1, zx1) - max(x0, zx0)
        overlap_h = min(y1, zy1) - max(y0, zy0)
        if overlap_w <= 0 or overlap_h <= 0:
            continue
        best = max(best, (overlap_w * overlap_h) / area)
        if best >= 1.0:
            break
    return best


class Pipeline:
    """Pipeline d'un document, page par page."""

    def __init__(self, config: PipelineConfig) -> None:
        if not isinstance(config, PipelineConfig):
            raise TypeError(
                f"PipelineConfig attendu, {type(config).__name__} reçu"
            )
        self.config = config
        self._on_stage: Optional[Callable[[int, str], None]] = None
        self._worker: Any = None
        self._saved_pages: List[Path] = []
        # Compteurs par document : permettent de vérifier R8 (texte natif
        # utilisé sans OCR) sans exposer de détails internes du worker.
        self.ocr_pages: int = 0
        self.native_pages: int = 0

    # ------------------------------------------------------------ callbacks
    def on_stage(self, cb: Callable[[int, str], None]) -> None:
        self._on_stage = cb

    def _emit(self, page_number: int, stage: str) -> None:
        if self._on_stage:
            self._on_stage(page_number, stage)

    @property
    def saved_page_paths(self) -> List[Path]:
        """Fichiers ``page_NNN.json`` écrits immédiatement (R6)."""
        return list(self._saved_pages)

    # ------------------------------------------------------------ cycle de vie
    def _create_worker(self) -> Any:
        mode = self.config.mode
        if mode == "simple":
            # Imports locaux : le cœur reste léger (R1) et aucun moteur n'est
            # chargé pour les modes encore stubbés.
            from ocr_app.workers.simple import SimpleWorker

            return SimpleWorker(models_dir=self.config.models_dir)
        if mode == "medium":
            from ocr_app.workers.medium import MediumWorker

            return MediumWorker(models_dir=self.config.models_dir)
        # modes advanced ET approfondi : même backend Docling (R1/R13).
        from ocr_app.workers.advanced import AdvancedWorker

        return AdvancedWorker(models_dir=self.config.models_dir)

    def _ensure_worker(self) -> Any:
        if self._worker is not None:
            return self._worker
        worker = self._create_worker()
        try:
            worker.initialize()
        except Exception:
            try:
                worker.shutdown()
            except Exception:  # pragma: no cover - nettoyage best effort
                logger.debug("arrêt du worker après échec d'initialisation", exc_info=True)
            raise
        self._worker = worker
        return worker

    def close(self) -> None:
        """Arrête le worker et libère le moteur (idempotent)."""
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                worker.shutdown()
            except Exception:  # pragma: no cover - arrêt best effort
                logger.exception("erreur à l'arrêt du worker %s", self.config.mode)

    # ------------------------------------------------------------ traitement
    def _needs_prepared_image(self) -> bool:
        """Vrai si l'image envoyée au moteur doit être préparée (Approfondi)."""
        return bool(self.config.filter_params) or bool(self.config.excluded_zones)

    def _prepare_image(self, image: Image.Image, dpi: int) -> Image.Image:
        """Filtres + masque blanc des zones, dimensions inchangées.

        Les zones sont en points PDF : elles sont peintes à l'échelle du
        rendu (``dpi``) en blanc papier (couleur fond du document — jamais
        noir, sinon l'OCR y lirait un bloc sombre). Les bboxes du résultat
        restent comparables aux points PDF : l'image garde ses dimensions.
        """
        prepared = image
        if self.config.filter_params:
            from ocr_app.core.preprocessing import apply_filters

            prepared = apply_filters(prepared, self.config.filter_params)
        if self.config.excluded_zones:
            scale = float(dpi) / 72.0
            painter = ImageDraw.Draw(prepared)
            for x0, y0, x1, y1 in self.config.excluded_zones:
                painter.rectangle(
                    (
                        x0 * scale,
                        y0 * scale,
                        min(x1 * scale, prepared.width),
                        min(y1 * scale, prepared.height),
                    ),
                    fill="white",
                )
        return prepared

    def _prune_excluded_blocks(self, page: Page) -> Page:
        """Retire les blocs dont ≥ ``ZONE_PRUNE_RATIO`` intersecte une zone."""
        zones = self.config.excluded_zones
        if not zones:
            return page
        kept = [
            block
            for block in page.blocks
            if _zone_overlap_ratio(block.bbox, zones) < ZONE_PRUNE_RATIO
        ]
        if len(kept) != len(page.blocks):
            logger.debug(
                "page %s : %d bloc(s) retiré(s) par zones d'exclusion",
                page.number,
                len(page.blocks) - len(kept),
            )
            page.blocks = kept
        return page

    def process_document(self, source_path: str) -> Document:
        """Traite un document page par page, en sauvegardant chaque page (R6)."""
        source = Path(source_path)
        self._saved_pages = []
        self.ocr_pages = 0
        self.native_pages = 0
        renderer = DocumentRenderer(source, dpi=self.config.dpi)
        document = Document(
            title=source.stem or "document",
            source_path=str(source),
        )
        try:
            total = renderer.page_count
            for index in range(total):
                page, image = self._process_index(renderer, index, index + 1)
                # R6 : la page est écrite avant de commencer la suivante.
                self._save_page(page, image, renderer.page_dpi(index))
                if image is not None:
                    # R7 : une seule page en mémoire à la fois.
                    image.close()
                document.add_page(page)
        finally:
            renderer.close()
            self.close()
        return document

    def process_page(self, page_input: str, page_number: int) -> Page:
        """Traite une page isolée (compatibilité) et la sauvegarde si configuré.

        Le worker reste vivant après l'appel ; utiliser :meth:`close` pour le
        libérer. ``process_document`` gère ce cycle automatiquement.
        """
        renderer = DocumentRenderer(page_input, dpi=self.config.dpi)
        try:
            index = page_number - 1
            if index < 0 or index >= renderer.page_count:
                index = 0
            page, image = self._process_index(renderer, index, page_number)
            self._save_page(page, image, renderer.page_dpi(index))
            if image is not None:
                image.close()
            return page
        finally:
            renderer.close()

    def _process_index(
        self,
        renderer: DocumentRenderer,
        index: int,
        page_number: int,
    ) -> Tuple[Page, Optional[Image.Image]]:
        """Traite une page et retourne ``(page, image_rendue)``.

        L'image (rendu 300 DPI, R7) accompagne toujours la page : elle est
        écrite en ``page_NNN.png`` par :meth:`_save_page` — plan v1, une
        image ET un JSON par page — puis libérée par l'appelant.
        """
        self._emit(page_number, PipelineStage.RENDER.value)

        # Mode medium : placeholder explicite, mais le document est tout de
        # même parcouru page par page. Modes advanced/approfondi : Docling
        # réel (venv d'évaluation, R1/R13) via ``AdvancedWorker``. En
        # approfondi avec filtres ou zones, l'image rendue 300 DPI est
        # préparée (filtres + masque blanc) et remise au moteur à la place
        # du document original (dimensions inchangées → bboxes comparables).
        if self.config.mode != "simple":
            self._emit(page_number, PipelineStage.PREPROCESS.value)
            if self._needs_prepared_image():
                image = renderer.render_page(index)
                dpi = renderer.page_dpi(index)
                prepared = self._prepare_image(image, dpi)
                try:
                    prepared.info["dpi"] = (int(dpi), int(dpi))
                except (AttributeError, TypeError):  # pragma: no cover
                    pass
                if image is not prepared:
                    image.close()
                self._emit(page_number, PipelineStage.LAYOUT.value)
                self._emit(page_number, PipelineStage.OCR.value)
                page = self._ensure_worker().process_page(
                    prepared, page_number
                )
            else:
                # Sans filtre ni zone : document original (qualité native).
                self._emit(page_number, PipelineStage.LAYOUT.value)
                self._emit(page_number, PipelineStage.OCR.value)
                page = self._ensure_worker().process_page(
                    str(renderer.source_path), page_number
                )
            self._emit(page_number, PipelineStage.TABLES.value)
            self._emit(page_number, PipelineStage.RECONSTRUCT.value)
            page = self._finish_page(page, page_number, renderer)
            page = self._prune_excluded_blocks(page)
            image = renderer.render_page(index)
            return page, image

        native_blocks = []
        if self.config.use_native_text:
            native_blocks = renderer.extract_native_blocks(index)
        native_text = "\n\n".join(block.text for block in native_blocks)

        if is_significant_native_text(native_text):
            # R8 : aucun OCR ; segmentation en blocs réels (R9), et rendu
            # 300 DPI pour l'aperçu du document original (plan v1). Les
            # zones d'exclusion s'appliquent aussi ici (filtrage de blocs) :
            # les filtres d'image, eux, n'ont aucun effet sur un texte
            # natif (aucun OCR effectué).
            self.native_pages += 1
            self._emit(page_number, PipelineStage.PREPROCESS.value)
            self._emit(page_number, PipelineStage.LAYOUT.value)
            self._emit(page_number, PipelineStage.OCR.value)
            self._emit(page_number, PipelineStage.TABLES.value)
            blocks = [
                Block(
                    type=BlockType.TITLE if native.is_title else BlockType.PARAGRAPH,
                    text=native.text,
                    bbox=native.bbox,
                    confidence=1.0,
                    block_id=f"p{page_number:03d}-native-b{position:03d}",
                )
                for position, native in enumerate(native_blocks)
            ]
            page = Page(
                number=page_number,
                blocks=blocks,
                source=str(renderer.source_path),
                confidence=1.0,
            )
            page = self._prune_excluded_blocks(page)
            self._emit(page_number, PipelineStage.RECONSTRUCT.value)
            return page, renderer.render_page(index)

        image = renderer.render_page(index)
        page_dpi = renderer.page_dpi(index)
        self.ocr_pages += 1
        self._emit(page_number, PipelineStage.PREPROCESS.value)
        if self._needs_prepared_image():
            image = self._prepare_image(image, page_dpi)
        self._emit(page_number, PipelineStage.LAYOUT.value)
        self._emit(page_number, PipelineStage.OCR.value)
        page = self._ensure_worker().process_page(image, page_number, dpi=page_dpi)
        self._emit(page_number, PipelineStage.TABLES.value)
        self._emit(page_number, PipelineStage.RECONSTRUCT.value)
        page = self._finish_page(page, page_number, renderer)
        page = self._prune_excluded_blocks(page)
        return page, image

    @staticmethod
    def _finish_page(
        page: Page,
        page_number: int,
        renderer: DocumentRenderer,
    ) -> Page:
        if not isinstance(page, Page):
            raise PipelineError(
                "le worker a retourné un objet invalide "
                f"({type(page).__name__}) pour la page {page_number}"
            )
        page.number = page_number
        page.source = str(renderer.source_path)
        return page

    # ------------------------------------------------------------ sauvegarde
    def _save_page(
        self,
        page: Page,
        image: Optional[Image.Image] = None,
        dpi: Optional[int] = None,
    ) -> None:
        """Écrit ``work_dir/page_NNN.json`` et ``page_NNN.png`` (R6, plan v1).

        Le JSON est l'artefact critique : son échec reste fatal. L'image
        (rendu 300 DPI de la page originale, native ou OCR) est écrite à
        côté, de façon best effort : un échec d'encodage PNG est journalisé
        sans perdre le résultat OCR, et l'aperçu retombera sur le placeholder.
        """
        self._emit(page.number, PipelineStage.SAVE.value)
        work_dir = self.config.work_dir
        if work_dir:
            target = Path(work_dir) / f"page_{page.number:03d}.json"
            try:
                page.save_json(target)
            except (OSError, ValueError) as exc:
                raise PipelineError(
                    f"sauvegarde de la page {page.number} impossible "
                    f"({target}) : {exc}"
                ) from exc
            self._saved_pages.append(target)
            if image is not None:
                self._save_page_image(page, image, dpi, target.with_suffix(".png"))
        self._emit(page.number, PipelineStage.DONE.value)

    def _save_page_image(
        self,
        page: Page,
        image: Image.Image,
        dpi: Optional[int],
        target: Path,
    ) -> None:
        """Écrit l'image de la page (``page_NNN.png``), sans rien casser.

        Idempotent : si le fichier existe déjà (retraitement dans le même
        dossier de travail, relance d'une page), il n'est pas réécrit —
        l'image existante est déjà le rendu de cette page du document.
        """
        try:
            if target.is_file():
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            save_kwargs: dict = {"format": "PNG"}
            if dpi:
                save_kwargs["dpi"] = (int(dpi), int(dpi))
            image.save(target, **save_kwargs)
        except (OSError, ValueError) as exc:
            logger.warning(
                "image de la page %s non écrite (%s) : %s",
                page.number,
                target,
                exc,
            )
