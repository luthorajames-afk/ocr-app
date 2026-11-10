"""Persistance des réglages « Approfondi » par page (zones d'exclusion, filtres).

Le panneau Approfondi mémorise, par document ET par page, les zones
d'exclusion dessinées sur l'aperçu et les paramètres de filtres :

    %LOCALAPPDATA%\\AOCRI\\deep\\<sha1(chemin du document)[:12]>\\page_NNN.json

Contenu :

    {"zones": [[x0, y0, x1, y1], …], "filters": {"contrast": 130, …} | null}

- ``zones``   : rectangles en points PDF (origine haut-gauche, convention du
  modèle de données) — ce sont les coordonnées du document, réutilisables
  quel que soit le DPI d'affichage ;
- ``filters`` : dict JSON-sérialisable des filtres (cf.
  ``core/preprocessing.py``), ``null`` quand aucun filtre n'est actif.

Robustesse : lecture tolérante (fichier absent, JSON corrompu, clés
manquantes → état par défaut, jamais d'exception vers l'interface) ;
écriture atomique (fichier temporaire + ``os.replace``, même pattern que
les réglages et les pages OCR) ; R14 : UTF-8 explicite (chemins accentués).
Création paresseuse : rien n'est écrit sur disque avant la première
sauvegarde effective.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

# Sous-dossier de %LOCALAPPDATA%\AOCRI (surchargeable pour les tests).
ENV_DEEP_DIR = "AOCRI_DEEP_DIR"

# Nom du sous-dossier sous le répertoire de données de l'application.
DEEP_SUBDIR = "deep"

#: Zone d'exclusion : (x0, y0, x1, y1) en points PDF.
Zone = Tuple[float, float, float, float]


@dataclass(frozen=True)
class DeepState:
    """État Approfondi d'une page : zones d'exclusion + filtres."""

    zones: Tuple[Zone, ...] = ()
    filters: Optional[dict] = None


def deep_root() -> Path:
    """Racine du stockage Approfondi (surcharge ``AOCRI_DEEP_DIR``, tests)."""
    override = os.environ.get(ENV_DEEP_DIR)
    if override:
        root = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            root = Path(base) / "AOCRI" / DEEP_SUBDIR
        else:  # pragma: no cover - repli hors Windows
            root = Path.home() / ".aocri" / DEEP_SUBDIR
    return root


def document_dir(source_path: str) -> Path:
    """Dossier du document : ``deep/<sha1(chemin)[:12]>`` (accents inclus)."""
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()
    return deep_root() / digest[:12]


def state_path(source_path: str, page_number: int) -> Path:
    """Chemin du JSON d'état : ``page_NNN.json`` (numérotation 1-based)."""
    number = max(1, int(page_number))
    return document_dir(source_path) / f"page_{number:03d}.json"


def load_state(source_path: str, page_number: int) -> DeepState:
    """Lit l'état d'une page ; repli sur les défauts (jamais d'exception).

    Lecture paresseuse : un fichier absent ne crée aucun dossier.
    """
    path = state_path(source_path, page_number)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return DeepState()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return DeepState()
    if not isinstance(data, dict):
        return DeepState()
    return DeepState(
        zones=_as_zones(data.get("zones")),
        filters=_as_filters(data.get("filters")),
    )


def save_state(
    source_path: str,
    page_number: int,
    zones: Sequence[Sequence[float]],
    filters: Optional[dict],
) -> Path:
    """Écrit l'état d'une page (atomique) et retourne le chemin écrit.

    Les zones sont stockées en listes JSON (points PDF, floats) ; les
    filtres sont enregistrés tels quels (dict JSON-sérialisable, ``None``
    pour « aucun filtre »). ``OSError`` se propage : l'appelant (interface)
    affiche l'échec plutôt que de croire à une persistance.
    """
    path = state_path(source_path, page_number)
    payload = {
        "zones": [
            zone_list
            for zone_list in (
                _as_zone_list(zone) for zone in (zones or ())
            )
            if zone_list is not None
        ],
        "filters": filters if isinstance(filters, dict) else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(encoded)
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        except OSError:  # pragma: no cover - nettoyage best effort
            pass
        raise
    return path


# --- Lecture tolérante --------------------------------------------------------


def _as_zone_list(zone: Any) -> Optional[List[float]]:
    """Zone sérialisable : 4 floats (repli sur l'ordre trié si inversée).

    Une zone non finie (NaN/inf — jamais produite par l'aperçu, mais un
    JSON hostile ne doit pas contaminer le store) retourne ``None`` : elle
    est ignorée à l'écriture.
    """
    try:
        values = [float(coord) for coord in (zone or ())][:4]
    except (TypeError, ValueError):
        return None
    while len(values) < 4:
        values.append(0.0)
    if not all(math.isfinite(value) for value in values):
        return None
    x0, y0, x1, y1 = values
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _as_zones(value: Any) -> Tuple[Zone, ...]:
    """Zones lues depuis le JSON ; entrées invalides ignorées."""
    if not isinstance(value, list):
        return ()
    zones: List[Zone] = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(coord) for coord in entry)
        except (TypeError, ValueError):
            continue
        # NaN/inf : les comparaisons sont toutes fausses, la garde suivante
        # ne suffit pas — un filtre de finitude explicite est nécessaire.
        if not all(
            math.isfinite(value) for value in (x0, y0, x1, y1)
        ):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        zones.append((x0, y0, x1, y1))
    return tuple(zones)


def _as_filters(value: Any) -> Optional[dict]:
    """Filtres lus depuis le JSON ; type invalide → ``None`` (défaut)."""
    if not isinstance(value, dict):
        return None
    return dict(value)


__all__ = [
    "DEEP_SUBDIR",
    "ENV_DEEP_DIR",
    "DeepState",
    "Zone",
    "deep_root",
    "document_dir",
    "load_state",
    "save_state",
    "state_path",
]
