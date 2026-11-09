"""Détection légère des modules OCR disponibles pour la section « Modules ».

Deux sources complémentaires, aucune n'importe de moteur lourd (R2) :

1. **import** : présence des paquets via ``importlib.util.find_spec`` —
   environnement de développement (venv par module, R13) ;
2. **registre** : ``ModuleRegistry`` (``ocr_app.modules.module_manifest``) —
   manifestes ``module.json`` installés (produits par ``scripts/make_module.py``),
   avec vérification du sha256 du worker et de la compatibilité de version
   (R15 : montée de version d'un module sans réinstaller le noyau).

Les dossiers de modules scannés (``registry_dirs``) :

- ``AOCRI_MODULES_DIR`` (surcharge manuelle/tests) ;
- ``<dossier de l'application>/modules`` — installation utilisateur finale
  (application gelée : modules livrés à côté de l'exe par les .iss, R15) ;
- ``<AppLocalDataLocation>/modules`` — emplacement de données de l'application ;
- ``<racine du dépôt>/dist/modules`` — sortie de ``scripts/make_module.py`` ;
- ``<racine du dépôt>/modules`` — manifestes de développement.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QStandardPaths

from ocr_app import __version__
from ocr_app.modules.module_manifest import ModuleManifest, ModuleRegistry

# Version de l'application, pour la compatibilité des modules (R15).
APP_VERSION = __version__

_LABELS = {
    "core": "Noyau · Tesseract",
    "medium": "Moyen · RapidOCR",
    "advanced": "Avancé",
}

_NOT_INSTALLED = "non installé"


@dataclass(frozen=True)
class ModuleInfo:
    key: str
    label: str
    installed: bool
    detail: str = ""


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def registry_dirs() -> List[Path]:
    """Dossiers de modules à scanner, dans l'ordre de priorité."""
    dirs: List[Path] = []
    override = os.environ.get("AOCRI_MODULES_DIR")
    if override:
        dirs.append(Path(override))
    # Application installée (gelée) : les .iss livrent les modules dans
    # <dossier de l'exe>/modules (ex. {userappdata}\Programs\AOCRI\modules).
    # En développement, ce dossier n'existe pas à côté du venv : ignoré.
    app_dir = Path(sys.executable).resolve().parent
    dirs.append(app_dir / "modules")
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if location:
        dirs.append(Path(location) / "modules")
    # Racine du dépôt : src/ocr_app/ui/modules_info.py -> parents[3].
    project = Path(__file__).resolve().parents[3]
    dirs.append(project / "dist" / "modules")
    dirs.append(project / "modules")
    return dirs


def _version_tuple(value: str) -> Tuple[int, int, int]:
    """(majeure, mineure, patch) numérique ; version illisible -> (0, 0, 0).

    Même sémantique que ``module_manifest._version_key`` : comparaison
    numérique (1.10 > 1.9), préfixe v/V et suffixe de pré-version ignorés.
    """
    text = str(value or "").strip().lstrip("vV")
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    parts: List[int] = []
    for component in core.split(".")[:3]:
        match = re.match(r"(\d+)", component.strip())
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def find_installed_module(
    key: str, modules_dir: Optional[str] = None
) -> Optional[ModuleManifest]:
    """Manifeste du module ``key`` si installé, vérifié et compatible.

    Fonction publique (utilisée aussi par ``ui.worker_commands`` pour
    récupérer l'exécutable du module) : un module n'est retenu que si son
    worker existe et que son empreinte sha256 correspond (manifeste corrompu
    = module ignoré) et si sa version est compatible avec l'application (R15).
    """
    bases = [Path(modules_dir)] if modules_dir else registry_dirs()
    for base in bases:
        registry = ModuleRegistry(base)
        try:
            manifests = registry.scan()
        except OSError:
            continue
        for manifest in manifests:
            if manifest.id != key:
                continue
            if not manifest.verify_sha256():
                continue
            # R15 : le module exige une version d'application minimale
            # (min_app_version) ; comparaison numérique, pas lexicographique.
            # (Registry.is_compatible compare manifest.version, champ inverse.)
            if _version_tuple(manifest.min_app_version) > _version_tuple(APP_VERSION):
                continue
            return manifest
    return None


def detect_modules(modules_dir: Optional[str] = None) -> List[ModuleInfo]:
    """État des modules : import détecté OU manifeste registre valide."""
    import_installed = {
        "core": _has_module("tesserocr") or _has_module("pytesseract"),
        "medium": _has_module("rapidocr") or _has_module("rapidocr_onnxruntime"),
        "advanced": _has_module("docling"),
    }

    infos: List[ModuleInfo] = []
    for key, label in _LABELS.items():
        installed = import_installed.get(key, False)
        detail = "" if installed else _NOT_INSTALLED
        if not installed:
            manifest = find_installed_module(key, modules_dir)
            if manifest is not None:
                installed = True
                detail = (
                    f"module.json v{manifest.version} "
                    f"(worker vérifié, {manifest.path})"
                )
        infos.append(ModuleInfo(key=key, label=label, installed=installed, detail=detail))
    return infos


def demo_modules() -> List[ModuleInfo]:
    """État de la maquette validée : Noyau et Moyen installés, Avancé absent."""
    return [
        ModuleInfo(key="core", label="Noyau · Tesseract", installed=True),
        ModuleInfo(key="medium", label="Moyen · RapidOCR", installed=True),
        ModuleInfo(
            key="advanced", label="Avancé", installed=False, detail="non installé"
        ),
    ]
