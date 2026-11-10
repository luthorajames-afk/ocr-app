"""T2.4 — Seconde passe ciblée + vote bi-moteur (Moyen/Avancé).

Deux mécanismes indépendants :

1. **Seconde passe** : les lignes dont la confiance est sous le seuil sont
   re-OCRisées isolément (recadrage + moteur de secours), ce qui améliore les
   caractères ambigus sans relancer la page entière.
2. **Vote bi-moteur** : comparaison de deux résultats (Moyen vs Avancé) par
   similarité de blocs ; le gagnant est choisi ligne à ligne, avec traçabilité.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Protocol

from PIL import Image

from core.pipeline.interfaces import OcrBlock, OcrResult

_LOGGER = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.60
SIMILARITY_TOLERANCE = 0.85
SECOND_PASS_MIN_SIMILARITY = 0.50


class RegionEngine(Protocol):
    """Moteur capable d'une OCR de zone (T2.4)."""

    def process_region(
        self,
        image: Image.Image,
        bbox: tuple[float, float, float, float],
        *,
        languages: list[str] | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class SecondPassReport:
    result: OcrResult
    retried_lines: int
    improved_lines: int
    replaced_lines: int


def low_confidence_blocks(
    result: OcrResult, threshold: float = CONFIDENCE_THRESHOLD
) -> list[int]:
    """Indices des blocs sous le seuil de confiance (hors blocs vides)."""
    return [
        index
        for index, block in enumerate(result.blocks)
        if block.text.strip() and block.confidence < threshold
    ]


def apply_second_pass(
    image: Image.Image,
    result: OcrResult,
    engine: RegionEngine,
    *,
    threshold: float = CONFIDENCE_THRESHOLD,
    languages: list[str] | None = None,
) -> SecondPassReport:
    """Re-OCRise les lignes faibles ; garde le meilleur texte par ligne."""
    blocks = list(result.blocks)
    retried = 0
    improved = 0
    replaced = 0

    for index in low_confidence_blocks(result, threshold):
        block = blocks[index]
        retried += 1
        try:
            retry_text = engine.process_region(image, block.bbox, languages=languages)
        except Exception as exc:  # noqa: BLE001 — moteur de secours indisponible
            _LOGGER.warning("seconde passe ignorée pour un bloc : %s", exc)
            continue
        retry_text = (retry_text or "").strip()
        if not retry_text:
            continue
        if retry_text == block.text:
            improved += 1
            blocks[index] = replace(block, confidence=max(block.confidence, 0.9))
            continue
        similarity = SequenceMatcher(None, block.text, retry_text).ratio()
        if similarity >= SECOND_PASS_MIN_SIMILARITY:
            # Même ligne plausible : la relecture ciblée fait foi (confiance
            # initiale sous le seuil, recadrage dédié).
            if retry_text != block.text:
                blocks[index] = replace(
                    block, text=retry_text, confidence=max(block.confidence, 0.80)
                )
                improved += 1
            continue
        if len(retry_text) >= len(block.text) * 1.15:
            blocks[index] = replace(block, text=retry_text, confidence=0.75)
            replaced += 1

    merged_text = "\n".join(block.text for block in blocks if block.text.strip())
    merged = replace(result, text=merged_text, blocks=tuple(blocks))
    return SecondPassReport(
        result=merged,
        retried_lines=retried,
        improved_lines=improved,
        replaced_lines=replaced,
    )


@dataclass(frozen=True, slots=True)
class VoteReport:
    result: OcrResult
    compared: int
    agreed: int
    primary_wins: int
    secondary_wins: int


def vote_results(
    primary: OcrResult,
    secondary: OcrResult,
    *,
    tolerance: float = SIMILARITY_TOLERANCE,
) -> VoteReport:
    """Vote bi-moteur ligne à ligne (Moyen = primary, Avancé = secondary).

    - lignes quasi identiques → texte le plus long (fidélité accents/ponctuation) ;
    - désaccord franc → confiance la plus haute (secondaire favorisé en cas d'égalité) ;
    - lignes présentes d'un seul côté → conservées (pas de perte de contenu).
    """
    compared = 0
    agreed = 0
    primary_wins = 0
    secondary_wins = 0
    voted: list[OcrBlock] = []

    secondary_blocks = list(secondary.blocks)
    used_secondary: set[int] = set()
    for block in primary.blocks:
        match_index = _best_match(block, secondary_blocks, used_secondary)
        if match_index is None:
            voted.append(block)
            primary_wins += 1
            continue
        used_secondary.add(match_index)
        other = secondary_blocks[match_index]
        compared += 1
        similarity = SequenceMatcher(None, block.text, other.text).ratio()
        # Accord = texte proche ET mêmes chiffres : un numéro d'article ou de
        # décret ne doit jamais être « voté » par ressemblance (T2.5).
        if similarity >= tolerance and _digits(block.text) == _digits(other.text):
            agreed += 1
            best_text = _prefer_text(block.text, other.text)
            voted.append(
                replace(
                    block,
                    text=best_text,
                    confidence=max(block.confidence, other.confidence),
                )
            )
        elif other.confidence > block.confidence:
            voted.append(other)
            secondary_wins += 1
        else:
            voted.append(block)
            primary_wins += 1

    for index, other in enumerate(secondary_blocks):
        if index not in used_secondary and other.text.strip():
            voted.append(other)
            secondary_wins += 1

    merged_text = "\n".join(block.text for block in voted if block.text.strip())
    return VoteReport(
        result=replace(primary, text=merged_text, blocks=tuple(voted)),
        compared=compared,
        agreed=agreed,
        primary_wins=primary_wins,
        secondary_wins=secondary_wins,
    )


def _best_match(
    block: OcrBlock,
    candidates: list[OcrBlock],
    used: set[int],
) -> int | None:
    """Bloc candidat de meilleure correspondance spatiale (centre le plus proche)."""
    cx = (block.bbox[0] + block.bbox[2]) / 2.0
    cy = (block.bbox[1] + block.bbox[3]) / 2.0
    best_index: int | None = None
    best_distance = float("inf")
    for index, candidate in enumerate(candidates):
        if index in used:
            continue
        dx = (candidate.bbox[0] + candidate.bbox[2]) / 2.0 - cx
        dy = (candidate.bbox[1] + candidate.bbox[3]) / 2.0 - cy
        distance = dx * dx + dy * dy
        if distance < best_distance:
            best_distance = distance
            best_index = index
    # Tolérance : match seulement si les blocs se recouvrent approximativement.
    if best_index is not None:
        candidate = candidates[best_index]
        if not _boxes_overlap(block.bbox, candidate.bbox):
            return None
    return best_index


def _prefer_text(first: str, second: str) -> str:
    """Choisit le texte le plus complet : longueur, puis accents conservés."""
    def score(text: str) -> tuple[int, int]:
        accented = sum(1 for char in text if ord(char) > 127)
        return (len(text), accented)
    return second if score(second) > score(first) else first


def _digits(text: str) -> str:
    """Séquence de chiffres d'un texte (garde-fou des références juridiques)."""
    return "".join(char for char in text if char.isdigit())


def _boxes_overlap(a: tuple[float, ...], b: tuple[float, ...], ratio: float = 0.2) -> bool:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    intersection = width * height
    smallest = min(
        max(a[2] - a[0], 1e-6) * max(a[3] - a[1], 1e-6),
        max(b[2] - b[0], 1e-6) * max(b[3] - b[1], 1e-6),
    )
    return intersection / smallest >= ratio
