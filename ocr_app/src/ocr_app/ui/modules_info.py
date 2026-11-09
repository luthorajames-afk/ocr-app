"""Détection légère des modules OCR disponibles pour la section « Modules ».

Seule la présence des paquets est vérifiée (importlib.util.find_spec), sans
import lourd : l'interface ne charge jamais un moteur OCR (R2).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ModuleInfo:
    key: str
    label: str
    installed: bool
    detail: str = ""


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def detect_modules() -> List[ModuleInfo]:
    core_installed = _has_module("tesserocr") or _has_module("pytesseract")
    medium_installed = _has_module("rapidocr") or _has_module(
        "rapidocr_onnxruntime"
    )
    advanced_installed = _has_module("docling")

    return [
        ModuleInfo(
            key="core",
            label="Noyau · Tesseract",
            installed=core_installed,
            detail="" if core_installed else "non installé",
        ),
        ModuleInfo(
            key="medium",
            label="Moyen · RapidOCR",
            installed=medium_installed,
            detail="" if medium_installed else "non installé",
        ),
        ModuleInfo(
            key="advanced",
            label="Avancé",
            installed=advanced_installed,
            detail="" if advanced_installed else "non installé",
        ),
    ]


def demo_modules() -> List[ModuleInfo]:
    """État de la maquette validée : Noyau et Moyen installés, Avancé absent."""
    return [
        ModuleInfo(key="core", label="Noyau · Tesseract", installed=True),
        ModuleInfo(key="medium", label="Moyen · RapidOCR", installed=True),
        ModuleInfo(
            key="advanced", label="Avancé", installed=False, detail="non installé"
        ),
    ]
