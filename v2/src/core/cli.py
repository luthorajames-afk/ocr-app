"""T1.2/T1.3 — CLI du noyau v2 (sans IHM).

Usage :
    python -m core.cli process --input <pdf|jpg|png> --output <fichier.txt>
    python -m core.cli info
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from core.metadata import APP_NAME, APP_VERSION
from core.pipeline.documents import load_document
from core.pipeline.tesseract_engine import TesseractEngine


def process_document(source: str, output: str, dpi: int) -> int:
    """Traite un document page par page ; texte natif d'abord (T1.3)."""
    started = time.perf_counter()
    pages = load_document(source, dpi=dpi)
    engine = TesseractEngine()
    parts: list[str] = []
    native_pages = 0
    ocr_pages = 0
    try:
        engine.initialize()
        for page in pages:
            header = f"=== Page {page.number} ==="
            if page.has_native_text:
                native_pages += 1
                parts.append(f"{header}\n{page.native_text}\n")
            else:
                ocr_pages += 1
                result = engine.process(page.image)
                parts.append(f"{header}\n{result.text}\n")
    finally:
        engine.shutdown()

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    elapsed = time.perf_counter() - started
    print(
        f"{len(pages)} page(s) — natives {native_pages}, OCR {ocr_pages} — "
        f"{elapsed:.2f}s — {target}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aocri", description=f"{APP_NAME} v2 — CLI")
    sub = parser.add_subparsers(dest="command")

    proc = sub.add_parser("process", help="Traiter un document en texte brut (mode Simple)")
    proc.add_argument("--input", required=True, help="PDF, JPG ou PNG")
    proc.add_argument("--output", required=True, help="Fichier .txt de sortie")
    proc.add_argument("--dpi", type=int, default=300, help="Rendu 72-300 DPI (défaut 300)")

    sub.add_parser("info", help="Version et moteur disponible")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "info":
        print(f"{APP_NAME} {APP_VERSION}")
        print(f"Tesseract : {TesseractEngine().version}")
        return 0
    if args.command == "process":
        return process_document(args.input, args.output, args.dpi)
    print("Commande requise : process | info", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
