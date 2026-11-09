"""Utilitaires fichiers de l'interface (volumes, chemins, dossier de travail).

R14 : les chemins Windows accentués et espacés sont pris en charge tels quels.
Aucun rendu ni OCR n'est effectué ici : seul le nombre de pages d'un PDF est lu.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QStandardPaths

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_DOTS_SPACES = re.compile(r"[ .]+$")

SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def probe_page_count(path: str) -> int:
    """Retourne le nombre de pages d'un PDF (1 pour une image ou en cas d'échec)."""
    suffix = Path(path).suffix.lower()
    if suffix != ".pdf":
        return 1
    try:
        import pypdfium2 as pdfium  # type: ignore

        document = pdfium.PdfDocument(str(path))
        try:
            count = len(document)
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()
        return max(1, int(count))
    except Exception:
        # PDF protégé ou illisible : le worker signalera l'erreur réelle.
        return 1


def work_root() -> Path:
    """Dossier racine des travaux OCR, surchargeable via AOCRI_WORK_DIR."""
    override = os.environ.get("AOCRI_WORK_DIR")
    if override:
        root = Path(override)
    else:
        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        if location:
            root = Path(location) / "ocr_work"
        else:  # pragma: no cover - repli si Qt ne fournit aucun chemin
            root = Path.home() / ".aocri" / "ocr_work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_stem(source_path: str, fallback: str = "document") -> str:
    """Conserve accents et espaces, retire uniquement les caractères interdits."""
    stem = Path(source_path).stem.strip()
    stem = _INVALID_WINDOWS_CHARS.sub("_", stem)
    stem = _TRAILING_DOTS_SPACES.sub("", stem)
    stem = stem.strip() or fallback
    return stem[:60]


def task_work_dir(root: Path, source_path: str, task_id: str) -> Path:
    """Dossier de travail unique et lisible pour une tâche (accents conservés)."""
    directory = root / f"{safe_stem(source_path)}_{task_id[:8]}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_export_stem(source_path: Optional[str]) -> str:
    if not source_path:
        return "resultat_ocr"
    return safe_stem(source_path, fallback="resultat_ocr")


def is_supported_input(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES
