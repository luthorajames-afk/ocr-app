"""Données d'exemple pour le mode démonstration (--demo).

Ces données reproduisent l'état de la maquette validée par le client :
trois fichiers, des statuts différents et une page de facture avec titre,
paragraphe et tableau. Elles ne sont jamais utilisées pour un vrai document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ocr_app.core.data_model import Block, BlockType, Cell, Page, Table
from ocr_app.core.task_queue import TaskStatus


def _table(rows: List[List[str]]) -> Table:
    grid = [
        [
            Cell(text=value, row_index=row_index, col_index=col_index)
            for col_index, value in enumerate(row)
        ]
        for row_index, row in enumerate(rows)
    ]
    cols = max((len(row) for row in rows), default=0)
    return Table(rows=len(rows), cols=cols, grid=grid)


def invoice_page(number: int = 4) -> Page:
    """Page de facture fidèle au contenu du mockup."""
    rows = [
        ["Désignation", "Qté", "PU HT", "TVA", "Total TTC"],
        ["Prestation de conseil", "2", "450,00 €", "20 %", "1 080,00 €"],
        ["Maintenance annuelle", "1", "1 200,00 €", "20 %", "1 440,00 €"],
        ["Frais de dossier", "1", "90,00 €", "20 %", "108,00 €"],
        ["Total", "", "", "", "2 628,00 €"],
    ]
    return Page(
        number=number,
        source="demo://facture_2026_041.pdf",
        confidence=0.96,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text="Facture n° 2026-041",
                bbox=(72.0, 70.0, 523.0, 106.0),
                confidence=0.98,
                block_id="demo-title",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text=(
                    "Client : Société Exemple SAS\n"
                    "Date : 9 novembre 2026\n"
                    "Échéance : 9 décembre 2026"
                ),
                bbox=(72.0, 118.0, 523.0, 208.0),
                confidence=0.96,
                block_id="demo-body",
            ),
            Block(
                type=BlockType.TABLE,
                table=_table(rows),
                bbox=(72.0, 236.0, 523.0, 484.0),
                confidence=0.94,
                block_id="demo-table",
            ),
        ],
    )


def generic_page(title: str, number: int = 1) -> Page:
    """Page d'exemple simple pour les autres fichiers de la démo."""
    rows = [
        ["Champ", "Valeur", "Observation"],
        ["Nom", "Société Exemple SAS", "—"],
        ["Date", "9 novembre 2026", "—"],
    ]
    return Page(
        number=number,
        source=f"demo://{title}",
        confidence=0.95,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text=title,
                bbox=(72.0, 70.0, 523.0, 106.0),
                confidence=0.97,
                block_id="demo-title",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text=(
                    "Document de démonstration traité hors ligne.\n"
                    "Le lien bloc ↔ texte est conservé pour l'édition avant copie."
                ),
                bbox=(72.0, 118.0, 523.0, 206.0),
                confidence=0.95,
                block_id="demo-body",
            ),
            Block(
                type=BlockType.TABLE,
                table=_table(rows),
                bbox=(72.0, 236.0, 523.0, 402.0),
                confidence=0.93,
                block_id="demo-table",
            ),
        ],
    )


@dataclass(frozen=True)
class DemoFile:
    filename: str
    total_pages: int
    status: TaskStatus
    current_page: int
    pages_done: int
    stage: str
    page: Optional[Page]


DEMO_FILES: List[DemoFile] = [
    DemoFile(
        filename="facture_2026_041.pdf",
        total_pages=12,
        status=TaskStatus.RUNNING,
        current_page=4,
        pages_done=7,
        stage="tables",
        page=invoice_page(4),
    ),
    DemoFile(
        filename="formulaire_cerfa.jpg",
        total_pages=2,
        status=TaskStatus.DONE,
        current_page=2,
        pages_done=2,
        stage="",
        page=generic_page("Formulaire CERFA n° 2042", 2),
    ),
    DemoFile(
        filename="scan_contrat_bail.png",
        total_pages=5,
        status=TaskStatus.PENDING,
        current_page=0,
        pages_done=0,
        stage="",
        page=None,
    ),
]
