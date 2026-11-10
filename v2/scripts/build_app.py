"""Build de l'application AOCRI v2 (PyInstaller --onedir, fenêtrée).

Sortie : dist/OCRApp/OCRApp.exe (contenu attendu par installers/core.iss).

Usage : python scripts/build_app.py [--sign]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def build_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconfirm",
        "--windowed",
        "--name",
        "OCRApp",
        "--distpath",
        str(PROJECT / "dist" / "_app_raw"),
        "--workpath",
        str(PROJECT / "build" / "app"),
        "--specpath",
        str(PROJECT / "build" / "app"),
        "--paths",
        str(PROJECT / "src"),
        "--collect-submodules",
        "core",
        "--hidden-import",
        "PySide6.QtSvg",
        str(PROJECT / "src" / "core" / "gui" / "__main__.py"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build AOCRI (--onedir)")
    parser.add_argument("--sign", action="store_true", help="ignoré (signature séparée)")
    parser.parse_args(argv)

    print("Construction de OCRApp.exe (--onedir)...")
    code = subprocess.call(build_command())
    if code != 0:
        print(f"Échec du build (code {code})", file=sys.stderr)
        return code

    raw = PROJECT / "dist" / "_app_raw" / "OCRApp"
    final = PROJECT / "dist" / "OCRApp"
    exe = raw / "OCRApp.exe"
    if not exe.is_file():
        print(f"Exécutable introuvable : {exe}", file=sys.stderr)
        return 1
    if final.exists():
        shutil.rmtree(final)
    shutil.move(str(raw), str(final))
    shutil.rmtree(PROJECT / "dist" / "_app_raw", ignore_errors=True)
    print(f"Application construite : {final / 'OCRApp.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
