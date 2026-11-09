"""Réglages persistants de l'application (phase 8).

Charge et sauvegarde les préférences OCR dans un JSON :

    %LOCALAPPDATA%\\AOCRI\\settings.json

Contenu :

    {"languages": ["fra", "eng"], "dpi": 300}

R14 : le fichier est lu et écrit en UTF-8 explicite (chemins et contenus
accentués). Valeurs par défaut robustes : fichier absent, JSON corrompu,
clé manquante ou valeur invalide — tout replie sur les défauts sans lever.

Ce module est volontairement sans dépendance Qt : il est utilisable depuis
l'interface, les workers de test et ``scripts/acceptance.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Langues OCR proposées (données Tesseract livrées dans models/tessdata, R4).
AVAILABLE_LANGUAGES: tuple = ("fra", "eng")
LANGUAGE_LABELS = {"fra": "Français", "eng": "Anglais"}

# R7 : le rendu des PDF est borné à 300 DPI ; seuls ces paliers sont proposés.
DPI_CHOICES: tuple = (150, 200, 300)
DEFAULT_DPI = 300
MAX_DPI = 300

_DEFAULT_LANGUAGES: List[str] = ["fra", "eng"]

# Sous-dossier de %LOCALAPPDATA% (surchargeable pour les tests).
ENV_SETTINGS_DIR = "AOCRI_SETTINGS_DIR"


@dataclass
class AppSettings:
    """Préférences OCR passées aux workers (``--lang``, ``--dpi``)."""

    languages: List[str] = field(default_factory=lambda: list(_DEFAULT_LANGUAGES))
    dpi: int = DEFAULT_DPI

    def language_string(self) -> str:
        """Argument ``--lang`` du worker : ``fra+eng`` (ordre canonique)."""
        return "+".join(self.clean_languages())

    def clean_languages(self) -> List[str]:
        """Langues valides, dans l'ordre canonique de :data:`AVAILABLE_LANGUAGES`."""
        wanted = {str(lang).strip().lower() for lang in self.languages}
        return [
            lang for lang in AVAILABLE_LANGUAGES if lang in wanted
        ]

    def effective_dpi(self) -> int:
        """DPI réellement utilisé, borné aux paliers et à 300 (R7)."""
        try:
            value = int(self.dpi)
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_DPI
        if value <= 0:
            return DEFAULT_DPI
        return min(value, MAX_DPI)


def default_settings() -> AppSettings:
    """Réglages par défaut : fra+eng, 300 DPI."""
    return AppSettings(
        languages=list(_DEFAULT_LANGUAGES), dpi=DEFAULT_DPI
    )


def settings_dir() -> Path:
    """Dossier des réglages : ``%LOCALAPPDATA%\\AOCRI`` (R14 : accents OK)."""
    override = os.environ.get(ENV_SETTINGS_DIR)
    if override:
        base = Path(override)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data) / "AOCRI"
        else:  # pragma: no cover - repli si la variable est absente
            base = Path.home() / "AppData" / "Local" / "AOCRI"
    return base


def settings_path() -> Path:
    """Chemin complet du fichier de réglages."""
    return settings_dir() / "settings.json"


def _parse_languages(value: object) -> List[str]:
    """Langues valides du JSON brut ; repli sur fra+eng si rien d'exploitable."""
    if not isinstance(value, (list, tuple)):
        return list(_DEFAULT_LANGUAGES)
    wanted = {
        str(item).strip().lower()
        for item in value
        if isinstance(item, str) and str(item).strip()
    }
    cleaned = [
        lang for lang in AVAILABLE_LANGUAGES if lang in wanted
    ]
    return cleaned or list(_DEFAULT_LANGUAGES)


def _parse_dpi(value: object) -> int:
    """DPI du JSON brut, borné aux paliers connus ; repli sur 300 (R7)."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_DPI
    if number <= 0:
        return DEFAULT_DPI
    return min(number, MAX_DPI)


def load_settings(path: Optional[Path] = None) -> AppSettings:
    """Charge les réglages ; tout problème (absent, corrompu) → défauts.

    Le dossier parent n'a pas besoin d'exister : un fichier absent est un cas
    normal (premier lancement), jamais une erreur.
    """
    target = Path(path) if path is not None else settings_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return default_settings()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return default_settings()
    if not isinstance(data, dict):
        return default_settings()
    return AppSettings(
        languages=_parse_languages(data.get("languages")),
        dpi=_parse_dpi(data.get("dpi")),
    )


def save_settings(settings: AppSettings, path: Optional[Path] = None) -> Path:
    """Écrit les réglages en UTF-8 (R14) et retourne le chemin utilisé.

    Écriture **atomique** : le JSON est d'abord écrit dans un fichier
    temporaire du même dossier puis renommé par ``os.replace`` — un arrêt
    brutal en pleine écriture ne peut jamais laisser un ``settings.json``
    à moitié écrit (qui serait rechargé comme corrompu). Le dossier parent
    est créé au besoin. ``OSError`` reste levé : l'appelant (dialogue de
    réglages) affiche l'erreur dans la barre de statut.
    """
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "languages": settings.clean_languages(),
        "dpi": settings.effective_dpi(),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path = target.with_name(target.name + ".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, target)
    except OSError:
        # Ne jamais laisser un fichier temporaire derrière si le renommage
        # a échoué (disque plein, verrou antivirus…) : l'ancien réglage,
        # lui, reste intact et sera rechargé au prochain lancement.
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def settings_summary(settings: AppSettings) -> str:
    """Résumé court pour la barre d'outils : « FR + EN · 300 DPI »."""
    codes = {"fra": "FR", "eng": "EN"}
    languages = settings.clean_languages()
    if not languages:
        languages = list(_DEFAULT_LANGUAGES)
    labels = " + ".join(codes.get(lang, lang.upper()) for lang in languages)
    return f"{labels} · {settings.effective_dpi()} DPI"
