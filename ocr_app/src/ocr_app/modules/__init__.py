"""Détection et chargement des modules optionnels.

Chaque module a un fichier module.json (identifiant, version, version minimale
de l'application, mode, worker, empreintes SHA-256).
"""

from ocr_app.modules.module_manifest import ModuleManifest, ModuleRegistry

__all__ = ["ModuleManifest", "ModuleRegistry"]