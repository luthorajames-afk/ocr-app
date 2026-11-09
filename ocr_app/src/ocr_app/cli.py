"""Interface en ligne de commande (phase 1 : cœur sans interface).

Exemple :
    python -m ocr_app.cli process --input document.pdf --mode simple --output ./out
    python -m ocr_app process --input document.pdf --mode simple --output ./out

Le mode Simple effectue un OCR réel (Tesseract/tesserocr) ; les modes Moyen et
Avancé restent des placeholders explicites en phase 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ocr_app.core.exports import Exporter
from ocr_app.core.pipeline import MAX_DPI, Pipeline, PipelineConfig
from ocr_app.core.rendering import SUPPORTED_SUFFIXES

# R7 : le rendu PDF est limité à 300 DPI (constante partagée avec le pipeline).
_STUB_MODES = ("medium", "advanced")


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

    sub.add_parser("info", help="Affiche des informations sur les moteurs disponibles")
    return parser


def _configure_stdio() -> None:
    """UTF-8 explicite : chemins accentués et messages lisibles (R14)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - flux non reconfigurable
            pass


def _on_stage(page_number: int, stage: str) -> None:
    print(f"[page {page_number}] {stage}", flush=True)


def _validate_input(input_path: Path) -> bool:
    if not input_path.exists():
        print(f"Erreur : fichier introuvable : {input_path}", file=sys.stderr)
        return False
    if not input_path.is_file():
        print(
            f"Erreur : chemin qui n'est pas un fichier : {input_path}",
            file=sys.stderr,
        )
        return False
    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        accepted = ", ".join(sorted(SUPPORTED_SUFFIXES))
        print(
            f"Erreur : format non supporté : {suffix or '(sans extension)'} "
            f"(formats acceptés : {accepted})",
            file=sys.stderr,
        )
        return False
    return True


def _run_process(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not _validate_input(input_path):
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

    if args.mode in _STUB_MODES:
        print(
            f"Avertissement : le mode '{args.mode}' n'est pas implémenté en "
            "phase 1 ; seuls des blocs placeholder sont produits.",
            file=sys.stderr,
        )

    config = PipelineConfig(
        mode=args.mode,
        dpi=dpi,
        work_dir=str(work_dir),
    )
    pipeline = Pipeline(config)
    pipeline.on_stage(_on_stage)

    try:
        doc = pipeline.process_document(str(input_path))
    except Exception as exc:  # noqa: BLE001 - la CLI rend l'erreur lisible
        print(f"Erreur pendant le traitement : {exc}", file=sys.stderr)
        return 1

    # R6 : les pages ont déjà été écrites dans work/ au fil du traitement.
    # Ce second passage produit les fichiers finaux à la racine de sortie,
    # attendus par l'interface et les exports.
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

    total_blocks = sum(len(page.blocks) for page in doc.pages)
    total_chars = sum(
        len(block.text) for page in doc.pages for block in page.blocks
    )
    confidences = [
        page.confidence for page in doc.pages if page.blocks
    ]
    average_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )

    print(
        f"Pages : {len(doc.pages)} | blocs : {total_blocks} | "
        f"caractères : {total_chars} | "
        f"confiance moyenne : {average_confidence * 100:.1f} %"
    )
    print(f"Terminé : {len(doc.pages)} page(s) traitée(s) en mode {args.mode}.")
    print(f"Sortie : {output_dir}")
    print("Fichiers : result.md, result.html, result.txt, document.json, page_XXX.json")
    return 0


def _run_info() -> int:
    from ocr_app import __version__

    print(f"ocr-app version {__version__}")
    print("Application OCR hors ligne (Windows) - cœur phase 1")
    try:
        from ocr_app.core.ocr_engine import available_engines, resolve_tessdata_dir

        directory = resolve_tessdata_dir()
        languages = sorted(path.stem for path in directory.glob("*.traineddata"))
        print(f"Moteurs : {', '.join(available_engines())}")
        print(f"tessdata : {directory} ({', '.join(languages)})")
    except Exception as exc:  # noqa: BLE001 - diagnostic best effort
        print(f"tessdata : indisponible ({exc})")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
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
