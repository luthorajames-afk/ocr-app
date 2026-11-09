"""Diagnostic réutilisable des moteurs OCR disponibles.

Ne télécharge rien et n'installe rien. Les imports lourds (docling, paddleocr,
easyocr) ne sont pas exécutés : seule leur présence sur disque est vérifiée.

Usage :
    python scripts/check_engines.py
    python scripts/check_engines.py --self-test
    python scripts/check_engines.py --json docs/engines_status.json
    python scripts/check_engines.py --json-only

Sortie lisible par défaut ; JSON via --json <chemin> ou --json-only.
Code retour : 0 si le diagnostic s'est déroulé, 1 en cas d'erreur interne.
"""

from __future__ import annotations

import argparse
import glob
import importlib.metadata as importlib_metadata
import importlib.util
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # accents lisibles dans la console Windows / Git Bash
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]

TESSERACT_BINARY_CANDIDATES = [
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    Path("C:/ProgramData/chocolatey/bin/tesseract.exe"),
]

# Fiches descriptives. Les tailles sont approximatives (wheel téléchargé).
ENGINE_SPECS: list[dict[str, Any]] = [
    {
        "key": "tesserocr",
        "label": "tesserocr",
        "modules": ["tesserocr"],
        "distributions": ["tesserocr"],
        "license": "MIT (wrapper) ; Apache-2.0 (Tesseract)",
        "approx_size": "~24 Mo (wheel Windows, Tesseract 5.5.3 embarqué)",
        "modes": ["Simple"],
        "downloads_models_at_runtime": False,
        "python_requires": ">=3.9",
        "dependencies": [("Pillow", "PIL")],
        "notes": "tessdata local requis (eng/fra). Pas besoin de tesseract.exe : "
                 "la bibliothèque Tesseract est liée dans le wheel Windows.",
    },
    {
        "key": "tesseract_cli",
        "label": "tesseract (binaire)",
        "modules": [],
        "distributions": [],
        "license": "Apache-2.0",
        "approx_size": "~60-100 Mo installé + tessdata",
        "modes": ["Simple", "Moyen"],
        "downloads_models_at_runtime": False,
        "python_requires": None,
        "dependencies": [],
        "notes": "Requis par pytesseract uniquement. tesserocr embarque sa propre bibliothèque.",
    },
    {
        "key": "pytesseract",
        "label": "pytesseract",
        "modules": ["pytesseract"],
        "distributions": ["pytesseract"],
        "license": "Apache-2.0",
        "approx_size": "~0,02 Mo + Tesseract requis",
        "modes": ["Simple", "Moyen"],
        "downloads_models_at_runtime": False,
        "python_requires": ">=3.8",
        "dependencies": [("Pillow", "PIL"), ("binaire tesseract.exe", "__binary__")],
        "notes": "Wrapper ligne de commande : sans tesseract.exe il ne peut rien faire.",
    },
    {
        "key": "rapidocr_onnxruntime",
        "label": "rapidocr-onnxruntime",
        "modules": ["rapidocr_onnxruntime"],
        "distributions": ["rapidocr-onnxruntime"],
        "license": "Apache-2.0",
        "approx_size": "~14 Mo (modèles inclus) + OpenCV + onnxruntime",
        "modes": ["Moyen"],
        "downloads_models_at_runtime": False,
        "python_requires": ">=3.6,<3.13",
        "dependencies": [("OpenCV", "cv2"), ("onnxruntime", "onnxruntime")],
        "notes": "Ancien nom du paquet. Modèles embarqués (hors ligne). "
                 "Incompatible avec Python 3.13+.",
    },
    {
        "key": "rapidocr",
        "label": "rapidocr",
        "modules": ["rapidocr"],
        "distributions": ["rapidocr"],
        "license": "Apache-2.0",
        "approx_size": "~26 Mo (modèles inclus) + OpenCV + onnxruntime",
        "modes": ["Moyen"],
        "downloads_models_at_runtime": False,
        "python_requires": ">=3.8,<4",
        "dependencies": [("OpenCV", "cv2"), ("onnxruntime", "onnxruntime")],
        "notes": "Successeur de rapidocr-onnxruntime, compatible Python 3.14. "
                 "Modèles embarqués dans le wheel (R4 respectée).",
    },
    {
        "key": "docling",
        "label": "docling",
        "modules": ["docling"],
        "distributions": ["docling"],
        "license": "MIT (code) ; MIT (docling-ibm-models)",
        "approx_size": "~3-5 Go avec PyTorch et les modèles",
        "modes": ["Avancé"],
        "downloads_models_at_runtime": True,
        "python_requires": ">=3.10,<4.0",
        "dependencies": [
            ("PyTorch", "torch"),
            ("torchvision", "torchvision"),
            ("docling-ibm-models", "docling_ibm_models"),
            ("Hugging Face Hub", "huggingface_hub"),
        ],
        "notes": "Télécharge les modèles layout/table depuis Hugging Face au premier "
                 "usage. Pour R4 : pré-télécharger et fixer DOCLING_ARTIFACTS_PATH.",
    },
    {
        "key": "paddleocr",
        "label": "paddleocr",
        "modules": ["paddleocr"],
        "distributions": ["paddleocr", "paddlex"],
        "license": "Apache-2.0",
        "approx_size": "~1 Go (paddlepaddle + modèles)",
        "modes": ["Moyen", "Avancé"],
        "downloads_models_at_runtime": True,
        "python_requires": ">=3.8",
        # paddlepaddle 3.3.1 : wheels Windows jusqu'à cp313 seulement (2026-11).
        "wheel_python_max": (3, 13),
        "dependencies": [("PaddlePaddle", "paddle"), ("OpenCV", "cv2")],
        "notes": "Pas de wheel paddlepaddle cp314 Windows : Python 3.12/3.13 requis. "
                 "PaddleX télécharge les modèles au premier usage (R4 à traiter).",
    },
    {
        "key": "easyocr",
        "label": "easyocr",
        "modules": ["easyocr"],
        "distributions": ["easyocr"],
        "license": "Apache-2.0",
        "approx_size": "~3 Go (PyTorch) + modèles",
        "modes": ["Moyen"],
        "downloads_models_at_runtime": True,
        "python_requires": None,
        "dependencies": [
            ("PyTorch", "torch"),
            ("torchvision", "torchvision"),
            ("OpenCV", "cv2"),
        ],
        "notes": "Télécharge ses modèles au premier usage (R4 non respectée sans "
                 "pré-téléchargement et model_storage_directory explicite).",
    },
]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _module_present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _dist_version(names: list[str]) -> str | None:
    for name in names:
        try:
            return importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _python_matches(spec: str | None) -> bool | None:
    """Évalue un sous-ensemble de specifiers (>=, >, <=, <, ==, !=)."""
    if not spec:
        return None
    current = tuple(sys.version_info[:3])

    def compare(target: tuple[int, ...]) -> int:
        size = max(len(current), len(target))
        left = current + (0,) * (size - len(current))
        right = target + (0,) * (size - len(target))
        return (left > right) - (left < right)

    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        for operator in (">=", "<=", "==", "!=", ">", "<"):
            if not clause.startswith(operator):
                continue
            result = compare(_version_tuple(clause[len(operator):]))
            ok = {
                ">=": result >= 0,
                "<=": result <= 0,
                "==": result == 0,
                "!=": result != 0,
                ">": result > 0,
                "<": result < 0,
            }[operator]
            if not ok:
                return False
            break
    return True


def find_tesseract_binary() -> dict[str, Any]:
    found: Path | None = None
    on_path = shutil.which("tesseract")
    candidates = [Path(on_path)] if on_path else []
    candidates.extend(TESSERACT_BINARY_CANDIDATES)
    for candidate in candidates:
        try:
            if candidate.is_file():
                found = candidate
                break
        except OSError:
            continue

    version = None
    error = None
    if found:
        try:
            proc = subprocess.run(
                [str(found), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                errors="replace",
            )
            output = (proc.stdout or proc.stderr or "").strip()
            version = output.splitlines()[0] if output else None
        except Exception as exc:  # pragma: no cover - dépend de la machine
            error = f"{type(exc).__name__}: {exc}"

    return {
        "available": found is not None,
        "path": str(found) if found else None,
        "version": version,
        "error": error,
        "searched": ["PATH"] + [str(p) for p in TESSERACT_BINARY_CANDIDATES],
    }


def _candidate_tessdata_dirs() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    env_value = os.environ.get("TESSDATA_PREFIX")
    if env_value:
        candidates.append((Path(env_value), "TESSDATA_PREFIX"))
        candidates.append((Path(env_value) / "tessdata", "TESSDATA_PREFIX"))
    candidates.append((REPO_ROOT / "models" / "tessdata", "models/tessdata du projet"))
    for binary in TESSERACT_BINARY_CANDIDATES:
        candidates.append((binary.parent / "tessdata", "installation Tesseract"))
    return candidates


def find_tessdata(explicit: str | None) -> dict[str, Any]:
    warning = None
    if explicit:
        directory = Path(explicit)
        if directory.is_dir():
            files = sorted(
                Path(p).stem for p in glob.glob(str(directory / "*.traineddata"))
            )
            return {
                "path": str(directory),
                "source": "argument --tessdata",
                "languages": files,
                "warning": None,
            }
        warning = f"--tessdata ignoré (dossier introuvable) : {explicit}"

    existing: list[tuple[Path, str, list[str]]] = []
    for directory, source in _candidate_tessdata_dirs():
        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue
        files = sorted(
            Path(p).stem for p in glob.glob(str(directory / "*.traineddata"))
        )
        existing.append((directory, source, files))
        if files:
            return {
                "path": str(directory),
                "source": source,
                "languages": files,
                "warning": warning,
            }
    if existing:
        directory, source, files = existing[0]
        return {
            "path": str(directory),
            "source": source,
            "languages": files,
            "warning": warning,
        }
    return {"path": None, "source": None, "languages": [], "warning": warning}


def _tesserocr_details(tessdata_path: str | None) -> dict[str, Any]:
    details: dict[str, Any] = {
        "importable": False,
        "engine_version": None,
        "languages": [],
        "error": None,
    }
    try:
        import tesserocr  # import léger, pas de chargement de modèle

        details["importable"] = True
        try:
            details["engine_version"] = tesserocr.tesseract_version().strip()
        except Exception as exc:
            details["error"] = f"tesseract_version: {type(exc).__name__}: {exc}"
        try:
            if tessdata_path:
                _, languages = tesserocr.get_languages(tessdata_path)
            else:
                _, languages = tesserocr.get_languages()
            details["languages"] = list(languages)
        except Exception as exc:
            details["error"] = f"get_languages: {type(exc).__name__}: {exc}"
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
    return details


def evaluate_engine(
    spec: dict[str, Any],
    tessdata: dict[str, Any],
    binary: dict[str, Any],
) -> dict[str, Any]:
    key = spec["key"]
    result: dict[str, Any] = {
        "label": spec["label"],
        "installed": False,
        "module_present": False,
        "usable": False,
        "status": "absent",
        "version": None,
        "engine_version": None,
        "language_data": [],
        "missing_dependencies": [],
        "python_compatible": None,
        "python_requires": spec.get("python_requires"),
        "downloads_models_at_runtime": spec["downloads_models_at_runtime"],
        "license": spec["license"],
        "approx_size": spec["approx_size"],
        "modes": spec["modes"],
        "notes": spec["notes"],
        "detail": "",
    }

    if key == "tesseract_cli":
        result["installed"] = binary["available"]
        result["usable"] = binary["available"]
        result["version"] = binary["version"]
        result["status"] = "ok" if binary["available"] else "absent"
        result["detail"] = (
            f"{binary['path']} ; {binary['version']}"
            if binary["available"]
            else "introuvable (PATH + emplacements standards)"
        )
        return result

    modules = spec["modules"]
    result["module_present"] = all(_module_present(m) for m in modules) if modules else False
    result["version"] = _dist_version(spec["distributions"]) if spec["distributions"] else None
    result["installed"] = result["module_present"] and (
        bool(result["version"]) or not spec["distributions"]
    )

    result["python_compatible"] = _python_matches(spec.get("python_requires"))
    if spec.get("wheel_python_max"):
        wheel_max = tuple(spec["wheel_python_max"])
        result["python_compatible"] = (
            result["python_compatible"] is not False
            and tuple(sys.version_info[:2]) <= wheel_max
        )

    missing: list[str] = []
    for display, module in spec["dependencies"]:
        if module == "__binary__":
            if not binary["available"]:
                missing.append(display)
        elif not _module_present(module):
            missing.append(display)
    result["missing_dependencies"] = missing

    if key == "tesserocr":
        details = _tesserocr_details(tessdata["path"])
        result["module_present"] = details["importable"]
        result["installed"] = details["importable"]
        result["engine_version"] = details["engine_version"]
        result["language_data"] = details["languages"]
        if details["error"]:
            result["notes"] = f"{spec['notes']} ({details['error']})"
        if not tessdata["path"]:
            missing.append("tessdata (fichiers .traineddata)")
        elif not details["languages"]:
            missing.append("langues tessdata (eng/fra)")
        result["missing_dependencies"] = missing

    if key == "pytesseract":
        result["language_data"] = tessdata["languages"]

    result["usable"] = (
        result["installed"]
        and result["python_compatible"] is not False
        and not result["missing_dependencies"]
    )

    if not result["installed"]:
        result["status"] = "absent"
    elif result["python_compatible"] is False:
        result["status"] = "incompatible"
    elif result["usable"]:
        result["status"] = "ok"
    else:
        result["status"] = "partial"

    result["detail"] = _engine_detail(result, spec, binary)
    return result


def _engine_detail(
    result: dict[str, Any],
    spec: dict[str, Any],
    binary: dict[str, Any],
) -> str:
    if result["status"] == "absent":
        if result["python_compatible"] is False:
            return "non installé ; incompatible avec cette version de Python"
        return "non installé"
    if result["status"] == "incompatible":
        return (
            f"installé mais incompatible Python {sys.version_info.major}."
            f"{sys.version_info.minor}"
        )
    if result["status"] == "partial":
        missing = ", ".join(result["missing_dependencies"]) or "dépendances"
        return f"dépendances manquantes : {missing}"
    if spec["key"] == "tesserocr":
        version = (result["engine_version"] or "").splitlines()
        first = version[0] if version else (result["version"] or "?")
        langs = ", ".join(result["language_data"]) or "aucune"
        return f"{first} ; langues: {langs}"
    if spec["key"] == "pytesseract":
        return f"binaire: {binary['path']}"
    if spec["key"] == "tesseract_cli":
        return f"{binary['path']} ; {binary['version']}"
    return f"version {result['version'] or '?'}"


# ---------------------------------------------------------------------------
# Test trivial (image générée avec Pillow)
# ---------------------------------------------------------------------------

def _make_test_image(text: str):
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in (
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 56)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(probe)
    box = draw.textbbox((0, 0), text, font=font)
    width = max(200, box[2] - box[0] + 80)
    height = max(100, box[3] - box[1] + 80)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40 - box[0], 40 - box[1]), text, font=font, fill="black")
    return image


def _run_tesserocr_test(image, tessdata_path: str, languages: list[str]) -> dict[str, Any]:
    import tesserocr

    lang = "fra" if "fra" in languages else ("eng" if "eng" in languages else "eng")
    with tesserocr.PyTessBaseAPI(path=tessdata_path, lang=lang) as api:
        api.SetImage(image)
        return {"lang": lang, "text": api.GetUTF8Text()}


def _run_pytesseract_test(image, tessdata_path: str, languages: list[str]) -> dict[str, Any]:
    import pytesseract

    lang = "fra" if "fra" in languages else "eng"
    return {"lang": lang, "text": pytesseract.image_to_string(image, lang=lang)}


def _run_rapidocr_test(image) -> dict[str, Any]:
    import numpy as np

    try:
        from rapidocr import RapidOCR  # nouveau paquet 3.x

        output = RapidOCR()(np.array(image))
        texts = getattr(output, "txts", None)
        if texts is None and isinstance(output, tuple):
            texts = output[0]
        return {"text": "\n".join(texts or [])}
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR  # ancien paquet 1.x

        output = RapidOCR()(np.array(image))
        texts = output[0]
        if texts and isinstance(texts[0], (list, tuple)):
            texts = [item[1] for item in texts]
        return {"text": "\n".join(texts or [])}


def run_self_test(
    engines: dict[str, Any],
    tessdata: dict[str, Any],
    languages: list[str],
) -> dict[str, Any]:
    expected = "Bonjour éàç"
    results: list[dict[str, Any]] = []

    try:
        image = _make_test_image(expected)
        image_size = list(image.size)
    except Exception as exc:
        return {
            "expected": expected,
            "image_size": None,
            "results": [
                {
                    "engine": "pillow",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ],
        }

    def record(engine: str, func) -> None:
        start = time.perf_counter()
        try:
            payload = func()
            elapsed = round(time.perf_counter() - start, 3)
            text = (payload.get("text") or "").strip()
            results.append(
                {
                    "engine": engine,
                    "status": "ok",
                    "lang": payload.get("lang"),
                    "output": text,
                    "match": text == expected,
                    "elapsed_s": elapsed,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "engine": engine,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_s": round(time.perf_counter() - start, 3),
                }
            )

    def skipped_reason(key: str) -> str:
        engine = engines[key]
        if not engine["installed"]:
            if engine["python_compatible"] is False:
                return (
                    f"non installé ; incompatible Python "
                    f"{sys.version_info.major}.{sys.version_info.minor}"
                )
            return "non installé"
        return "; ".join(engine["missing_dependencies"]) or "indisponible"

    if engines["tesserocr"]["usable"]:
        record(
            "tesserocr",
            lambda: _run_tesserocr_test(image, tessdata["path"], languages),
        )
    else:
        results.append(
            {
                "engine": "tesserocr",
                "status": "skipped",
                "reason": skipped_reason("tesserocr"),
            }
        )

    if engines["pytesseract"]["usable"]:
        record(
            "pytesseract",
            lambda: _run_pytesseract_test(image, tessdata["path"], languages),
        )
    else:
        results.append(
            {
                "engine": "pytesseract",
                "status": "skipped",
                "reason": skipped_reason("pytesseract"),
            }
        )

    if engines["rapidocr"]["usable"]:
        record("rapidocr", lambda: _run_rapidocr_test(image))
    elif engines["rapidocr_onnxruntime"]["usable"]:
        record("rapidocr_onnxruntime", lambda: _run_rapidocr_test(image))
    else:
        results.append(
            {
                "engine": "rapidocr",
                "status": "skipped",
                "reason": skipped_reason("rapidocr"),
            }
        )

    for key, reason in (
        ("docling", "initialisation lourde et téléchargement de modèles : test hors R4"),
        ("paddleocr", "initialisation lourde et téléchargement de modèles : test hors R4"),
        ("easyocr", "téléchargement de modèles au premier usage : test refusé (R4)"),
    ):
        results.append({"engine": key, "status": "skipped", "reason": reason})

    return {
        "expected": expected,
        "image_size": image_size,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def environment_info() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "architecture": f"{struct.calcsize('P') * 8}-bit",
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "tessdata_prefix": os.environ.get("TESSDATA_PREFIX"),
        "pip_version": _dist_version(["pip"]),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    env = environment_info()
    tessdata = find_tessdata(args.tessdata)
    binary = find_tesseract_binary()

    engines = {
        spec["key"]: evaluate_engine(spec, tessdata, binary)
        for spec in ENGINE_SPECS
    }

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    if not languages:
        languages = tessdata["languages"] or ["fra", "eng"]

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": env,
        "tessdata": tessdata,
        "tesseract_binary": binary,
        "engines": engines,
        "summary": {
            "installed": [k for k, v in engines.items() if v["installed"]],
            "usable": [k for k, v in engines.items() if v["usable"]],
            "unusable": [k for k, v in engines.items() if not v["usable"]],
        },
        "self_test": None,
    }
    if args.self_test:
        report["self_test"] = run_self_test(engines, tessdata, languages)
    return report


STATUS_LABELS = {
    "ok": "OK",
    "partial": "PARTIEL",
    "absent": "ABSENT",
    "incompatible": "INCOMPATIBLE",
}


def print_report(report: dict[str, Any]) -> None:
    env = report["environment"]
    tessdata = report["tessdata"]

    print(f"Diagnostic moteurs OCR - {report['generated_at']}")
    print()
    print("Environnement")
    print(f"  Python           {env['python_version']} ({env['architecture']})  {env['python_executable']}")
    print(f"  Plateforme       {env['platform']}")
    print(f"  TESSDATA_PREFIX  {env['tessdata_prefix'] or '(non défini)'}")
    if tessdata["path"]:
        languages = ", ".join(tessdata["languages"]) or "aucune"
        print(f"  tessdata         {tessdata['path']}  [{languages}]  ({tessdata['source']})")
    else:
        print("  tessdata         introuvable (aucun .traineddata)")
    if tessdata.get("warning"):
        print(f"  Avertissement    {tessdata['warning']}")
    binary = report["tesseract_binary"]
    print(
        "  tesseract.exe    "
        + (f"{binary['path']}  ({binary['version']})" if binary["available"] else "introuvable")
    )
    print()

    print("Moteurs")
    print(f"  {'MOTEUR':<24} {'ÉTAT':<14} {'VERSION':<10} {'MODE':<14} DÉTAIL")
    print(f"  {'-' * 24} {'-' * 14} {'-' * 10} {'-' * 14} {'-' * 40}")
    for engine in report["engines"].values():
        status = STATUS_LABELS.get(engine["status"], engine["status"].upper())
        version = (engine["version"] or "-").splitlines()[0]
        modes = "/".join(engine["modes"])
        print(f"  {engine['label']:<24} {status:<14} {version:<10} {modes:<14} {engine['detail']}")
    print()

    summary = report["summary"]
    print(
        f"Synthèse : {len(summary['usable'])} moteur(s) utilisable(s) "
        f"({', '.join(summary['usable']) or 'aucun'}) ; "
        f"{len(summary['installed'])} installé(s)."
    )

    self_test = report.get("self_test")
    if self_test:
        print()
        print(f"Test trivial (Pillow) - attendu : {self_test['expected']!r}")
        for result in self_test["results"]:
            if result["status"] == "ok":
                verdict = "IDENTIQUE" if result.get("match") else "DIFFÉRENT"
                print(
                    f"  {result['engine']:<20} {verdict:<10} "
                    f"sortie={result.get('output')!r} ({result.get('elapsed_s')} s)"
                )
            elif result["status"] == "skipped":
                print(f"  {result['engine']:<20} IGNORÉ     {result['reason']}")
            else:
                print(f"  {result['engine']:<20} ERREUR     {result.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic des moteurs OCR disponibles (aucune installation, aucun téléchargement)"
    )
    parser.add_argument("--json", metavar="CHEMIN", help="écrit le rapport JSON dans ce fichier")
    parser.add_argument("--json-only", action="store_true", help="n'affiche que le JSON sur stdout")
    parser.add_argument("--self-test", action="store_true", help="OCR d'une image test 'Bonjour éàç'")
    parser.add_argument("--tessdata", metavar="DOSSIER", help="dossier tessdata explicite")
    parser.add_argument(
        "--languages",
        default="",
        metavar="fra,eng",
        help="langues à tester (défaut : langues tessdata détectées)",
    )
    args = parser.parse_args()

    try:
        report = build_report(args)
    except Exception as exc:
        print(f"Erreur interne : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not args.json_only:
            print(f"\nJSON écrit dans {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
