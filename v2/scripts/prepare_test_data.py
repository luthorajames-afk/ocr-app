"""T0.3 — Prépare le jeu de test v2 (30 documents + truth.txt).

Source de vérité : corpus v1 validé `../ocr_app/data/test_suite` (42 documents).
Le script sélectionne 30 documents équilibrés (factures, formulaires, scans,
multi-colonnes/mixtes), copie le document et son `truth.txt`, et écrit
`tests/data/manifest.json` (id, chemin, difficulté, thème, vérité terrain).

Usage (depuis v2/) :
    python scripts/prepare_test_data.py            # copie si absent
    python scripts/prepare_test_data.py --force    # réécrit tout
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent.parent
SOURCE_MANIFEST = V2_ROOT.parent / "ocr_app" / "data" / "test_suite" / "manifest.json"
TARGET = V2_ROOT / "tests" / "data"

# Répartition cible : 10 factures, 7 formulaires, 7 scans, 6 multi-colonnes/mixtes.
QUOTAS = {"facture": 10, "formulaire": 7, "scan": 7, "document_multicolonne": 3, "mixte": 3}


def select_items(items: list[dict]) -> list[dict]:
    """Sélection équilibrée par thème puis par difficulté, ordre stable."""
    chosen: list[dict] = []
    for theme, quota in QUOTAS.items():
        pool = [item for item in items if item.get("theme") == theme]
        # Favoriser un mélange easy/medium/hard.
        pool.sort(key=lambda item: (item.get("difficulty", ""), item.get("id", "")))
        chosen.extend(pool[:quota])
    return chosen


def copy_item(item: dict, force: bool) -> dict:
    src_doc = SOURCE_MANIFEST.parent / item["filename"]
    src_truth = SOURCE_MANIFEST.parent / item["text_file"]
    doc_name = Path(item["filename"]).name
    truth_name = Path(item["text_file"]).name

    dst_doc = TARGET / "documents" / doc_name
    dst_truth = TARGET / "truth" / truth_name
    dst_doc.parent.mkdir(parents=True, exist_ok=True)
    dst_truth.parent.mkdir(parents=True, exist_ok=True)

    if force or not dst_doc.exists():
        shutil.copy2(src_doc, dst_doc)
    if force or not dst_truth.exists():
        shutil.copy2(src_truth, dst_truth)

    return {
        "id": item["id"],
        "document": f"documents/{doc_name}",
        "truth": f"truth/{truth_name}",
        "difficulty": item.get("difficulty"),
        "theme": item.get("theme"),
        "pages": item.get("pages", 1),
        "has_accents": item.get("has_accents", True),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prépare le jeu de test v2 (30 documents)")
    parser.add_argument("--force", action="store_true", help="réécrire les fichiers existants")
    args = parser.parse_args()

    if not SOURCE_MANIFEST.is_file():
        print(f"Corpus source introuvable : {SOURCE_MANIFEST}")
        return 1

    items = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))["items"]
    selected = select_items(items)
    entries = [copy_item(item, args.force) for item in selected]

    manifest = {
        "version": 1,
        "source": str(SOURCE_MANIFEST),
        "documents": entries,
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    by_theme: dict[str, int] = {}
    for entry in entries:
        by_theme[entry["theme"]] = by_theme.get(entry["theme"], 0) + 1
    print(f"{len(entries)} documents copiés vers {TARGET}")
    print("Répartition :", dict(sorted(by_theme.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
