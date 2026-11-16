"""Correcteur machine à écrire : mots mixtes lettre/chiffre, références intactes."""

from __future__ import annotations

from core.pipeline.typewriter import fix_typewriter


def test_common_typewriter_artifacts() -> None:
    cases = {
        "travaill4": "travaillé",
        "rnodifi4e": "modifiée",
        "arr4t4": "arrêté",
        "p4riode": "période",
        "d4termin4s": "déterminés",
        "art1cle": "article",
        "ex4cut4s": "exécutés",
    }
    for source, expected in cases.items():
        fixed, corrections = fix_typewriter(source)
        assert fixed == expected, (source, fixed)
        assert corrections and corrections[0][1] == expected


def test_apostrophe_tokens_are_split() -> None:
    fixed, _ = fix_typewriter("à l'arr4t4 du 5 MARS 1975")
    assert "l'arrêté" in fixed


def test_references_are_untouched() -> None:
    fixed, corrections = fix_typewriter(
        "VU le décret n° 74/759 du 26 août 1974 ; 1 431 018 100 FCFA"
    )
    assert corrections == []
    assert "74/759" in fixed and "1 431 018 100" in fixed


def test_unknown_mixed_words_stay_unchanged() -> None:
    fixed, corrections = fix_typewriter("le code 4Vet7 reste XYZ4Q")
    assert corrections == []
    assert "4Vet7" in fixed
