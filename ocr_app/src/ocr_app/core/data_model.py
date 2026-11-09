"""Modèle de données d'une page OCR.

Une page est une liste de blocs. Chaque bloc a un type, des coordonnées,
un texte, un score de confiance et, pour un tableau, une grille de cellules.
Tous les exports et la prévisualisation sont générés depuis cette structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple, Any
from enum import Enum


class BlockType(str, Enum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    HEADER = "header"
    FOOTER = "footer"


# Un bloc est décrit par (x0, y0, x1, y1) en points (1 pt = 1/72 po).
BBox = Tuple[float, float, float, float]


@dataclass
class Cell:
    """Une cellule de tableau, avec sa position et son état de fusion."""

    text: str
    row_index: int = 0
    col_index: int = 0
    merged_from: Optional[Tuple[int, int, int, int]] = None  # (r0, c0, r1, c1)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Table:
    """Grille de cellules d'un tableau détecté sur la page."""

    rows: int = 0
    cols: int = 0
    grid: List[List[Optional[Cell]]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "grid": [
                [(c.to_dict() if c is not None else None) for c in row]
                for row in self.grid
            ],
        }


@dataclass
class Block:
    """Un bloc de contenu détecté sur une page."""

    type: BlockType
    text: str = ""
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    table: Optional[Table] = None
    # Identifiant du bloc pour le lien bloc ↔ texte (édition avant copie).
    block_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        if self.table is not None:
            d["table"] = self.table.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        block = cls(
            type=BlockType(d["type"]),
            text=d.get("text", ""),
            bbox=tuple(d.get("bbox", [0.0, 0.0, 0.0, 0.0])),
            confidence=d.get("confidence", 0.0),
            block_id=d.get("block_id"),
        )
        if d.get("table") is not None:
            td = d["table"]
            table = Table(
                rows=td.get("rows", 0),
                cols=td.get("cols", 0),
            )
            grid: List[List[Optional[Cell]]] = []
            for row in td.get("grid", []):
                grid_row: List[Optional[Cell]] = []
                for c in row:
                    grid_row.append(
                        Cell(**c) if c is not None else None
                    )
                grid.append(grid_row)
            table.grid = grid
            block.table = table
        return block


@dataclass
class Page:
    """Une page traitée : numéro, liste de blocs, métadonnées."""

    number: int
    blocks: List[Block] = field(default_factory=list)
    source: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "blocks": [b.to_dict() for b in self.blocks],
            "source": self.source,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Page":
        return cls(
            number=d["number"],
            blocks=[Block.from_dict(b) for b in d.get("blocks", [])],
            source=d.get("source"),
            confidence=d.get("confidence", 1.0),
        )

    def save_json(self, path: str) -> None:
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "Page":
        import json

        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class Document:
    """Document complet : liste de pages ordonnées."""

    title: str
    pages: List[Page] = field(default_factory=list)
    source_path: Optional[str] = None

    def add_page(self, page: Page) -> None:
        self.pages.append(page)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "pages": [p.to_dict() for p in self.pages],
            "source_path": self.source_path,
        }

    def save_json(self, path: str) -> None:
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)