"""T2.1 — Banc de test autonome : CER et WER sur le corpus v2.

Usage (depuis v2/) :
    python tests/bench/bench_runner.py                 # tout le corpus
    python tests/bench/bench_runner.py --limit 5
    python tests/bench/bench_runner.py --json report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import Literal, TypedDict

V2_ROOT = Path(__file__).resolve().parents[2]
SRC = V2_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.pipeline.documents import load_document
from core.pipeline.tesseract_engine import TesseractEngine

DATA = V2_ROOT / "tests" / "data"


class ManifestEntry(TypedDict):
    id: str
    document: str
    truth: str
    theme: str
    difficulty: str


class BenchRow(TypedDict):
    id: str
    theme: str
    difficulty: str
    cer: float
    wer: float
    seconds: float


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    return " ".join(text.split())


def edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def cer(hypothesis: str, reference: str) -> float:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(hyp, ref) / len(ref)


def wer(hypothesis: str, reference: str) -> float:
    ref_words = normalize(reference).split()
    hyp_words = normalize(hypothesis).split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return edit_distance(hyp_words, ref_words) / len(ref_words)


def _select(limit: int | None, theme: str | None) -> list[ManifestEntry]:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    entries: list[ManifestEntry] = list(manifest["documents"])
    if theme:
        entries = [entry for entry in entries if entry.get("theme") == theme]
    if limit:
        entries = entries[:limit]
    return entries


def run(limit: int | None = None, theme: str | None = None) -> dict[str, object]:
    entries = _select(limit, theme)

    engine = TesseractEngine()
    results: list[BenchRow] = []
    try:
        engine.initialize()
        for index, entry in enumerate(entries, 1):
            document = DATA / entry["document"]
            truth = (DATA / entry["truth"]).read_text(encoding="utf-8")
            started = time.perf_counter()
            pages = load_document(document, dpi=200)
            parts: list[str] = []
            for page in pages:
                if page.has_native_text:
                    parts.append(page.native_text)
                else:
                    parts.append(engine.process(page.image).text)
            hypothesis = "\n".join(parts)
            elapsed = time.perf_counter() - started
            row: BenchRow = {
                "id": entry["id"],
                "theme": entry["theme"],
                "difficulty": entry["difficulty"],
                "cer": round(cer(hypothesis, truth), 4),
                "wer": round(wer(hypothesis, truth), 4),
                "seconds": round(elapsed, 2),
            }
            results.append(row)
            print(
                f"[{index}/{len(entries)}] {row['id']:10s} CER {row['cer']:6.2%} "
                f"WER {row['wer']:6.2%} ({elapsed:.1f}s)"
            )
    finally:
        engine.shutdown()

    def mean(key: Literal["cer", "wer"]) -> float:
        return sum(r[key] for r in results) / len(results) if results else 0.0

    by_theme: dict[str, dict[str, float]] = {}
    for row in results:
        bucket = by_theme.setdefault(row["theme"], {"n": 0, "cer": 0.0, "wer": 0.0})
        bucket["n"] += 1
        bucket["cer"] += row["cer"]
        bucket["wer"] += row["wer"]
    for bucket in by_theme.values():
        bucket["cer"] = round(bucket["cer"] / bucket["n"], 4)
        bucket["wer"] = round(bucket["wer"] / bucket["n"], 4)

    return {
        "documents": len(results),
        "mean_cer": round(mean("cer"), 4),
        "mean_wer": round(mean("wer"), 4),
        "by_theme": by_theme,
        "results": results,
    }


def ablation(limit: int | None = None, theme: str | None = None) -> dict[str, dict[str, float]]:
    """T2.6 — Ablation : brut vs pretraite vs pretraite+lexique.

    Retourne le CER/WER moyen par configuration sur le même échantillon.
    """
    from core.pipeline.lexicon import correct_text
    from core.pipeline.preprocess import preprocess

    entries = _select(limit, theme)

    configs: dict[str, dict[str, bool]] = {
        "brut": {"preprocess": False, "lexicon": False},
        "pretraite": {"preprocess": True, "lexicon": False},
        "pretraite+lexique": {"preprocess": True, "lexicon": True},
    }
    engine = TesseractEngine()
    scores: dict[str, list[tuple[float, float]]] = {name: [] for name in configs}
    try:
        engine.initialize()
        for entry in entries:
            document = DATA / entry["document"]
            truth = (DATA / entry["truth"]).read_text(encoding="utf-8")
            pages = load_document(document, dpi=200)
            page_texts: dict[str, list[str]] = {name: [] for name in configs}
            for page in pages:
                if page.has_native_text:
                    for name in configs:
                        page_texts[name].append(page.native_text)
                    continue
                for name, options in configs.items():
                    image = page.image
                    if options["preprocess"]:
                        image = preprocess(image).image
                    text = engine.process(image).text
                    if options["lexicon"]:
                        text = correct_text(text).text
                    page_texts[name].append(text)
            for name, parts in page_texts.items():
                hypothesis = "\n".join(parts)
                scores[name].append((cer(hypothesis, truth), wer(hypothesis, truth)))
    finally:
        engine.shutdown()

    report: dict[str, dict[str, float]] = {}
    for name, values in scores.items():
        if not values:
            continue
        report[name] = {
            "n": len(values),
            "mean_cer": round(sum(v[0] for v in values) / len(values), 4),
            "mean_wer": round(sum(v[1] for v in values) / len(values), 4),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Banc CER/WER AOCRI v2")
    parser.add_argument("--ablation", action="store_true", help="analyse d'ablation T2.6")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--theme", default=None, help="filtrer par thème (scan, facture, …)")
    parser.add_argument("--json", default=None, help="écrire le rapport JSON")
    args = parser.parse_args()

    if args.ablation:
        ablation_report = ablation(args.limit, args.theme)
        for name, value in ablation_report.items():
            print(
                f"{name:20s} CER {value['mean_cer']:.2%} · WER {value['mean_wer']:.2%} "
                f"({value['n']} docs)"
            )
        if args.json:
            Path(args.json).write_text(
                json.dumps(ablation_report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Rapport : {args.json}")
        return 0

    report = run(args.limit, args.theme)
    print(f"\nCER moyen {report['mean_cer']:.2%} · WER moyen {report['mean_wer']:.2%}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Rapport : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
