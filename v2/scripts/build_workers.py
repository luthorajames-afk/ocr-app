"""T7.2 — Build PyInstaller des workers (--onedir, isolation par venv).

R3 : jamais --onefile. R13 : chaque worker lourd est construit depuis son venv
dédié (AOCRI_MEDIUM_VENV / AOCRI_ADVANCED_VENV, ou .venv-eval-* voisin).

Usage :
    python scripts/build_workers.py --target simple
    python scripts/build_workers.py --target medium --venv D:\\venvs\\medium
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TARGETS: dict[str, dict[str, object]] = {
    "simple": {
        "module": "workers.worker_main",
        "venv_env": None,
        "requires": ("tesserocr",),
    },
    "medium": {
        "module": "workers.worker_main",
        "venv_env": "AOCRI_MEDIUM_VENV",
        "requires": ("rapidocr", "rapidocr_onnxruntime"),
    },
    "advanced": {
        "module": "workers.worker_main",
        "venv_env": "AOCRI_ADVANCED_VENV",
        "requires": ("docling",),
    },
}


def default_venv(target: str) -> Path | None:
    env_name = TARGETS[target]["venv_env"]
    if env_name is None:
        return None
    assert isinstance(env_name, str)
    override = os.environ.get(env_name)
    if override:
        base = Path(override)
        return base / "Scripts" / "python.exe" if base.is_dir() else base
    sibling = PROJECT.parent / "ocr_app" / f".venv-eval-{target}"
    candidate = sibling / "Scripts" / "python.exe"
    return candidate if candidate.is_file() else None


def interpreter_for(target: str, venv: Path | None = None) -> Path:
    chosen = venv or default_venv(target)
    return chosen if chosen is not None and chosen.is_file() else Path(sys.executable)


def build_command(target: str, venv: Path | None = None) -> list[str]:
    """Commande PyInstaller --onedir (aucune option --onefile possible)."""
    python = interpreter_for(target, venv)
    dist = PROJECT / "dist" / f"worker_{target}"
    work = PROJECT / "build" / f"worker_{target}"
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconfirm",
        "--name",
        f"worker_{target}",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--paths",
        str(PROJECT / "src"),
    ]
    if target == "simple":
        tessdata = PROJECT / "models" / "tessdata"
        if tessdata.is_dir():
            command += ["--add-data", f"{tessdata};models/tessdata"]
        command += ["--hidden-import", "tesserocr"]
    command.append(str(PROJECT / "src" / "workers" / "worker_main.py"))
    return command


def enforce_onedir(command: list[str]) -> None:
    """Garde-fou R3 : refuse toute commande --onefile."""
    if "--onefile" in command:
        raise ValueError("R3 : PyInstaller --onefile interdit pour AOCRI")


def build(target: str, venv: Path | None = None) -> int:
    if target not in TARGETS:
        print(f"Cible inconnue : {target}", file=sys.stderr)
        return 1
    command = build_command(target, venv)
    enforce_onedir(command)
    print(f"Build {target} avec {command[0]}...")
    return subprocess.call(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build workers AOCRI (--onedir)")
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--venv", default=None, help="python.exe du venv du module")
    args = parser.parse_args(argv)
    return build(args.target, Path(args.venv) if args.venv else None)


if __name__ == "__main__":
    raise SystemExit(main())
