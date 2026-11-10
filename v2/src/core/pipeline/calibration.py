"""Étalonnage qualité OCR — CER/WER vs vérité terrain + confiance.

Permet de mesurer objectivement la qualité d'un traitement et d'ajuster le
seuil de confiance des blocs à vérifier manuellement.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

EXCELLENT_CER = 0.05
GOOD_CER = 0.12
AVERAGE_CER = 0.25


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    cer: float
    wer: float
    characters: int
    words: int
    mean_confidence: float
    low_confidence_blocks: int
    total_blocks: int
    threshold: float

    @property
    def verdict(self) -> str:
        if self.cer <= EXCELLENT_CER:
            return "excellent"
        if self.cer <= GOOD_CER:
            return "bon"
        if self.cer <= AVERAGE_CER:
            return "moyen"
        return "faible"

    def to_dict(self) -> dict[str, object]:
        return {
            "cer": round(self.cer, 4),
            "wer": round(self.wer, 4),
            "characters": self.characters,
            "words": self.words,
            "mean_confidence": round(self.mean_confidence, 4),
            "low_confidence_blocks": self.low_confidence_blocks,
            "total_blocks": self.total_blocks,
            "threshold": self.threshold,
            "verdict": self.verdict,
        }


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    return " ".join(text.split())


def edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def cer(hypothesis: str, reference: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(hyp, ref) / len(ref)


def wer(hypothesis: str, reference: str) -> float:
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(hyp, ref) / len(ref)


def calibrate(
    hypothesis: str,
    reference: str,
    records: list[dict[str, object]] | None = None,
    *,
    threshold: float = 0.60,
) -> CalibrationReport:
    """Calcule CER/WER et la distribution de confiance des blocs."""
    confidences: list[float] = []
    low = 0
    total = 0
    for record in records or []:
        blocks = record.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or not str(block.get("text", "")).strip():
                continue
            total += 1
            try:
                confidence = float(block.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            confidences.append(confidence)
            if confidence < threshold:
                low += 1

    normalized = normalize(hypothesis)
    return CalibrationReport(
        cer=cer(hypothesis, reference),
        wer=wer(hypothesis, reference),
        characters=len(normalized),
        words=len(normalized.split()),
        mean_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
        low_confidence_blocks=low,
        total_blocks=total,
        threshold=threshold,
    )
