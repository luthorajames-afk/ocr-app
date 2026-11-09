"""Worker mode Moyen : scans, mise en page modérée.

Moteurs : Tesseract, RapidOCR, OpenCV. Prétraitement : redressement, contraste,
débruitage. Tableaux : structure simple. Vitesse moyenne.
"""

from __future__ import annotations

from typing import Any
from ocr_app.core.data_model import Page, Block, BlockType
from ocr_app.core.worker import BaseWorker


class MediumWorker(BaseWorker):
    def __init__(self, models_dir: str | None = None) -> None:
        super().__init__(mode="medium", models_dir=models_dir)

    def initialize(self) -> None:
        # Charger RapidOCR / OpenCV une fois (R5 : worker vivant tout le doc).
        pass

    def process_page(self, page_input: Any, page_number: int) -> Page:
        # Stub de squelette : à remplacer par le pipeline Moyen (phase 1/3).
        return Page(
            number=page_number,
            source=str(page_input),
            blocks=[
                Block(
                    type=BlockType.PARAGRAPH,
                    text=f"[page {page_number}] texte moyen (squelette MediumWorker)",
                    bbox=(0.0, 0.0, 100.0, 100.0),
                    confidence=0.90,
                )
            ],
            confidence=0.90,
        )

    def shutdown(self) -> None:
        pass