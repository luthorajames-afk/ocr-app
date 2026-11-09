"""Comparateur de moteurs OCR — Phase 2.

Évalue un moteur sur un échantillon stratifié du jeu de test et produit un JSON
avec le CER par document et la moyenne.

Chaque moteur tourne dans son propre venv (R13) :
    .venv-eval-medium\\Scripts\\python scripts\\compare_engines.py --engine rapidocr
    .venv-eval-advanced\\Scripts\\python scripts\\compare_engines.py --engine docling
    python scripts\\compare_engines.py --engine tesseract

Prérequis :
    rapidocr : rapidocr, onnxruntime, pypdfium2, pillow
    docling  : docling
    tesseract: tesserocr (moteur du noyau)

Note R4 : Docling télécharge ses modèles au premier usage (évaluation uniquement).
En production, les artefacts seront pré-téléchargés (DOCLING_ARTIFACTS_PATH).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "test_suite" / "manifest.json"
TESSDATA = ROOT / "models" / "tessdata"


# ---------------------------------------------------------------------------
# Métriques (identiques à scripts/evaluate.py, sans dépendance à ocr_app)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def cer(ocr_text: str, truth_text: str) -> float:
    ocr_n, truth_n = normalize(ocr_text), normalize(truth_text)
    if not truth_n:
        return 0.0 if not ocr_n else 1.0
    return levenshtein(ocr_n, truth_n) / len(truth_n)


# ---------------------------------------------------------------------------
# Extraction de texte par moteur
# ---------------------------------------------------------------------------

def _render_pdf_pages(path: Path, dpi: int = 300):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                image = page.render(scale=dpi / 72).to_pil()
                yield index + 1, image
            finally:
                page.close()
    finally:
        pdf.close()


def _native_pdf_text(path: Path) -> str:
    import pypdfium2 as pdfium

    parts = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                parts.append(page.get_textpage().get_text_range())
            finally:
                page.close()
    finally:
        pdf.close()
    return "\n".join(parts)


def text_rapidocr(path: Path) -> str:
    from rapidocr import RapidOCR

    engine = RapidOCR()
    parts = []
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        for _, image in _render_pdf_pages(path):
            result = engine(image)
            if result.txts:
                parts.extend(result.txts)
    else:
        result = engine(str(path))
        if result.txts:
            parts.extend(result.txts)
    return "\n".join(parts)


def text_docling(path: Path) -> str:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def text_tesseract(path: Path) -> str:
    import tesserocr
    from PIL import Image

    parts = []
    suffix = path.suffix.lower()
    images = []
    if suffix == ".pdf":
        images = [image for _, image in _render_pdf_pages(path)]
    else:
        with Image.open(path) as img:
            images = [img.convert("RGB")]

    with tesserocr.PyTessBaseAPI(path=str(TESSDATA), lang="fra+eng") as api:
        for image in images:
            api.SetImage(image)
            text = api.GetUTF8Text()
            if text:
                parts.append(text)
    return "\n".join(parts)


ENGINES = {
    "rapidocr": text_rapidocr,
    "docling": text_docling,
    "tesseract": text_tesseract,
}


# ---------------------------------------------------------------------------
# Échantillonnage stratifié
# ---------------------------------------------------------------------------

def stratified_sample(items: list[dict], limit: int) -> list[dict]:
    """Échantillon équilibré par difficulté puis thème, ordre stable."""
    if limit >= len(items):
        return items
    by_difficulty: dict[str, list[dict]] = {}
    for item in items:
        by_difficulty.setdefault(item.get("difficulty", "?"), []).append(item)
    picked: list[dict] = []
    index = 0
    while len(picked) < limit:
        added = False
        for difficulty in ("easy", "medium", "hard"):
            bucket = by_difficulty.get(difficulty, [])
            if index < len(bucket):
                picked.append(bucket[index])
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break
        index += 1
    return picked


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

def run(engine_name: str, limit: int, output_dir: Path, native_first: bool = True) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sample = stratified_sample(manifest["items"], limit)
    engine = ENGINES[engine_name]

    results = []
    for i, item in enumerate(sample, 1):
        doc_path = ROOT / "data" / "test_suite" / item["filename"]
        truth_path = ROOT / "data" / "test_suite" / item["text_file"]
        if not doc_path.exists() or not truth_path.exists():
            continue
        truth = truth_path.read_text(encoding="utf-8")
        started = time.perf_counter()
        try:
            text = ""
            used = engine_name
            if native_first and item.get("page_modes") and all(m == "native" for m in item["page_modes"]):
                native = _native_pdf_text(doc_path)
                if native.strip():
                    text = native
                    used = "native"
            if not text:
                text = engine(doc_path)
            elapsed = time.perf_counter() - started
            value = cer(text, truth)
            results.append({
                "id": item["id"],
                "difficulty": item.get("difficulty"),
                "theme": item.get("theme"),
                "used": used,
                "cer": round(value, 4),
                "seconds": round(elapsed, 2),
                "chars": len(normalize(text)),
            })
            print(f"[{i}/{len(sample)}] {item['id']:10s} {used:8s} CER {value:6.2%} ({elapsed:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(sample)}] ERREUR {item['id']}: {exc}")
            results.append({"id": item["id"], "difficulty": item.get("difficulty"), "error": str(exc)})

    scored = [r["cer"] for r in results if "cer" in r]
    by_difficulty: dict[str, list[float]] = {}
    for r in results:
        if "cer" in r:
            by_difficulty.setdefault(r["difficulty"], []).append(r["cer"])

    report = {
        "engine": engine_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "documents": len(results),
        "mean_cer": round(sum(scored) / len(scored), 4) if scored else None,
        "by_difficulty": {
            k: {"documents": len(v), "mean_cer": round(sum(v) / len(v), 4)}
            for k, v in sorted(by_difficulty.items())
        },
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"engine_{engine_name}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCER moyen {engine_name} : {report['mean_cer']:.2%}" if report["mean_cer"] is not None else "Aucun résultat")
    print(f"Rapport : {path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Comparaison de moteurs OCR sur le jeu de test")
    parser.add_argument("--engine", required=True, choices=sorted(ENGINES))
    parser.add_argument("--limit", type=int, default=12, help="Taille de l'échantillon (défaut 12)")
    parser.add_argument("--output", default=str(ROOT / "data" / "eval"))
    parser.add_argument("--no-native-first", action="store_true", help="Forcer l'OCR même sur PDF natif")
    args = parser.parse_args()
    run(args.engine, args.limit, Path(args.output), native_first=not args.no_native_first)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
