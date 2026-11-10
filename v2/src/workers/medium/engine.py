"""Module Moyen — moteur RapidOCR (PP-OCR, onnxruntime), import paresseux."""

from __future__ import annotations

import time
from collections.abc import Sequence

from PIL import Image

from core.pipeline.interfaces import (
    EngineCapabilities,
    OcrBlock,
    OcrEngine,
    OcrResult,
)


def _as_list(value: object) -> list[object]:
    """Convertit un tableau numpy / tuple / None en liste (sans 'or')."""
    if value is None:
        return []
    try:
        items: list[object] = list(value)  # type: ignore[call-overload]
    except TypeError:
        return []
    return items


class RapidOcrEngine(OcrEngine):
    """RapidOCR dans son environnement (venv medium), jamais dans le noyau."""

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine = None
        self._version = ""

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_layout=True, supports_tables=False, languages=("fra", "eng")
        )

    @property
    def is_initialized(self) -> bool:
        return self._engine is not None

    @property
    def version(self) -> str:
        if self._version:
            return self._version
        try:
            from importlib.metadata import version

            self._version = version("rapidocr")
        except Exception:  # noqa: BLE001 — métadonnées optionnelles
            self._version = "unknown"
        return self._version

    def initialize(self) -> None:
        if self._engine is None:
            from rapidocr import RapidOCR  # import paresseux (R1/R13)

            self._engine = RapidOCR()

    def process(
        self,
        image: Image.Image,
        *,
        languages: Sequence[str] | None = None,
    ) -> OcrResult:
        if self._engine is None:
            self.initialize()
        assert self._engine is not None
        started = time.perf_counter()
        result = self._engine(image.convert("RGB"))
        texts = _as_list(getattr(result, "txts", None))
        boxes = _as_list(getattr(result, "boxes", None))
        scores = _as_list(getattr(result, "scores", None))

        blocks: list[OcrBlock] = []
        scale = 72.0 / 300.0  # bboxes en points (page rendue à 300 DPI)
        for index, text in enumerate(texts):
            bbox = (0.0, 0.0, 0.0, 0.0)
            if index < len(boxes):
                points = boxes[index]
                try:
                    xs = [float(point[0]) for point in points]
                    ys = [float(point[1]) for point in points]
                    bbox = (
                        min(xs) * scale,
                        min(ys) * scale,
                        max(xs) * scale,
                        max(ys) * scale,
                    )
                except (TypeError, ValueError, IndexError):
                    bbox = (0.0, 0.0, 0.0, 0.0)
            confidence = float(scores[index]) if index < len(scores) else 0.0
            blocks.append(
                OcrBlock(text=str(text), bbox=bbox, confidence=confidence, kind="line")
            )
        blocks.sort(key=lambda block: (round(block.bbox[1], 1), block.bbox[0]))
        text = "\n".join(block.text for block in blocks)
        return OcrResult(
            text=text,
            blocks=tuple(blocks),
            engine=self.name,
            engine_version=self.version,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def shutdown(self) -> None:
        self._engine = None
