"""Modèle de données d'une page OCR.

Une page est une liste de blocs. Chaque bloc a un type, des coordonnées,
un texte, un score de confiance et, pour un tableau, une grille de cellules.
Tous les exports et la prévisualisation sont générés depuis cette structure.

Robustesse (R14) : toutes les lectures/écritures JSON sont explicites en UTF-8
(lecture ``utf-8-sig`` pour tolérer un BOM) et les écritures sont atomiques :
un fichier de page n'est jamais laissé partiellement écrit (utile pour R6).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Tuple


class BlockType(str, Enum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    HEADER = "header"
    FOOTER = "footer"


# Un bloc est décrit par (x0, y0, x1, y1) en points (1 pt = 1/72 po).
BBox = Tuple[float, float, float, float]
_DEFAULT_BBOX: BBox = (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Helpers de conversion tolérante (données JSON possiblement incomplètes)
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    # NaN/Inf ne sont pas sérialisables en JSON valide.
    return result if math.isfinite(result) else default


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return default
    return str(value)


def _as_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return _as_text(value)


def _as_bbox(value: Any) -> BBox:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return (
            _as_float(value[0]),
            _as_float(value[1]),
            _as_float(value[2]),
            _as_float(value[3]),
        )
    return _DEFAULT_BBOX


def _as_merged_from(value: Any) -> Optional[Tuple[int, int, int, int]]:
    """Normalise ``merged_from`` en tuple d'entiers (JSON le rend en liste)."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    coords: List[int] = []
    for item in value:
        if isinstance(item, bool):
            return None
        try:
            coords.append(int(item))
        except (TypeError, ValueError, OverflowError):
            return None
    return (coords[0], coords[1], coords[2], coords[3])


def _as_block_type(value: Any) -> BlockType:
    """Type de bloc tolérant : un type inconnu devient un paragraphe.

    Cela permet à une version future du worker d'ajouter des types sans faire
    planter la lecture des pages existantes (R15).
    """
    if isinstance(value, BlockType):
        return value
    if isinstance(value, str):
        try:
            return BlockType(value.strip().lower())
        except ValueError:
            pass
    return BlockType.PARAGRAPH


def _normalise_grid(grid: Any) -> List[List[Optional["Cell"]]]:
    """Normalise une grille JSON en ``list[list[Cell | None]]``.

    - ``grid`` absent / ``None`` / non-liste -> grille vide ;
    - une cellule ``None`` reste ``None`` (trou dans le tableau) ;
    - une cellule texte (cas de certains moteurs) devient un ``Cell`` ;
    - les index de ligne/colonne manquants sont déduits de la position.
    """
    if not isinstance(grid, (list, tuple)):
        return []
    normalised: List[List[Optional[Cell]]] = []
    for row_index, row in enumerate(grid):
        if not isinstance(row, (list, tuple)):
            # Ligne invalide : on conserve la position avec une ligne vide.
            normalised.append([])
            continue
        grid_row: List[Optional[Cell]] = []
        for col_index, cell in enumerate(row):
            if cell is None:
                grid_row.append(None)
            elif isinstance(cell, Cell):
                grid_row.append(cell)
            elif isinstance(cell, dict):
                grid_row.append(
                    Cell.from_dict(cell, row_index=row_index, col_index=col_index)
                )
            elif isinstance(cell, str):
                grid_row.append(
                    Cell(text=cell, row_index=row_index, col_index=col_index)
                )
            else:
                grid_row.append(
                    Cell(
                        text=_as_text(cell),
                        row_index=row_index,
                        col_index=col_index,
                    )
                )
        normalised.append(grid_row)
    return normalised


@dataclass
class Cell:
    """Une cellule de tableau, avec sa position et son état de fusion."""

    text: str
    row_index: int = 0
    col_index: int = 0
    merged_from: Optional[Tuple[int, int, int, int]] = None  # (r0, c0, r1, c1)

    def __post_init__(self) -> None:
        self.text = _as_text(self.text)
        self.row_index = max(0, _as_int(self.row_index, 0))
        self.col_index = max(0, _as_int(self.col_index, 0))
        self.merged_from = _as_merged_from(self.merged_from)

    def to_dict(self) -> dict:
        # Construction explicite : ``dataclasses.asdict`` copie récursivement
        # tout le graphe, ce qui est coûteux sur les gros tableaux.
        return {
            "text": self.text,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "merged_from": self.merged_from,
        }

    @classmethod
    def from_dict(
        cls,
        d: Any,
        *,
        row_index: Optional[int] = None,
        col_index: Optional[int] = None,
    ) -> "Cell":
        if isinstance(d, Cell):
            return d
        if not isinstance(d, dict):
            raise TypeError(
                f"cellule invalide : objet attendu, {type(d).__name__} reçu"
            )
        return cls(
            text=d.get("text", ""),
            row_index=_as_int(
                d.get("row_index"), row_index if row_index is not None else 0
            ),
            col_index=_as_int(
                d.get("col_index"), col_index if col_index is not None else 0
            ),
            merged_from=d.get("merged_from"),
        )


@dataclass
class Table:
    """Grille de cellules d'un tableau détecté sur la page."""

    rows: int = 0
    cols: int = 0
    grid: List[List[Optional[Cell]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rows = max(0, _as_int(self.rows, 0))
        self.cols = max(0, _as_int(self.cols, 0))
        self.grid = _normalise_grid(self.grid)
        if self.grid:
            if self.rows <= 0:
                self.rows = len(self.grid)
            if self.cols <= 0:
                self.cols = max(len(row) for row in self.grid)

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "grid": [
                [(c.to_dict() if c is not None else None) for c in row]
                for row in self.grid
            ],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Table":
        if isinstance(d, Table):
            return d
        if not isinstance(d, dict):
            raise ValueError(
                f"tableau invalide : objet attendu, {type(d).__name__} reçu"
            )
        return cls(
            rows=d.get("rows", 0),
            cols=d.get("cols", 0),
            grid=d.get("grid", []),
        )


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

    def __post_init__(self) -> None:
        self.type = _as_block_type(self.type)
        self.text = _as_text(self.text)
        self.bbox = _as_bbox(self.bbox)
        self.confidence = _as_float(self.confidence, 0.0)
        if isinstance(self.table, dict):
            self.table = Table.from_dict(self.table)
        elif self.table is not None and not isinstance(self.table, Table):
            self.table = None
        self.block_id = _as_optional_str(self.block_id)

    def to_dict(self) -> dict:
        # Pas de ``asdict`` ici : il copierait récursivement le tableau (toutes
        # les cellules) puis ``to_dict`` le reconvertirait une seconde fois.
        return {
            "type": self.type.value,
            "text": self.text,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "table": self.table.to_dict() if self.table is not None else None,
            "block_id": self.block_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Block":
        if isinstance(d, Block):
            return d
        if not isinstance(d, dict):
            raise ValueError(
                f"bloc invalide : objet attendu, {type(d).__name__} reçu"
            )
        return cls(
            type=d.get("type", BlockType.PARAGRAPH.value),
            text=d.get("text", ""),
            bbox=d.get("bbox", _DEFAULT_BBOX),
            confidence=d.get("confidence", 0.0),
            table=d.get("table"),
            block_id=d.get("block_id"),
        )


@dataclass
class Page:
    """Une page traitée : numéro, liste de blocs, métadonnées."""

    number: int
    blocks: List[Block] = field(default_factory=list)
    source: Optional[str] = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.number = _as_int(self.number, 0)
        self.source = _as_optional_str(self.source)
        self.confidence = _as_float(self.confidence, 1.0)

        raw_blocks = self.blocks
        if raw_blocks is None:
            raw_blocks = []
        elif not isinstance(raw_blocks, (list, tuple)):
            raw_blocks = []
        blocks: List[Block] = []
        for item in raw_blocks:
            if isinstance(item, Block):
                blocks.append(item)
            elif isinstance(item, dict):
                # Tolérance : le pipeline d'un autre module peut encore
                # fournir des blocs sous forme de dictionnaires.
                try:
                    blocks.append(Block.from_dict(item))
                except (TypeError, ValueError):
                    continue
        self.blocks = blocks

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "blocks": [b.to_dict() for b in self.blocks],
            "source": self.source,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Page":
        if isinstance(d, Page):
            return d
        if not isinstance(d, dict):
            raise ValueError(
                f"page invalide : objet attendu, {type(d).__name__} reçu"
            )
        return cls(
            number=d.get("number", 0),
            blocks=d.get("blocks", []),
            source=d.get("source"),
            confidence=d.get("confidence", 1.0),
        )

    def save_json(self, path: str | os.PathLike[str]) -> None:
        """Écrit la page en JSON UTF-8, de façon atomique (R6)."""
        _atomic_write_json(path, self.to_dict())

    @classmethod
    def load_json(cls, path: str | os.PathLike[str]) -> "Page":
        return cls.from_dict(_load_json_object(path, "page"))


@dataclass
class Document:
    """Document complet : liste de pages ordonnées."""

    title: str
    pages: List[Page] = field(default_factory=list)
    source_path: Optional[str] = None

    def __post_init__(self) -> None:
        self.title = _as_text(self.title)
        self.source_path = _as_optional_str(self.source_path)
        raw_pages = self.pages
        if raw_pages is None:
            raw_pages = []
        elif not isinstance(raw_pages, (list, tuple)):
            raw_pages = []
        pages: List[Page] = []
        for item in raw_pages:
            if isinstance(item, Page):
                pages.append(item)
            elif isinstance(item, dict):
                try:
                    pages.append(Page.from_dict(item))
                except (TypeError, ValueError):
                    continue
        self.pages = pages

    def add_page(self, page: Page) -> None:
        if isinstance(page, Page):
            self.pages.append(page)
        elif isinstance(page, dict):
            self.pages.append(Page.from_dict(page))
        else:
            raise TypeError(
                f"page invalide : Page attendu, {type(page).__name__} reçu"
            )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "pages": [p.to_dict() for p in self.pages],
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Document":
        if isinstance(d, Document):
            return d
        if not isinstance(d, dict):
            raise ValueError(
                f"document invalide : objet attendu, {type(d).__name__} reçu"
            )
        return cls(
            title=d.get("title", ""),
            pages=d.get("pages", []),
            source_path=d.get("source_path"),
        )

    def save_json(self, path: str | os.PathLike[str]) -> None:
        """Écrit le document en JSON UTF-8, de façon atomique."""
        _atomic_write_json(path, self.to_dict())

    @classmethod
    def load_json(cls, path: str | os.PathLike[str]) -> "Document":
        return cls.from_dict(_load_json_object(path, "document"))


# ---------------------------------------------------------------------------
# Entrées/sorties JSON (UTF-8 explicite, écriture atomique)
# ---------------------------------------------------------------------------
def _atomic_write_json(path: str | os.PathLike[str], payload: dict) -> None:
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_json_object(path: str | os.PathLike[str], what: str) -> dict:
    target = Path(path)
    try:
        with open(target, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise FileNotFoundError(f"{what} introuvable : {target}") from None
    except UnicodeDecodeError as exc:
        raise ValueError(f"{what} non UTF-8 : {target}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON invalide dans {what} {target} "
            f"(ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"{what} invalide : objet JSON attendu dans {target}, "
            f"{type(data).__name__} reçu"
        )
    return data
