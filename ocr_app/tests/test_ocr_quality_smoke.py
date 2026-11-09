"""Smoke test de qualité OCR — documents faciles du jeu de test.

Pour 2–3 factures natives faciles (``facts/fact_001`` à ``fact_003``), on
applique le worker Simple réel (tesserocr) aux pages rendues à 300 DPI, puis on
compare le texte OCR agrégé à la vérité terrain ``truth/*.txt`` avec
``difflib.SequenceMatcher``.

Le seuil est volontairement bas (0,55) pour rester non flaky : il détecte une
régression franche (texte vide, mauvais document, encodage cassé), pas les
variations normales de Tesseract. Les ratios mesurés sont affichés.

Garde-fous : skip si tesserocr/tessdata/pypdfium2 manque, et skip explicite
tant que SimpleWorker est un stub « squelette ».
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_SUITE_DIR = REPO_ROOT / "data" / "test_suite"
FACTS_DIR = TEST_SUITE_DIR / "facts"
TRUTH_DIR = TEST_SUITE_DIR / "truth"
TESSDATA_DIR = REPO_ROOT / "models" / "tessdata"
STUB_MARKER = "squelette"

EASY_FACT_IDS = ("fact_001", "fact_002", "fact_003")
RATIO_THRESHOLD = 0.55
RENDER_DPI = 300


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_environment() -> None:
    pytest.importorskip("tesserocr")
    pytest.importorskip("pypdfium2")
    if not (TESSDATA_DIR / "fra.traineddata").is_file():
        pytest.skip(f"tessdata fra absent : {TESSDATA_DIR}")


def _make_simple_worker():
    """Instancie SimpleWorker en tolérant les deux conventions de models_dir."""
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


def _process_image(worker, image, page_number: int, image_path: Path):
    """PIL.Image d'abord, puis repli sur un chemin PNG accentué (R14)."""
    try:
        return worker.process_page(image, page_number)
    except (TypeError, AttributeError):
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path)
        return worker.process_page(str(image_path), page_number)


def _block_text(block) -> str:
    """Texte d'un bloc, cellules de tableau incluses."""
    parts: list[str] = []
    text = getattr(block, "text", None)
    if isinstance(text, str) and text:
        parts.append(text)

    table = getattr(block, "table", None)
    grid = getattr(table, "grid", None) if table is not None else None
    if grid:
        for row in grid:
            cells: list[str] = []
            for cell in row:
                cell_text = getattr(cell, "text", "") if cell is not None else ""
                cells.append(cell_text if isinstance(cell_text, str) else "")
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pages_text(pages) -> str:
    parts: list[str] = []
    for page in pages:
        for block in getattr(page, "blocks", []):
            text = _block_text(block)
            if text:
                parts.append(text)
    return "\n".join(parts)


def _normalize(text: str) -> str:
    """Normalisation conseillée par le README du jeu de test avant comparaison."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ")  # espace insécable
    text = text.replace("\u202f", " ")  # espace fine insécable
    text = text.replace("\u00ad", "")  # trait d'union conditionnel
    text = text.replace("\f", " ")  # séparateur de page de la vérité terrain
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _render_pdf_pages(pdf_path: Path) -> list:
    """Rend chaque page du PDF en image Pillow à 300 DPI (R7)."""
    import pypdfium2 as pdfium

    images = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        scale = RENDER_DPI / 72.0
        for page in pdf:
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil())
    finally:
        close = getattr(pdf, "close", None)
        if callable(close):
            close()
    return images


def _skip_if_stub(text: str, label: str) -> None:
    if STUB_MARKER in text.casefold():
        pytest.skip(
            f"{label} est encore un stub « squelette » : "
            "la mesure de qualité Phase 1 n'est pas applicable"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", EASY_FACT_IDS)
def test_ocr_quality_easy_fact(doc_id, tmp_path):
    """Le texte OCR d'une facture facile reste proche de la vérité terrain."""
    _require_environment()

    pdf_path = FACTS_DIR / f"{doc_id}.pdf"
    truth_path = TRUTH_DIR / f"{doc_id}.txt"
    assert pdf_path.is_file(), f"document de test absent : {pdf_path}"
    assert truth_path.is_file(), f"vérité terrain absente : {truth_path}"

    images = _render_pdf_pages(pdf_path)
    assert images, f"aucune page rendue depuis {pdf_path}"

    worker = _make_simple_worker()
    pages = []
    try:
        for page_number, image in enumerate(images, start=1):
            pages.append(
                _process_image(
                    worker,
                    image,
                    page_number,
                    tmp_path / f"{doc_id}_page_{page_number}_éàç.png",
                )
            )
    finally:
        worker.shutdown()

    ocr_text = _pages_text(pages)
    _skip_if_stub(ocr_text, "SimpleWorker.process_page")

    truth_text = truth_path.read_text(encoding="utf-8")
    normalized_truth = _normalize(truth_text)
    normalized_ocr = _normalize(ocr_text)
    assert normalized_ocr, f"texte OCR vide pour {doc_id}"

    ratio = difflib.SequenceMatcher(
        None, normalized_truth, normalized_ocr, autojunk=False
    ).ratio()

    # Rapport demandé : ratio visible avec pytest -s (ou en cas d'échec).
    print(
        f"\n[qualité OCR] {doc_id} : ratio={ratio:.4f} "
        f"(seuil {RATIO_THRESHOLD:.2f}, "
        f"{len(normalized_ocr)}/{len(normalized_truth)} caractères)"
    )

    assert ratio >= RATIO_THRESHOLD, (
        f"qualité OCR insuffisante pour {doc_id} : ratio={ratio:.4f} "
        f"< seuil {RATIO_THRESHOLD:.2f} "
        f"({len(normalized_ocr)} caractères OCR / "
        f"{len(normalized_truth)} caractères de vérité)"
    )
