"""Entrée de l'interface AOCRI v2 : ``python -m core.gui``."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from core.gui.main_window import MainWindow
from core.gui.theme import apply_theme
from core.metadata import APP_EDITOR, APP_NAME, APP_VERSION


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(prog="aocri-gui", description=f"{APP_NAME} v2")
    parser.add_argument("--file", action="append", default=[], help="document à ajouter")
    parser.add_argument("--dark", action="store_true", help="thème sombre")
    args, qt_args = parser.parse_known_args(arguments[1:])

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([arguments[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_EDITOR)
    saved = QSettings("Kriesty Solutions", "AOCRI").value("dark", False)
    dark = bool(args.dark or os.environ.get("AOCRI_DARK") == "1" or saved in (True, "true", "1"))
    apply_theme(app, dark=dark)

    window = MainWindow()
    for path in args.file:
        window.add_file(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
