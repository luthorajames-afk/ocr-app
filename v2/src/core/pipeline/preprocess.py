"""T2.3 — Prétraitement adaptatif : redressement, Sauvola local, DPI.

Aucune dépendance lourde : numpy + PIL (cv2 absent du noyau, R1).
Le prétraitement est piloté par le triage qualité (T2.2) : chaque étape ne
s'exécute que si elle est nécessaire, ou sur demande explicite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from core.pipeline.image_quality import QualityReport, estimate_skew, triage

MAX_DPI = 300
MIN_DPI = 150
TARGET_DPI = 300


@dataclass(frozen=True, slots=True)
class PreprocessConfig:
    """Options de prétraitement ; ``None`` = décision automatique par triage."""

    deskew: bool | None = None
    binarize: bool | None = None
    target_dpi: int | None = None
    sauvola_window: int = 25
    sauvola_k: float = 0.2


@dataclass(frozen=True, slots=True)
class PreprocessResult:
    image: Image.Image
    applied: tuple[str, ...]
    quality_before: QualityReport
    quality_after: QualityReport


def _to_gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def deskew(image: Image.Image, max_angle: float = 8.0) -> tuple[Image.Image, float]:
    """Redresse l'image selon l'angle dominant ; retourne (image, correction).

    ``estimate_skew`` retourne l'inclinaison du document ; la correction est
    son opposé (PIL : angle positif = sens anti-horaire).
    """
    skew = estimate_skew(image, max_angle=max_angle)
    if abs(skew) < 0.25:
        return image, 0.0
    correction = -skew
    rotated = image.rotate(
        correction, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
    )
    return rotated, correction


def _box_mean(array: np.ndarray, radius: int) -> np.ndarray:
    """Moyenne locale par intégrale (fenêtre carrée 2r+1), bord par réplication."""
    padded = np.pad(array, radius, mode="edge")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    h, w = array.shape
    size = 2 * radius + 1
    total = (
        integral[size : size + h, size : size + w]
        - integral[0:h, size : size + w]
        - integral[size : size + h, 0:w]
        + integral[0:h, 0:w]
    )
    mean: np.ndarray = total / float(size * size)
    return mean


def sauvola_binarize(
    image: Image.Image, window: int = 25, k: float = 0.2, r: float = 128.0
) -> Image.Image:
    """Binarisation locale de Sauvola (seuil = m·(1 + k·(s/R − 1)))."""
    gray = _to_gray(image)
    if gray.size == 0:
        return image.convert("L")
    radius = max(1, window // 2)
    mean = _box_mean(gray, radius)
    squared = _box_mean(gray * gray, radius)
    std = np.sqrt(np.maximum(squared - mean * mean, 0.0))
    threshold = mean * (1.0 + k * (std / r - 1.0))
    binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(binary, mode="L")


def is_low_resolution(dpi: int | None) -> bool:
    """Vrai si la résolution native est insuffisante pour l'OCR."""
    return dpi is not None and dpi < MIN_DPI


def clamp_dpi(dpi: int | None) -> int:
    if dpi is None:
        return TARGET_DPI
    return max(MIN_DPI, min(int(dpi), MAX_DPI))


def preprocess(
    image: Image.Image,
    *,
    dpi: int | None = None,
    config: PreprocessConfig | None = None,
) -> PreprocessResult:
    """Applique le prétraitement adaptatif et retourne l'image + journal."""
    config = config or PreprocessConfig()
    quality_before = triage(image)

    do_deskew = config.deskew if config.deskew is not None else quality_before.is_skewed
    do_binarize = (
        config.binarize
        if config.binarize is not None
        else (quality_before.is_low_contrast or quality_before.is_blurry)
    )

    applied: list[str] = []
    result = image.convert("RGB")

    if do_deskew:
        result, angle = deskew(result)
        if angle:
            applied.append(f"deskew:{angle:+.1f}deg")

    if do_binarize:
        result = sauvola_binarize(
            result, window=config.sauvola_window, k=config.sauvola_k
        ).convert("RGB")
        applied.append("sauvola")

    if is_low_resolution(dpi):
        applied.append(f"upsample:{dpi}->{clamp_dpi(dpi)}dpi")

    quality_after = triage(result)
    return PreprocessResult(
        image=result,
        applied=tuple(applied),
        quality_before=quality_before,
        quality_after=quality_after,
    )
