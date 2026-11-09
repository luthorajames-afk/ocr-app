"""Harnais d'évaluation OCR — Phase 2.

Mesure sur le jeu de test (`data/test_suite/`) :
- CER (Character Error Rate) entre texte OCR et vérité terrain ;
- rappel des cellules de tableaux (TEDS-lite cell-level) ;
- agrégats par difficulté et par thème.

Usage :
    python scripts/evaluate.py --mode simple
    python scripts/evaluate.py --mode simple --limit 5
    python scripts/evaluate.py --mode simple --output data/eval
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ocr_app.core.data_model import Document, Block, BlockType  # noqa: E402
from ocr_app.core.pipeline import Pipeline, PipelineConfig  # noqa: E402


# --------------------------------------------------------------------------
# Normalisation et métriques
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Normalise sans perdre les accents : NFKC, espaces unifiés, trim."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def levenshtein(a: str, b: str) -> int:
    """Distance d'édition caractère (O(n*m), suffisant pour ~10k caractères)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            insert = previous[j] + 1
            delete = current[j - 1] + 1
            replace = previous[j - 1] + (ca != cb)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def cer(ocr_text: str, truth_text: str) -> float:
    """Character Error Rate = edit_distance / len(truth). 0 = parfait."""
    ocr_n = normalize(ocr_text)
    truth_n = normalize(truth_text)
    if not truth_n:
        return 0.0 if not ocr_n else 1.0
    return levenshtein(ocr_n, truth_n) / len(truth_n)


def block_text(document: Document) -> str:
    """Texte plat d'un document, dans l'ordre des blocs."""
    parts: list[str] = []
    for page in document.pages:
        for block in page.blocks:
            if block.type == BlockType.TABLE and block.table is not None:
                for row in block.table.grid:
                    parts.append(" | ".join(c.text if c else "" for c in row))
            elif block.text:
                parts.append(block.text)
    return "\n".join(parts)


def table_cell_recall(ocr_text: str, tables: list[dict]) -> float | None:
    """TEDS-lite : fraction des cellules vérité retrouvées dans le texte OCR.

    Approximation honnête pour le mode Simple (qui aplatit les tableaux) :
    présence textuelle de chaque cellule non vide, insensible à la casse.
    """
    cells: list[str] = []
    for table in tables:
        for row in table.get("grid", []):
            for cell in row:
                cell = (cell or "").strip()
                if cell:
                    cells.append(cell)
    if not cells:
        return None
    haystack = normalize(ocr_text).casefold()
    found = 0
    for cell in cells:
        needle = normalize(cell).casefold()
        if needle and needle in haystack:
            found += 1
    return found / len(cells)


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------

def evaluate(mode: str, limit: int | None, output_dir: Path) -> dict:
    manifest_path = ROOT / "data" / "test_suite" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["items"]
    if limit:
        items = items[:limit]

    work_root = output_dir / "work"
    work_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    by_difficulty: dict[str, list[float]] = defaultdict(list)
    by_theme: dict[str, list[float]] = defaultdict(list)
    by_difficulty_cells: dict[str, list[float]] = defaultdict(list)

    for index, item in enumerate(items, 1):
        doc_path = ROOT / "data" / "test_suite" / item["filename"]
        truth_path = ROOT / "data" / "test_suite" / item["text_file"]
        if not doc_path.exists() or not truth_path.exists():
            print(f"[{index}/{len(items)}] SKIP {item['id']} (fichier manquant)")
            continue

        work_dir = work_root / item["id"]
        config = PipelineConfig(
            mode=mode,
            dpi=300,
            use_native_text=True,
            work_dir=str(work_dir),
            models_dir=str(ROOT / "models" / "tessdata"),
        )
        started = time.perf_counter()
        try:
            document = Pipeline(config).process_document(str(doc_path))
            elapsed = time.perf_counter() - started
            ocr_text = block_text(document)
            truth_text = truth_path.read_text(encoding="utf-8")
            char_error = cer(ocr_text, truth_text)
            cell_recall = table_cell_recall(ocr_text, item.get("tables", []))

            result = {
                "id": item["id"],
                "theme": item.get("theme"),
                "difficulty": item.get("difficulty"),
                "pages": len(document.pages),
                "seconds": round(elapsed, 3),
                "cer": round(char_error, 4),
                "table_cell_recall": (round(cell_recall, 4) if cell_recall is not None else None),
                "ocr_chars": len(normalize(ocr_text)),
                "truth_chars": len(normalize(truth_text)),
            }
            results.append(result)
            by_difficulty[item.get("difficulty", "?")].append(char_error)
            by_theme[item.get("theme", "?")].append(char_error)
            if cell_recall is not None:
                by_difficulty_cells[item.get("difficulty", "?")].append(cell_recall)

            cells = f", cellules {cell_recall:.2%}" if cell_recall is not None else ""
            print(f"[{index}/{len(items)}] {item['id']:10s} CER {char_error:6.2%}{cells} ({elapsed:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - rapport d'échec explicite
            print(f"[{index}/{len(items)}] ERREUR {item['id']}: {exc}")
            results.append({"id": item["id"], "error": str(exc)})

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    report = {
        "mode": mode,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "documents": len(results),
        "mean_cer": round(mean([r["cer"] for r in results if "cer" in r]), 4),
        "mean_table_cell_recall": round(
            mean([r["table_cell_recall"] for r in results if r.get("table_cell_recall") is not None]), 4
        ),
        "by_difficulty": {
            k: {
                "documents": len(v),
                "mean_cer": round(mean(v), 4),
                "mean_table_cell_recall": round(mean(by_difficulty_cells.get(k, [])), 4),
            }
            for k, v in sorted(by_difficulty.items())
        },
        "by_theme": {
            k: {"documents": len(v), "mean_cer": round(mean(v), 4)}
            for k, v in sorted(by_theme.items())
        },
        "results": results,
    }
    return report


def write_report(report: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"eval_{report['mode']}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        f"# Évaluation OCR — mode {report['mode']}",
        "",
        f"Date : {report['timestamp']} · Documents : {report['documents']}",
        "",
        "## Synthèse",
        "",
        f"- **CER moyen : {report['mean_cer']:.2%}**",
        f"- Rappel cellules de tableaux (TEDS-lite) : {report['mean_table_cell_recall']:.2%}",
        "",
        "## Par difficulté",
        "",
        "| Difficulté | Documents | CER moyen | Cellules retrouvées |",
        "|---|---:|---:|---:|",
    ]
    for difficulty, data in report["by_difficulty"].items():
        md.append(
            f"| {difficulty} | {data['documents']} | {data['mean_cer']:.2%} | "
            f"{data['mean_table_cell_recall']:.2%} |"
        )
    md += [
        "",
        "## Par thème",
        "",
        "| Thème | Documents | CER moyen |",
        "|---|---:|---:|",
    ]
    for theme, data in report["by_theme"].items():
        md.append(f"| {theme} | {data['documents']} | {data['mean_cer']:.2%} |")

    md += [
        "",
        "## Détail par document",
        "",
        "| Document | Difficulté | Pages | CER | Cellules | Temps |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in report["results"]:
        if "error" in r:
            md.append(f"| {r['id']} | — | — | ERREUR | — | — |")
            continue
        cells = f"{r['table_cell_recall']:.1%}" if r.get("table_cell_recall") is not None else "—"
        md.append(
            f"| {r['id']} | {r['difficulty']} | {r['pages']} | {r['cer']:.1%} | {cells} | {r['seconds']:.1f}s |"
        )

    (output_dir / f"eval_{report['mode']}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    md_path = output_dir / f"eval_{report['mode']}.md"
    print(f"\nRapport : {json_path}")
    print(f"Rapport : {md_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Évaluation OCR (CER + TEDS-lite) sur le jeu de test")
    parser.add_argument("--mode", choices=["simple", "medium", "advanced"], default="simple")
    parser.add_argument("--limit", type=int, default=None, help="Limiter le nombre de documents")
    parser.add_argument("--output", default=str(ROOT / "data" / "eval"), help="Dossier de sortie")
    args = parser.parse_args()

    report = evaluate(args.mode, args.limit, Path(args.output))
    write_report(report, Path(args.output))

    print(f"\nCER moyen : {report['mean_cer']:.2%}")
    print(f"Cellules retrouvées : {report['mean_table_cell_recall']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
