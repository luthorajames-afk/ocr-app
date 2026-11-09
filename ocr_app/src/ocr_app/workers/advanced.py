"""Worker mode Avancé : colonnes, tableaux, formulaires complexes.

Moteur : Docling (``core/advanced_engine.py``) — layout, tableaux, lecture
structurée du document. Règles :

- R1/R13 : Docling ne vit que dans le venv d'évaluation dédié
  (``.venv-eval-advanced``, Python 3.12) ; l'import est paresseux : si Docling
  est absent de l'environnement courant, le worker retombe sur le squelette
  historique (placeholder explicite) au lieu de planter.
- R2 : le moteur ne s'exécute que dans le sous-processus du worker.
- R4 : modèles Docling déjà en cache local (hors-ligne, cf. advanced_engine).
- R5 : le worker reste vivant pendant tout le document. Docling convertit le
  fichier entier à la première page demandée ; les pages suivantes servent
  le résultat en cache (une conversion par document, pas par page).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ocr_app.core.data_model import Page, Block, BlockType
from ocr_app.core.worker import BaseWorker

logger = logging.getLogger(__name__)


class AdvancedWorker(BaseWorker):
    def __init__(self, models_dir: str | None = None) -> None:
        super().__init__(mode="advanced", models_dir=models_dir)
        # {numéro de page: blocs} rempli à la première page demandée (R5).
        self._page_blocks: Optional[Dict[int, List[Block]]] = None
        # Vrai si Docling est indisponible : pages placeholder à la volée.
        self._degraded = False

    def initialize(self) -> None:
        # R5 : rien à charger ici — Docling est instancié (paresseusement)
        # à la première page, quand le chemin du document est connu.
        pass

    def process_page(self, page_input: Any, page_number: int) -> Page:
        if self._page_blocks is None:
            self._page_blocks = self._convert_document(str(page_input))
        blocks = list(self._page_blocks.get(page_number, []))
        if not blocks and self._degraded:
            blocks = [self._skeleton_block(page_number)]
        for position, block in enumerate(blocks):
            block.block_id = f"p{page_number:03d}-docling-b{position:03d}"
        return Page(
            number=page_number,
            source=str(page_input),
            blocks=blocks,
            confidence=0.98 if self._degraded else 1.0,
        )

    def _convert_document(self, source_path: str) -> Dict[int, List[Block]]:
        """Conversion Docling du document entier (une fois par document).

        Sans Docling dans l'environnement courant (mode lancé hors venv
        d'évaluation), repli sur le squelette : pages placeholder générées à
        la volée, jamais de plantage brut.
        """
        try:
            from ocr_app.core.advanced_engine import DoclingEngine
        except ImportError as exc:  # pragma: no cover - environnement sans venv
            logger.warning("mode Avancé : Docling indisponible (%s)", exc)
            self._degraded = True
            return {}
        engine = DoclingEngine()
        try:
            engine.initialize()
            return engine.convert_document(source_path)
        finally:
            engine.shutdown()

    @staticmethod
    def _skeleton_block(page_number: int) -> Block:
        """Placeholder du squelette (Docling absent de l'environnement)."""
        return Block(
            type=BlockType.PARAGRAPH,
            text=(
                f"[page {page_number}] mode Avancé : moteur Docling non "
                "installé dans cet environnement — placeholder (squelette "
                "AdvancedWorker, accents : éèàç)."
            ),
            bbox=(0.0, 0.0, 100.0, 100.0),
            confidence=0.98,
        )

    def shutdown(self) -> None:
        self._page_blocks = None
        self._degraded = False
