"""Worker mode Simple : documents propres, texte courant.

Moteur : Tesseract via ``tesserocr`` (R12 : le moteur est derrière
``OcrEngine``). Pas de prétraitement, pas de tableau. Vitesse rapide.

Le worker reçoit soit une image PIL (cas normal : le pipeline a déjà rendu la
page), soit un chemin de fichier (image ou PDF) pour un usage direct.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Tuple

from PIL import Image

from ocr_app.core.data_model import Block, BlockType, Page
from ocr_app.core.ocr_engine import EngineResult, OcrEngine, create_engine
from ocr_app.core.rendering import DocumentRenderer
from ocr_app.core.worker import BaseWorker


class SimpleWorker(BaseWorker):
    """Worker OCR du mode Simple (Tesseract)."""

    def __init__(
        self,
        models_dir: str | None = None,
        engine: Optional[OcrEngine] = None,
    ) -> None:
        super().__init__(mode="simple", models_dir=models_dir)
        # Injection possible d'un moteur (tests, évaluation R10/R12).
        self._engine = engine

    # ------------------------------------------------------------ cycle de vie
    def initialize(self) -> None:
        if self._engine is None:
            self._engine = create_engine("tesseract", tessdata_dir=self.models_dir)
        self._engine.initialize()

    def shutdown(self) -> None:
        engine = self._engine
        self._engine = None
        if engine is not None:
            engine.shutdown()

    # ------------------------------------------------------------------ OCR
    def process_page(
        self,
        page_input: Any,
        page_number: int,
        *,
        dpi: int = 300,
    ) -> Page:
        image, effective_dpi, source = self._load_image(page_input, page_number, dpi)

        if self._engine is None:
            self.initialize()
        assert self._engine is not None  # initialize() garantit le moteur

        result = self._engine.recognize(image, dpi=effective_dpi)
        return _page_from_result(
            result,
            page_number=page_number,
            dpi=effective_dpi,
            source=source,
        )

    @staticmethod
    def _load_image(
        page_input: Any,
        page_number: int,
        dpi: int,
    ) -> Tuple[Image.Image, int, Optional[str]]:
        """Retourne (image PIL, DPI réel, chemin source éventuel)."""
        if isinstance(page_input, Image.Image):
            return page_input, _safe_dpi(dpi), None

        if isinstance(page_input, (str, os.PathLike)):
            path = os.fspath(page_input)
            renderer = DocumentRenderer(path, dpi=dpi)
            try:
                index = page_number - 1
                if index < 0 or index >= renderer.page_count:
                    index = 0
                image = renderer.render_page(index)
                return image, renderer.page_dpi(index), str(path)
            finally:
                renderer.close()

        raise TypeError(
            "page_input doit être une image PIL ou un chemin de fichier, "
            f"{type(page_input).__name__} reçu"
        )


def _page_from_result(
    result: EngineResult,
    *,
    page_number: int,
    dpi: int,
    source: Optional[str],
) -> Page:
    scale = 72.0 / float(_safe_dpi(dpi))
    blocks: list[Block] = []
    confidences: list[float] = []

    for engine_block in result.blocks:
        text = (engine_block.text or "").strip()
        if not text:
            continue
        x0, y0, x1, y1 = engine_block.bbox_px
        confidence = _clamp_confidence(engine_block.confidence)
        blocks.append(
            Block(
                type=BlockType.PARAGRAPH,
                text=text,
                bbox=(
                    float(x0) * scale,
                    float(y0) * scale,
                    float(x1) * scale,
                    float(y1) * scale,
                ),
                confidence=confidence,
                block_id=f"p{page_number:03d}-b{len(blocks):03d}",
            )
        )
        confidences.append(confidence)

    if not blocks and result.text.strip():
        # Repli : le moteur a du texte mais aucun bloc exploitable.
        text = result.text.strip()
        blocks.append(
            Block(
                type=BlockType.PARAGRAPH,
                text=text,
                bbox=(0.0, 0.0, 0.0, 0.0),
                confidence=_clamp_confidence(result.confidence),
                block_id=f"p{page_number:03d}-b000",
            )
        )
        confidences.append(_clamp_confidence(result.confidence))

    if result.confidence > 0:
        page_confidence = _clamp_confidence(result.confidence)
    elif confidences:
        page_confidence = sum(confidences) / len(confidences)
    else:
        page_confidence = 0.0

    return Page(
        number=page_number,
        blocks=blocks,
        source=source,
        confidence=page_confidence,
    )


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _safe_dpi(value: Any) -> int:
    try:
        dpi = int(value)
    except (TypeError, ValueError, OverflowError):
        return 300
    return max(1, dpi)
