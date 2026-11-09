"""Manifest et registre des modules optionnels (medium, advanced).

Les manifestes ``module.json`` sont lus en UTF-8 explicite (R14) et les erreurs
d'un module (JSON invalide, encodage cassé, champs manquants) n'empêchent pas
la détection des autres modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_VERSION_PART = re.compile(r"(\d+)")
_REQUIRED_FIELDS = ("id", "version", "mode", "worker", "sha256")


def _version_key(value: str) -> tuple[int, int, int]:
    """Convertit une version en clé numérique comparable.

    ``"1.10.2"`` -> ``(1, 10, 2)``, ``"v2"`` -> ``(2, 0, 0)``,
    suffixe de pré-version ignoré (``"1.2.0-beta"`` -> ``(1, 2, 0)``).
    Une version illisible vaut ``(0, 0, 0)``.
    """
    text = str(value or "").strip().lstrip("vV")
    if not text:
        return (0, 0, 0)
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    parts: list[int] = []
    for component in core.split(".")[:3]:
        match = _VERSION_PART.match(component.strip())
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


@dataclass
class ModuleManifest:
    id: str
    version: str
    min_app_version: str
    mode: str  # "simple", "medium", "advanced"
    worker: str  # nom de l'exécutable worker, ex: worker_medium.exe
    sha256: str  # empreinte du worker pour vérification d'intégrité
    path: Optional[str] = None  # dossier d'installation du module

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "min_app_version": self.min_app_version,
            "mode": self.mode,
            "worker": self.worker,
            "sha256": self.sha256,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ModuleManifest":
        if isinstance(d, ModuleManifest):
            return d
        if not isinstance(d, dict):
            raise ValueError(
                f"manifeste de module invalide : objet attendu, "
                f"{type(d).__name__} reçu"
            )
        invalid = [
            key
            for key in _REQUIRED_FIELDS
            if not isinstance(d.get(key), str) or not d[key].strip()
        ]
        if invalid:
            raise ValueError(
                "champs manquants ou invalides : " + ", ".join(invalid)
            )
        path = d.get("path")
        return cls(
            id=d["id"].strip(),
            version=d["version"].strip(),
            min_app_version=str(d.get("min_app_version") or "1.0.0").strip(),
            mode=d["mode"].strip(),
            worker=d["worker"].strip(),
            sha256=d["sha256"].strip(),
            path=path if isinstance(path, str) else None,
        )

    def verify_sha256(self) -> bool:
        """Vérifie l'empreinte SHA-256 du worker (module corrompu ?)."""
        if not self.path or not self.worker:
            return False
        # Le nom du worker doit rester un simple nom de fichier : un manifeste
        # ne doit pas pouvoir pointer hors du dossier du module.
        if Path(self.worker).name != self.worker:
            return False
        worker_path = Path(self.path) / self.worker
        if not worker_path.is_file():
            return False
        expected = self.sha256.strip().lower()
        if expected.startswith("sha256:"):
            expected = expected[len("sha256:"):].strip()
        if not expected:
            return False
        try:
            actual = sha256_file(worker_path)
        except OSError:
            return False
        return actual == expected


class ModuleRegistry:
    """Détecte les modules installés au démarrage de l'application."""

    def __init__(self, modules_dir: str | os.PathLike[str]) -> None:
        self.modules_dir = Path(modules_dir)
        self._modules: Dict[str, ModuleManifest] = {}

    def scan(self) -> list[ModuleManifest]:
        """Scanne le dossier des modules.

        Un module illisible est ignoré (journalisé en debug) sans empêcher la
        détection des autres. Le registre n'est remplacé qu'à la fin du scan.
        """
        found: Dict[str, ModuleManifest] = {}
        try:
            if not self.modules_dir.is_dir():
                self._modules = {}
                return []
            module_dirs = sorted(
                path for path in self.modules_dir.iterdir() if path.is_dir()
            )
        except OSError as exc:
            logger.debug("dossier de modules illisible %s : %s", self.modules_dir, exc)
            self._modules = {}
            return []

        for module_dir in module_dirs:
            manifest_path = module_dir / "module.json"
            try:
                text = manifest_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                if manifest_path.exists():
                    logger.debug("manifeste illisible %s : %s", manifest_path, exc)
                continue
            try:
                data = json.loads(text)
                manifest = ModuleManifest.from_dict(data)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug("manifeste invalide %s : %s", manifest_path, exc)
                continue

            manifest.path = str(module_dir)
            if manifest.id in found:
                logger.warning(
                    "module %s en double : %s remplace %s",
                    manifest.id,
                    module_dir,
                    found[manifest.id].path,
                )
            found[manifest.id] = manifest

        self._modules = found
        return list(found.values())

    def get(self, module_id: str) -> Optional[ModuleManifest]:
        return self._modules.get(module_id)

    def list(self) -> list[ModuleManifest]:
        return list(self._modules.values())

    def is_compatible(self, module_id: str, app_version: str) -> bool:
        """Vrai si la version du module est compatible avec l'application.

        La comparaison est numérique (1.10 > 1.9), pas lexicographique.
        """
        manifest = self.get(module_id)
        if manifest is None:
            return False
        return _version_key(manifest.version) >= _version_key(app_version)


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier par blocs de 1 Mio."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
