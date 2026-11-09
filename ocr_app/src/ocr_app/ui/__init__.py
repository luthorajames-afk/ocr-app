"""Interface utilisateur PySide6 (Qt).

L'interface ne fait aucun calcul d'OCR ; elle pilote les workers via le protocole JSON
et affiche la progression, la pause, la reprise et l'annulation.

Squelette : le module est importable. La GUI réelle est branchée à `TaskQueue` et aux
workers (phase 4). Pas de dépendance PySide6 imposée au moment du squelette (phase 4).
"""

from ocr_app.core.task_queue import TaskQueue, TaskStatus

__all__ = ["TaskQueue", "TaskStatus"]
