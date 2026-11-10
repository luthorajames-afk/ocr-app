"""T1.1 — Tests de contrat des interfaces OcrEngine / ScannerBackend.

Écrits depuis la spécification (BACKLOG T1.1), sans dépendre d'une
implémentation concrète : seules des doublures de test sont utilisées.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import pytest
from PIL import Image

from core.pipeline.interfaces import (
    EngineCapabilities,
    OcrBlock,
    OcrEngine,
    OcrResult,
    ScannerBackend,
    ScanPage,
    ScanSource,
)


class _FakeEngine(OcrEngine):
    name = "fake"

    def __init__(self) -> None:
        self._initialized = False
        self.calls = 0

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(supports_layout=True)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> None:
        self._initialized = True

    def process(
        self,
        image: Image.Image,
        *,
        languages: Sequence[str] | None = None,
    ) -> OcrResult:
        self.calls += 1
        return OcrResult(
            text="Bonjour",
            blocks=(OcrBlock(text="Bonjour", bbox=(0.0, 0.0, 10.0, 10.0), confidence=1.0),),
            engine=self.name,
            engine_version="0",
            duration_ms=0.0,
        )

    def shutdown(self) -> None:
        self._initialized = False


class _FakeScanner(ScannerBackend):
    name = "fake"

    def is_available(self) -> bool:
        return True

    def list_sources(self) -> tuple[ScanSource, ...]:
        return (ScanSource(source_id="0", name="Scanner test"),)

    def acquire(
        self,
        source_id: str | None = None,
        *,
        dpi: int = 300,
        page_limit: int | None = None,
    ) -> tuple[ScanPage, ...]:
        image = Image.new("RGB", (10, 10), "white")
        count = 1 if page_limit is None else max(0, min(page_limit, 1))
        return tuple(ScanPage(image=image, dpi=dpi, page_number=i + 1) for i in range(count))

    def close(self) -> None:
        pass


def test_interfaces_are_abstract() -> None:
    with pytest.raises(TypeError):
        OcrEngine()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        ScannerBackend()  # type: ignore[abstract]


def test_engine_lifecycle_and_process() -> None:
    engine = _FakeEngine()
    assert engine.is_initialized is False
    engine.initialize()
    assert engine.is_initialized is True
    result = engine.process(Image.new("RGB", (5, 5)))
    assert isinstance(result, OcrResult)
    assert result.text == "Bonjour"
    assert result.blocks and isinstance(result.blocks[0], OcrBlock)
    engine.shutdown()
    assert engine.is_initialized is False


def test_engine_capabilities_defaults() -> None:
    caps = EngineCapabilities()
    assert caps.supports_layout is False
    assert caps.supports_tables is False
    assert "fra" in caps.languages and "eng" in caps.languages


def test_result_dataclasses_are_frozen() -> None:
    block = OcrBlock(text="x", bbox=(0, 0, 1, 1), confidence=0.9)
    with pytest.raises(dataclasses.FrozenInstanceError):
        block.text = "y"  # type: ignore[misc]


def test_scanner_contract() -> None:
    scanner = _FakeScanner()
    assert scanner.is_available()
    sources = scanner.list_sources()
    assert sources and isinstance(sources[0], ScanSource)
    pages = scanner.acquire(sources[0].source_id, dpi=300, page_limit=1)
    assert len(pages) == 1
    assert isinstance(pages[0], ScanPage)
    assert pages[0].page_number == 1
    assert pages[0].dpi == 300
    scanner.close()


def test_fake_engine_does_not_duplicate_initialization_state() -> None:
    engine = _FakeEngine()
    engine.initialize()
    engine.initialize()  # idempotent : reste initialisé
    assert engine.is_initialized is True
    assert engine.calls == 0
