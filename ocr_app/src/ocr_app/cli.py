"""Interface en ligne de commande (phase 1 : cœur sans interface).

Exemple :
    python -m ocr_app.cli process --input document.pdf --mode simple --output ./out
    python -m ocr_app process --input document.pdf --mode simple --output ./out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ocr_app.core.exports import Exporter
from ocr_app.core.pipeline import Pipeline, PipelineConfig

# R7 : le rendu PDF est limité à 300 DPI.
MAX_DPI = 300


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
        "--dpi", type=int, default=MAX_DPI,
        help=f"Résolution de rendu (max {MAX_DPI} DPI)",
    )

    sub.add_parser("info", help="Affiche des informations sur le squelette")
    return parser


def _on_stage(page_number: int, stage: str) -> None:
    print(f"[page {page_number}] {stage}", flush=True)


def _run_process(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erreur : fichier introuvable : {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_file():
        print(f"Erreur : chemin qui n'est pas un fichier : {input_path}", file=sys.stderr)
        return 1
    if args.dpi <= 0:
        print("Erreur : --dpi doit être un entier strictement positif", file=sys.stderr)
        return 2

    dpi = min(args.dpi, MAX_DPI)
    if args.dpi > MAX_DPI:
        print(
            f"Note : --dpi {args.dpi} limité à {MAX_DPI} DPI (R7).",
            file=sys.stderr,
        )

    output_dir = Path(args.output)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = output_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Erreur : dossier de sortie inaccessible : {exc}", file=sys.stderr)
        return 1

    config = PipelineConfig(mode=args.mode, dpi=dpi, work_dir=str(work_dir))
    pipeline = Pipeline(config)
    pipeline.on_stage(_on_stage)

    try:
        doc = pipeline.process_document(str(input_path))
    except Exception as exc:  # noqa: BLE001 - la CLI doit rendre une erreur lisible
        print(f"Erreur pendant le traitement : {exc}", file=sys.stderr)
        return 1

    # R6 : chaque page terminée est écrite immédiatement en JSON. L'API
    # `process_document` retourne encore le document complet ; quand elle
    # exposera un flux page par page, cette sauvegarde ira dans le callback.
    try:
        for page in doc.pages:
            page.save_json(output_dir / f"page_{page.number:03d}.json")
        doc.save_json(output_dir / "document.json")
    except (OSError, ValueError) as exc:
        print(f"Erreur d'écriture des pages : {exc}", file=sys.stderr)
        return 1

    exporter = Exporter(doc)
    try:
        exporter.save_plain_text(output_dir / "result.txt")
        exporter.save_markdown(output_dir / "result.md")
        exporter.save_html(output_dir / "result.html")
    except Exception as exc:  # noqa: BLE001 - export optionnel en erreur
        print(f"Erreur pendant l'export : {exc}", file=sys.stderr)
        return 1

    print(f"Terminé : {len(doc.pages)} page(s) traitée(s) en mode {args.mode}.")
    print(f"Sortie : {output_dir}")
    return 0


def _run_info() -> int:
    from ocr_app import __version__

    print(f"ocr-app version {__version__}")
    print("Application OCR hors ligne (Windows) - squelette v1")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "info":
        return _run_info()
    if args.command == "process":
        try:
            return _run_process(args)
        except KeyboardInterrupt:
            print("\nInterrompu.", file=sys.stderr)
            return 130

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
