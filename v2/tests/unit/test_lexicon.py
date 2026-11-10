"""T2.5 — Tests de la post-correction lexique (références intouchables)."""

from __future__ import annotations

from core.pipeline.lexicon import DEFAULT_LEXICON, correct_text


def test_corrects_near_miss_legal_word() -> None:
    report = correct_text("le decret est publie")
    assert "décret" in report.text
    assert report.count >= 1


def test_never_touches_article_numbers() -> None:
    source = "article 12-3 du decret n° 2026-041"
    report = correct_text(source)
    assert "article 12-3" in report.text
    assert "2026-041" in report.text
    assert "12" in report.protected_tokens or "article 12-3" in report.text


def test_never_touches_dates_amounts_emails_iban() -> None:
    source = (
        "échéance 09/11/2026, montant 1 450,90 €, "
        "contact@exemple.fr, IBAN FR76 3000 4008 2800 0123 4567 890"
    )
    report = correct_text(source)
    assert "09/11/2026" in report.text
    assert "1 450,90" in report.text
    assert "contact@exemple.fr" in report.text
    assert "FR76 3000 4008 2800 0123 4567 890" in report.text


def test_does_not_replace_far_words() -> None:
    report = correct_text("xyzzy plugh")
    assert report.count == 0


def test_custom_lexicon_is_used() -> None:
    report = correct_text("le contart est signe", lexicon=("contrat",))
    assert "contrat" in report.text


def test_default_lexicon_contains_core_terms() -> None:
    for term in ("décret", "arrêté", "article", "facture", "tva"):
        assert term in DEFAULT_LEXICON


def test_empty_text_is_stable() -> None:
    report = correct_text("")
    assert report.text == ""
    assert report.count == 0


def test_english_legal_terms_and_artifacts() -> None:
    report = correct_text("the govemment expenditrue section", language="en")
    assert "government" in report.text
    assert "expenditure" in report.text


def test_bilingual_mode_corrects_french_and_english() -> None:
    report = correct_text("le decret et the govemment", language="bilingual")
    assert "décret" in report.text
    assert "government" in report.text


def test_language_selection_limits_scope() -> None:
    english_only = correct_text("le decret", language="en")
    assert "décret" not in english_only.text


def test_short_tokens_are_untouched() -> None:
    report = correct_text("le la un")
    assert report.text == "le la un"
    assert report.count == 0
