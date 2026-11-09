"""Mapping mode OCR → commande du worker réel (phase 7).

La fenêtre n'instancie jamais un moteur (R2) : chaque mode est exécuté dans un
sous-processus lancé par ``WorkerClient``. La commande résolue par mode est, par
ordre de préférence :

1. l'exécutable packagé du module (manifeste ``module.json`` vérifié par
   ``ModuleRegistry`` — sha256 + compatibilité, R13/R15), avec ``--mode`` ;
2. pour le mode Avancé, le venv d'évaluation dédié ``.venv-eval-advanced``
   (Python 3.12 + Docling, R1/R13) : ``<venv>/Scripts/python.exe -u -m
   ocr_app.workers.worker_main --mode advanced`` — le paquet ``ocr_app`` y est
   installé en mode éditable sans dépendances (``pip install -e . --no-deps``)
   et Docling y est déjà présent avec ses modèles en cache local (R4) ;
3. en application gelée (PyInstaller), l'exécutable du noyau installé à côté
   de l'interface (``<dossier de l'exe>/workers/worker_<mode>/``) ;
4. en développement, ``python -m ocr_app.workers.worker_main --mode <mode>``.

La disponibilité d'un mode est déterminée par ``modules_info.detect_modules``
(import dans le venv de développement, venv d'évaluation pour Avancé, ou
manifeste vérifié) :

- ``simple``   : noyau, toujours disponible ;
- ``medium`` / ``advanced`` : disponibles seulement si leur moteur est
  résolvable (module installé ou venv d'évaluation pour Avancé), sinon un
  message clair « module non installé » est retourné pour la file de tâches
  (aucun worker lancé).

``resolve_worker_commands`` retourne le couple
``(commandes disponibles, messages d'indisponibilité)`` consommé par le
``TaskRunner`` ; une commande absente sans message d'indisponibilité conserve
le repli historique sur le worker factice de développement (``fake_worker``),
utilisé par les tests de protocole.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from ocr_app.ui.modules_info import detect_modules, find_installed_module
from ocr_app.ui.task_format import mode_label

WORKER_MAIN_MODULE = "ocr_app.workers.worker_main"

# Modes exposés à l'interface, dans l'ordre d'affichage.
MODES: Tuple[str, ...] = ("simple", "medium", "advanced")

# Clé de module correspondant à chaque mode (core = noyau = mode simple).
_MODE_TO_MODULE_KEY = {"simple": "core", "medium": "medium", "advanced": "advanced"}

# Venv d'évaluation du mode Avancé (R1/R13) : Docling + modèles en cache,
# installé à côté du dépôt. Surchargeable pour les tests/machines atypiques.
ADVANCED_VENV_DIRNAME = ".venv-eval-advanced"
ADVANCED_VENV_ENV_VAR = "AOCRI_ADVANCED_VENV"


def worker_main_command(mode: str) -> Tuple[str, ...]:
    """Commande du worker principal en développement (interpréteur courant)."""
    mode = str(mode).strip().lower()
    return (sys.executable, "-u", "-m", WORKER_MAIN_MODULE, "--mode", mode)


def _repository_root() -> Path:
    """Racine du dépôt en développement (src/ocr_app/ui → parents[3])."""
    return Path(__file__).resolve().parents[3]


def _venv_python(venv_dir: Path) -> Optional[Path]:
    """Interpréteur d'un venv (layout Windows ``Scripts/`` ou POSIX ``bin/``)."""
    for relative in (
        Path("Scripts") / "python.exe",
        Path("Scripts") / "python",
        Path("bin") / "python.exe",
        Path("bin") / "python",
    ):
        candidate = venv_dir / relative
        if candidate.is_file():
            return candidate
    return None


def _venv_has_docling(venv_dir: Path) -> bool:
    """Vrai si le paquet Docling est présent dans le venv (contrôle local).

    Vérification par le système de fichiers, sans sous-processus ni import
    (R2 : aucun moteur chargé dans l'interface) : on cherche ``docling/``
    dans les site-packages du venv (layout Windows ``Lib/site-packages`` ou
    POSIX ``lib/pythonX.Y/site-packages``).
    """
    candidates = [venv_dir / "Lib" / "site-packages"]
    lib = venv_dir / "lib"
    try:
        if lib.is_dir():
            candidates.extend(
                child / "site-packages" for child in sorted(lib.iterdir()) if child.is_dir()
            )
    except OSError:  # pragma: no cover - arborescence illisible
        pass
    for site_packages in candidates:
        if (site_packages / "docling" / "__init__.py").is_file():
            return True
    return False


def advanced_venv_command() -> Optional[Tuple[str, ...]]:
    """Commande worker Avancé via le venv d'évaluation, si utilisable.

    Retourne ``(<venv>/python.exe, -u, -m worker_main, --mode, advanced)``
    quand le venv existe et contient Docling ; ``None`` sinon (le mode reste
    alors « non installé » sauf manifeste/module importable).

    Le venv d'évaluation est un artefact ambiant de la machine de
    développement : il n'est consulté que lorsqu'aucun dossier de modules
    explicite n'est fourni (``modules_dir is None``, chemin de l'interface).
    Un appel avec ``modules_dir`` évalue la disponibilité restreinte à ce
    registre — sémantique utilisée par les tests et les installations
    portables.
    """
    override = os.environ.get(ADVANCED_VENV_ENV_VAR, "").strip()
    venv_dir = Path(override) if override else _repository_root() / ADVANCED_VENV_DIRNAME
    if not override and not venv_dir.is_dir():
        return None
    python = _venv_python(venv_dir)
    if python is None or not _venv_has_docling(venv_dir):
        return None
    return (str(python), "-u", "-m", WORKER_MAIN_MODULE, "--mode", "advanced")


def _core_worker_command(mode: str) -> Optional[Tuple[str, ...]]:
    """Exécutable worker du noyau installé à côté de l'application gelée.

    Layout livré par ``installers/core.iss`` : ``<app>\\workers\\worker_<mode>\\
    worker_<mode>.exe``. Les exécutables étant des builds de ``worker_main``,
    le mode leur est passé explicitement (``--mode`` ; défaut de l'exe :
    « simple », donc jamais implicite).
    """
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    exe = exe_dir / "workers" / f"worker_{mode}" / f"worker_{mode}.exe"
    if exe.is_file():
        return (str(exe), "--mode", mode)
    return None


def _manifest_worker_command(
    mode: str, modules_dir: Optional[str]
) -> Optional[Tuple[str, ...]]:
    """Exécutable d'un module installé (manifeste vérifié), avec ``--mode``."""
    manifest = find_installed_module(mode, modules_dir=modules_dir)
    if manifest is None:
        return None
    exe = Path(manifest.path or "") / manifest.worker
    if exe.is_file():
        return (str(exe), "--mode", mode)
    return None


def accepts_input_document(command: Optional[Sequence[str]]) -> bool:
    """Vrai si la commande désigne un worker qui traite un document réel.

    ``worker_main`` (module ou exécutable packagé — tous les exécutables
    produits par ``scripts/build.py`` en dérivent) accepte ``--input`` ;
    ``fake_worker`` ne le connaît pas : le ``TaskRunner`` ne doit passer le
    document d'entrée qu'aux workers qui le traitent réellement.
    """
    if not command:
        return False
    parts = tuple(str(part) for part in command)
    if WORKER_MAIN_MODULE in parts:
        return True
    # Exécutable packagé : worker_<mode>.exe livré par build.py/core.iss.
    first = parts[0]
    return first.lower().endswith(".exe") and Path(first).is_file()


def unavailable_message(mode: str) -> str:
    """Message clair affiché quand un module du mode n'est pas installé."""
    label = mode_label(mode)
    return (
        f"Module « {label} » non installé — ajoutez le module "
        f"« {mode} » (dossier modules/{mode}) pour activer ce mode."
    )


def resolve_worker_commands(
    modules_dir: Optional[str] = None,
) -> Tuple[Dict[str, Tuple[str, ...]], Dict[str, str]]:
    """Résout la commande worker par mode disponible.

    Retourne un couple :

    - ``commands``      : mode → commande du worker (séquence d'arguments) ;
    - ``unavailable``   : mode → message « module non installé ».

    Le mode simple est toujours disponible (noyau). Le mode Avancé est
    résolu vers le venv d'évaluation ``.venv-eval-advanced`` quand il existe
    (Docling + modèles en cache, R1/R13/R4) — uniquement en résolution non
    scopée (``modules_dir is None``), cf. ``advanced_venv_command``. Un mode
    sans commande et sans message conserve le repli ``fake_worker`` du
    développement.
    """
    commands: Dict[str, Tuple[str, ...]] = {}
    unavailable: Dict[str, str] = {}

    infos = {info.key: info for info in detect_modules(modules_dir=modules_dir)}
    for mode in MODES:
        key = _MODE_TO_MODULE_KEY.get(mode, mode)
        if mode == "simple":
            # Noyau : toujours disponible (R1 — tesserocr léger, pas de
            # PyTorch). Exécutable installé si l'app est gelée, sinon dev.
            commands[mode] = _core_worker_command(mode) or worker_main_command(mode)
            continue
        # Module optionnel : exécutable du manifeste vérifié d'abord, puis
        # venv d'évaluation (mode Avancé, Docling — R1/R13, uniquement en
        # résolution non scoping), puis commande de développement si le
        # paquet est importable, sinon absent.
        installed_command = _manifest_worker_command(mode, modules_dir)
        if installed_command is None and mode == "advanced" and modules_dir is None:
            installed_command = advanced_venv_command()
        if installed_command is None:
            info = infos.get(key)
            if info is not None and info.installed:
                installed_command = worker_main_command(mode)
        if installed_command is not None:
            commands[mode] = installed_command
        else:
            unavailable[mode] = unavailable_message(mode)
    return commands, unavailable
