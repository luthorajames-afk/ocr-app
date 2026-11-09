"""Script de construction des workers avec PyInstaller.

Règle R3 : mode --onedir uniquement, jamais --onefile (démarrage rapide,
moins de faux positifs antivirus).

Point d'entrée unique : ``src/ocr_app/workers/worker_main.py`` (protocole
JSON stdin/stdout, phase 7). Chaque worker est construit par mode :

    python scripts/build.py --worker simple   # noyau + OCR réel Tesseract
    python scripts/build.py --worker medium   # module Moyen (RapidOCR/OpenCV)
    python scripts/build.py --worker advanced # module Avancé (Docling, R1)

Le worker simple embarque les données de langue Tesseract
(``models/tessdata`` via --add-data, R4 : aucun téléchargement). Les workers
medium/advanced ne doivent PAS être construits dans l'environnement du noyau :
construire depuis le venv du module (R13).

Sortie (--onedir) : ``dist/worker_<mode>/worker_<mode>/worker_<mode>.exe``.

Signature (R11) : ``--sign`` appelle ``scripts/sign.py`` après le build ;
sans certificat configuré, le build réussit avec un avertissement.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent

# Toutes les données embarquées utilisent le séparateur Windows « ; ».
WORKERS = {
    "simple": {
        "entry": "src/ocr_app/workers/worker_main.py",
        "add_data": ["models/tessdata;models/tessdata"],
        "notes": "OCR réel tesserocr ; tessdata embarquée (R4).",
    },
    "medium": {
        "entry": "src/ocr_app/workers/worker_main.py",
        "add_data": [],
        # R13 : un des paquets suffit (nom de module selon la version).
        "requires": ("rapidocr", "rapidocr_onnxruntime"),
        "notes": (
            "À construire depuis le venv du module Moyen avec rapidocr+opencv "
            "installés (R13) — pas dans l'env du noyau"
        ),
    },
    "advanced": {
        "entry": "src/ocr_app/workers/worker_main.py",
        "add_data": [],
        "requires": ("docling",),
        "notes": (
            "À construire depuis le venv du module Avancé avec docling/torch "
            "installés (R1/R13) — pas dans l'env du noyau"
        ),
    },
}


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def check_module_dependencies(worker: str) -> Optional[str]:
    """Message d'erreur si les dépendances du module manquent, sinon None.

    R1/R13 : construire medium/advanced depuis un environnement sans leurs
    paquets produirait un exécutable qui échoue au premier OCR ; le build est
    refusé en amont (aucune exception silencieuse).
    """
    requires = WORKERS[worker].get("requires")
    if not requires:
        return None
    if any(_has_module(name) for name in requires):
        return None
    return (
        f"Dépendances absentes pour le worker « {worker} » : "
        f"aucun de {', '.join(requires)} n'est importable par "
        f"{sys.executable}. Construisez ce worker depuis le venv de son "
        f"module (R13). {WORKERS[worker]['notes']}"
    )


def check_tessdata() -> Optional[str]:
    """Message d'erreur si models/tessdata est absente ou vide (R4)."""
    tessdata = PROJECT_ROOT / "models" / "tessdata"
    if not tessdata.is_dir() or not any(tessdata.glob("*.traineddata")):
        return (
            f"models/tessdata introuvable ou sans *.traineddata ({tessdata}) : "
            "la tessdata ne peut pas être embarquée (R4)."
        )
    return None


def worker_exe(worker: str) -> Path:
    """Chemin attendu de l'exécutable après build (--onedir)."""
    return PROJECT_ROOT / "dist" / f"worker_{worker}" / f"worker_{worker}" / f"worker_{worker}.exe"


def build_command(worker: str) -> List[str]:
    spec = WORKERS[worker]
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",  # R3 : jamais --onefile
        "--noconfirm",
        "--name",
        f"worker_{worker}",
        "--distpath",
        str(PROJECT_ROOT / "dist" / f"worker_{worker}"),
        "--workpath",
        str(PROJECT_ROOT / "build" / f"worker_{worker}"),
        "--specpath",
        str(PROJECT_ROOT / "build" / f"worker_{worker}"),
    ]
    for item in spec["add_data"]:
        # Source absolue (le --specpath déplace le CWD du spec) ; destination
        # relative au _MEIPASS (résolu au runtime par ocr_engine, R4).
        src, _, dest = item.partition(";")
        src_path = Path(src)
        if not src_path.is_absolute():
            src_path = PROJECT_ROOT / src
        cmd += ["--add-data", f"{src_path};{dest}"]
    cmd.append(str(PROJECT_ROOT / spec["entry"]))
    return cmd


def check_pyinstaller() -> bool:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def build(worker: str, sign: bool = False) -> int:
    if worker not in WORKERS:
        print(
            f"Worker inconnu : {worker}. Choix : {', '.join(WORKERS)}",
            file=sys.stderr,
        )
        return 1

    if worker in ("medium", "advanced"):
        missing = check_module_dependencies(worker)
        if missing is not None:
            # R1/R13 : jamais de build medium/advanced sans dépendances.
            print(f"Build refusé : {missing}", file=sys.stderr)
            return 1

    if worker == "simple":
        missing_tessdata = check_tessdata()
        if missing_tessdata is not None:
            print(f"Build refusé : {missing_tessdata}", file=sys.stderr)
            return 1

    if not check_pyinstaller():
        print(
            "PyInstaller est indisponible : l'installer avec "
            "`python -m pip install pyinstaller` (déjà présent dans l'extra dev).",
            file=sys.stderr,
        )
        return 1

    print(f"Construction du worker '{worker}' (--onedir, R3)...")
    code = subprocess.call(build_command(worker))
    if code != 0:
        print(
            f"Échec du build du worker '{worker}' (code {code}). "
            "Cause fréquente : DLL native non détectée (tesserocr/leptonica) — "
            "ajouter un hook PyInstaller ou --hidden-import adéquat.",
            file=sys.stderr,
        )
        return code

    exe = worker_exe(worker)
    if not exe.is_file():
        print(
            f"Build terminé mais exécutable introuvable : {exe}",
            file=sys.stderr,
        )
        return 1
    print(f"Worker '{worker}' construit : {exe}")

    if sign:
        print("Signature du worker (R11)...")
        sign_code = subprocess.call(
            [sys.executable, str(SCRIPTS_DIR / "sign.py"), str(exe)]
        )
        if sign_code == 2:
            print(
                "Avertissement : signature impossible (prérequis manquants) — "
                "binaire NON signé, voir le guide ci-dessus.",
                file=sys.stderr,
            )
        elif sign_code != 0:
            print("Échec de la signature (R11).", file=sys.stderr)
            return 1
        else:
            print("Worker signé.")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/build.py",
        description=(
            "Build des workers OCR (PyInstaller --onedir, R3). "
            "Point d'entrée : src/ocr_app/workers/worker_main.py."
        ),
    )
    parser.add_argument(
        "--worker",
        required=True,
        choices=sorted(WORKERS),
        help="Worker à construire (simple embarque models/tessdata, R4)",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="signer le binaire après le build via scripts/sign.py (R11)",
    )
    args = parser.parse_args(argv)
    return build(args.worker, sign=args.sign)


if __name__ == "__main__":
    raise SystemExit(main())
