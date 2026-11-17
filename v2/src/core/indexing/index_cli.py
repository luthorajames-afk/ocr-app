"""CLI de vérification de l'index (sans IHM).

Usage (depuis ``v2``, avec ``src`` dans PYTHONPATH) ::

    python -m core.indexing.index_cli index <work_dir...>
    python -m core.indexing.index_cli search "ma requête" --top-k 10
    python -m core.indexing.index_cli duplicates --threshold 0.8

Le modèle e5 est cherché par défaut dans ``models/`` puis dans
``../app_pdf_pro/models`` ; surcharge possible avec ``--model-dir`` ou
``AOCRI_E5_MODEL_DIR``. ``--embedder hashing`` permet un essai sans modèle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.indexing.embedder import HashingEmbedder, OnnxE5Embedder
from core.indexing.search import index_work_dirs, related_documents, search, similar_documents
from core.indexing.store import CorpusIndex


def _add_common(parser: argparse.ArgumentParser, *, with_embedder: bool) -> None:
    parser.add_argument("--index-dir", type=Path, default=None, help="dossier d'index")
    parser.add_argument(
        "--backend",
        choices=("auto", "numpy", "chroma"),
        default="auto",
        help="stockage vectoriel (défaut auto)",
    )
    if with_embedder:
        parser.add_argument(
            "--embedder",
            choices=("onnx", "hashing"),
            default="onnx",
            help="vecteuriseur (hashing = test sans modèle)",
        )
        parser.add_argument("--model-dir", type=Path, default=None)
        parser.add_argument(
            "--dimension", type=int, default=768, help="dimension de HashingEmbedder"
        )


def _build_embedder(args: argparse.Namespace) -> OnnxE5Embedder | HashingEmbedder:
    if args.embedder == "hashing":
        return HashingEmbedder(dimension=args.dimension)
    return OnnxE5Embedder(model_dir=args.model_dir)


def _open_index(args: argparse.Namespace) -> CorpusIndex:
    return CorpusIndex(args.index_dir, backend=args.backend)


def _snippet(text: str, width: int = 140) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= width else f"{compact[: width - 1]}…"


def _cmd_index(args: argparse.Namespace) -> int:
    index = _open_index(args)
    report = index_work_dirs(args.work_dirs, _build_embedder(args), index, replace=args.replace)
    for document in report.indexed:
        print(f"+ {document.md5[:10]}  {document.source}  ({document.page_count} pages)")
    for skipped in report.skipped:
        print(f"= déjà indexé : {skipped}")
    for error in report.errors:
        print(f"! {error}", file=sys.stderr)
    print(
        f"{len(report.indexed)} document(s), {report.pages_indexed} page(s) indexée(s), "
        f"{len(report.skipped)} ignoré(s), {len(report.errors)} erreur(s) "
        f"— backend={index.backend_name}, lexical={index.lexical_backend}"
    )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    index = _open_index(args)
    hits = search(index, _build_embedder(args), args.query, k=args.top_k, mode=args.mode)
    if not hits:
        print("Aucun résultat.")
        return 0
    for position, hit in enumerate(hits, start=1):
        print(
            f"{position:2d}. {hit.score:.4f}  p.{hit.page:<3d}  "
            f"[{hit.md5[:8]}]  {hit.source}"
        )
        print(f"    {_snippet(hit.text)}")
    return 0


def _cmd_duplicates(args: argparse.Namespace) -> int:
    index = _open_index(args)
    pairs = similar_documents(
        index,
        args.threshold,
        mode=args.mode,
        top_k_per_page=args.top_k_per_page,
    )
    if not pairs:
        print("Aucune paire au-dessus du seuil.")
        return 0
    for pair in pairs:
        pages = ", ".join(f"{a}-{b} ({score:.3f})" for a, b, score in pair.top_pages)
        print(f"{pair.score:.3f}  {pair.doc_a.source}  <->  {pair.doc_b.source}")
        print(f"       pages : {pages}")
    return 0


def _cmd_related(args: argparse.Namespace) -> int:
    index = _open_index(args)
    related = related_documents(index, args.md5, k=args.top_k)
    if not related:
        print("Aucun document lié (document absent ou corpus trop petit).")
        return 0
    for item in related:
        print(f"{item.score:.3f}  {item.document.source}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aocri-index", description="Index AOCRI hors ligne")
    sub = parser.add_subparsers(dest="command", required=True)

    index_parser = sub.add_parser("index", help="Indexer des dossiers work AOCRI")
    index_parser.add_argument("work_dirs", nargs="+", type=Path)
    index_parser.add_argument("--replace", action="store_true")
    _add_common(index_parser, with_embedder=True)
    index_parser.set_defaults(handler=_cmd_index)

    search_parser = sub.add_parser("search", help="Rechercher des pages")
    search_parser.add_argument("query")
    search_parser.add_argument("-k", "--top-k", type=int, default=10)
    search_parser.add_argument(
        "--mode", choices=("hybrid", "semantic", "lexical"), default="hybrid"
    )
    _add_common(search_parser, with_embedder=True)
    search_parser.set_defaults(handler=_cmd_search)

    duplicates_parser = sub.add_parser("duplicates", help="Détecter les doublons")
    duplicates_parser.add_argument("--threshold", type=float, default=0.8)
    duplicates_parser.add_argument("--mode", choices=("exact", "topk"), default="exact")
    duplicates_parser.add_argument("--top-k-per-page", type=int, default=25)
    _add_common(duplicates_parser, with_embedder=False)
    duplicates_parser.set_defaults(handler=_cmd_duplicates)

    related_parser = sub.add_parser("related", help="Documents liés à un MD5")
    related_parser.add_argument("md5")
    related_parser.add_argument("-k", "--top-k", type=int, default=5)
    _add_common(related_parser, with_embedder=False)
    related_parser.set_defaults(handler=_cmd_related)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, ImportError, RuntimeError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
