"""Interface utilisateur PySide6 (Qt).

L'interface ne fait aucun calcul d'OCR ; elle pilote les workers via le protocole
JSON et affiche la progression, la pause, la reprise et l'annulation.

Lancement :
    python -m ocr_app.ui
    python -m ocr_app.ui --demo
"""

from __future__ import annotations

from typing import Any

from ocr_app.core.task_queue import TaskQueue, TaskStatus

__all__ = ["MainWindow", "TaskQueue", "TaskStatus", "run"]


def __getattr__(name: str) -> Any:
    """Import paresseux : le squelette reste importable sans charger PySide6."""
    if name == "MainWindow":
        from ocr_app.ui.main_window import MainWindow

        return MainWindow
    if name == "run":
        from ocr_app.ui.app import main

        return main
    raise AttributeError(f"module {__name__!r} n'a pas d'attribut {name!r}")
