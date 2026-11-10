"""T1.2 — Moteur Simple : Tesseract local via tesserocr (texte brut)."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path

import tesserocr
from PIL import Image

from core.pipeline.interfaces import (
    EngineCapabilities,
    OcrBlock,
    OcrEngine,
    OcrResult,
)

DEFAULT_LANGUAGES = ("fra", "eng")


def resolve_tessdata() -> Path:
    """Chemin tessdata explicite (R4) : env, puis models/tessdata du dépôt."""
    env_path = os.environ.get("TESSDATA_PREFIX")
    if env_path and Path(env_path).is_dir():
        return Path(env_path)
    repo_models = Path(__file__).resolve().parents[3] / "models" / "tessdata"
    if repo_models.is_dir():
        return repo_models
    raise FileNotFoundError(
        "tessdata introuvable : définir TESSDATA_PREFIX ou fournir models/tessdata"
    )


class TesseractEngine(OcrEngine):
    """Moteur Tesseract 5 (Simple) : texte brut, blocs par ligne détectée."""

    name = "tesseract"

    def __init__(self, tessdata: str | Path | None = None) -> None:
        self._tessdata = Path(tessdata) if tessdata else resolve_tessdata()
        self._api: tesserocr.PyTessBaseAPI | None = None

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(supports_native_text=True, languages=DEFAULT_LANGUAGES)

    @property
    def is_initialized(self) -> bool:
        return self._api is not None

    @property
    def version(self) -> str:
        version = tesserocr.tesseract_version()
        return str(version).splitlines()[0].strip()

    def initialize(self) -> None:
        if self._api is not None:
            return
        self._api = tesserocr.PyTessBaseAPI(
            path=str(self._tessdata), lang="+".join(DEFAULT_LANGUAGES)
        )

    def process(
        self,
        image: Image.Image,
        *,
        languages: Sequence[str] | None = None,
    ) -> OcrResult:
        if self._api is None:
            self.initialize()
        assert self._api is not None
        started = time.perf_counter()

        if languages:
            self._api.SetVariable("tessedit_char_whitelist", "")
            self._api.Init(path=str(self._tessdata), lang="+".join(languages))

        self._api.SetImage(image if image.mode == "RGB" else image.convert("RGB"))
        text = (self._api.GetUTF8Text() or "").strip()
        blocks = tuple(self._extract_blocks(image))
        return OcrResult(
            text=text,
            blocks=blocks,
            engine=self.name,
            engine_version=self.version,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _extract_blocks(self, image: Image.Image) -> list[OcrBlock]:
        """Blocs par ligne de texte avec bbox (points, origine haut-gauche)."""
        assert self._api is not None
        dpi = 300.0
        scale = 72.0 / dpi
        blocks: list[OcrBlock] = []
        try:
            components = self._api.GetComponentImages(
                tesserocr.RIL.TEXTLINE, True, text_only=True
            )
        except Exception:  # noqa: BLE001 — tesserocr sans layout fiable
            return blocks
        for _index, box, _image, _para in components:
            left, top, right, bottom = box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]
            blocks.append(
                OcrBlock(
                    text="",
                    bbox=(left * scale, top * scale, right * scale, bottom * scale),
                    confidence=0.0,
                    kind="line",
                )
            )
        return blocks

    def shutdown(self) -> None:
        if self._api is not None:
            self._api.End()
            self._api = None
