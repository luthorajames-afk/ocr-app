"""T2.4 — Tests de la seconde passe et du vote bi-moteur."""

from __future__ import annotations

from PIL import Image

from core.pipeline.interfaces import OcrBlock, OcrResult
from core.pipeline.second_pass import (
    apply_second_pass,
    low_confidence_blocks,
    vote_results,
)


def _result(*blocks: OcrBlock, engine: str = "test") -> OcrResult:
    text = "\n".join(block.text for block in blocks if block.text)
    return OcrResult(
        text=text, blocks=tuple(blocks), engine=engine, engine_version="0", duration_ms=0.0
    )


class _FakeRegionEngine:
    def __init__(self, answers: dict[tuple[float, ...], str]) -> None:
        self.answers = answers
        self.calls: list[tuple[float, ...]] = []

    def process_region(self, image, bbox, *, languages=None):
        self.calls.append(tuple(bbox))
        return self.answers.get(tuple(bbox), "")


def test_low_confidence_blocks_selects_only_weak_nonempty() -> None:
    result = _result(
        OcrBlock("net", (0, 0, 10, 10), 0.95),
        OcrBlock("flou", (0, 20, 10, 30), 0.30),
        OcrBlock("", (0, 40, 10, 50), 0.10),
    )
    assert low_confidence_blocks(result, 0.6) == [1]


def test_second_pass_improves_weak_line() -> None:
    weak = OcrBlock("décrel", (10, 10, 100, 20), 0.35)
    result = _result(weak)
    engine = _FakeRegionEngine({(10.0, 10.0, 100.0, 20.0): "décret"})
    report = apply_second_pass(Image.new("RGB", (200, 200)), result, engine, threshold=0.6)
    assert report.retried_lines == 1
    assert report.result.blocks[0].text == "décret"
    assert engine.calls == [(10.0, 10.0, 100.0, 20.0)]


def test_second_pass_keeps_original_when_no_retry_text() -> None:
    weak = OcrBlock("test", (0, 0, 50, 10), 0.2)
    result = _result(weak)
    engine = _FakeRegionEngine({})
    report = apply_second_pass(Image.new("RGB", (100, 100)), result, engine)
    assert report.result.blocks[0].text == "test"
    assert report.retried_lines == 1


def test_second_pass_ignores_strong_lines() -> None:
    strong = OcrBlock("bon", (0, 0, 10, 10), 0.99)
    result = _result(strong)
    engine = _FakeRegionEngine({(0.0, 0.0, 10.0, 10.0): "mauvais"})
    report = apply_second_pass(Image.new("RGB", (50, 50)), result, engine)
    assert report.retried_lines == 0
    assert report.result.blocks[0].text == "bon"


def test_vote_agreement_keeps_longest_text() -> None:
    primary = _result(OcrBlock("le decret est publie", (0, 0, 100, 10), 0.80), engine="medium")
    secondary = _result(OcrBlock("le décret est publié", (0, 0, 100, 10), 0.70), engine="advanced")
    report = vote_results(primary, secondary)
    assert report.compared == 1
    assert report.agreed == 1
    assert report.result.blocks[0].text == "le décret est publié"


def test_vote_disagreement_prefers_higher_confidence() -> None:
    primary = _result(OcrBlock("article 4", (0, 0, 100, 10), 0.40), engine="medium")
    secondary = _result(OcrBlock("article 7", (0, 0, 100, 10), 0.90), engine="advanced")
    report = vote_results(primary, secondary)
    assert report.secondary_wins == 1
    assert report.result.blocks[0].text == "article 7"


def test_vote_keeps_unmatched_blocks_from_both_sides() -> None:
    primary = _result(OcrBlock("ligne A", (0, 0, 50, 10), 0.9))
    secondary = _result(
        OcrBlock("ligne A", (0, 0, 50, 10), 0.9),
        OcrBlock("note de bas de page", (0, 300, 200, 310), 0.8),
    )
    report = vote_results(primary, secondary)
    texts = [block.text for block in report.result.blocks]
    assert "ligne A" in texts
    assert "note de bas de page" in texts


def test_vote_does_not_match_blocks_without_overlap() -> None:
    primary = _result(OcrBlock("gauche", (0, 0, 50, 10), 0.9))
    secondary = _result(OcrBlock("droite", (300, 300, 350, 310), 0.95))
    report = vote_results(primary, secondary)
    assert report.compared == 0
    assert len(report.result.blocks) == 2
