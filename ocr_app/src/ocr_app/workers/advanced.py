"""Worker mode Avancé : colonnes, tableaux, formulaires complexes.

Moteurs : Docling, modèles de layout et de tableaux. Prétraitement complet avec
correction d'orientation. Tableaux : structure complète, cellules fusionnées.
Vitesse lente. Règle R1 : pas de PyTorch dans le noyau, ici seulement.
Règle R5 : le worker reste vivant pendant tout le document.
"""

from __future__ import annotations

from typing import Any
from ocr_app.core.data_model import Page, Block, BlockType
from ocr_app.core.worker import BaseWorker


class AdvancedWorker(BaseWorker):
    def __init__(self, models_dir: str | None = None) -> None:
        super().__init__(mode="advanced", models_dir=models_dir)

    def initialize(self) -> None:
        # Charger Docling / modèles de layout une fois (R5).
        # Règle R4 : chemins de modèles explicites, aucun téléchargement.
        pass

    def process_page(self, page_input: Any, page_number: int) -> Page:
        # Stub de squelette : à remplacer par Docling / PP-Structure (phase 2).
        return Page(
            number=page_number,
            source=str(page_input),
            blocks=[
                Block(
                    type=BlockType.PARAGRAPH,
                    text=f"[page {page_number}] texte avancé (squelette AdvancedWorker)",
                    bbox=(0.0, 0.0, 100.0, 100.0),
                    confidence=0.98,
                )
            ],
            confidence=0.98,
        )

    def shutdown(self) -> None:
        pass