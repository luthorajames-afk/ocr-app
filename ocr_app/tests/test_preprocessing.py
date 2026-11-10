"""Tests du prétraitement du mode Approfondi (``core/preprocessing.py``).

Module pur numpy + Pillow, sans Qt : couvre le contrat de
``apply_filters`` / ``apply_preset`` / ``normalize_params`` /
``list_presets`` exigé par la mission :

- dimensions de sortie identiques à l'entrée, quel que soit le filtre ;
- sortie différente de l'entrée dès qu'un paramètre est actif ;
- identité parfaite avec les paramètres neutres (défauts) ;
- garde image vide (0×0) : pas de crash (``np.pad``), mêmes dims ;
- modes L/RGBA/LA convertis (fond blanc sous la transparence) ;
- params hostiles (NaN, inf, booléens « "0"/"false" ») → défauts/clamp ;
- presets ink/yellowed/low_contrast/phone_photo présents, libellés FR ;
- performance : preset ink sur A4 300 DPI bruitée < 800 ms (marge vs la
  cible < 500 ms), mesure répétée (min de 3) et rapportée en sortie.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PIL import Image

from ocr_app.core.preprocessing import (
    DEFAULT_FILTER_PARAMS,
    PARAM_BRIGHTNESS,
    PARAM_BINARIZE,
    PARAM_CONTRAST,
    PARAM_DENOISE,
    PARAM_SHADOW_REMOVAL,
    PRESETS,
    PreprocessingError,
    apply_filters,
    apply_preset,
    list_presets,
    normalize_params,
    params_differ_from_defaults,
    preset_params,
)

# A4 à 300 DPI (R7) : 2480 × 3508 px.
A4_W_300DPI = 2480
A4_H_300DPI = 3508


def _test_image(width: int = 60, height: int = 40, seed: int = 7) -> Image.Image:
    """Image RGB de test : fond clair + traits sombres (document factice)."""
    rng = np.random.default_rng(seed)
    array = np.full((height, width, 3), 235, dtype=np.int16)
    array[::6, 5 : width - 5] = (40, 40, 40)  # lignes de « texte »
    array += rng.integers(-12, 12, size=array.shape, dtype=np.int16)  # bruit léger
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB")


def _pixels(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"))


# --- apply_filters : contrat de sortie ----------------------------------------


def test_output_dimensions_identical_with_active_filters() -> None:
    image = _test_image()
    for params in (
        {"brightness": 150},
        {"contrast": 180},
        {"shadow_removal": 80},
        {"denoise": 3},
        {"denoise": 5},
        {"binarize": True},
        {"binarize": True, "binarize_window": 9},
    ):
        result = apply_filters(image, params)
        assert result.size == image.size, f"dims changées par {params}"
        assert result.mode == "RGB", f"mode non RGB avec {params}: {result.mode}"


def test_active_params_change_pixels() -> None:
    image = _test_image()
    result = apply_filters(image, {"contrast": 180, "shadow_removal": 60})
    assert not np.array_equal(_pixels(result), _pixels(image)), (
        "des paramètres actifs doivent modifier l'image"
    )


@pytest.mark.parametrize(
    "params",
    [
        None,
        {},
        dict(DEFAULT_FILTER_PARAMS),
        {"brightness": 100.0, "contrast": 100.0},
    ],
    ids=["none", "empty", "defaults", "neutral-explicit"],
)
def test_neutral_params_are_identity(params) -> None:
    image = _test_image()
    result = apply_filters(image, params)
    assert result.size == image.size
    assert np.array_equal(_pixels(result), _pixels(image)), (
        "paramètres neutres : l'image doit être inchangée (identité)"
    )


def test_params_differ_from_defaults_flags() -> None:
    assert params_differ_from_defaults(None) is False
    assert params_differ_from_defaults({}) is False
    assert params_differ_from_defaults({"contrast": 130}) is True
    assert params_differ_from_defaults({"denoise": 3}) is True
    assert params_differ_from_defaults({"binarize": True}) is True


def test_preset_ink_changes_image_and_keeps_dims() -> None:
    image = _test_image(120, 90)
    result = apply_preset(image, "ink")
    assert result.size == image.size
    assert result.mode == "RGB"
    assert not np.array_equal(_pixels(result), _pixels(image))


# --- Garde image vide ----------------------------------------------------------


@pytest.mark.parametrize("params", [None, {"contrast": 130}, PRESETS["ink"]])
def test_empty_image_does_not_crash(params) -> None:
    """Garde 0×0 : ``np.pad`` échouerait sans la garde early-return."""
    empty = Image.new("RGB", (0, 0))
    result = apply_filters(empty, params)
    assert result.size == (0, 0), "dimensions identiques attendues (0×0)"
    assert result.mode == "RGB"


def test_one_pixel_image_with_preset() -> None:
    result = apply_filters(Image.new("RGB", (1, 1), (10, 10, 10)), PRESETS["ink"])
    assert result.size == (1, 1)
    assert result.mode == "RGB"


# --- Conversions de mode -------------------------------------------------------


def test_l_mode_converted_to_rgb() -> None:
    gray = Image.fromarray(np.full((20, 30), 128, dtype=np.uint8), mode="L")
    result = apply_filters(gray, {"contrast": 150})
    assert result.mode == "RGB"
    assert result.size == gray.size


def test_rgba_transparency_flattened_on_white() -> None:
    rgba = Image.new("RGBA", (20, 20), (0, 0, 0, 0))  # tout transparent
    result = apply_filters(rgba, {"brightness": 100})  # params neutres
    assert result.mode == "RGB"
    pixels = _pixels(result)
    assert (pixels == 255).all(), (
        "la transparence doit devenir du papier blanc, jamais noir"
    )


def test_la_mode_converted() -> None:
    la = Image.new("LA", (15, 15), (60, 255))
    result = apply_filters(la, {"contrast": 120})
    assert result.mode == "RGB"
    assert result.size == la.size


def test_palette_mode_converted() -> None:
    palette = Image.new("P", (10, 10))
    palette.putpalette([i for v in range(256) for i in (v, v, v)])
    result = apply_filters(palette, {"brightness": 120})
    assert result.mode == "RGB"


def test_non_pil_input_rejected() -> None:
    with pytest.raises(PreprocessingError):
        apply_filters("pas une image", {"contrast": 120})  # type: ignore[arg-type]


# --- Paramètres hostiles -------------------------------------------------------


def test_nan_params_fall_back_to_defaults() -> None:
    cleaned = normalize_params(
        {
            PARAM_BRIGHTNESS: float("nan"),
            PARAM_CONTRAST: float("nan"),
            PARAM_SHADOW_REMOVAL: float("nan"),
            PARAM_DENOISE: float("nan"),
        }
    )
    assert cleaned[PARAM_BRIGHTNESS] == 100.0
    assert cleaned[PARAM_CONTRAST] == 100.0
    assert cleaned[PARAM_SHADOW_REMOVAL] == 0.0
    assert cleaned[PARAM_DENOISE] == 0


def test_infinite_params_clamped_to_bounds() -> None:
    cleaned = normalize_params(
        {
            PARAM_BRIGHTNESS: float("inf"),
            PARAM_CONTRAST: float("-inf"),
            PARAM_SHADOW_REMOVAL: float("inf"),
        }
    )
    assert cleaned[PARAM_BRIGHTNESS] == 200.0
    assert cleaned[PARAM_CONTRAST] == 50.0
    assert cleaned[PARAM_SHADOW_REMOVAL] == 100.0


def test_out_of_range_values_clamped() -> None:
    cleaned = normalize_params(
        {PARAM_BRIGHTNESS: 10_000, PARAM_CONTRAST: -5, PARAM_SHADOW_REMOVAL: 400}
    )
    assert cleaned[PARAM_BRIGHTNESS] == 200.0
    assert cleaned[PARAM_CONTRAST] == 50.0
    assert cleaned[PARAM_SHADOW_REMOVAL] == 100.0


def test_denoise_snapped_to_valid_choices() -> None:
    for raw, expected in ((1, 0), (2, 3), (3, 3), (4, 3), (5, 5), (9, 5), (-7, 0)):
        assert normalize_params({PARAM_DENOISE: raw})[PARAM_DENOISE] == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, True),
        (False, False),
        (1, True),  # 0/1 numériques acceptés (anciens réglages persistés)
        (0, False),
        (1.0, True),
        ("0", False),  # bool("0") est True en Python : booléen strict exigé
        ("false", False),
        ("1", False),
        ("true", False),
        ("oui", False),
        (None, False),
        ([], False),
    ],
)
def test_binarize_strict_bool(raw, expected) -> None:
    """« "0"/"false" ne valent pas True » : seul un booléen (ou 0/1) compte."""
    assert normalize_params({PARAM_BINARIZE: raw})[PARAM_BINARIZE] is expected


def test_binarize_window_forced_odd_and_clamped() -> None:
    assert normalize_params({"binarize_window": 26})["binarize_window"] == 25
    assert normalize_params({"binarize_window": 4})["binarize_window"] == 5
    assert normalize_params({"binarize_window": 1000})["binarize_window"] == 101


def test_unknown_keys_ignored_and_non_dict_rejected() -> None:
    cleaned = normalize_params({"unknown_key": 42, PARAM_CONTRAST: 140})
    assert "unknown_key" not in cleaned
    assert cleaned[PARAM_CONTRAST] == 140.0
    for bad in ("contrast", [130], 42):
        with pytest.raises(PreprocessingError):
            normalize_params(bad)


def test_apply_filters_accepts_hostile_json_like_params() -> None:
    """Le dict hostile complet passe par normalize_params (aucun crash)."""
    image = _test_image(40, 30)
    result = apply_filters(
        image,
        {
            PARAM_BRIGHTNESS: "x",
            PARAM_CONTRAST: None,
            PARAM_SHADOW_REMOVAL: [1],
            PARAM_DENOISE: "3",
            PARAM_BINARIZE: "false",
            "extra": True,
        },
    )
    assert result.size == image.size
    assert result.mode == "RGB"


# --- Presets --------------------------------------------------------------------


def test_four_presets_present_with_french_labels() -> None:
    presets = dict(list_presets())
    assert set(presets) == {"ink", "yellowed", "low_contrast", "phone_photo"}
    assert presets["ink"] == "Taches d'encre"
    assert presets["yellowed"] == "Papier jauni"
    assert presets["low_contrast"] == "Faible contraste"
    assert presets["phone_photo"] == "Photo téléphone"
    assert list_presets()[0][0] == "ink", "ordre d'affichage : ink en premier"


@pytest.mark.parametrize("name", ["ink", "yellowed", "low_contrast", "phone_photo"])
def test_each_preset_normalized_and_active(name: str) -> None:
    params = preset_params(name)
    assert set(DEFAULT_FILTER_PARAMS) <= set(params), (
        "un preset normalisé porte toutes les clés par défaut"
    )
    assert params_differ_from_defaults(params), f"le preset {name} filtre l'image"
    image = _test_image(100, 70)
    result = apply_preset(image, name)
    assert result.size == image.size
    assert result.mode == "RGB"
    assert not np.array_equal(_pixels(result), _pixels(image))


def test_unknown_preset_rejected() -> None:
    with pytest.raises(PreprocessingError):
        preset_params("bogus")
    with pytest.raises(PreprocessingError):
        preset_params("")


def test_preset_returns_a_copy() -> None:
    first = preset_params("ink")
    first[PARAM_CONTRAST] = 999.0
    assert preset_params("ink")[PARAM_CONTRAST] == PRESETS["ink"][PARAM_CONTRAST]


# --- Performance -----------------------------------------------------------------


def _noisy_a4_image(seed: int = 42) -> Image.Image:
    """Page A4 300 DPI bruitée : fond papier + lignes de texte + grain."""
    rng = np.random.default_rng(seed)
    gray = np.full((A4_H_300DPI, A4_W_300DPI), 245, dtype=np.float32)
    for top in range(120, A4_H_300DPI - 120, 90):
        gray[top : top + 14, 200 : A4_W_300DPI - 200] = 35.0  # lignes de texte
    gray += rng.normal(0.0, 14.0, size=gray.shape)  # grain de scan
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=-1)  # 3 canaux pour mode="RGB"
    return Image.fromarray(rgb, mode="RGB")


def test_perf_ink_preset_a4_300dpi_under_budget() -> None:
    """Preset ink sur A4 300 DPI bruitée : min de 3 mesures < 800 ms.

    Cible mission < 500 ms ; 800 ms gardent une marge pour une machine
    chargée (cf. deep_mode_review.md C6). La mesure est imprimée pour le
    rapport.
    """
    image = _noisy_a4_image()
    assert image.size == (A4_W_300DPI, A4_H_300DPI)

    timings: list[float] = []
    for _ in range(3):
        started = time.perf_counter()
        result = apply_preset(image, "ink")
        timings.append((time.perf_counter() - started) * 1000.0)
        assert result.size == image.size

    best = min(timings)
    median = sorted(timings)[1]
    print(
        f"\n[perf] preset ink {A4_W_300DPI}x{A4_H_300DPI} : "
        f"runs={', '.join(f'{t:.0f} ms' for t in timings)} ; "
        f"min={best:.0f} ms ; médiane={median:.0f} ms"
    )
    assert best < 800.0, (
        f"preset ink trop lent : min {best:.0f} ms (runs {timings}) >= 800 ms"
    )
