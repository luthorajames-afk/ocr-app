"""Détection des tampons/cachets : blocs OCR parasites sur encre colorée.

Les tampons administratifs (visa du Premier Ministre, « DUPLICATA ») qui
chevauchent le texte produisent des blocs OCR sans valeur. Le filtre est
volontairement conservateur : on ne supprime un bloc que si l'encre rouge
couvre la quasi-totalité de sa boîte, ou si la zone est teintée ET que la
confiance OCR est effondrée. Un titre rouge du document original (confiance
normale) est conservé, de même que les numéros de visa lisibles.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.pipeline.interfaces import OcrBlock

DENSE_RED_RATIO = 0.25
TEINTED_RED_RATIO = 0.06
LOW_CONFIDENCE = 0.35


def red_ratio(image: Image.Image, bbox: tuple[float, float, float, float], dpi: int = 300) -> float:
    """Proportion de pixels d'encre rouge dans la boîte (points, haut-gauche)."""
    scale = dpi / 72.0
    x0, y0, x1, y1 = bbox
    left = max(0, int(x0 * scale))
    top = max(0, int(y0 * scale))
    right = min(image.width, int(x1 * scale))
    bottom = min(image.height, int(y1 * scale))
    if right <= left or bottom <= top:
        return 0.0
    crop = image.crop((left, top, right, bottom))
    downsampled = crop.resize((max(1, crop.width // 2), max(1, crop.height // 2)))
    array = np.asarray(downsampled, dtype=np.int16)
    red, green, blue = array[..., 0], array[..., 1], array[..., 2]
    mask = (red > 80) & (red > green * 1.1) & (red > blue * 1.1)
    return float(mask.mean())


def filter_stamp_blocks(
    blocks: list[OcrBlock],
    image: Image.Image,
    dpi: int = 300,
) -> list[OcrBlock]:
    """Retire les blocs parasites imputables à un tampon coloré."""
    kept: list[OcrBlock] = []
    for block in blocks:
        ratio = red_ratio(image, block.bbox, dpi=dpi)
        dense = ratio >= DENSE_RED_RATIO
        teinted_garbage = ratio >= TEINTED_RED_RATIO and block.confidence < LOW_CONFIDENCE
        if dense or teinted_garbage:
            continue
        kept.append(block)
    return kept
