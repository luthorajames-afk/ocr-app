"""T7.1 — Modules optionnels hors ligne : manifeste, SHA-256, chargement.

Un module est un dossier contenant ``module.json`` + l'exécutable worker.
Aucune découverte réseau : seuls les dossiers locaux sont scannés.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MODES = ("medium", "advanced")
KNOWN_MODES = {"medium", "advanced", "approfondi"}


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    id: str
    version: str
    min_app_version: str
    mode: str
    worker: str
    sha256: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "min_app_version": self.min_app_version,
            "mode": self.mode,
            "worker": self.worker,
            "sha256": self.sha256,
            "path": self.path,
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def version_at_least(candidate: str, minimum: str) -> bool:
    return _version_tuple(candidate) >= _version_tuple(minimum)


def manifest_from_dir(module_dir: str | Path) -> ModuleManifest | None:
    directory = Path(module_dir)
    manifest_path = directory / "module.json"
    if not manifest_path.is_file():
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        manifest = ModuleManifest(
            id=str(raw["id"]),
            version=str(raw["version"]),
            min_app_version=str(raw.get("min_app_version", "0.0.0")),
            mode=str(raw["mode"]),
            worker=str(raw["worker"]),
            sha256=str(raw["sha256"]),
            path=str(directory),
        )
    except KeyError:
        return None
    if manifest.mode not in KNOWN_MODES:
        return None
    if "/" in manifest.worker or "\\" in manifest.worker or ".." in manifest.worker:
        return None
    return manifest


def verify_module(manifest: ModuleManifest) -> bool:
    """Vérifie l'empreinte SHA-256 du worker (module corrompu -> False)."""
    worker = Path(manifest.path) / manifest.worker
    if not worker.is_file():
        return False
    return sha256_file(worker) == manifest.sha256


def scan_modules(modules_dir: str | Path) -> list[ModuleManifest]:
    root = Path(modules_dir)
    if not root.is_dir():
        return []
    manifests: list[ModuleManifest] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = manifest_from_dir(child)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def available_modes(
    modules_dir: str | Path,
    app_version: str,
) -> dict[str, ModuleManifest]:
    """Modes disponibles : manifeste valide, SHA-256 correct, version compatible."""
    result: dict[str, ModuleManifest] = {}
    for manifest in scan_modules(modules_dir):
        if not version_at_least(app_version, manifest.min_app_version):
            continue
        if not verify_module(manifest):
            continue
        result[manifest.mode] = manifest
    return result
