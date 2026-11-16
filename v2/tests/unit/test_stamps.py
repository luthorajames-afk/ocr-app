"""Filtre des tampons : blocs parasites retirés, texte sain conservé."""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.pipeline.interfaces import OcrBlock
from core.pipeline.stamps import filter_stamp_blocks, red_ratio


def image_with_red_zone(x0: int, y0: int, x1: int, y1: int, density: float) -> Image.Image:
    rng = np.random.default_rng(42)
    img = Image.new("RGB", (850, 1200), "white")
    arr = np.asarray(img).copy()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if rng.random() < density:
                arr[y, x] = (180, 60, 60)
    return Image.fromarray(arr)


def block(text: str, bbox: tuple[float, float, float, float], conf: float = 0.9) -> OcrBlock:
    return OcrBlock(text=text, bbox=bbox, confidence=conf, kind="line")


def test_red_ratio_measures_ink() -> None:
    plain = image_with_red_zone(0, 0, 10, 10, 0.0)
    assert red_ratio(plain, (0.0, 0.0, 50.0, 50.0)) == 0.0
    stamped = image_with_red_zone(0, 0, 300, 300, 0.8)
    assert red_ratio(stamped, (0.0, 0.0, 100.0, 100.0)) > 0.25


def test_dense_stamp_block_is_dropped() -> None:
    img = image_with_red_zone(0, 0, 600, 600, 0.8)
    blocks = [
        block("UU00U2( E", (5.0, 5.0, 140.0, 30.0), conf=0.22),
        block("Texte sain du document", (5.0, 700.0, 400.0, 730.0), conf=0.92),
    ]
    kept = filter_stamp_blocks(blocks, img)
    assert [item.text for item in kept] == ["Texte sain du document"]


def test_teinted_low_confidence_is_dropped() -> None:
    img = image_with_red_zone(0, 0, 600, 300, 0.15)
    blocks = [
        block("garbage du tampon", (5.0, 5.0, 200.0, 30.0), conf=0.20),
        block("phrase sous le tampon", (5.0, 100.0, 400.0, 130.0), conf=0.85),
    ]
    kept = filter_stamp_blocks(blocks, img)
    assert [item.text for item in kept] == ["phrase sous le tampon"]


def test_stamp_serial_with_good_confidence_is_kept() -> None:
    img = image_with_red_zone(0, 0, 300, 120, 0.10)
    blocks = [block("004209 30 AVR 2020", (5.0, 10.0, 150.0, 30.0), conf=0.86)]
    kept = filter_stamp_blocks(blocks, img)
    assert [item.text for item in kept] == ["004209 30 AVR 2020"]
