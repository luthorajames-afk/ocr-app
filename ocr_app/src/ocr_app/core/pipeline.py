"""Pipeline d'OCR par mode.

Stages d'une page (dépendent du profil du mode choisi) :
    rendu 300 DPI (ou texte natif) → prétraitement → analyse de mise en page
    → OCR par bloc → structure des tableaux → reconstruction → sauvegarde
    → signal page_done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Callable
from enum import Enum
from pathlib import Path

from ocr_app.core.data_model import Block, BlockType, Page, Document


class PipelineStage(str, Enum):
    RENDER = "render"
    PREPROCESS = "preprocess"
    LAYOUT = "layout"
    OCR = "ocr"
    TABLES = "tables"
    RECONSTRUCT = "reconstruct"
    SAVE = "save"
    DONE = "done"


@dataclass
class PipelineConfig:
    mode: str  # "simple", "medium", "advanced"
    dpi: int = 300
    use_native_text: bool = True
    work_dir: Optional[str] = None


class Pipeline:
    """Pipeline d'une page. L'implémentation concrète délègue au worker."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._on_stage: Optional[Callable[[int, str], None]] = None

    def on_stage(self, cb: Callable[[int, str], None]) -> None:
        self._on_stage = cb

    def _emit(self, page_number: int, stage: str) -> None:
        if self._on_stage:
            self._on_stage(page_number, stage)

    def process_page(self, page_input: str, page_number: int) -> Page:
        """Traite une page d'entrée et retourne un Page (stub de squelette).

        Dans la phase 1, ce stub retourne un bloc paragraphe placeholder.
        Le worker réel (phase 3) implémente la chaîne complète.
        """
        self._emit(page_number, PipelineStage.RENDER.value)
        self._emit(page_number, PipelineStage.PREPROCESS.value)
        self._emit(page_number, PipelineStage.LAYOUT.value)
        self._emit(page_number, PipelineStage.OCR.value)
        self._emit(page_number, PipelineStage.TABLES.value)
        self._emit(page_number, PipelineStage.RECONSTRUCT.value)

        page = Page(
            number=page_number,
            source=page_input,
            blocks=[
                Block(
                    type=BlockType.PARAGRAPH,
                    text=(
                        f"[page {page_number}] contenu OCR "
                        "(squelette - à remplacer par le worker)"
                    ),
                    bbox=(0.0, 0.0, 100.0, 100.0),
                    confidence=0.0,
                )
            ],
            confidence=1.0,
        )
        self._emit(page_number, PipelineStage.SAVE.value)
        self._emit(page_number, PipelineStage.DONE.value)
        return page

    def process_document(self, source_path: str) -> Document:
        """Traite un document page par page (stub)."""
        doc = Document(title=Path(source_path).stem, source_path=source_path)
        # Stub : une seule page placeholder.
        page = self.process_page(source_path, 1)
        doc.add_page(page)
        return doc