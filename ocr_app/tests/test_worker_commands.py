"""Tests de la résolution des commandes worker par mode (phase 7).

Headless. Deux familles :

1. fonctions pures de ``ui.worker_commands`` / ``ui.modules_info`` :
   - sans module installé : repli ``python -m ocr_app.workers.worker_main
     --mode <mode>`` ;
   - avec un manifeste ``module.json`` factice + un exécutable factice dans un
     dossier de modules temporaire (surcharge ``AOCRI_MODULES_DIR``) :
     commande = exécutable du module avec ``--mode`` ;
   - module corrompu (sha256 faux) : repli + mode « non installé » ;
   - mode inconnu : message clair ; ``simple`` jamais « non disponible » ;
2. ``TaskRunner`` hors écran (``QT_QPA_PLATFORM=offscreen`` posé avant tout
   import de PySide6) : une tâche d'un mode dont le module n'est pas installé
   passe en ERROR sans qu'aucun worker soit lancé.

Les tests qui dépendent de l'absence des paquets de module sont ignorés si
ceux-ci sont pourtant importables dans l'environnement courant.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="modules_info passe par QStandardPaths")

from ocr_app.core.task_queue import TaskStatus
from ocr_app.ui.modules_info import detect_modules, registry_dirs
from ocr_app.ui.worker_commands import (
    WORKER_MAIN_MODULE,
    accepts_input_document,
    resolve_worker_commands,
    unavailable_message,
    worker_main_command,
)


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


MEDIUM_IMPORTABLE = _importable("rapidocr") or _importable("rapidocr_onnxruntime")
ADVANCED_IMPORTABLE = _importable("docling")

# Si le paquet du module est importable, le mode devient disponible sans
# manifeste : les prémisses « non installé » ne tiennent plus.
requires_medium_absent = pytest.mark.skipif(
    MEDIUM_IMPORTABLE, reason="paquet RapidOCR importable : medium serait « installé »"
)
requires_advanced_absent = pytest.mark.skipif(
    ADVANCED_IMPORTABLE, reason="paquet Docling importable : advanced serait « installé »"
)


def _write_fake_module(
    root: Path,
    mode: str,
    *,
    sha256: str = None,
    version: str = "1.0.0",
) -> Path:
    """module.json factice + exécutable factice dans ``root/<mode>/``.

    Retourne le chemin de l'exécutable. Sans ``sha256``, l'empreinte correcte
    du fichier écrit est calculée (module « vérifiable »).
    """
    module_dir = root / mode
    module_dir.mkdir(parents=True, exist_ok=True)
    exe = module_dir / f"worker_{mode}.exe"
    exe.write_bytes(b"MZ contenu factice du worker \xe9\xa7")
    digest = sha256 if sha256 is not None else hashlib.sha256(exe.read_bytes()).hexdigest()
    (module_dir / "module.json").write_text(
        json.dumps(
            {
                "id": mode,
                "version": version,
                "min_app_version": "1.0.0",
                "mode": mode,
                "worker": exe.name,
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return exe


# --- Repli développement ------------------------------------------------------

def test_fallback_python_m_without_any_module(tmp_path: Path) -> None:
    commands, unavailable = resolve_worker_commands(modules_dir=str(tmp_path / "vide"))

    # simple : toujours disponible, commande python -m en développement.
    assert commands["simple"] == (
        sys.executable,
        "-u",
        "-m",
        WORKER_MAIN_MODULE,
        "--mode",
        "simple",
    )
    assert "simple" not in unavailable, "simple (noyau) n'est jamais « non installé »"

    if not MEDIUM_IMPORTABLE:
        assert "medium" not in commands
        message = unavailable["medium"]
        assert "non installé" in message
        assert "modules/medium" in message, "le message indique le dossier attendu"
        assert "Moyen" in message, "le libellé du mode est cité"
    if not ADVANCED_IMPORTABLE:
        assert "advanced" not in commands
        assert "non installé" in unavailable["advanced"]


def test_worker_main_command_carries_the_mode() -> None:
    for mode in ("simple", "medium", "advanced"):
        command = worker_main_command(mode)
        assert command == (
            sys.executable,
            "-u",
            "-m",
            WORKER_MAIN_MODULE,
            "--mode",
            mode,
        ), f"--mode {mode} doit toujours être passé explicitement"


# --- Module installé (surcharge AOCRI_MODULES_DIR) ----------------------------

def test_env_override_dir_exposes_module_exe(tmp_path: Path, monkeypatch) -> None:
    modules_root = tmp_path / "modules externe éàç"
    modules_root.mkdir()
    exe = _write_fake_module(modules_root, "medium")

    monkeypatch.setenv("AOCRI_MODULES_DIR", str(modules_root))
    assert modules_root in registry_dirs(), "la surcharge env est scannée en premier"

    infos = {info.key: info for info in detect_modules()}
    assert infos["medium"].installed, "manifeste factice vérifié → module installé"
    assert "module.json" in infos["medium"].detail
    assert str(modules_root) in infos["medium"].detail

    commands, unavailable = resolve_worker_commands()
    assert commands["medium"] == (str(exe), "--mode", "medium")
    assert "medium" not in unavailable


def test_manifest_command_accepted_for_input_document(tmp_path: Path) -> None:
    exe = _write_fake_module(tmp_path / "mods", "medium")
    commands, _ = resolve_worker_commands(modules_dir=str(tmp_path / "mods"))
    assert commands["medium"] == (str(exe), "--mode", "medium")
    # Un exécutable packagé (build de worker_main) accepte --input.
    assert accepts_input_document((str(exe), "--mode", "medium")) is True
    assert accepts_input_document(worker_main_command("simple")) is True
    assert accepts_input_document(None) is False
    assert accepts_input_document(()) is False


# --- Module corrompu ----------------------------------------------------------

@requires_medium_absent
def test_corrupt_module_falls_back_and_is_unavailable(tmp_path: Path) -> None:
    _write_fake_module(tmp_path / "mods", "medium", sha256="de" * 32)
    commands, unavailable = resolve_worker_commands(modules_dir=str(tmp_path / "mods"))

    assert "medium" not in commands, "sha256 faux : l'exécutable n'est jamais utilisé"
    assert "non installé" in unavailable["medium"]

    infos = {info.key: info for info in detect_modules(modules_dir=str(tmp_path / "mods"))}
    assert not infos["medium"].installed


@requires_medium_absent
def test_manifest_traversal_worker_is_never_resolved(tmp_path: Path) -> None:
    """Un manifeste qui fait sortir le worker du dossier du module est ignoré."""
    exe = _write_fake_module(tmp_path / "mods", "medium")
    manifest_path = tmp_path / "mods" / "medium" / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["worker"] = f"../../{exe.name}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    commands, unavailable = resolve_worker_commands(modules_dir=str(tmp_path / "mods"))
    assert "medium" not in commands, "traversal : commande refusée"
    assert "non installé" in unavailable["medium"]


# --- Modes et messages --------------------------------------------------------

def test_unknown_mode_gets_clear_message_and_no_command() -> None:
    message = unavailable_message("bogus")
    assert "bogus" in message
    assert "non installé" in message, "le message reste clair pour un mode inconnu"

    commands, unavailable = resolve_worker_commands(modules_dir=None)
    assert "bogus" not in commands
    assert "bogus" not in unavailable
    assert set(commands) | set(unavailable) == {"simple", "medium", "advanced"}


def test_simple_is_never_unavailable(tmp_path: Path) -> None:
    for modules_dir in (str(tmp_path / "vide"), str(tmp_path / "autres éàç")):
        Path(modules_dir).mkdir(parents=True, exist_ok=True)
        commands, unavailable = resolve_worker_commands(modules_dir=modules_dir)
        assert "simple" in commands
        assert "simple" not in unavailable
        assert commands["simple"][-2:] == ("--mode", "simple")


# --- TaskRunner (hors écran) ---------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@requires_medium_absent
def test_task_runner_medium_unavailable_errors_without_launching(
    tmp_path: Path, qapp
) -> None:
    from ocr_app.ui.task_runner import TaskRunner

    commands, unavailable = resolve_worker_commands(modules_dir=str(tmp_path / "vide"))
    assert "medium" in unavailable, "prémisse : module Moyen non installé"

    runner = TaskRunner(worker_commands=commands, unavailable_modes=unavailable)
    try:
        errors: list[str] = []
        runner.error_occurred.connect(lambda task_id, message: errors.append(message))

        source = tmp_path / "source medium éàç.pdf"
        source.write_bytes(b"%PDF-1.7 fake")
        task = runner.add_task(str(source), mode="medium", total_pages=1)

        assert task.status == TaskStatus.ERROR
        assert task.error_message == unavailable["medium"], "message clair conservé"
        assert errors == [unavailable["medium"]]
        assert runner.current_task_id is None, "aucune tâche n'a pris le worker"
        assert not runner._worker.is_running, "aucun worker lancé"
        assert task.result_dir is None, "aucun dossier de travail alloué (pas de lancement)"
    finally:
        runner.shutdown()


def test_task_runner_still_accepts_simple_mode(tmp_path: Path, qapp) -> None:
    """Le mode simple reste intégrable : sa commande est bien celle du noyau."""
    from ocr_app.ui.task_runner import TaskRunner

    commands, unavailable = resolve_worker_commands(modules_dir=str(tmp_path / "vide"))
    assert "simple" in commands

    runner = TaskRunner(worker_commands=commands, unavailable_modes=unavailable)
    try:
        task = runner.create_task(str(tmp_path / "f.pdf"), mode="simple", total_pages=1)
        assert task.status == TaskStatus.PENDING
        runner.shutdown()  # ne démarre pas la tâche : aucun worker ici
    finally:
        runner.shutdown()
