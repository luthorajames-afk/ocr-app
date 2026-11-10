"""T2.2 — Triage de qualité d'image avant OCR (numpy + PIL, sans cv2).

Trois indicateurs :
- netteté      : variance du Laplacien (basse = flou) ;
- inclinaison  : angle dominant par profil de projection (méthode numpy) ;
- contraste    : écart-type des niveaux de gris (RMS).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

SHARP_THRESHOLD = 120.0
LOW_CONTRAST_THRESHOLD = 32.0
SKEW_TOLERANCE = 0.75


@dataclass(frozen=True, slots=True)
class QualityReport:
    sharpness: float
    contrast: float
    skew_degrees: float

    @property
    def is_blurry(self) -> bool:
        return self.sharpness < SHARP_THRESHOLD

    @property
    def is_low_contrast(self) -> bool:
        return self.contrast < LOW_CONTRAST_THRESHOLD

    @property
    def is_skewed(self) -> bool:
        return abs(self.skew_degrees) > SKEW_TOLERANCE

    @property
    def needs_preprocessing(self) -> bool:
        return self.is_blurry or self.is_low_contrast or self.is_skewed


def _gray_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def laplacian_variance(image: Image.Image) -> float:
    """Variance du Laplacien (noyau 3×3), mesure classique de netteté."""
    gray = _gray_array(image)
    if gray.size == 0:
        return 0.0
    kernel = np.array([[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]])
    padded = np.pad(gray, 1, mode="edge")
    height, width = gray.shape
    response = np.zeros_like(gray)
    for i in range(3):
        for j in range(3):
            response += kernel[i, j] * padded[i : i + height, j : j + width]
    return float(response.var())


def rms_contrast(image: Image.Image) -> float:
    gray = _gray_array(image)
    return float(gray.std()) if gray.size else 0.0


def _projection_score(angle: float, gray: np.ndarray) -> float:
    """Variance des profils de projection horizontaux après rotation.

    Un texte aligné produit des rangées nettement séparées → variance élevée.
    """
    image = Image.fromarray(gray.astype(np.uint8))
    rotated = image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
    array = np.asarray(rotated, dtype=np.float64)
    row_sums = (255.0 - array).sum(axis=1)
    return float(row_sums.var())


def estimate_skew(image: Image.Image, max_angle: float = 8.0, step: float = 0.5) -> float:
    """Angle d'inclinaison dominant en degrés (recherche grossière ±8°).

    ``gray`` est réduit en largeur pour rester rapide sur une page 300 DPI.
    """
    gray = _gray_array(image)
    if gray.size == 0:
        return 0.0
    small = Image.fromarray(gray.astype(np.uint8))
    if small.width > 900:
        ratio = 900 / small.width
        small = small.resize((900, max(1, int(small.height * ratio))))
    reduced = np.asarray(small, dtype=np.uint8)

    best_angle = 0.0
    best_score = -math.inf
    angle = -max_angle
    while angle <= max_angle + 1e-9:
        score = _projection_score(angle, reduced)
        if score > best_score:
            best_score = score
            best_angle = angle
        angle = round(angle + step, 3)
    # ``best_angle`` est l'angle de CORRECTION (rotation à appliquer pour
    # redresser) ; l'inclinaison du document est son opposé.
    return -best_angle


def triage(image: Image.Image) -> QualityReport:
    buf = io.BytesIO()
    image.convert("L").save(buf, format="PNG")
    buf.seek(0)
    with Image.open(buf) as opened:
        opened.load()
        quality = opened.copy()
    return QualityReport(
        sharpness=laplacian_variance(quality),
        contrast=rms_contrast(quality),
        skew_degrees=estimate_skew(quality),
    )
