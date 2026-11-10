"""Prétraitement d'image du mode Approfondi (filtres purs numpy + Pillow).

Documents difficiles (taches d'encre, papier jauni, photo de téléphone) :
chaque filtre est une fonction pure ``PIL.Image → PIL.Image`` pilotée par un
dictionnaire de paramètres JSON-sérialisable (le même dict traverse l'option
``--filter-json`` du worker et la persistance de l'interface). Aucune
dépendance Qt, aucun nouveau paquet : numpy (déjà présent) et Pillow
suffisent — ``cv2`` est volontairement écarté.

Filtres disponibles (clés de :data:`DEFAULT_FILTER_PARAMS`) :

- ``brightness``      : luminosité, 50–200 % ;
- ``contrast``        : contraste, 50–200 % ; au delà de 100 %, un
  autocontraste par percentiles (coupe symétrique ≤ 3 %) resserre en plus
  les hautes lumières et les ombres ;
- ``shadow_removal``  : suppression d'ombres/taches 0–100 % — le fond est
  estimé par un flou gaussien large (calculé sur une version réduite de la
  page puis rééchantillonné : le fond est par nature très basse fréquence),
  la luminance est divisée par ce fond (correction d'éclairement), puis
  normalisée (hautes lumières ramenées au blanc par un percentile robuste) ;
  la force mélange l'original et l'image corrigée ;
- ``denoise``         : 0 (aucun), 3 ou 5 — filtre médian 3×3 (exact) ou
  double passage 3×3 (approximation d'une médiane 5×5, choisie pour tenir
  le budget < 500 ms/page 300 DPI) ;
- ``binarize``        : binarisation adaptative (Sauvola simplifié : seuil
  local ``m × (1 + k × (s/R − 1))`` par images intégrales numpy, fenêtre
  ~25 px) — texte noir sur fond blanc.

Implémentation : le débruitage médian et la correction d'ombre opèrent sur
la **luminance** (le grain de scan et les ombres sont essentiellement
luminance) — dès qu'un filtre de nettoyage est actif, la sortie est en
niveaux de gris : le mode Approfondi prépare la page pour l'OCR, la teinte
n'y contribue pas, et une réécriture couleur du delta coûterait ~600 ms de
plus par page (au delà du budget). La médiane est calculée par un réseau de
tri min/max vectorisé numpy (~19 comparaisons, coût indépendant du
contenu) : le filtre médian Pillow natif est écarté car ~10× plus lent à
300 DPI. Luminosité et contraste sont fusionnés en une seule LUT 256
entrées par canal (équation de ``ImageEnhance.Brightness`` puis
``Contrast``).

Sortie : mêmes dimensions que l'entrée, mode RGB (L/LA/RGBA/P convertis en
entrée, fond blanc sous la transparence ; la chaîne de nettoyage produit
une luminance reconvertie en RGB en fin de chaîne). Performance visée :
< 500 ms par page A4 300 DPI pour les presets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# --- Paramètres ---------------------------------------------------------------

PARAM_BRIGHTNESS = "brightness"
PARAM_CONTRAST = "contrast"
PARAM_SHADOW_REMOVAL = "shadow_removal"
PARAM_DENOISE = "denoise"
PARAM_BINARIZE = "binarize"
PARAM_BINARIZE_WINDOW = "binarize_window"

BRIGHTNESS_RANGE = (50.0, 200.0)
CONTRAST_RANGE = (50.0, 200.0)
SHADOW_REMOVAL_RANGE = (0.0, 100.0)
DENOISE_CHOICES = (0, 3, 5)
BINARIZE_WINDOW_RANGE = (5, 101)

#: Paramètres par défaut = image inchangée (identité).
DEFAULT_FILTER_PARAMS: Dict[str, Any] = {
    PARAM_BRIGHTNESS: 100.0,
    PARAM_CONTRAST: 100.0,
    PARAM_SHADOW_REMOVAL: 0.0,
    PARAM_DENOISE: 0,
    PARAM_BINARIZE: False,
    PARAM_BINARIZE_WINDOW: 25,
}

#: Presets du mode Approfondi (libellés FR affichés par :func:`list_presets`).
PRESETS: Dict[str, Dict[str, Any]] = {
    "ink": {
        PARAM_BRIGHTNESS: 105.0,
        PARAM_CONTRAST: 130.0,
        PARAM_SHADOW_REMOVAL: 65.0,
        PARAM_DENOISE: 3,
        PARAM_BINARIZE: False,
    },
    "yellowed": {
        PARAM_BRIGHTNESS: 108.0,
        PARAM_CONTRAST: 120.0,
        PARAM_SHADOW_REMOVAL: 40.0,
        PARAM_DENOISE: 0,
        PARAM_BINARIZE: False,
    },
    "low_contrast": {
        PARAM_BRIGHTNESS: 102.0,
        PARAM_CONTRAST: 160.0,
        PARAM_SHADOW_REMOVAL: 20.0,
        PARAM_DENOISE: 0,
        PARAM_BINARIZE: False,
    },
    "phone_photo": {
        PARAM_BRIGHTNESS: 105.0,
        PARAM_CONTRAST: 125.0,
        PARAM_SHADOW_REMOVAL: 80.0,
        PARAM_DENOISE: 3,
        PARAM_BINARIZE: False,
    },
}

PRESET_LABELS: Dict[str, str] = {
    "ink": "Taches d'encre",
    "yellowed": "Papier jauni",
    "low_contrast": "Faible contraste",
    "phone_photo": "Photo téléphone",
}

# Constantes de la suppression d'ombres / binarisation.
_SHADOW_DOWNSCALE = 8  # fond estimé sur 1/8 de résolution (rapide, suffisant)
_SHADOW_PERCENTILE = 99.5  # normalisation : hautes lumières → blanc
_SAUVOLA_K = 0.2
_SAUVOLA_R = 128.0
_MAD_TO_STD = 1.25  # écart absolu moyen ≈ écart-type pour une loi normale
_MEDIAN_TILE_ROWS = 512  # blocs du réseau médian : 12×(512×2480) ≈ 15 Mo (L2/L3)


class PreprocessingError(ValueError):
    """Paramètres de filtre ou preset invalide."""


def list_presets() -> List[Tuple[str, str]]:
    """Presets disponibles ``(nom, libellé FR)``, dans l'ordre d'affichage."""
    return [(name, PRESET_LABELS.get(name, name)) for name in PRESETS]


def preset_params(name: str) -> Dict[str, Any]:
    """Paramètres normalisés d'un preset (copie ; ``ValueError`` si inconnu)."""
    wanted = str(name or "").strip().lower()
    if wanted not in PRESETS:
        raise PreprocessingError(
            f"preset inconnu : {name!r} "
            f"(presets disponibles : {', '.join(PRESETS)})"
        )
    return normalize_params(PRESETS[wanted])


def normalize_params(params: Any) -> Dict[str, Any]:
    """Valide et borne les paramètres de filtre (dict JSON-sérialisable).

    Valeurs manquantes → défauts ; clés inconnues ignorées ; valeurs hors
    bornes ramenées à la borne la plus proche ; ``denoise`` arrondi au choix
    valide (0/3/5) le plus proche ; ``binarize_window`` forcée impaire.
    Un non-dict (chaîne, liste…) lève ``PreprocessingError``.
    """
    if params is None:
        return dict(DEFAULT_FILTER_PARAMS)
    if not isinstance(params, dict):
        raise PreprocessingError(
            f"paramètres de filtre invalides : dict attendu, "
            f"{type(params).__name__} reçu"
        )
    cleaned: Dict[str, Any] = dict(DEFAULT_FILTER_PARAMS)

    for key, (low, high) in (
        (PARAM_BRIGHTNESS, BRIGHTNESS_RANGE),
        (PARAM_CONTRAST, CONTRAST_RANGE),
        (PARAM_SHADOW_REMOVAL, SHADOW_REMOVAL_RANGE),
    ):
        cleaned[key] = _as_clamped_float(params.get(key), cleaned[key], low, high)

    cleaned[PARAM_DENOISE] = _closest_choice(
        params.get(PARAM_DENOISE), DENOISE_CHOICES, cleaned[PARAM_DENOISE]
    )
    # Binarisation : booléen strict. JSON hostile ("0", "false", "oui"…) →
    # défaut ``False`` plutôt qu'une vérité Python surprenante (bool("0")
    # est True) ; 0/1 numériques acceptés (anciens réglages persistés).
    raw_bin = params.get(PARAM_BINARIZE, False)
    if isinstance(raw_bin, bool):
        cleaned[PARAM_BINARIZE] = raw_bin
    elif isinstance(raw_bin, (int, float)):
        cleaned[PARAM_BINARIZE] = bool(raw_bin)
    else:
        cleaned[PARAM_BINARIZE] = False
    window = _as_clamped_float(
        params.get(PARAM_BINARIZE_WINDOW),
        cleaned[PARAM_BINARIZE_WINDOW],
        *BINARIZE_WINDOW_RANGE,
    )
    # Fenêtre impaire garantie (noyau centré) : 25 → 25, 26 → 25.
    window_int = int(round(window))
    if window_int % 2 == 0:
        window_int -= 1
    cleaned[PARAM_BINARIZE_WINDOW] = max(5, window_int)
    return cleaned


def params_differ_from_defaults(params: Any) -> bool:
    """Vrai si les paramètres produisent un filtre (≠ identité)."""
    try:
        cleaned = normalize_params(params)
    except PreprocessingError:
        return True
    for key, default in DEFAULT_FILTER_PARAMS.items():
        if cleaned.get(key) != default:
            return True
    return False


def apply_filters(image: Image.Image, params: Any) -> Image.Image:
    """Applique les filtres demandés ; mêmes dimensions, sortie RGB.

    Ordre d'application : débruitage → suppression d'ombres/taches →
    luminosité + contraste (LUT) → autocontraste (contraste > 100 %) →
    binarisation adaptative. Le dict ``params`` est normalisé
    (:func:`normalize_params`) : aucune valeur hors bornes ne passe.
    """
    if not isinstance(image, Image.Image):
        raise PreprocessingError(
            f"image PIL attendue, {type(image).__name__} reçu"
        )
    if image.size[0] <= 0 or image.size[1] <= 0:
        # Image vide (0×0) : aucun filtre applicable (np.pad échouerait) ;
        # mêmes dimensions, même mode que le chemin normal.
        return _to_rgb(image)
    cleaned = normalize_params(params)
    result = _to_rgb(image)

    denoise = int(cleaned[PARAM_DENOISE])
    shadow = float(cleaned[PARAM_SHADOW_REMOVAL]) / 100.0

    if denoise >= 3 or shadow > 0.0:
        result = _apply_luminance_correction(result, denoise, shadow)

    brightness = float(cleaned[PARAM_BRIGHTNESS])
    contrast = float(cleaned[PARAM_CONTRAST])
    if abs(brightness - 100.0) > 1e-6 or abs(contrast - 100.0) > 1e-6:
        result = _apply_tone_lut(result, brightness, contrast)
        if contrast > 100.0:
            # Autocontraste par percentiles : au delà de 100 %, resserre les
            # extrêmes (au plus 3 % de pixels sacrifiés de chaque côté).
            cutoff = min(3.0, (contrast - 100.0) / 10.0)
            if cutoff >= 0.1:
                result = ImageOps.autocontrast(result, cutoff=(cutoff, cutoff))

    if bool(cleaned[PARAM_BINARIZE]):
        result = _binarize_adaptive(result, int(cleaned[PARAM_BINARIZE_WINDOW]))

    if result.mode != "RGB":  # chaîne de nettoyage → luminance ; RGB uniforme
        result = result.convert("RGB")
    if result.size != image.size:  # pragma: no cover - invariant défensif
        result = result.resize(image.size, Image.BILINEAR)
    return result


def apply_preset(image: Image.Image, preset_name: str) -> Image.Image:
    """Applique un preset nommé (:data:`PRESETS`) à une image PIL."""
    return apply_filters(image, preset_params(preset_name))


# --- Internes -----------------------------------------------------------------


def _as_clamped_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if number != number:  # NaN
        return float(default)
    return min(high, max(low, number))


def _closest_choice(value: Any, choices: Tuple[int, ...], default: int) -> int:
    try:
        number = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return min(choices, key=lambda choice: (abs(choice - number), choice))


def _to_rgb(image: Image.Image) -> Image.Image:
    """L/LA/RGBA/P → RGB (fond blanc sous la transparence), dimensions gardées."""
    if image.mode == "RGB":
        return image
    if image.mode in ("L", "P"):
        return image.convert("RGB")
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _median3_gray(gray: np.ndarray) -> np.ndarray:
    """Médiane 3×3 exacte par réseau de tri (19 comparaisons, Devillard).

    Équivalent ``ImageFilter.MedianFilter(3)`` sur le canal, en ~40 opérations
    vectorisées min/max au lieu d'un balayage naïf par pixel (~10× plus lent
    à 300 DPI). Entrée/sortie : ``uint8`` 2D, mêmes dimensions.

    Le réseau traite la page par **blocs de lignes** (résidents en cache L2,
    halo d'une ligne en haut/bas) : bit-identique au réseau pleine page, la
    médiane 3×3 est une opération strictement locale — mais les tampons
    9+3×(bloc) tiennent alors en cache au lieu de 11 tableaux pleine page,
    soit ~1,7× moins de temps sur une page A4 300 DPI (mesuré).
    """
    height, width = gray.shape
    out = np.empty_like(gray)
    # Réseau de tri médian 9 éléments (N. Devillard, d'après Paeth) :
    # chaque ligne trie une paire ; p[4] porte la médiane à la fin.
    pairs = ((1, 2), (4, 5), (7, 8), (0, 1), (3, 4), (6, 7),
             (1, 2), (4, 5), (7, 8), (0, 3), (5, 8), (4, 7),
             (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4),
             (4, 2))
    tile_rows = _MEDIAN_TILE_ROWS
    for y0 in range(0, height, tile_rows):
        y1 = min(height, y0 + tile_rows)
        tile_height = y1 - y0
        top = 1 if y0 > 0 else 0
        bottom = 1 if y1 < height else 0
        slab = gray[y0 - top : y1 + bottom]
        if top == 0 and bottom == 0:
            slab = np.pad(slab, ((1, 1), (1, 1)), mode="edge")
        elif top == 0:
            slab = np.pad(slab, ((1, 0), (1, 1)), mode="edge")
        elif bottom == 0:
            slab = np.pad(slab, ((0, 1), (1, 1)), mode="edge")
        else:
            slab = np.pad(slab, ((0, 0), (1, 1)), mode="edge")
        p = [
            slab[dy : dy + tile_height, dx : dx + width]
            for dy in (0, 1, 2)
            for dx in (0, 1, 2)
        ]
        # 12 tampons réutilisés (9 en circulation + marge de démarrage,
        # où les vues initiales ne sont pas recyclables) : zéro allocation
        # par comparaison, contrairement à ``np.minimum(a, b)`` qui en
        # alloue deux à chaque échange.
        bufs = [np.empty_like(p[4]) for _ in range(12)]
        owner: list = [None] * 9
        free = list(range(12))

        def sort2(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
            low_index = free.pop()
            high_index = free.pop()
            low = np.minimum(a, b, out=bufs[low_index])
            high = np.maximum(a, b, out=bufs[high_index])
            return low, high, low_index, high_index

        for a, b in pairs:
            low, high, low_index, high_index = sort2(p[a], p[b])
            for slot in (a, b):
                if owner[slot] is not None:
                    free.append(owner[slot])
                    owner[slot] = None
            p[a], p[b] = low, high
            owner[a], owner[b] = low_index, high_index
        out[y0:y1] = p[4]
    return out


def _remove_shadows_gray(gray: np.ndarray, strength: float) -> np.ndarray:
    """Correction d'éclairement sur la luminance (float32 0..255).

    Le **facteur de correction** (division par le fond + normalisation) est
    calculé sur 1/8 de résolution — le fond et le facteur sont par nature
    très basse fréquence — puis rééchantillonné à pleine résolution (mode
    ``F``, C natif) : la division 8,7 Mpx pleine résolution est évitée.
    Le mélange par ``strength`` fond l'original et l'image corrigée.
    """
    height, width = gray.shape
    small_w = max(16, width // _SHADOW_DOWNSCALE)
    small_h = max(16, height // _SHADOW_DOWNSCALE)
    small = Image.fromarray(gray.astype(np.uint8, copy=False), mode="L")
    small = small.resize((small_w, small_h), Image.BILINEAR)
    pixels_small = np.asarray(small, dtype=np.float32)
    radius = max(3.0, min(small_w, small_h) / 6.0)
    background = small.filter(ImageFilter.GaussianBlur(radius=radius))
    bg = np.maximum(np.asarray(background, dtype=np.float32), 1.0)
    factor = np.clip(pixels_small / bg * 255.0, 0.0, 255.0)

    # Normalisation : percentile robuste sur l'échantillon 1/8 (les hautes
    # lumières du papier sont ramenées au blanc sans débordement).
    high = float(np.percentile(factor, _SHADOW_PERCENTILE))
    if high > 1e-3:
        factor *= 255.0 / high
        np.clip(factor, 0.0, 255.0, out=factor)

    factor_full = Image.fromarray(factor, mode="F").resize(
        (width, height), Image.BILINEAR
    )
    factor_full = np.asarray(factor_full, dtype=np.float32)
    return gray + strength * (factor_full - gray)


def _apply_luminance_correction(
    rgb: Image.Image, denoise: int, shadow: float
) -> Image.Image:
    """Débruitage + suppression d'ombres, calculés sur la luminance.

    La chaîne de nettoyage (débruitage médian, correction d'ombre) opère en
    niveaux de gris et sa sortie EST l'image filtrée : le mode Approfondi
    prépare la page pour l'OCR, la teinte n'y contribue pas. Une réécriture
    couleur du delta (26 Mpx × 3 canaux) coûterait ~600 ms de plus par page
    à 300 DPI — au delà du budget. Retour : image ``L`` mêmes dimensions.
    """
    original = np.asarray(rgb.convert("L"), dtype=np.float32)
    corrected = original
    if denoise >= 3:
        median = _median3_gray(original.astype(np.uint8, copy=False))
        if denoise >= 5:
            # Médiane 3×3 + lissage boîte 3×3 : approximation d'une médiane
            # 5×5 sans le coût quadratique du réseau 25 éléments (> 500 ms).
            smoothed = Image.fromarray(median, mode="L").filter(
                ImageFilter.BoxBlur(1)
            )
            median = np.asarray(smoothed, dtype=np.uint8)
        corrected = np.asarray(median, dtype=np.float32)
    if shadow > 0.0:
        corrected = _remove_shadows_gray(corrected, shadow)

    return Image.fromarray(
        np.clip(corrected, 0.0, 255.0).astype(np.uint8, copy=False), mode="L"
    )


def _apply_tone_lut(
    image: Image.Image, brightness: float, contrast: float
) -> Image.Image:
    """Luminosité puis contraste en une seule LUT 256 entrées.

    Équation exacte de ``ImageEnhance.Brightness(b/100)`` puis
    ``ImageEnhance.Contrast(c/100)`` : ``v' = (v·b − 128)·c + 128`` —
    une seule passe C (``Image.point``) au lieu de deux mélanges d'images.
    """
    b = brightness / 100.0
    c = contrast / 100.0
    values = np.arange(256, dtype=np.float32)
    lut = np.clip((values * b - 128.0) * c + 128.0, 0.0, 255.0).astype(np.uint8)
    # PIL attend 256 entrées PAR canal pour une image RVB (même LUT partout).
    flat = lut.tolist()
    return image.point(flat * len(image.getbands()))


def _binarize_adaptive(image: Image.Image, window: int) -> Image.Image:
    """Binarisation adaptative (Sauvola simplifié), fenêtre ``window`` px.

    Seuil local ``T = m × (1 + k × (s/R − 1))`` (k = 0.2, R = 128) où m est
    la moyenne de la fenêtre et s une estimation robuste de l'écart-type :
    l'écart absolu moyen (MAD) de la fenêtre, multiplié par 1,25. Moyenne et
    MAD sont obtenues par deux lissages boîte séparables (C natif Pillow) —
    ~10× plus rapide que des images intégrales float64 à 300 DPI, sans
    perte utile de précision (la quantification du MAD est inférieure au
    gray level). Les pixels plus clairs que le seuil deviennent blancs
    (papier), les plus sombres noirs (encre). Fenêtre impaire garantie.
    """
    gray_img = image.convert("L")
    gray = np.asarray(gray_img, dtype=np.float32)
    if gray.size == 0:  # pragma: no cover - défensif
        return image
    win = max(3, min(int(window), min(gray.shape)))
    if win % 2 == 0:
        win -= 1
    radius = max(1, win // 2)

    mean = np.asarray(
        gray_img.filter(ImageFilter.BoxBlur(radius)), dtype=np.float32
    )
    deviation = np.abs(gray - mean)
    mad_img = Image.fromarray(
        np.clip(deviation, 0.0, 255.0).astype(np.uint8, copy=False), mode="L"
    )
    mad = np.asarray(
        mad_img.filter(ImageFilter.BoxBlur(radius)), dtype=np.float32
    )
    std = mad * _MAD_TO_STD

    threshold = mean * (1.0 + _SAUVOLA_K * (std / _SAUVOLA_R - 1.0))
    binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(binary, mode="L").convert("RGB")


__all__ = [
    "DEFAULT_FILTER_PARAMS",
    "PRESETS",
    "PRESET_LABELS",
    "PreprocessingError",
    "apply_filters",
    "apply_preset",
    "list_presets",
    "normalize_params",
    "params_differ_from_defaults",
    "preset_params",
]
