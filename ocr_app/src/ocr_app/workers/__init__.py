"""Workers OCR par mode : simple, medium, advanced.

Chaque worker s'exécute dans son propre processus (R2) avec ses propres
dépendances (R13). Le worker Avancé reste vivant pendant tout le document (R5).
"""

from ocr_app.workers.simple import SimpleWorker
from ocr_app.workers.medium import MediumWorker
from ocr_app.workers.advanced import AdvancedWorker

__all__ = ["SimpleWorker", "MediumWorker", "AdvancedWorker"]