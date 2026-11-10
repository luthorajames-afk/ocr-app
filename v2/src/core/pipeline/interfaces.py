"""T1.1 — Contrats d'interface du noyau (typage strict, AGENTS.md §4).

Deux abstractions fondatrices, sans aucune implémentation concrète :

- ``OcrEngine``      : moteur de reconnaissance de texte (Tesseract, RapidOCR,
  Docling…), exécuté hors du processus d'IHM.
- ``ScannerBackend`` : source d'acquisition de pages (WIA, TWAIN, eSCL…),
  exécuté dans un thread COM dédié (phase 3b).

Les objets échangés sont des dataclasses gelées (immuables, typées) : les
implémentations ne peuvent pas les muter en place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class OcrBlock:
    """Bloc de texte détecté (coordonnées en points, origine haut-gauche)."""

    text: str
    bbox: BBox
    confidence: float
    kind: str = "paragraph"


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Résultat complet d'une page."""

    text: str
    blocks: tuple[OcrBlock, ...]
    engine: str
    engine_version: str
    duration_ms: float


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Capacités déclarées d'un moteur (sélection de mode, R10)."""

    supports_layout: bool = False
    supports_tables: bool = False
    supports_native_text: bool = False
    languages: tuple[str, ...] = ("fra", "eng")


@dataclass(frozen=True, slots=True)
class ScanPage:
    """Page acquise par un scanner physique."""

    image: Image.Image
    dpi: int
    page_number: int


@dataclass(frozen=True, slots=True)
class ScanSource:
    """Source d'acquisition exposée par un backend."""

    source_id: str
    name: str
    kind: str = "scanner"


class OcrEngine(ABC):
    """Contrat d'un moteur OCR. Une instance = un sous-processus worker."""

    name: ClassVar[str]

    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        """Capacités statiques du moteur (jamais None)."""

    @property
    @abstractmethod
    def is_initialized(self) -> bool:
        """Vrai après ``initialize()`` et avant ``shutdown()``."""

    @abstractmethod
    def initialize(self) -> None:
        """Charge modèles et ressources (une seule fois par processus, R5)."""

    @abstractmethod
    def process(
        self,
        image: Image.Image,
        *,
        languages: Sequence[str] | None = None,
    ) -> OcrResult:
        """Traite une page et retourne le texte et les blocs détectés."""

    @abstractmethod
    def shutdown(self) -> None:
        """Libère les ressources ; idempotent."""


class ScannerBackend(ABC):
    """Contrat d'une source d'acquisition de documents."""

    name: ClassVar[str]

    @abstractmethod
    def is_available(self) -> bool:
        """Vrai si le backend peut être utilisé sur cette machine."""

    @abstractmethod
    def list_sources(self) -> tuple[ScanSource, ...]:
        """Sources disponibles (vide si aucune)."""

    @abstractmethod
    def acquire(
        self,
        source_id: str | None = None,
        *,
        dpi: int = 300,
        page_limit: int | None = None,
    ) -> tuple[ScanPage, ...]:
        """Acquiert des pages ; ``page_limit`` borne le lot (None = tout)."""

    @abstractmethod
    def close(self) -> None:
        """Ferme la session matérielle ; idempotent."""
