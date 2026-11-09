"""Interface en ligne de commande (phase 1 : cœur sans interface).

Exemple :
    python -m ocr_app.cli process --input document.pdf --mode simple --output ./out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ocr_app.core.pipeline import Pipeline, PipelineConfig
from ocr_app.core.exports import Exporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-app",
        description="Application OCR hors ligne (Windows) - interface CLI",
    )
    sub = parser.add_subparsers(dest="command")

    p_proc = sub.add_parser("process", help="Traite un document par OCR")
    p_proc.add_argument("--input", required=True, help="Chemin du document PDF/JPG/PNG")
    p_proc.add_argument(
        "--mode", choices=["simple", "medium", "advanced"], default="simple",
        help="Mode de qualité (défaut: simple)",
    )
    p_proc.add_argument(
        "--output", default="./out", help="Dossier de sortie (défaut: ./out)",
    )
    p_proc.add_argument(
        "--dpi", type=int, default=300, help="Résolution de rendu (max 300 DPI)",
    )

    p_info = sub.add_parser("info", help="Affiche des informations sur le squelette")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "info":
        from ocr_app import __version__
        print(f"ocr-app version {__version__}")
        print("Application OCR hors ligne (Windows) - squelette v1")
        return 0

    if args.command == "process":
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Erreur : fichier introuvable : {input_path}", file=sys.stderr)
            return 1

        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

        config = PipelineConfig(mode=args.mode, dpi=min(args.dpi, 300))
        pipeline = Pipeline(config)

        def on_stage(page_number: int, stage: str) -> None:
            print(f"[page {page_number}] {stage}")

        doc = pipeline.process_document(str(input_path))
        for page in doc.pages:
            on_stage(page.number, "done")

        exporter = Exporter(doc)
        exporter.save_plain_text(str(output_dir / "result.txt"))
        exporter.save_markdown(str(output_dir / "result.md"))
        exporter.save_html(str(output_dir / "result.html"))

        print(f"Terminé : {len(doc.pages)} page(s) traitée(s) en mode {args.mode}.")
        print(f"Sortie : {output_dir}")
        return 0

    parser.print_help()
    return 1