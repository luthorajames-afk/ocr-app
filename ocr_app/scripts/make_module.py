"""Génération du manifeste module.json après build (phase 7).

Prépare le dossier de distribution d'un module optionnel (medium/advanced,
R13/R15) et écrit le manifeste réel consommé par ``ModuleRegistry`` :

1. copie le worker (exécutable PyInstaller --onedir, R3) dans le dossier du
   module (défaut : ``dist/modules/<module>``) ;
2. copie les modèles si ``--models`` est fourni ;
3. calcule le sha256 du worker via
   ``ocr_app.modules.module_manifest.sha256_file`` ;
4. écrit ``module.json`` : id, version, min_app_version, mode, worker, sha256.

Usage :

    python scripts/make_module.py --module medium \
        --worker dist/worker_medium/worker_medium.exe --version 1.0.0

Un ``module.json`` existant n'est jamais écrasé silencieusement : ajoutez
``--force`` pour le remplacer (montée de version explicite, R15).

Vérification ensuite possible :

    python -c "from ocr_app.modules.module_manifest import ModuleRegistry; \
               m = ModuleRegistry('dist/modules'); print(m.scan())"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# Import du paquet installé (editable) : aucune logique dupliquée.
from ocr_app.modules.module_manifest import sha256_file

MODULES = ("medium", "advanced")

DEFAULT_MIN_APP_VERSION = "1.0.0"

# Version lisible par module_manifest (comparaison numérique 1.10 > 1.9) :
# composants numériques séparés par des points, préfixe v/V et suffixe de
# pré-version tolérés (ex. 1.0.0, v2, 1.2.0-beta).
_VERSION_RE = re.compile(r"^[vV]?\d+(\.\d+)*([-+][\w.]+)?$")


def _validate_version(label: str, value: str) -> Optional[str]:
    """Message d'erreur si la version n'est pas exploitable, sinon None."""
    if not _VERSION_RE.match(value):
        return (
            f"{label} invalide : {value!r} — format attendu X[.Y[.Z]] "
            "(composants numériques, ex. 1.0.0)."
        )
    return None


def _fail(message: str) -> int:
    print(f"make_module : {message}", file=sys.stderr)
    return 1


def make_module(
    module: str,
    worker: Path,
    version: str,
    *,
    output_dir: Optional[Path] = None,
    min_app_version: str = DEFAULT_MIN_APP_VERSION,
    models_dir: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """Prépare le dossier du module et écrit module.json ; retourne son chemin."""
    module = module.strip().lower()
    if module not in MODULES:
        raise ValueError(
            f"module inconnu : {module!r} (modules attendus : {', '.join(MODULES)}). "
            "Le noyau (mode simple) est livré par installers/core.iss, sans module.json."
        )
    if not worker.is_file():
        raise FileNotFoundError(f"worker introuvable : {worker}")
    version = str(version).strip()
    if not version:
        raise ValueError("version vide")
    invalid = _validate_version("--version", version)
    if invalid:
        raise ValueError(invalid)
    min_app_version = str(min_app_version).strip() or DEFAULT_MIN_APP_VERSION
    invalid = _validate_version("--min-app-version", min_app_version)
    if invalid:
        raise ValueError(invalid)

    target_dir = Path(output_dir) if output_dir else Path("dist") / "modules" / module
    manifest_path = target_dir / "module.json"
    # Pas d'écrasement silencieux d'un manifeste existant : une montée de
    # version doit être un geste explicite (R15). Vérifié AVANT toute copie
    # pour ne pas modifier le dossier cible à moitié en cas de refus.
    if manifest_path.exists() and not force:
        raise ValueError(
            f"module.json existe déjà : {manifest_path} — relancez avec --force "
            "pour le remplacer explicitement (montée de version R15)."
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copie du worker (sauf s'il est déjà dans le dossier cible).
    worker_name = worker.name
    target_worker = target_dir / worker_name
    if worker.resolve() != target_worker.resolve():
        shutil.copy2(worker, target_worker)

    # Modèles optionnels du module (R4 : livrés, jamais téléchargés).
    if models_dir is not None:
        models_dir = Path(models_dir)
        if not models_dir.is_dir():
            raise FileNotFoundError(f"dossier de modèles introuvable : {models_dir}")
        target_models = target_dir / "models"
        if models_dir.resolve() != target_models.resolve():
            if target_models.exists():
                shutil.rmtree(target_models)
            shutil.copytree(models_dir, target_models)

    sha256 = sha256_file(target_worker)
    manifest = {
        "id": module,
        "version": version,
        "min_app_version": min_app_version,
        "mode": module,
        "worker": worker_name,
        "sha256": sha256,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/make_module.py",
        description=(
            "Génère module.json (sha256 réel du worker) après build PyInstaller, "
            "pour installation via installers/medium.iss ou advanced.iss."
        ),
    )
    parser.add_argument(
        "--module",
        required=True,
        choices=MODULES,
        help="module à préparer (medium ou advanced)",
    )
    parser.add_argument(
        "--worker",
        required=True,
        type=Path,
        help="chemin du worker construit (ex. dist/worker_medium/worker_medium.exe)",
    )
    parser.add_argument(
        "--version",
        default="1.0.0",
        help="version du module (défaut : 1.0.0)",
    )
    parser.add_argument(
        "--min-app-version",
        default=DEFAULT_MIN_APP_VERSION,
        help=f"version minimale de l'application (défaut : {DEFAULT_MIN_APP_VERSION})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="dossier du module (défaut : dist/modules/<module>)",
    )
    parser.add_argument(
        "--models",
        type=Path,
        default=None,
        help="dossier de modèles à embarquer (optionnel, copié vers <output>/models)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="remplacer un module.json existant (montée de version R15, geste explicite)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest_path = make_module(
            args.module,
            args.worker,
            args.version,
            output_dir=args.output_dir,
            min_app_version=args.min_app_version,
            models_dir=args.models,
            force=args.force,
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        return _fail(str(exc))

    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"module.json écrit : {manifest_path}")
    print(f"  id={data['id']}  version={data['version']}  mode={data['mode']}")
    print(f"  worker={data['worker']}")
    print(f"  sha256={data['sha256']}")
    print(
        "Vérification : ModuleRegistry("
        f"{manifest_path.parent.parent.as_posix()!r}).scan()"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
