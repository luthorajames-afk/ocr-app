"""T7.1/T7.2/T7.4 — Modules SHA-256, build --onedir, audit sécurité."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V2_ROOT / "src"))
sys.path.insert(0, str(V2_ROOT / "scripts"))

import build_workers
import security_audit

from core.io.modules import (
    available_modes,
    manifest_from_dir,
    scan_modules,
    sha256_file,
    verify_module,
    version_at_least,
)


def _module_dir(tmp_path: Path, *, corrupt: bool = False) -> Path:
    module = tmp_path / "modules" / "medium"
    module.mkdir(parents=True)
    worker = module / "worker_medium.exe"
    worker.write_bytes(b"fake worker binary")
    digest = hashlib.sha256(worker.read_bytes()).hexdigest()
    (module / "module.json").write_text(
        json.dumps(
            {
                "id": "medium",
                "version": "1.0.0",
                "min_app_version": "0.1.0",
                "mode": "medium",
                "worker": "worker_medium.exe",
                "sha256": digest if not corrupt else "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    return module


def test_manifest_scan_and_sha256(tmp_path: Path) -> None:
    _module_dir(tmp_path)
    manifests = scan_modules(tmp_path / "modules")
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.mode == "medium"
    assert verify_module(manifest) is True
    assert sha256_file(Path(manifest.path) / manifest.worker) == manifest.sha256


def test_corrupt_module_is_rejected(tmp_path: Path) -> None:
    _module_dir(tmp_path, corrupt=True)
    assert available_modes(tmp_path / "modules", "0.1.0") == {}


def test_manifest_rejects_traversal(tmp_path: Path) -> None:
    module = _module_dir(tmp_path)
    data = json.loads((module / "module.json").read_text(encoding="utf-8"))
    data["worker"] = "../../evil.exe"
    (module / "module.json").write_text(json.dumps(data), encoding="utf-8")
    assert manifest_from_dir(module) is None


def test_version_comparison_numeric() -> None:
    assert version_at_least("1.10.0", "1.9.0")
    assert not version_at_least("1.2.0", "1.10.0")


def test_build_command_is_onedir_and_isolated(tmp_path: Path) -> None:
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    command = build_workers.build_command("medium", fake_python)
    assert "--onedir" in command
    assert "--onefile" not in command
    assert str(fake_python) == command[0]
    with pytest.raises(ValueError):
        build_workers.enforce_onedir(["pyinstaller", "--onefile", "x.py"])


def test_security_checks_fuzz_and_bomb() -> None:
    ok, detail = security_audit.check_export_fuzzing()
    assert ok, detail
    ok, detail = security_audit.check_decompression_bomb()
    assert ok, detail


def test_installers_are_per_user_and_shared_appid() -> None:
    texts = [
        (V2_ROOT / "installers" / name).read_text(encoding="utf-8")
        for name in ("core.iss", "medium.iss", "advanced.iss")
    ]
    assert all("PrivilegesRequired=lowest" in text for text in texts)
    app_ids = {
        next(line for line in text.splitlines() if line.startswith("AppId="))
        for text in texts
    }
    assert len(app_ids) == 1
    assert all("{localappdata}" in text for text in texts)
    assert "UsePreviousAppDir=no" in texts[1] and "UsePreviousAppDir=no" in texts[2]


import pytest
