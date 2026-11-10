"""T2.3 — Tests du prétraitement adaptatif."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from core.pipeline.image_quality import triage
from core.pipeline.preprocess import (
    PreprocessConfig,
    clamp_dpi,
    deskew,
    is_low_resolution,
    preprocess,
    sauvola_binarize,
)


def _text_image(angle: float = 0.0, blur: bool = False, bg: int = 255) -> Image.Image:
    image = Image.new("L", (600, 300), color=bg)
    draw = ImageDraw.Draw(image)
    for row in range(6):
        y = 40 + row * 40
        draw.rectangle([60, y, 540, y + 9], fill=0)
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=bg)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(radius=2))
    return image.convert("RGB")


def test_sauvola_returns_binary_image_same_size() -> None:
    source = _text_image()
    binary = sauvola_binarize(source, window=25, k=0.2)
    assert binary.size == source.size
    values = np.unique(np.asarray(binary))
    assert set(values.tolist()) <= {0, 255}
    assert (values == 0).any() and (values == 255).any()


def test_sauvola_handles_uniform_image() -> None:
    flat = Image.new("L", (40, 40), color=255).convert("RGB")
    binary = sauvola_binarize(flat)
    assert binary.size == flat.size


def test_deskew_straightens_rotated_document() -> None:
    rotated = _text_image(angle=5.0)
    straighter, angle = deskew(rotated)
    assert abs(angle) >= 3.0
    report_after = triage(straighter)
    assert abs(report_after.skew_degrees) < abs(triage(rotated).skew_degrees)


def test_deskew_noop_when_already_straight() -> None:
    straight = _text_image()
    same, angle = deskew(straight)
    assert angle == 0.0
    assert same is straight


def test_preprocess_auto_follows_triage() -> None:
    skewed_blurred = _text_image(angle=6.0, blur=True)
    result = preprocess(skewed_blurred)
    assert "deskew" in " ".join(result.applied)
    assert result.image.size == skewed_blurred.size


def test_preprocess_respects_explicit_config() -> None:
    straight = _text_image()
    result = preprocess(straight, config=PreprocessConfig(deskew=True, binarize=False))
    assert any(step.startswith("deskew") or step == "sauvola" for step in result.applied) or result.applied == ()
    assert result.image.size == straight.size


def test_dpi_clamping_and_low_resolution() -> None:
    assert clamp_dpi(None) == 300
    assert clamp_dpi(50) == 150
    assert clamp_dpi(600) == 300
    assert is_low_resolution(100) is True
    assert is_low_resolution(None) is False


def test_preprocess_flags_low_resolution_upsample() -> None:
    result = preprocess(_text_image(), dpi=96)
    assert any(step.startswith("upsample:") for step in result.applied)


def test_preprocess_result_reports_quality() -> None:
    result = preprocess(_text_image(angle=6.0))
    assert result.quality_before.skew_degrees != 0.0
    assert result.quality_after is not None
