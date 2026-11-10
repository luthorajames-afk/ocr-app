"""T3.3 — Sauvegarde prévoyante page par page (`page_schema.json`, versionné).

Chaque page traitée est écrite immédiatement ; une reprise après panne peut
ignorer les pages déjà présentes (`saved_pages`). Toute évolution du schéma
incrémente ``PAGE_SCHEMA_VERSION`` et passe par ``migrate`` (AGENTS.md §3).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from core.metadata import PAGE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PageRecord:
    schema_version: int
    page: int
    source: str
    engine: str
    engine_version: str
    duration_ms: float
    text: str
    blocks: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def migrate(data: dict[str, object]) -> dict[str, object]:
    """Migration ascendante des enregistrements de page.

    Aucune migration connue pour l'instant (version 1) : les données plus
    récentes que le schéma courant sont refusées explicitement.
    """
    raw_version = data.get("schema_version", 0)
    version = int(raw_version) if isinstance(raw_version, (int, float, str)) else 0
    if version > PAGE_SCHEMA_VERSION:
        raise ValueError(
            f"page_schema v{version} plus récent que le noyau "
            f"(v{PAGE_SCHEMA_VERSION}) : mise à jour requise"
        )
    return data


def page_path(work_dir: str | Path, page_number: int) -> Path:
    return Path(work_dir) / f"page_{page_number:03d}.json"


def save_page(record: PageRecord, work_dir: str | Path) -> Path:
    """Écriture atomique (temp + os.replace) — un crash ne laisse pas de fichier partiel."""
    target = page_path(work_dir, record.page)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {**record.to_dict(), "schema_version": PAGE_SCHEMA_VERSION},
        ensure_ascii=False,
        indent=2,
    )
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_blocks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def load_page(work_dir: str | Path, page_number: int) -> PageRecord | None:
    path = page_path(work_dir, page_number)
    if not path.is_file():
        return None
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"page illisible (objet attendu) : {path}")
    data = migrate({str(key): value for key, value in raw.items()})
    return PageRecord(
        schema_version=_as_int(data.get("schema_version"), 1),
        page=_as_int(data.get("page"), page_number),
        source=_as_str(data.get("source")),
        engine=_as_str(data.get("engine")),
        engine_version=_as_str(data.get("engine_version")),
        duration_ms=_as_float(data.get("duration_ms")),
        text=_as_str(data.get("text")),
        blocks=_as_blocks(data.get("blocks")),
    )


def saved_pages(work_dir: str | Path) -> set[int]:
    """Numéros de pages déjà sauvegardées (reprise après panne)."""
    root = Path(work_dir)
    if not root.is_dir():
        return set()
    pages: set[int] = set()
    for path in root.glob("page_*.json"):
        try:
            pages.add(int(path.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return pages
