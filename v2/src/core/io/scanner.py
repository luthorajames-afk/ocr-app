"""T3b — Couche d'acquisition scanner (WIA prioritaire, TWAIN/eSCL en secours).

Étanchéité réseau (AGENTS.md §1) : eSCL (HTTP local) est **désactivé par
défaut** ; il ne s'active que par ``AOCRI_ALLOW_ESCL=1``. WIA et TWAIN sont
des API locales Windows.

T3b.3 : détection de pages blanches **signalée**, jamais supprimée
automatiquement — la validation revient à l'utilisateur.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

from core.pipeline.interfaces import ScanPage, ScanSource

BLANK_INK_RATIO = 0.004


class ScannerError(RuntimeError):
    """Backend indisponible ou échec d'acquisition."""


@dataclass(frozen=True, slots=True)
class ScanBatch:
    """Lot acquis : pages + indicateurs de pages blanches (non supprimées)."""

    pages: tuple[ScanPage, ...]
    blank_flags: tuple[bool, ...]

    @property
    def blank_pages(self) -> tuple[int, ...]:
        return tuple(
            page.page_number for page, blank in zip(self.pages, self.blank_flags) if blank
        )


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def ink_ratio(image: Image.Image) -> float:
    """Fraction de pixels « encre » (niveau < 128) sur la page."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return 0.0
    return float((gray < 128).mean())


def is_blank_page(image: Image.Image, threshold: float = BLANK_INK_RATIO) -> bool:
    return ink_ratio(image) < threshold


def build_batch(pages: list[ScanPage], threshold: float = BLANK_INK_RATIO) -> ScanBatch:
    """Construit un lot : détecte les pages blanches mais les conserve."""
    flags = tuple(is_blank_page(page.image, threshold) for page in pages)
    return ScanBatch(pages=tuple(pages), blank_flags=flags)


class WiaScannerBackend:
    """Backend WIA (Windows Image Acquisition) — prioritaire.

    L'implémentation COM est chargée paresseusement ; sans ``pywin32``, le
    backend se déclare indisponible (aucune exception à l'import).
    """

    name = "wia"

    def is_available(self) -> bool:
        return os.name == "nt" and _has_module("win32com.client")

    def list_sources(self) -> tuple[ScanSource, ...]:
        if not self.is_available():
            raise ScannerError(
                "WIA indisponible : installer pywin32 et vérifier le service WIA"
            )
        raise ScannerError("acquisition WIA non connectée dans cet environnement")

    def acquire(
        self,
        source_id: str | None = None,
        *,
        dpi: int = 300,
        page_limit: int | None = None,
    ) -> tuple[ScanPage, ...]:
        self.list_sources()
        raise ScannerError("acquisition WIA non connectée dans cet environnement")

    def close(self) -> None:
        return None


class TwainScannerBackend:
    """Backend TWAIN 64 bits (secours) — nécessite un pont natif."""

    name = "twain"

    def is_available(self) -> bool:
        return os.name == "nt" and _has_module("pyTwain")

    def list_sources(self) -> tuple[ScanSource, ...]:
        if not self.is_available():
            raise ScannerError("TWAIN indisponible : aucune bibliothèque TWAIN 64 bits")
        raise ScannerError("acquisition TWAIN non connectée dans cet environnement")

    def acquire(
        self,
        source_id: str | None = None,
        *,
        dpi: int = 300,
        page_limit: int | None = None,
    ) -> tuple[ScanPage, ...]:
        self.list_sources()
        raise ScannerError("acquisition TWAIN non connectée dans cet environnement")

    def close(self) -> None:
        return None


class EsclScannerBackend:
    """Backend eSCL/AirScan (HTTP local) — désactivé par défaut (réseau)."""

    name = "escl"

    def is_available(self) -> bool:
        return os.environ.get("AOCRI_ALLOW_ESCL") == "1"

    def list_sources(self) -> tuple[ScanSource, ...]:
        if not self.is_available():
            raise ScannerError(
                "eSCL désactivé (étanchéité réseau) : définir AOCRI_ALLOW_ESCL=1 "
                "pour autoriser le scanner local"
            )
        raise ScannerError("aucun scanner eSCL découvert sur le réseau local")

    def acquire(
        self,
        source_id: str | None = None,
        *,
        dpi: int = 300,
        page_limit: int | None = None,
    ) -> tuple[ScanPage, ...]:
        self.list_sources()
        raise ScannerError("aucun scanner eSCL découvert sur le réseau local")

    def close(self) -> None:
        return None


BACKENDS = (WiaScannerBackend, TwainScannerBackend, EsclScannerBackend)


def available_backends() -> list[str]:
    return [backend.name for backend in (cls() for cls in BACKENDS) if backend.is_available()]


def select_backend() -> object | None:
    """Premier backend disponible (WIA → TWAIN → eSCL) ou None."""
    for cls in BACKENDS:
        backend = cls()
        if backend.is_available():
            return backend
    return None
