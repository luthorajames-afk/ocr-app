"""Script de construction des workers avec PyInstaller.

Règle R3 : mode --onedir uniquement, jamais --onefile (démarrage rapide,
moins de faux positifs antivirus).

Usage :
    python scripts/build.py --worker simple   # noyau + worker Simple (Tesseract)
    python scripts/build.py --worker medium   # module Moyen (RapidOCR/OpenCV)
    python scripts/build.py --worker advanced # module Avancé (Docling)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WORKERS = {
    "simple": "src/ocr_app/workers/simple.py",
    "medium": "src/ocr_app/workers/medium.py",
    "advanced": "src/ocr_app/workers/advanced.py",
}

ENTRY_MAP = {
    "simple": "ocr_app.cli:main",
}


def build(worker: str) -> int:
    if worker not in WORKERS:
        print(f"Worker inconnu : {worker}. Choix : {', '.join(WORKERS)}", file=sys.stderr)
        return 1

    spec_name = f"worker_{worker}"
    dist_dir = Path("dist") / f"worker_{worker}"
    cmd = [
        "pyinstaller",
        "--onedir",          # R3 : jamais --onefile
        "--name", spec_name,
        "--distpath", str(dist_dir),
        "--workpath", str(Path("build") / f"worker_{worker}"),
        "--specpath", str(Path("build") / f"worker_{worker}"),
        WORKERS[worker],
    ]
    print(f"Construction du worker '{worker}' (--onedir)...")
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build des workers OCR (PyInstaller --onedir)")
    parser.add_argument("--worker", required=True, choices=sorted(WORKERS), help="Worker à construire")
    args = parser.parse_args()
    return build(args.worker)


if __name__ == "__main__":
    raise SystemExit(main())