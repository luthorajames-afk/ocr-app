"""T3b — Tests des backends scanner et du lot avec pages blanches."""

from __future__ import annotations

import os

import pytest
from PIL import Image, ImageDraw

from core.io import scanner
from core.pipeline.interfaces import ScanPage


def _text_page(number: int = 1) -> ScanPage:
    image = Image.new("L", (400, 500), 255)
    draw = ImageDraw.Draw(image)
    for row in range(10):
        y = 40 + row * 40
        draw.rectangle([40, y, 360, y + 14], fill=0)
    return ScanPage(image=image.convert("RGB"), dpi=300, page_number=number)


def _blank_page(number: int = 1) -> ScanPage:
    image = Image.new("RGB", (400, 500), "white")
    return ScanPage(image=image, dpi=300, page_number=number)


def test_ink_ratio_distinguishes_text_and_blank() -> None:
    assert scanner.ink_ratio(_text_page().image) > 0.01
    assert scanner.ink_ratio(_blank_page().image) == 0.0


def test_batch_flags_blank_pages_without_removing() -> None:
    pages = [_text_page(1), _blank_page(2), _text_page(3)]
    batch = scanner.build_batch(pages)
    assert len(batch.pages) == 3  # aucune suppression automatique
    assert batch.blank_flags == (False, True, False)
    assert batch.blank_pages == (2,)


def test_escl_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AOCRI_ALLOW_ESCL", raising=False)
    backend = scanner.EsclScannerBackend()
    assert backend.is_available() is False
    with pytest.raises(scanner.ScannerError):
        backend.acquire()


def test_escl_enabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AOCRI_ALLOW_ESCL", "1")
    assert scanner.EsclScannerBackend().is_available() is True


def test_wia_declares_unavailable_without_pywin32() -> None:
    backend = scanner.WiaScannerBackend()
    if os.name == "nt" and scanner._has_module("win32com.client"):
        pytest.skip("pywin32 installé : comportement matériel dépendant")
    assert backend.is_available() is False
    with pytest.raises(scanner.ScannerError):
        backend.list_sources()


def test_select_backend_returns_none_or_available() -> None:
    backend = scanner.select_backend()
    if backend is None:
        assert scanner.available_backends() == []
    else:
        assert backend.name in scanner.available_backends()
