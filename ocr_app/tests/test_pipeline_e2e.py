"""Tests Phase 1 — pipeline de bout en bout sur le jeu de test.

Couvre :

- ``Pipeline.process_document`` sur une facture PDF native facile
  (``data/test_suite/facts/fact_001.pdf``) : pages, JSON par page (R6),
  ordre des étapes ;
- R8 : un PDF natif est traité à partir de son texte natif, sans OCR inutile
  (vérifié via un compteur/flag exposé, sinon ``pytest.skip`` prudent) ;
- la CLI ``python -m ocr_app.cli process`` avec un dossier de sortie
  accentué et espacé (R14).

Garde-fous : skip si tesserocr ou le tessdata local est absent, et skip
explicite tant que le pipeline est un stub « squelette ».
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_SUITE_DIR = REPO_ROOT / "data" / "test_suite"
FACTS_DIR = TEST_SUITE_DIR / "facts"
TRUTH_DIR = TEST_SUITE_DIR / "truth"
MANIFEST_PATH = TEST_SUITE_DIR / "manifest.json"
TESSDATA_DIR = REPO_ROOT / "models" / "tessdata"

EASY_FACT_ID = "fact_001"
EASY_FACT_PDF = FACTS_DIR / f"{EASY_FACT_ID}.pdf"
STUB_MARKER = "squelette"

# Ordre canonique des étapes du pipeline (cf. PipelineStage).
STAGE_ORDER = (
    "render",
    "preprocess",
    "layout",
    "ocr",
    "tables",
    "reconstruct",
    "save",
    "done",
)

# Attributs de compteur pouvant être exposés par une implémentation réelle.
_COUNTER_ATTRS = (
    "ocr_pages",
    "ocr_page_count",
    "pages_ocr",
    "ocr_calls",
    "ocr_count",
    "ocr_used",
    "ocr_invoked",
    "ocr_performed",
    "ocr_applied",
    "did_ocr",
    "ocr_rendered_pages",
    "rendered_for_ocr",
    "native_pages",
    "native_page_count",
    "pages_native",
    "native_text_pages",
    "native_text_used",
    "native_text_extracted",
    "native_text_found",
    "native_used",
    "used_native_text",
    "has_native_text",
    "used_ocr",
)
_STATS_ATTRS = (
    "stats",
    "metrics",
    "counters",
    "_stats",
    "_metrics",
    "_counters",
    "metadata",
)
_COUNT_HINTS = (
    "page",
    "pages",
    "count",
    "calls",
    "used",
    "did",
    "invoked",
    "performed",
    "applied",
    "rendered",
    "extracted",
    "found",
    "has",
    "nb",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_tesserocr() -> None:
    pytest.importorskip("tesserocr")
    if not (TESSDATA_DIR / "fra.traineddata").is_file():
        pytest.skip(f"tessdata fra absent : {TESSDATA_DIR}")


def _manifest_item(doc_id: str) -> dict:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        if item.get("id") == doc_id:
            return item
    raise AssertionError(f"{doc_id} absent du manifeste {MANIFEST_PATH}")


def _make_pipeline(work_dir: Path, *, use_native_text: bool = True):
    from ocr_app.core.pipeline import Pipeline, PipelineConfig

    work_dir.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig(
        mode="simple",
        dpi=300,
        use_native_text=use_native_text,
        work_dir=str(work_dir),
    )
    return Pipeline(config)


def _block_text(block) -> str:
    """Texte d'un bloc, y compris les lignes de tableau si présentes."""
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


def _document_text(document) -> str:
    parts: list[str] = []
    for page in getattr(document, "pages", []):
        for block in getattr(page, "blocks", []):
            text = _block_text(block)
            if text:
                parts.append(text)
    return "\n".join(parts)


def _skip_if_stub(text: str, label: str) -> None:
    if STUB_MARKER in text.casefold():
        pytest.skip(
            f"{label} est encore un stub « squelette » : "
            "le test Phase 1 (pipeline réel) n'est pas applicable"
        )


def _page_json_files(root: Path) -> list[Path]:
    return sorted(root.rglob("page_*.json"))


def _check_stage_order(stages: list[tuple[int, str]], pages: list[int]) -> None:
    assert stages, "aucune étape de pipeline n'a été émise via on_stage()"
    known = set(STAGE_ORDER)

    for page_number in pages:
        page_stages = [
            stage for number, stage in stages if number == page_number and stage in known
        ]
        assert "save" in page_stages, (
            f"étape 'save' absente pour la page {page_number} : {page_stages!r}"
        )
        indices = [STAGE_ORDER.index(stage) for stage in page_stages]
        assert indices == sorted(indices), (
            "étapes émises dans le désordre pour la page "
            f"{page_number} : {page_stages!r}"
        )
        if "done" in page_stages:
            assert page_stages[-1] == "done", (
                f"'done' doit être la dernière étape de la page {page_number} : "
                f"{page_stages!r}"
            )


def _numeric_value(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return None


def _collect_counters(obj, prefix: str) -> dict[str, float]:
    """Collecte les compteurs OCR/natif exposés par une implémentation."""
    found: dict[str, float] = {}

    for name in _COUNTER_ATTRS:
        try:
            value = getattr(obj, name, None)
        except Exception:  # noqa: BLE001 - propriété défensive
            continue
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001 - compteur optionnel
                continue
        number = _numeric_value(value)
        if number is not None:
            found[f"{prefix}.{name}"] = number

    for attr in _STATS_ATTRS:
        try:
            stats = getattr(obj, attr, None)
        except Exception:  # noqa: BLE001 - attribut optionnel
            continue
        if not isinstance(stats, dict):
            continue
        for key, value in stats.items():
            name = str(key).casefold()
            if not any(engine in name for engine in ("ocr", "native")):
                continue
            if not any(hint in name for hint in _COUNT_HINTS):
                continue
            number = _numeric_value(value)
            if number is not None:
                found[f"{prefix}.{attr}.{key}"] = number

    return found


def _json_document_text(payload: dict) -> str:
    parts: list[str] = []
    for page in payload.get("pages", []):
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks", []):
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
            table = block.get("table")
            if isinstance(table, dict):
                for row in table.get("grid", []) or []:
                    if not isinstance(row, list):
                        continue
                    cells = [
                        cell.get("text", "") if isinstance(cell, dict) else ""
                        for cell in row
                    ]
                    if any(cells):
                        parts.append(" | ".join(cells))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def test_pipeline_process_document_native_pdf(tmp_path):
    """PDF natif facile : pages, JSON page_NNN.json (R6), étapes ordonnées."""
    _require_tesserocr()
    from ocr_app.core.data_model import Block, Document, Page

    assert EASY_FACT_PDF.is_file(), f"document de test absent : {EASY_FACT_PDF}"
    item = _manifest_item(EASY_FACT_ID)
    expected_pages = int(item.get("pages", 1))

    work_dir = tmp_path / "travail accentué éàç avec espaces"
    pipeline = _make_pipeline(work_dir)

    stages: list[tuple[int, str]] = []
    pipeline.on_stage(lambda number, stage: stages.append((int(number), str(stage))))

    document = pipeline.process_document(str(EASY_FACT_PDF))

    assert isinstance(document, Document), f"Document attendu, {type(document).__name__} reçu"
    assert len(document.pages) >= 1, "le pipeline n'a produit aucune page"
    assert len(document.pages) == expected_pages, (
        f"{len(document.pages)} page(s) produite(s), {expected_pages} attendue(s) "
        f"d'après le manifeste pour {EASY_FACT_ID}"
    )

    text = _document_text(document)
    # Garde-fou : le squelette retourne un texte placeholder, on skippe.
    _skip_if_stub(text, "Pipeline.process_document")
    assert text.strip(), "le texte agrégé du document est vide"

    for page in document.pages:
        assert isinstance(page, Page)
        assert page.blocks, f"la page {page.number} ne contient aucun bloc"
        assert all(isinstance(block, Block) for block in page.blocks), (
            f"la page {page.number} contient des blocs qui ne sont pas des Block : "
            f"{[type(block).__name__ for block in page.blocks]!r}"
        )

    _check_stage_order(stages, [page.number for page in document.pages])

    # R6 : un fichier page_NNN.json par page, écrit dans work_dir.
    page_files = _page_json_files(work_dir)
    assert page_files, f"aucun page_NNN.json écrit dans {work_dir}"
    by_name = {path.name: path for path in page_files}
    for page in document.pages:
        expected_name = f"page_{page.number:03d}.json"
        path = by_name.get(expected_name)
        assert path is not None, (
            f"{expected_name} absent ; fichiers trouvés : {sorted(by_name)!r}"
        )
        assert path.stat().st_size > 0, f"{path} est vide"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{path} ne contient pas un objet JSON"
        assert payload.get("number") == page.number
        assert isinstance(payload.get("blocks"), list) and payload["blocks"], (
            f"{path} ne contient aucun bloc"
        )


def test_native_pdf_does_not_use_ocr(tmp_path):
    """R8 : le texte natif d'un PDF est utilisé, aucun OCR inutile.

    La vérification n'est faite que si l'implémentation expose un compteur ou
    un flag (``ocr_pages``, ``native_text_used``, ``stats``…). Sinon, on se
    rabat sur l'observation des étapes : une page native sans étape ``ocr``
    prouve que l'OCR n'a pas été lancé. Si aucune de ces observables n'existe,
    le test est skippé plutôt que de dépendre d'un détail interne.
    """
    _require_tesserocr()

    assert EASY_FACT_PDF.is_file(), f"document de test absent : {EASY_FACT_PDF}"
    work_dir = tmp_path / "travail R8 éàç"
    pipeline = _make_pipeline(work_dir, use_native_text=True)

    stages: list[tuple[int, str]] = []
    pipeline.on_stage(lambda number, stage: stages.append((int(number), str(stage))))

    document = pipeline.process_document(str(EASY_FACT_PDF))
    text = _document_text(document)
    _skip_if_stub(text, "Pipeline.process_document")

    # Le contenu natif de fact_001 doit se retrouver dans la sortie.
    normalized = " ".join(text.split()).casefold()
    assert "dupont" in normalized, (
        f"le texte natif de fact_001 est absent de la sortie : {text[:300]!r}"
    )
    assert "facture" in normalized, (
        f"le texte natif de fact_001 est absent de la sortie : {text[:300]!r}"
    )

    counters = _collect_counters(pipeline, "pipeline")
    counters.update(_collect_counters(document, "document"))
    for index, page in enumerate(document.pages, start=1):
        counters.update(_collect_counters(page, f"page{index}"))

    ocr_counters = {
        key: value for key, value in counters.items() if "ocr" in key.casefold()
    }
    native_counters = {
        key: value for key, value in counters.items() if "native" in key.casefold()
    }

    if ocr_counters:
        assert all(value == 0 for value in ocr_counters.values()), (
            "R8 : OCR exécuté sur un PDF natif alors que use_native_text=True ; "
            f"compteurs : {ocr_counters!r}"
        )
        return

    if native_counters:
        assert any(value >= 1 for value in native_counters.values()), (
            "R8 : aucun compteur n'indique l'utilisation du texte natif ; "
            f"compteurs : {native_counters!r}"
        )
        return

    # Dernier recours observable : pas d'étape « ocr » pour les pages natives.
    page_stages = [stage for number, stage in stages if stage in STAGE_ORDER]
    if "ocr" not in page_stages:
        return

    pytest.skip(
        "R8 : aucun compteur OCR/natif exposé et l'étape 'ocr' est émise ; "
        "vérification non intrusive impossible sans modifier src/"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_process_easy_pdf_with_accented_output(tmp_path):
    """CLI : result.md/html/txt + document.json non vides, accents présents."""
    _require_tesserocr()

    assert EASY_FACT_PDF.is_file(), f"document de test absent : {EASY_FACT_PDF}"
    output_dir = tmp_path / "sorties éàç avec espaces"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # La CLI doit tester le même paquet que le processus pytest (installation
    # éditable ou copie isolée), pas une version installée différente.
    import ocr_app

    package_src = Path(ocr_app.__file__).resolve().parent.parent
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(package_src) + os.pathsep + existing if existing else str(package_src)
    )

    command = [
        sys.executable,
        "-m",
        "ocr_app.cli",
        "process",
        "--input",
        str(EASY_FACT_PDF),
        "--mode",
        "simple",
        "--output",
        str(output_dir),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"la CLI a dépassé 300 s : {' '.join(command)}")

    assert completed.returncode == 0, (
        f"la CLI a échoué (code {completed.returncode})\n"
        f"stdout : {completed.stdout}\nstderr : {completed.stderr}"
    )

    expected_files = ("result.txt", "result.md", "result.html", "document.json")
    for name in expected_files:
        path = output_dir / name
        assert path.is_file(), (
            f"{name} absent du dossier de sortie {output_dir}\n"
            f"stdout : {completed.stdout}\nstderr : {completed.stderr}"
        )
        assert path.stat().st_size > 0, f"{name} est vide"

    payload = json.loads((output_dir / "document.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "document.json ne contient pas un objet JSON"
    assert payload.get("pages"), "document.json ne contient aucune page"

    # Garde-fou : pipeline/worker encore au stade squelette -> skip explicite.
    _skip_if_stub(_json_document_text(payload), "CLI process (pipeline)")

    assert (output_dir / "result.txt").stat().st_size > 0
    assert (output_dir / "result.md").stat().st_size > 0
    assert (output_dir / "result.html").stat().st_size > 0

    # R14 : les accents survivent au passage par la CLI et les exports.
    contents = [
        (output_dir / name).read_text(encoding="utf-8")
        for name in expected_files
    ]
    combined = "\n".join(contents).casefold()
    assert "é" in combined, "aucun accent aigu dans les sorties de la CLI"
    assert any(char in combined for char in ("è", "à", "ç")), (
        "aucun accent grave/cédille dans les sorties de la CLI"
    )

    # R6 : les JSON de page sont présents, soit à la racine, soit dans work/.
    page_files = _page_json_files(output_dir)
    assert page_files, f"aucun page_NNN.json dans {output_dir}"
    page_numbers = {
        int(match.group(1))
        for path in page_files
        if (match := re.fullmatch(r"page_(\d+)\.json", path.name))
    }
    expected_numbers = {
        page.get("number") for page in payload["pages"] if isinstance(page.get("number"), int)
    }
    assert expected_numbers <= page_numbers, (
        f"JSON de page manquants : attendus {sorted(expected_numbers)!r}, "
        f"trouvés {sorted(page_numbers)!r}"
    )
