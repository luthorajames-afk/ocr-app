"""Worker mode Simple : documents propres, texte courant.

Moteur : Tesseract. Pas de prétraitement, pas de tableau. Vitesse rapide.
"""

from __future__ import annotations

from typing import Any
from ocr_app.core.data_model import Page, Block, BlockType
from ocr_app.core.worker import BaseWorker


class SimpleWorker(BaseWorker):
    def __init__(self, models_dir: str | None = None) -> None:
        super().__init__(mode="simple", models_dir=models_dir)

    def initialize(self) -> None:
        # Tesseract est dans le noyau ; aucune dépendance lourde à charger ici.
        pass

    def process_page(self, page_input: Any, page_number: int) -> Page:
        # Stub de squelette : à remplacer par l'appel Tesseract (phase 1).
        return Page(
            number=page_number,
            source=str(page_input),
            blocks=[
                Block(
                    type=BlockType.PARAGRAPH,
                    text=f"[page {page_number}] texte simple (squelette SimpleWorker)",
                    bbox=(0.0, 0.0, 100.0, 100.0),
                    confidence=0.95,
                )
            ],
            confidence=0.95,
        )

    def shutdown(self) -> None:
        pass