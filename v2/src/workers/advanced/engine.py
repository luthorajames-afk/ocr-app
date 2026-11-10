"""Module Avancé — moteur Docling (layout + tableaux), import paresseux.

Exécuté uniquement dans le venv dédié (R1/R13) : le noyau ne charge jamais
Docling. R4 : cache de modèles local, `HF_HUB_OFFLINE` forcé.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from core.pipeline.interfaces import (
    EngineCapabilities,
    OcrBlock,
    OcrEngine,
    OcrResult,
)

TITLE_LABELS = {"title", "section_header", "page_header"}


class DoclingEngine(OcrEngine):
    """Conversion Docling d'une page image → blocs structurés (bbox PDF)."""

    name = "docling"

    def __init__(self) -> None:
        self._converter = None
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._version = ""

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_layout=True, supports_tables=True, languages=("fra", "eng")
        )

    @property
    def is_initialized(self) -> bool:
        return self._converter is not None

    @property
    def version(self) -> str:
        if self._version:
            return self._version
        try:
            from importlib.metadata import version

            self._version = version("docling")
        except Exception:  # noqa: BLE001 — métadonnées optionnelles
            self._version = "unknown"
        return self._version

    def initialize(self) -> None:
        if self._converter is not None:
            return
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from docling.document_converter import DocumentConverter  # paresseux

        self._converter = DocumentConverter()
        self._tempdir = tempfile.TemporaryDirectory(prefix="aocri-docling-")

    def process(
        self,
        image: Image.Image,
        *,
        languages: Sequence[str] | None = None,
    ) -> OcrResult:
        if self._converter is None:
            self.initialize()
        assert self._converter is not None and self._tempdir is not None
        started = time.perf_counter()

        temp_path = Path(self._tempdir.name) / "page.png"
        image.convert("RGB").save(temp_path, format="PNG", dpi=(300, 300))
        result = self._converter.convert(str(temp_path))
        document = result.document

        blocks: list[OcrBlock] = []
        for item in self._iter_items(document):
            label = str(getattr(item, "label", "")).split(".")[-1].lower()
            text = str(getattr(item, "text", "") or "").strip()
            if label == "table":
                try:
                    text = str(item.export_to_markdown()).strip()
                except Exception:  # noqa: BLE001 — tableau non exportable
                    text = ""
            if not text:
                continue
            bbox = self._bbox(item, document)
            kind = "title" if label in TITLE_LABELS else "line"
            blocks.append(OcrBlock(text=text, bbox=bbox, confidence=1.0, kind=kind))

        blocks.sort(key=lambda block: (round(block.bbox[1], 1), block.bbox[0]))
        full_text = "\n".join(block.text for block in blocks)
        return OcrResult(
            text=full_text,
            blocks=tuple(blocks),
            engine=self.name,
            engine_version=self.version,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def shutdown(self) -> None:
        self._converter = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    # ------------------------------------------------------------- interne
    @staticmethod
    def _iter_items(document: object) -> object:  # objet Docling (API variable)
        try:
            for item, _level in document.iterate_items():  # type: ignore[attr-defined]
                yield item
        except Exception:  # noqa: BLE001 — API Docling variable
            return

    @staticmethod
    def _bbox(item: object, document: object) -> tuple[float, float, float, float]:
        """BBox en points, origine haut-gauche (0,0,0,0 si non fournie)."""
        try:
            provenance = getattr(item, "prov", None) or []
            if not provenance:
                return (0.0, 0.0, 0.0, 0.0)
            box = provenance[0].bbox
            page_no = int(getattr(provenance[0], "page_no", 1) or 1)
            page = document.pages.get(page_no)  # type: ignore[attr-defined]
            page_height = float(page.size.height) if page is not None else float(box.t + box.b)
            origin = str(getattr(box, "coord_origin", "BOTTOMLEFT")).upper()
            if "BOTTOM" in origin:
                y0 = page_height - float(box.t)
                y1 = page_height - float(box.b)
            else:
                y0 = float(box.t)
                y1 = float(box.b)
            return (
                float(box.l),
                min(y0, y1),
                float(box.r),
                max(y0, y1),
            )
        except Exception:  # noqa: BLE001 — provenance incomplète
            return (0.0, 0.0, 0.0, 0.0)
