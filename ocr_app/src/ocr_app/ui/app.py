"""Point d'entrée de l'interface : ``python -m ocr_app.ui``."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from PySide6.QtWidgets import QApplication

from ocr_app import __version__
from ocr_app.ui.icons import app_icon
from ocr_app.ui.main_window import MainWindow
from ocr_app.ui.theme import apply_theme


def create_application(argv: Optional[List[str]] = None) -> QApplication:
    """Crée (ou réutilise) l'application Qt et applique le thème AOCRI."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        app = existing
    else:
        app = QApplication(argv if argv is not None else sys.argv[:1])

    app.setApplicationName("AOCRI")
    app.setApplicationDisplayName("AOCRI")
    app.setOrganizationName("Kriesty Solutions")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ocr_app.ui",
        description="Interface graphique AOCRI (OCR hors ligne).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="affiche l'état de la maquette validée (données d'exemple)",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="CHEMIN",
        help="ajoute un fichier PDF/JPG/PNG au démarrage (répétable)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    parser = build_parser()
    args, qt_args = parser.parse_known_args(arguments[1:])

    app = create_application([arguments[0], *qt_args])
    demo = bool(args.demo or os.environ.get("AOCRI_DEMO") == "1")

    window = MainWindow(demo=demo)
    if args.file:
        window.add_paths(args.file)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
