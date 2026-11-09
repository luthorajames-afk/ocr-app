"""Tests du packaging des modules et du build PyInstaller (phase 7).

Headless, sans build réel :

- ``scripts/make_module.py`` : module.json avec sha256 correct (recalculé et
  comparé), ``--force`` nécessaire pour écraser, version invalide rejetée,
  nom de module hors liste refusé (pas de path traversal par le nom),
  champ ``worker`` toujours un simple nom de fichier ;
- ``ModuleRegistry`` (``ocr_app.modules.module_manifest``) : scan d'un dossier
  temporaire → module trouvé, ``verify_sha256()`` vrai ; après modification du
  worker → faux ; manifeste invalide ignoré ; ``worker`` traversant refusé ;
- ``scripts/build.py`` : parsing propre des arguments (``--onefile`` absent et
  refusé), validation des dépendances medium/advanced (refus explicite sans
  lancer PyInstaller), commande de build strictement ``--onedir`` (R3),
  pré-vérification tessdata (R4).

Les scripts sont chargés par chemin (``scripts/`` n'est pas un paquet).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"aocri_t7_{name}", SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_module = _load_script("make_module")
build = _load_script("build")


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


MEDIUM_IMPORTABLE = _importable("rapidocr") or _importable("rapidocr_onnxruntime")
ADVANCED_IMPORTABLE = _importable("docling")

requires_medium_absent = pytest.mark.skipif(
    MEDIUM_IMPORTABLE, reason="paquet RapidOCR importable : le refus ne s'applique pas"
)
requires_advanced_absent = pytest.mark.skipif(
    ADVANCED_IMPORTABLE, reason="paquet Docling importable : le refus ne s'applique pas"
)


def _fake_worker(directory: Path, name: str = "worker_medium.exe", payload: bytes = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    worker = directory / name
    worker.write_bytes(payload if payload is not None else b"MZ worker factice \xe9\xa7")
    return worker


# --- make_module : module.json ------------------------------------------------

def test_module_json_sha256_matches_recomputed(tmp_path: Path) -> None:
    worker = _fake_worker(tmp_path / "inputs")
    output = tmp_path / "modules" / "medium"

    manifest_path = make_module.make_module("medium", worker, "1.0.0", output_dir=output)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert data["id"] == "medium"
    assert data["mode"] == "medium"
    assert data["version"] == "1.0.0"
    assert data["min_app_version"] == "1.0.0"
    assert data["worker"] == "worker_medium.exe"

    # Recalcul indépendant du sha256 du worker copié dans le dossier du module.
    copied = output / "worker_medium.exe"
    expected = hashlib.sha256(copied.read_bytes()).hexdigest()
    assert data["sha256"] == expected


def test_worker_field_is_bare_filename(tmp_path: Path) -> None:
    worker = _fake_worker(tmp_path / "inputs", name="worker_advanced.exe")
    manifest_path = make_module.make_module(
        "advanced", worker, "1.0.0", output_dir=tmp_path / "modules" / "advanced"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["worker"] == "worker_advanced.exe"
    assert "/" not in data["worker"] and "\\" not in data["worker"]


def test_overwrite_requires_force(tmp_path: Path, capsys) -> None:
    worker = _fake_worker(tmp_path / "inputs")
    output = tmp_path / "modules" / "medium"
    make_module.make_module("medium", worker, "1.0.0", output_dir=output)

    # Sans --force : refus explicite, dossier cible inchangé.
    with pytest.raises(ValueError, match="--force"):
        make_module.make_module("medium", worker, "1.1.0", output_dir=output)
    data = json.loads((output / "module.json").read_text(encoding="utf-8"))
    assert data["version"] == "1.0.0", "écrasement silencieux interdit"

    # En CLI : exit 1 avec message explicite.
    code = make_module.main(
        ["--module", "medium", "--worker", str(worker), "--version", "1.1.0",
         "--output-dir", str(output)]
    )
    assert code == 1
    assert "--force" in capsys.readouterr().err

    # Avec --force : remplacement explicite.
    manifest_path = make_module.make_module(
        "medium", worker, "1.1.0", output_dir=output, force=True
    )
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == "1.1.0"


def test_invalid_version_rejected(tmp_path: Path, capsys) -> None:
    worker = _fake_worker(tmp_path / "inputs")
    with pytest.raises(ValueError, match="invalide"):
        make_module.make_module(
            "medium", worker, "abc", output_dir=tmp_path / "v1" / "medium"
        )
    with pytest.raises(ValueError, match="invalide"):
        make_module.make_module(
            "medium", worker, "1.0.0", min_app_version="pas.une.version",
            output_dir=tmp_path / "v2" / "medium",
        )
    # En CLI : exit 1.
    assert make_module.main(
        ["--module", "medium", "--worker", str(worker), "--version", "abc",
         "--output-dir", str(tmp_path / "v3" / "medium")]
    ) == 1
    assert "invalide" in capsys.readouterr().err


def test_unknown_module_name_rejected(tmp_path: Path) -> None:
    worker = _fake_worker(tmp_path / "inputs")
    with pytest.raises(ValueError, match="core"):
        make_module.make_module("core", worker, "1.0.0", output_dir=tmp_path / "c")
    # Un nom de module avec séparateurs ne peut pas servir de traversée.
    with pytest.raises(ValueError, match="module inconnu"):
        make_module.make_module("medium\\..\\..\\evil", worker, "1.0.0")
    with pytest.raises(ValueError, match="module inconnu"):
        make_module.make_module("../../evil", worker, "1.0.0")


def test_missing_worker_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="introuvable"):
        make_module.make_module(
            "medium", tmp_path / "nope.exe", "1.0.0",
            output_dir=tmp_path / "modules" / "medium",
        )


# --- ModuleRegistry : scan et vérification ------------------------------------

def _prepare_module(root: Path) -> Path:
    worker = _fake_worker(root / "inputs")
    make_module.make_module("medium", worker, "1.0.0", output_dir=root / "modules" / "medium")
    return root / "modules"


def test_registry_scan_finds_module_and_verifies(tmp_path: Path) -> None:
    from ocr_app.modules.module_manifest import ModuleRegistry

    modules_root = _prepare_module(tmp_path)
    registry = ModuleRegistry(modules_root)

    manifests = registry.scan()
    assert [m.id for m in manifests] == ["medium"]
    manifest = registry.get("medium")
    assert manifest.version == "1.0.0"
    assert manifest.worker == "worker_medium.exe"
    assert manifest.verify_sha256() is True


def test_registry_verify_fails_after_worker_modified(tmp_path: Path) -> None:
    from ocr_app.modules.module_manifest import ModuleRegistry

    modules_root = _prepare_module(tmp_path)
    registry = ModuleRegistry(modules_root)
    assert registry.scan()[0].verify_sha256() is True

    worker = modules_root / "medium" / "worker_medium.exe"
    worker.write_bytes(b"contenu corrompu ou remplace")

    assert registry.scan()[0].verify_sha256() is False, "module corrompu détecté"


def test_registry_ignores_invalid_manifest_but_keeps_others(tmp_path: Path) -> None:
    from ocr_app.modules.module_manifest import ModuleRegistry

    modules_root = _prepare_module(tmp_path)
    # Module voisin illisible : JSON cassé puis champs manquants.
    broken = modules_root / "broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "module.json").write_text("{ pas du json", encoding="utf-8")
    empty = modules_root / "empty_fields"
    empty.mkdir(parents=True, exist_ok=True)
    (empty / "module.json").write_text(json.dumps({"id": "medium"}), encoding="utf-8")

    manifests = ModuleRegistry(modules_root).scan()
    assert [m.id for m in manifests] == ["medium"], "les modules valides restent détectés"


def test_manifest_with_traversal_worker_fails_verification(tmp_path: Path) -> None:
    from ocr_app.modules.module_manifest import ModuleManifest, ModuleRegistry

    outside = _fake_worker(tmp_path / "inputs")
    module_dir = tmp_path / "evil" / "medium"
    module_dir.mkdir(parents=True)
    (module_dir / "module.json").write_text(
        json.dumps(
            {
                "id": "medium",
                "version": "1.0.0",
                "min_app_version": "1.0.0",
                "mode": "medium",
                "worker": f"../../{outside.name}",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    manifest = ModuleRegistry(tmp_path / "evil").scan()[0]
    assert manifest.worker.startswith("..")
    assert manifest.verify_sha256() is False, "path traversal impossible"

    direct = ModuleManifest.from_dict(manifest.to_dict())
    assert direct.verify_sha256() is False


# --- scripts/build.py ---------------------------------------------------------

def test_simple_worker_has_no_dependency_gate() -> None:
    assert build.check_module_dependencies("simple") is None


@requires_medium_absent
def test_medium_refused_without_dependencies(monkeypatch) -> None:
    message = build.check_module_dependencies("medium")
    assert message is not None
    assert "Dépendances absentes" in message
    assert "rapidocr" in message
    assert "venv" in message, "le message oriente vers le venv du module (R13)"

    # Aucun PyInstaller invoqué : le refus précède tout lancement.
    def _no_launch(*args, **kwargs):
        raise AssertionError("PyInstaller ne doit pas être invoqué pour medium")

    monkeypatch.setattr(build.subprocess, "call", _no_launch)
    monkeypatch.setattr(build.subprocess, "run", _no_launch)
    assert build.build("medium") == 1


@requires_advanced_absent
def test_advanced_refused_without_dependencies(monkeypatch) -> None:
    message = build.check_module_dependencies("advanced")
    assert message is not None
    assert "docling" in message
    assert "R1" in message or "R13" in message

    def _no_launch(*args, **kwargs):
        raise AssertionError("PyInstaller ne doit pas être invoqué pour advanced")

    monkeypatch.setattr(build.subprocess, "call", _no_launch)
    monkeypatch.setattr(build.subprocess, "run", _no_launch)
    assert build.build("advanced") == 1


def test_build_refuses_unknown_worker() -> None:
    assert build.build("onefile") == 1
    assert build.build("bogus") == 1


def test_parser_is_clean_and_has_no_onefile(monkeypatch) -> None:
    # Le parsing est inline dans main() ; on remplace build() pour vérifier
    # le dispatch sans jamais invoquer PyInstaller.
    seen: dict = {}

    def _fake_build(worker: str, sign: bool = False) -> int:
        seen["worker"] = worker
        seen["sign"] = sign
        return 0

    monkeypatch.setattr(build, "build", _fake_build)

    assert build.main(["--worker", "simple"]) == 0
    assert seen == {"worker": "simple", "sign": False}
    assert build.main(["--worker", "advanced", "--sign"]) == 0
    assert seen == {"worker": "advanced", "sign": True}

    # --onefile n'existe pas (R3) : erreur argparse propre (exit 2), avant
    # tout appel à build().
    with pytest.raises(SystemExit) as exc:
        build.main(["--worker", "simple", "--onefile"])
    assert exc.value.code == 2
    assert "worker" not in seen or seen["worker"] == "advanced"

    # « onefile » n'est pas un choix de --worker non plus.
    with pytest.raises(SystemExit) as exc:
        build.main(["--worker", "onefile"])
    assert exc.value.code == 2


def test_build_command_is_strictly_onedir() -> None:
    for worker in build.WORKERS:
        command = build.build_command(worker)
        assert "--onedir" in command, f"R3 : {worker} en --onedir"
        assert "--onefile" not in command, f"R3 : jamais --onefile pour {worker}"
        assert command[-1].endswith("worker_main.py"), "point d'entrée unique worker_main"
        assert "--name" in command and f"worker_{worker}" in command


def test_worker_exe_layout_is_onedir() -> None:
    exe = build.worker_exe("simple")
    assert exe == (
        PROJECT_ROOT / "dist" / "worker_simple" / "worker_simple" / "worker_simple.exe"
    ), "layout --onedir : dist/worker_<mode>/worker_<mode>/worker_<mode>.exe"


def test_check_tessdata(tmp_path: Path, monkeypatch) -> None:
    # Dans ce dépôt, la tessdata existe avec des traineddata (R4).
    assert build.check_tessdata() is None

    # Racine vide : dossier entier absent.
    monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path / "vide")
    message = build.check_tessdata()
    assert message is not None
    assert "tessdata" in message

    # Dossier présent mais sans traineddata.
    empty = tmp_path / "sans_langues" / "models" / "tessdata"
    empty.mkdir(parents=True)
    monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path / "sans_langues")
    message = build.check_tessdata()
    assert message is not None
    assert "traineddata" in message
