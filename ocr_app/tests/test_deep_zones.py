"""Tests des zones d'exclusion du mode Approfondi (parsing, pipeline, store).

Trois niveaux, tous headless (aucun Qt) :

1. ``workers/worker_main.parse_exclude_zones`` — format CLI
   ``"x0,y0,x1,y1;…"`` : rectangles valides normalisés (x0 ≤ x1, y0 ≤ y1),
   zones dégénérées ignorées, malformations / NaN / inf / texte rejetés
   (``ValueError`` → exit 2 côté worker, cf. test_deep_worker.py) ;
2. ``core/pipeline`` — ``PipelineConfig.excluded_zones`` normalisé (NaN/inf
   → ``ValueError``), masque blanc papier (jamais noir) sur l'image envoyée
   au moteur, prune des blocs dont ≥ 50 % de la surface intersecte UNE zone
   (règle documentée : max par zone, pas l'union — cf. review C11 ;
   « exactement 50 % » → le bloc est retiré, seuil inclusif) ;
3. ``ui/exclusion_store`` — persistance par page via ``AOCRI_DEEP_DIR``
   (dossier accentué), roundtrip zones + filtres, lecture corrompue →
   défauts, zones non finies ignorées à l'écriture comme à la lecture,
   écriture atomique (aucun ``.tmp`` résiduel), création paresseuse
   (aucun fichier à l'import / à la lecture d'une page absente).
"""

from __future__ import annotations

import math
import os

import numpy as np
import pytest
from PIL import Image

from ocr_app.core.data_model import Block, BlockType, Page
from ocr_app.core.pipeline import (
    Pipeline,
    PipelineConfig,
    ZONE_PRUNE_RATIO,
    _zone_overlap_ratio,
)
from ocr_app.workers.worker_main import parse_exclude_zones

pytest.importorskip("numpy")

from ocr_app.ui import exclusion_store  # noqa: E402 - stdlib uniquement


# --- parse_exclude_zones (CLI worker) ------------------------------------------


def test_parse_exclude_zones_single_valid() -> None:
    assert parse_exclude_zones("0,0,100,50") == ((0.0, 0.0, 100.0, 50.0),)


def test_parse_exclude_zones_swapped_corners_normalized() -> None:
    assert parse_exclude_zones("100,50,0,0") == ((0.0, 0.0, 100.0, 50.0),)
    assert parse_exclude_zones("20,30,10,5") == ((10.0, 5.0, 20.0, 30.0),)


def test_parse_exclude_zones_multiple_and_whitespace() -> None:
    zones = parse_exclude_zones(" 0 , 0 , 10 , 10 ; 20,30,40,50 ")
    assert zones == ((0.0, 0.0, 10.0, 10.0), (20.0, 30.0, 40.0, 50.0))


def test_parse_exclude_zones_empty_and_blank() -> None:
    assert parse_exclude_zones(None) == ()
    assert parse_exclude_zones("") == ()
    assert parse_exclude_zones("   ") == ()
    assert parse_exclude_zones(";;") == ()


@pytest.mark.parametrize(
    "raw",
    [
        "5,5,5,5",  # point dégénéré
        "0,0,0,10",  # largeur nulle
        "10,10,10,20",  # largeur nulle (échangée)
    ],
)
def test_parse_exclude_zones_degenerate_ignored(raw: str) -> None:
    assert parse_exclude_zones(raw) == ()


@pytest.mark.parametrize(
    "raw",
    [
        "1,2,3",  # 3 coordonnées
        "1,2,3,4,5",  # 5 coordonnées
        "abc,1,2,3",  # texte
        "1,2,3,x",
        "1;2;3;4",  # « ; » est le séparateur : 4 parties invalides
    ],
)
def test_parse_exclude_zones_malformed_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_exclude_zones(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "nan,0,100,100",
        "0,0,100,inf",
        "-inf,0,1,1",
        "0,nan,50,50",
    ],
)
def test_parse_exclude_zones_non_finite_rejected(raw: str) -> None:
    """NaN/inf se parsent comme des floats valides : garde explicite."""
    with pytest.raises(ValueError):
        parse_exclude_zones(raw)


def test_parse_exclude_zones_floats_accepted() -> None:
    zones = parse_exclude_zones("42.2,21,198,42.8")
    assert zones == ((42.2, 21.0, 198.0, 42.8),)


# --- PipelineConfig.excluded_zones ----------------------------------------------


def test_pipeline_config_zones_normalized() -> None:
    config = PipelineConfig(
        mode="approfondi",
        excluded_zones=[(100, 50, 0, 0), [5, 5, 5, 5], (1, 2, 3, 4)],
    )
    assert config.excluded_zones == ((0.0, 0.0, 100.0, 50.0), (1.0, 2.0, 3.0, 4.0))


def test_pipeline_config_zones_empty_by_default() -> None:
    config = PipelineConfig(mode="approfondi")
    assert config.excluded_zones == ()
    assert config.filter_params is None


@pytest.mark.parametrize(
    "zones",
    [
        [(float("nan"), 0.0, 10.0, 10.0)],
        [(0.0, 0.0, float("inf"), 10.0)],
        "0,0,10,10",  # une chaîne n'est pas une séquence de rectangles
        [(0, 0, 10)],  # longueur invalide
        [(0, 0, 10, 10, 20)],
        ["0,0,10,10"],  # rectangle-chaîne refusé
    ],
)
def test_pipeline_config_invalid_zones_rejected(zones) -> None:
    with pytest.raises(ValueError):
        PipelineConfig(mode="approfondi", excluded_zones=zones)


def test_pipeline_config_filter_params_normalized() -> None:
    config = PipelineConfig(mode="approfondi", filter_params={"contrast": 9999})
    assert config.filter_params is not None
    assert config.filter_params["contrast"] == 200.0


def test_pipeline_config_filter_params_non_dict_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(mode="approfondi", filter_params="contrast")


def test_zone_overlap_ratio() -> None:
    block = (0.0, 0.0, 100.0, 100.0)
    assert _zone_overlap_ratio(block, ((0.0, 0.0, 100.0, 100.0),)) == 1.0
    assert _zone_overlap_ratio(block, ((0.0, 0.0, 50.0, 100.0),)) == pytest.approx(0.5)
    assert _zone_overlap_ratio(block, ((200.0, 200.0, 300.0, 300.0),)) == 0.0
    assert _zone_overlap_ratio(block, ()) == 0.0


def _page_with_blocks() -> Page:
    return Page(
        number=1,
        source="memory://test",
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text="dans la zone",
                bbox=(10.0, 10.0, 110.0, 60.0),  # 100 % couvert ci-dessous
                confidence=0.9,
                block_id="b1",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text="hors de la zone",
                bbox=(300.0, 300.0, 500.0, 400.0),
                confidence=0.9,
                block_id="b2",
            ),
        ],
    )


def test_prune_block_fully_inside_zone_removed() -> None:
    pipeline = Pipeline(
        PipelineConfig(mode="approfondi", excluded_zones=((0.0, 0.0, 200.0, 100.0),))
    )
    page = pipeline._prune_excluded_blocks(_page_with_blocks())
    texts = [block.text for block in page.blocks]
    assert "dans la zone" not in texts
    assert "hors de la zone" in texts


def test_prune_block_partially_covered_kept() -> None:
    # Le bloc b2 (300..500 × 300..400) : la zone couvre 20 pt de large sur
    # 100 → 20 000 / 20 000 = 20 % de la surface → conservé.
    pipeline = Pipeline(
        PipelineConfig(mode="approfondi", excluded_zones=((480.0, 300.0, 900.0, 400.0),))
    )
    page = pipeline._prune_excluded_blocks(_page_with_blocks())
    assert [block.text for block in page.blocks] == [
        "dans la zone",
        "hors de la zone",
    ]


def test_prune_exactly_fifty_percent_is_documented_and_removed() -> None:
    """« au moins 50 % » (ZONE_PRUNE_RATIO inclusif) : le bloc est retiré.

    Bloc 100×100, zone couvrant exactement la moitié (50×100) → ratio = 0.5
    → ``_prune_excluded_blocks`` retire le bloc (kept si ratio < 0.5).
    """
    assert ZONE_PRUNE_RATIO == 0.5
    page = Page(
        number=1,
        source="memory://test",
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text="moitié pile",
                bbox=(0.0, 0.0, 100.0, 100.0),
                confidence=0.9,
                block_id="b1",
            )
        ],
    )
    pipeline = Pipeline(
        PipelineConfig(mode="approfondi", excluded_zones=((50.0, 0.0, 150.0, 100.0),))
    )
    pruned = pipeline._prune_excluded_blocks(page)
    assert _zone_overlap_ratio((0.0, 0.0, 100.0, 100.0), ((50.0, 0.0, 150.0, 100.0),)) == pytest.approx(0.5)
    assert pruned.blocks == [], "ratio pile 50 % : le bloc est retiré (>= 50 %)"


def test_prune_without_zones_is_noop() -> None:
    pipeline = Pipeline(PipelineConfig(mode="approfondi"))
    page = _page_with_blocks()
    assert pipeline._prune_excluded_blocks(page).blocks == page.blocks


def test_prune_two_overlapping_zones_do_not_union_documented() -> None:
    """Règle documentée (review C11) : max par UNE zone, pas l'union.

    Deux zones couvrant 40 % chacune du même bloc (sans dépasser 50 %
    individuellement) ne retirent pas le bloc.
    """
    page = Page(
        number=1,
        source="memory://test",
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text="cheval sur deux zones",
                bbox=(0.0, 0.0, 100.0, 100.0),
                confidence=0.9,
                block_id="b1",
            )
        ],
    )
    zones = ((0.0, 0.0, 40.0, 100.0), (60.0, 0.0, 100.0, 100.0))
    pipeline = Pipeline(PipelineConfig(mode="approfondi", excluded_zones=zones))
    kept = pipeline._prune_excluded_blocks(page)
    assert [block.text for block in kept.blocks] == ["cheval sur deux zones"]


# --- Masque blanc papier (_prepare_image) ----------------------------------------


def test_prepare_image_zones_painted_white_paper() -> None:
    """Zones peintes en BLANC papier sur l'image du moteur, jamais noir."""
    pipeline = Pipeline(
        PipelineConfig(mode="approfondi", excluded_zones=((10.0, 10.0, 110.0, 60.0),))
    )
    black = Image.new("RGB", (400, 200), (0, 0, 0))
    prepared = pipeline._prepare_image(black, dpi=72)  # échelle 1 :1 (pt → px)
    assert prepared.size == black.size, "dimensions identiques exigées"
    pixels = np.asarray(prepared.convert("RGB"))
    # Intérieur de la zone → blanc papier.
    assert (pixels[30:50, 30:90] == 255).all(), "la zone doit être peinte en blanc"
    # Juste en dehors → inchangé (noir).
    assert (pixels[0:5, 0:5] == 0).all(), "hors zone : image inchangée"


def test_prepare_image_zone_larger_than_page_clamped() -> None:
    pipeline = Pipeline(
        PipelineConfig(mode="approfondi", excluded_zones=((-100.0, -100.0, 5000.0, 5000.0),))
    )
    image = Image.new("RGB", (300, 300), (128, 128, 128))
    prepared = pipeline._prepare_image(image, dpi=72)
    assert prepared.size == image.size
    assert (np.asarray(prepared.convert("RGB")) == 255).all()


def test_prepare_image_with_filters_and_zones() -> None:
    """Filtres PUIS masque blanc : la zone reste blanche malgré les filtres."""
    pipeline = Pipeline(
        PipelineConfig(
            mode="approfondi",
            excluded_zones=((10.0, 10.0, 110.0, 60.0),),
            filter_params={"contrast": 180, "shadow_removal": 50},
        )
    )
    image = _noisy_gray()
    prepared = pipeline._prepare_image(image, dpi=72)
    assert prepared.size == image.size
    pixels = np.asarray(prepared.convert("RGB"))
    assert (pixels[30:50, 30:90] == 255).all()
    assert not np.array_equal(pixels, np.asarray(image.convert("RGB")))


def _noisy_gray(width: int = 200, height: int = 100) -> Image.Image:
    rng = np.random.default_rng(11)
    array = np.full((height, width), 240, dtype=np.int16)
    array[::4, 20:180] = 60
    array += rng.integers(-10, 10, size=array.shape, dtype=np.int16)
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="L")


# --- Store (persistance par page) ------------------------------------------------


@pytest.fixture()
def deep_dir(tmp_path, monkeypatch):
    """Store isolé dans un dossier accentué (R14), restauré après le test."""
    root = tmp_path / "deep store éàç"
    monkeypatch.setenv(exclusion_store.ENV_DEEP_DIR, str(root))
    return root


def test_store_lazy_no_file_created_at_import_or_load(deep_dir) -> None:
    source = deep_dir.parent / "document éàç.pdf"
    state = exclusion_store.load_state(str(source), 1)
    assert state == exclusion_store.DeepState()
    assert exclusion_store.deep_root() == deep_dir
    assert not deep_dir.exists(), "lecture d'une page absente : rien n'est créé"
    assert not list(deep_dir.parent.rglob("page_*.json"))


def test_store_roundtrip_zones_and_filters(deep_dir) -> None:
    source = deep_dir / "facture éàç 2026.pdf"
    zones = [(0.5, 1.25, 300.0, 60.0), (10.0, 30.0, 5.0, 40.0)]  # 2ᵉ inversée
    filters = {"contrast": 130, "denoise": 3, "shadow_removal": 65}
    path = exclusion_store.save_state(str(source), 3, zones, filters)

    assert path.is_file()
    assert path.name == "page_003.json", "numérotation page_NNN 1-based"
    assert path.parent == exclusion_store.document_dir(str(source))
    assert exclusion_store.deep_root() in path.parents

    state = exclusion_store.load_state(str(source), 3)
    # Normalisation par axe : x = min/max(10, 5) -> (5, 10) ; y = min/max(30, 40) -> (30, 40).
    assert state.zones == ((0.5, 1.25, 300.0, 60.0), (5.0, 30.0, 10.0, 40.0))
    assert state.filters == filters


def test_store_document_dir_is_stable_sha1(deep_dir) -> None:
    source = str(deep_dir / "mon document éàç.pdf")
    first = exclusion_store.document_dir(source)
    assert first == exclusion_store.document_dir(source)
    assert first.parent == deep_dir
    assert len(first.name) == 12, "sha1(chemin)[:12]"
    assert exclusion_store.state_path(source, 12).name == "page_012.json"


def test_store_corrupted_file_falls_back_to_defaults(deep_dir) -> None:
    source = deep_dir / "corrompu éàç.pdf"
    path = exclusion_store.state_path(str(source), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ceci n'est pas du JSON", encoding="utf-8")
    assert exclusion_store.load_state(str(source), 1) == exclusion_store.DeepState()

    path.write_text("[]", encoding="utf-8")  # JSON valide mais pas un dict
    assert exclusion_store.load_state(str(source), 1) == exclusion_store.DeepState()

    path.write_text('{"zones": "x", "filters": 42}', encoding="utf-8")  # types faux
    state = exclusion_store.load_state(str(source), 1)
    assert state.zones == ()
    assert state.filters is None


def test_store_hostile_nan_zones_ignored_on_read(deep_dir) -> None:
    """JSON hostile avec littéraux NaN/Infinity : zones ignorées (review C3)."""
    source = deep_dir / "hostile éàç.pdf"
    path = exclusion_store.state_path(str(source), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.loads (Python) accepte les littéraux NaN/Infinity non standard :
    # le store doit filtrer explicitement les zones non finies.
    path.write_text(
        '{"zones": [[0.0, 0.0, 100.0, 50.0], [NaN, 0, 50, 50],'
        ' [Infinity, 0, 20, 20]], "filters": null}',
        encoding="utf-8",
    )
    state = exclusion_store.load_state(str(source), 1)
    assert state.zones == ((0.0, 0.0, 100.0, 50.0),), "seule la zone finie survit"


def test_store_non_finite_zones_ignored_on_write(deep_dir) -> None:
    source = deep_dir / "écriture éàç.pdf"
    exclusion_store.save_state(
        str(source),
        1,
        zones=[(0.0, 0.0, 10.0, 10.0), (float("nan"), 0.0, 5.0, 5.0)],
        filters=None,
    )
    state = exclusion_store.load_state(str(source), 1)
    assert state.zones == ((0.0, 0.0, 10.0, 10.0),)


def test_store_filters_non_dict_stored_as_none(deep_dir) -> None:
    source = deep_dir / "filtres éàç.pdf"
    exclusion_store.save_state(str(source), 1, zones=[], filters="pas un dict")
    assert exclusion_store.load_state(str(source), 1).filters is None


def test_store_atomic_no_tmp_residual(deep_dir) -> None:
    """Écriture atomique : aucun fichier .tmp ne survit à un save réussi."""
    source = deep_dir / "atomique éàç.pdf"
    for page in (1, 2):
        exclusion_store.save_state(str(source), page, zones=[(0, 0, 5, 5)], filters=None)
    residuals = [p.name for p in deep_dir.rglob("*.tmp")]
    assert residuals == [], f"fichiers temporaires résiduels : {residuals}"


def test_store_atomic_tmp_cleaned_on_oserror(deep_dir, monkeypatch) -> None:
    """``os.replace`` en échec : le .tmp est nettoyé et l'OSError se propage."""
    source = deep_dir / "échec éàç.pdf"
    real_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("disque plein (simulé)")

    monkeypatch.setattr(exclusion_store.os, "replace", failing_replace)
    with pytest.raises(OSError):
        exclusion_store.save_state(str(source), 1, zones=[(0, 0, 5, 5)], filters=None)
    monkeypatch.setattr(exclusion_store.os, "replace", real_replace)
    residuals = [p.name for p in deep_dir.rglob("*.tmp")]
    assert residuals == [], f"le .tmp doit être nettoyé après échec : {residuals}"
    assert not exclusion_store.state_path(str(source), 1).is_file()


def test_store_zone_ordering_on_write(deep_dir) -> None:
    source = deep_dir / "ordre éàç.pdf"
    exclusion_store.save_state(str(source), 1, zones=[(9.0, 8.0, 1.0, 2.0)], filters=None)
    assert exclusion_store.load_state(str(source), 1).zones == ((1.0, 2.0, 9.0, 8.0),)


def test_store_zone_with_nan_literal_does_not_leak_through_roundtrip(deep_dir) -> None:
    """Chaîne complète : écriture → lecture → aucune zone non finie ne sort."""
    source = deep_dir / "roundtrip éàç.pdf"
    zones = [
        (0.0, 0.0, 100.0, 100.0),
        (float("inf"), 0.0, 20.0, 20.0),  # ignorée à l'écriture
        (5.0, float("nan"), 30.0, 40.0),  # ignorée à l'écriture
    ]
    exclusion_store.save_state(str(source), 2, zones, filters={"brightness": 105.0})
    state = exclusion_store.load_state(str(source), 2)
    assert state.zones == ((0.0, 0.0, 100.0, 100.0),)
    assert all(math.isfinite(coord) for zone in state.zones for coord in zone)
    assert state.filters == {"brightness": 105.0}
