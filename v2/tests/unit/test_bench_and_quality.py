"""T2.1/T2.2 — Tests du banc CER/WER et du triage qualité."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

V2_ROOT = Path(__file__).resolve().parents[2]
BENCH = V2_ROOT / "tests" / "bench"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from bench_runner import cer, edit_distance, normalize, wer

from core.pipeline.image_quality import (
    QualityReport,
    estimate_skew,
    laplacian_variance,
    rms_contrast,
    triage,
)


def _text_image(angle: float = 0.0, contrast: int = 0, blur: bool = False) -> Image.Image:
    """Lignes de texte (encre sombre) ; ``contrast`` = niveau d'encre (0 = noir)."""
    image = Image.new("L", (600, 300), color=255)
    draw = ImageDraw.Draw(image)
    for row in range(6):
        y = 40 + row * 40
        draw.rectangle([60, y, 540, y + 10], fill=contrast)
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(radius=3))
    return image.convert("RGB")


# --- CER / WER ---------------------------------------------------------------

def test_normalize_collapses_whitespace() -> None:
    assert normalize("  Bonjour\n\t le  monde ") == "Bonjour le monde"


def test_edit_distance_identity_and_shift() -> None:
    assert edit_distance("abc", "abc") == 0
    assert edit_distance("abc", "abd") == 1
    assert edit_distance("", "abc") == 3


def test_cer_perfect_and_total() -> None:
    reference = "Le décret n° 2026-041 est publié."
    assert cer(reference, reference) == 0.0
    assert cer("", reference) == 1.0


def test_wer_counts_word_errors() -> None:
    reference = "le texte est exact"
    assert wer("le texte est exact", reference) == 0.0
    assert wer("le texte exact", reference) == pytest.approx(0.25)


# --- Triage qualité ----------------------------------------------------------

def test_sharp_image_is_not_blurry() -> None:
    assert laplacian_variance(_text_image()) > 100.0


def test_blurred_image_has_lower_sharpness() -> None:
    sharp = laplacian_variance(_text_image())
    blurred = laplacian_variance(_text_image(blur=True))
    assert blurred < sharp


def test_low_contrast_detected() -> None:
    report = triage(_text_image(contrast=120))
    assert report.contrast < 60.0  # gris clair sur blanc


def test_skew_estimation_detects_rotation() -> None:
    angle = estimate_skew(_text_image(angle=6.0))
    assert abs(angle - 6.0) <= 1.5


def test_triage_report_flags_preprocessing_needs() -> None:
    report = triage(_text_image(angle=6.0, blur=True, contrast=120))
    assert isinstance(report, QualityReport)
    assert report.needs_preprocessing is True
    assert report.is_blurry is True
    assert report.is_skewed is True


def test_rms_contrast_of_flat_image_is_zero() -> None:
    flat = Image.new("L", (50, 50), color=255).convert("RGB")
    assert rms_contrast(flat) == 0.0
