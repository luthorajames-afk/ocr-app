"""Manifest et registre des modules optionnels (medium, advanced)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict
import json
import hashlib
from pathlib import Path


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
    def from_dict(cls, d: dict) -> "ModuleManifest":
        return cls(
            id=d["id"],
            version=d["version"],
            min_app_version=d.get("min_app_version", "1.0.0"),
            mode=d["mode"],
            worker=d["worker"],
            sha256=d["sha256"],
            path=d.get("path"),
        )

    def verify_sha256(self) -> bool:
        """Vérifie l'empreinte SHA-256 du worker (module corrompu ?)."""
        if not self.path:
            return False
        worker_path = Path(self.path) / self.worker
        if not worker_path.exists():
            return False
        h = hashlib.sha256()
        with open(worker_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest() == self.sha256


class ModuleRegistry:
    """Détecte les modules installés au démarrage de l'application."""

    def __init__(self, modules_dir: str) -> None:
        self.modules_dir = Path(modules_dir)
        self._modules: Dict[str, ModuleManifest] = {}

    def scan(self) -> list[ModuleManifest]:
        self._modules.clear()
        if not self.modules_dir.exists():
            return []
        for module_dir in sorted(p for p in self.modules_dir.iterdir() if p.is_dir()):
            manifest_path = module_dir / "module.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = ModuleManifest.from_dict(data)
                manifest.path = str(module_dir)
                self._modules[manifest.id] = manifest
            except (json.JSONDecodeError, KeyError):
                continue
        return list(self._modules.values())

    def get(self, module_id: str) -> Optional[ModuleManifest]:
        return self._modules.get(module_id)

    def list(self) -> list[ModuleManifest]:
        return list(self._modules.values())

    def is_compatible(self, module_id: str, app_version: str) -> bool:
        m = self.get(module_id)
        if m is None:
            return False
        return m.version >= app_version  # comparaison simplifiée


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()