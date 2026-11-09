"""Pipeline d'OCR par mode.

Stages d'une page (dépendent du profil du mode choisi) :
    rendu 300 DPI (ou texte natif) → prétraitement → analyse de mise en page
    → OCR par bloc → structure des tableaux → reconstruction → sauvegarde
    → signal done.

Phase 1 :
- mode ``simple`` : OCR réel Tesseract (via ``SimpleWorker``/``OcrEngine``) ;
- R8 : le texte natif d'un PDF est utilisé sans rendu ni OCR ;
- R7 : le PDF est traité page par page, à 300 DPI maximum ;
- R6 : chaque page terminée est sauvegardée immédiatement dans ``work_dir``
  avant de passer à la suivante ;
- modes ``medium`` et ``advanced`` : stubs explicites (placeholder), sans plantage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional

from ocr_app.core.data_model import Block, BlockType, Document, Page
from ocr_app.core.rendering import (
    DocumentRenderer,
    is_significant_native_text,
    normalise_dpi,
)

logger = logging.getLogger(__name__)

MAX_DPI = 300
_MODES = ("simple", "medium", "advanced")


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
    """

    mode: str = "simple"  # "simple", "medium", "advanced"
    dpi: int = MAX_DPI
    use_native_text: bool = True
    work_dir: Optional[str] = None
    models_dir: Optional[str] = None

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
                page = self._process_index(renderer, index, index + 1)
                # R6 : la page est écrite avant de commencer la suivante.
                self._save_page(page)
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
            page = self._process_index(renderer, index, page_number)
            self._save_page(page)
            return page
        finally:
            renderer.close()

    def _process_index(
        self,
        renderer: DocumentRenderer,
        index: int,
        page_number: int,
    ) -> Page:
        self._emit(page_number, PipelineStage.RENDER.value)

        # Modes non implémentés en phase 1 : placeholder explicite, mais le
        # document est tout de même parcouru page par page.
        if self.config.mode != "simple":
            self._emit(page_number, PipelineStage.PREPROCESS.value)
            self._emit(page_number, PipelineStage.LAYOUT.value)
            self._emit(page_number, PipelineStage.OCR.value)
            page = self._ensure_worker().process_page(
                str(renderer.source_path), page_number
            )
            self._emit(page_number, PipelineStage.TABLES.value)
            self._emit(page_number, PipelineStage.RECONSTRUCT.value)
            return self._finish_page(page, page_number, renderer)

        native_text = ""
        if self.config.use_native_text:
            native_text = renderer.extract_native_text(index)

        if is_significant_native_text(native_text):
            # R8 : aucune rasterisation, aucun OCR.
            self.native_pages += 1
            self._emit(page_number, PipelineStage.PREPROCESS.value)
            self._emit(page_number, PipelineStage.LAYOUT.value)
            self._emit(page_number, PipelineStage.OCR.value)
            self._emit(page_number, PipelineStage.TABLES.value)
            width, height = renderer.page_size_points(index)
            page = Page(
                number=page_number,
                blocks=[
                    Block(
                        type=BlockType.PARAGRAPH,
                        text=native_text,
                        bbox=(0.0, 0.0, width, height),
                        confidence=1.0,
                        block_id=f"p{page_number:03d}-native",
                    )
                ],
                source=str(renderer.source_path),
                confidence=1.0,
            )
            self._emit(page_number, PipelineStage.RECONSTRUCT.value)
            return page

        image = renderer.render_page(index)
        page_dpi = renderer.page_dpi(index)
        self.ocr_pages += 1
        self._emit(page_number, PipelineStage.PREPROCESS.value)
        self._emit(page_number, PipelineStage.LAYOUT.value)
        self._emit(page_number, PipelineStage.OCR.value)
        page = self._ensure_worker().process_page(image, page_number, dpi=page_dpi)
        self._emit(page_number, PipelineStage.TABLES.value)
        self._emit(page_number, PipelineStage.RECONSTRUCT.value)
        return self._finish_page(page, page_number, renderer)

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
    def _save_page(self, page: Page) -> None:
        """Écrit ``work_dir/page_NNN.json`` immédiatement (R6)."""
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
        self._emit(page.number, PipelineStage.DONE.value)
