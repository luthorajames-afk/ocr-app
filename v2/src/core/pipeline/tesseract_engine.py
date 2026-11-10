"""T1.2 — Moteur Simple : Tesseract local via tesserocr (texte brut)."""

from __future__ import annotations

import os
import sys
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
    """Chemin tessdata explicite (R4) : env, bundle PyInstaller, puis dépôt."""
    env_path = os.environ.get("TESSDATA_PREFIX")
    if env_path and Path(env_path).is_dir():
        return Path(env_path)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bundled = Path(bundle) / "models" / "tessdata"
        if bundled.is_dir():
            return bundled
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

    def process_region(
        self,
        image: Image.Image,
        bbox: tuple[float, float, float, float],
        *,
        languages: Sequence[str] | None = None,
    ) -> str:
        """OCR d'une zone (points, origine haut-gauche) — seconde passe T2.4.

        ``image`` doit être rendue à 300 DPI (convention des bboxes).
        """
        if self._api is None:
            self.initialize()
        assert self._api is not None
        if languages:
            self._api.Init(path=str(self._tessdata), lang="+".join(languages))

        scale = 300.0 / 72.0
        x0, y0, x1, y1 = bbox
        left = max(0, int(x0 * scale) - 4)
        top = max(0, int(y0 * scale) - 4)
        right = min(image.width, int(x1 * scale) + 4)
        bottom = min(image.height, int(y1 * scale) + 4)
        if right <= left or bottom <= top:
            return ""
        crop = image.crop((left, top, right, bottom))
        self._api.SetImage(crop if crop.mode == "RGB" else crop.convert("RGB"))
        return (self._api.GetUTF8Text() or "").strip()

    def _extract_blocks(self, image: Image.Image) -> list[OcrBlock]:
        """Lignes de texte avec bbox (points, origine haut-gauche) et confiance.

        S'appuie sur la sortie TSV de Tesseract : les mots sont agrégés par
        ligne (block/par/line) pour obtenir texte et confiance moyenne — la
        seconde passe (T2.4) cible les lignes sous le seuil.
        """
        assert self._api is not None
        scale = 72.0 / 300.0  # bboxes en points pour une page rendue à 300 DPI
        try:
            raw = self._api.GetTSVText(0)
        except Exception:  # noqa: BLE001 — moteur sans TSV
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        lines: dict[tuple[str, str, str, str], dict[str, float | str]] = {}
        for row in (raw or "").splitlines():
            parts = row.split("\t")
            if len(parts) < 12 or parts[0] != "5":  # niveau mot
                continue
            try:
                left, top, width, height = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
                confidence = float(parts[10])
            except (ValueError, IndexError):
                continue
            word = parts[11].strip()
            if not word:
                continue
            key = (parts[1], parts[2], parts[3], parts[4])
            entry = lines.setdefault(
                key,
                {"text": "", "x0": 1e12, "y0": 1e12, "x1": 0.0, "y1": 0.0, "conf": 0.0, "n": 0.0},
            )
            entry["text"] = f"{entry['text']} {word}".strip()
            entry["x0"] = min(float(entry["x0"]), float(left))
            entry["y0"] = min(float(entry["y0"]), float(top))
            entry["x1"] = max(float(entry["x1"]), float(left + width))
            entry["y1"] = max(float(entry["y1"]), float(top + height))
            entry["conf"] = float(entry["conf"]) + confidence
            entry["n"] = float(entry["n"]) + 1.0

        blocks = [
            OcrBlock(
                text=str(entry["text"]),
                bbox=(
                    float(entry["x0"]) * scale,
                    float(entry["y0"]) * scale,
                    float(entry["x1"]) * scale,
                    float(entry["y1"]) * scale,
                ),
                confidence=(float(entry["conf"]) / float(entry["n"]) / 100.0),
                kind="line",
            )
            for entry in lines.values()
            if float(entry["n"]) > 0
        ]
        blocks.sort(key=lambda block: (round(block.bbox[1], 1), block.bbox[0]))
        return blocks

    def shutdown(self) -> None:
        if self._api is not None:
            self._api.End()
            self._api = None
