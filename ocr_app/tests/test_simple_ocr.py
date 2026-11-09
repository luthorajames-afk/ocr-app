"""Tests Phase 1 — moteur OCR Simple (tesserocr).

Ces tests vérifient le moteur réel, pas le squelette :

- tesserocr lit une image générée avec Pillow et restitue les accents ;
- ``SimpleWorker.process_page`` retourne un objet ``Page`` composé de ``Block``
  (jamais de dictionnaires) avec du texte non vide.

Garde-fous demandés :

- ``pytest.importorskip("tesserocr")`` si le moteur n'est pas installé ;
- ``pytest.skip`` explicite tant que ``SimpleWorker`` est un stub « squelette » ;
- ``pytest.skip`` si le tessdata local (fra) est absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESSDATA_DIR = REPO_ROOT / "models" / "tessdata"
STUB_MARKER = "squelette"

# Texte de contrôle : minuscules accentuées présentes dans « éàç ».
OCR_TEXT = "Bonjour éàç"
OCR_FONT_SIZE = 48


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_tesserocr():
    """Importe tesserocr ou skippe, et vérifie la présence du tessdata fra."""
    tesserocr = pytest.importorskip("tesserocr")
    if not (TESSDATA_DIR / "fra.traineddata").is_file():
        pytest.skip(f"tessdata fra absent : {TESSDATA_DIR}")
    return tesserocr


def _load_font(size: int):
    """Charge une police TrueType qui sait dessiner é, à et ç."""
    from PIL import ImageFont

    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "calibri.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "times.ttf",
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)

    # Repli portable : police livrée avec Pillow (si disponible).
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Calibri.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue

    pytest.skip("aucune police TrueType avec accents disponible")


def _make_accent_image():
    """Image blanche « Bonjour éàç », assez grande pour un OCR stable."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1100, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 70), OCR_TEXT, font=_load_font(OCR_FONT_SIZE), fill="black")
    return image


def _make_simple_worker():
    """Instancie et initialise SimpleWorker, quel que soit le sens de models_dir.

    La phase 1 ne fige pas encore la convention exacte de ``models_dir``
    (dossier ``models`` ou dossier ``models/tessdata``). On essaie donc les
    conventions raisonnables avant d'échouer avec un message explicite.
    """
    _require_tesserocr()
    from ocr_app.workers.simple import SimpleWorker

    candidates = [None, str(TESSDATA_DIR), str(TESSDATA_DIR.parent)]
    errors: list[str] = []
    for models_dir in candidates:
        worker = None
        try:
            worker = (
                SimpleWorker(models_dir=models_dir)
                if models_dir is not None
                else SimpleWorker()
            )
            worker.initialize()
            return worker
        except Exception as exc:  # noqa: BLE001 - on essaie la convention suivante
            errors.append(f"models_dir={models_dir!r} : {exc!r}")
            if worker is not None:
                try:
                    worker.shutdown()
                except Exception:  # noqa: BLE001 - nettoyage best effort
                    pass

    pytest.fail(
        "SimpleWorker.initialize() a échoué pour toutes les conventions "
        "de models_dir :\n- " + "\n- ".join(errors)
    )


def _page_text(page) -> str:
    """Concatène le texte des blocs (tolérant aux blocs non-Block)."""
    parts: list[str] = []
    for block in getattr(page, "blocks", []):
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _skip_if_stub(text: str, label: str) -> None:
    if STUB_MARKER in text.casefold():
        pytest.skip(
            f"{label} est encore un stub « squelette » : "
            "le test Phase 1 (OCR réel) n'est pas applicable"
        )


def _process_image(worker, image, page_number: int, image_path: Path):
    """Appelle process_page avec une PIL.Image, repli sur un chemin fichier.

    Le contrat Phase 1 parle d'« une image » ; selon l'implémentation, il peut
    s'agir d'un objet Pillow ou d'un chemin. On privilégie l'objet Pillow, puis
    on retente avec un fichier PNG sur un chemin accentué (R14).
    """
    try:
        return worker.process_page(image, page_number)
    except (TypeError, AttributeError):
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path)
        return worker.process_page(str(image_path), page_number)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_tesserocr_reads_accented_text():
    """Le moteur tesserocr restitue « Bonjour » et les accents é, à, ç."""
    tesserocr = _require_tesserocr()
    image = _make_accent_image()

    with tesserocr.PyTessBaseAPI(path=str(TESSDATA_DIR), lang="fra") as api:
        api.SetImage(image)
        text = api.GetUTF8Text()

    normalized = " ".join(text.split())
    assert "bonjour" in normalized.casefold(), (
        f"« Bonjour » non reconnu par tesserocr : {text!r}"
    )
    missing = [char for char in "éàç" if char not in normalized]
    assert not missing, (
        f"accents non reconnus {missing!r} dans la sortie tesserocr : {text!r}"
    )


def test_simple_worker_process_page_returns_page_with_blocks(tmp_path):
    """SimpleWorker retourne une Page de Block (pas de dict), texte non vide."""
    _require_tesserocr()
    from ocr_app.core.data_model import Block, Page

    worker = _make_simple_worker()
    try:
        image = _make_accent_image()
        page = _process_image(
            worker,
            image,
            1,
            tmp_path / "image accentuée éàç.png",
        )
    finally:
        worker.shutdown()

    assert isinstance(page, Page), f"Page attendu, {type(page).__name__} reçu"
    assert page.number == 1
    assert page.blocks, "SimpleWorker.process_page a retourné une page sans bloc"

    text = _page_text(page)
    # Garde-fou explicite demandé : ne pas juger le squelette comme un échec.
    _skip_if_stub(text, "SimpleWorker.process_page")

    assert all(isinstance(block, Block) for block in page.blocks), (
        "les blocs doivent être des Block, pas des dict : "
        f"{[type(block).__name__ for block in page.blocks]!r}"
    )
    assert text.strip(), "le texte OCR agrégé est vide"
    assert "bonjour" in text.casefold(), (
        f"le texte OCR ne contient pas « Bonjour » : {text!r}"
    )
