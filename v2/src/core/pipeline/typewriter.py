"""Correcteur « machine à écrire » pour les scans de documents anciens.

Les jugements et arrêtés dactylographiés scannés produisent des mots mixing
lettres et chiffres : ``travaill4`` (é), ``arr4t4`` (ê/é), ``rnodifi4e``
(rn → m), ``art1cle`` (1 → i). Le correcteur génère les variantes par
position ambiguë, ne retient que celles qui existent dans le vocabulaire
juridique + français courant, et préserve la casse et les références
(numéros purs, ``n°``, dates) qui ne sont jamais touchés.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import product

from core.pipeline.lexicon import (
    ARTIFACT_FIXES_EN,
    ARTIFACT_FIXES_FR,
    BILINGUAL_LEXICON,
)

# Substitutions ambiguës par caractère (OCR machine à écrire / matricielle).
_AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "4": ("é", "ê", "è", "e", "a", "E", "i"),
    "1": ("l", "i", "I"),
    "0": ("o", "O"),
    "5": ("s", "S"),
    "3": ("e", "é"),
    "8": ("B",),
}

# Mot contenant chiffres ET lettres : signature typique de la frappe.
# L'apostrophe ne fait pas partie du mot : « l'arr4t4 » → « l » + « arr4t4 ».
_WORD = re.compile(r"[A-Za-zÀ-ÿ0-9]+")
_RN = re.compile(r"rn")


def _is_mixed_word(token: str) -> bool:
    """Vrai si le mot mêle lettres et chiffres (au moins 3 lettres)."""
    if len(token) < 4 or not any(char.isdigit() for char in token):
        return False
    return len(re.findall(r"[A-Za-zÀ-ÿ]", token)) >= 3

# Références jamais corrigées : numéros purs, n°, dates, codes.
_PROTECTED = re.compile(r"^(?:\d+|n°?\d*|\d+[/.,-]\d+)$")


def _strip_accents(word: str) -> str:
    """Clé de comparaison sans accents (é, ê, è, e équivalents pour l'OCR)."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _inflections(word: str) -> set[str]:
    """Flexions plausibles : +s, +x, é→és, ée→ées (validation seulement)."""
    forms = {word}
    if word.endswith(("é", "é")):
        forms.add(word + "s")
        forms.add(word[:-1] + "ée")
        forms.add(word[:-1] + "ées")
    if word.endswith("e"):
        forms.add(word + "s")
    else:
        forms.add(word + "s")
        forms.add(word + "x")
    return forms


def _vocabulary() -> tuple[dict[str, str], dict[str, str]]:
    """Vocabulaire exact et clé sans accents → forme canonique du dictionnaire."""
    base = {word.lower() for word in BILINGUAL_LEXICON}
    base |= {fixed.lower() for fixed in ARTIFACT_FIXES_FR.values()}
    base |= {fixed.lower() for fixed in ARTIFACT_FIXES_EN.values()}
    expanded: set[str] = set()
    for word in base:
        expanded |= _inflections(word)
    exact = {word: word for word in expanded}
    accent_free = {_strip_accents(word): word for word in expanded}
    return exact, accent_free


def _variants(token: str) -> list[str]:
    """Variantes plausibles d'un mot mixte, toutes combinaisons de positions.

    ``rn`` collé → ``m`` est appliqué d'abord (``rnodifi4e`` → ``modifi4e``),
    puis l'expansion des positions ambiguës s'effectue sur chaque souche.
    """
    stems = [token]
    if _RN.search(token.lower()):
        stems.append(token.lower().replace("rn", "m"))
        stems.append(token.lower().replace("rn", "m").capitalize())
    results: set[str] = set()
    for stem in stems:
        positions = [
            (index, _AMBIGUOUS[char.lower()])
            for index, char in enumerate(stem)
            if char.lower() in _AMBIGUOUS
        ]
        if len(positions) > 3:
            continue
        for combination in product(*(options for _pos, options in positions)):
            chars = list(stem)
            for (index, _options), replacement in zip(positions, combination):
                chars[index] = replacement
            candidate = "".join(chars)
            if candidate != token:
                results.add(candidate)
        if stem != token:
            results.add(stem)
    return list(results)


def _match_case(original: str, candidate: str) -> str:
    if original.isupper() and len(original) > 1:
        return candidate.upper()
    if original[:1].isupper():
        return candidate[:1].upper() + candidate[1:]
    return candidate


def fix_typewriter(
    text: str,
    vocabulary: tuple[dict[str, str], dict[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Corrige les mots mixtes lettre/chiffre validés par le vocabulaire.

    Retourne ``(texte_corrigé, corrections)`` ; les références (nombres purs,
    ``n°``, dates) ne sont jamais touchées. La validation compare exactement
    puis sans accents ; la forme du dictionnaire (accents canoniques) est
    restituée. Aucune correction non validée n'est appliquée.
    """
    exact, accent_free = (
        vocabulary if vocabulary is not None else _vocabulary()
    )
    corrections: list[tuple[str, str]] = []

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if not _is_mixed_word(token) or _PROTECTED.match(token):
            return token
        for candidate in _variants(token):
            lowered = candidate.lower()
            if lowered in exact:
                fixed = exact[lowered]
            elif _strip_accents(lowered) in accent_free:
                fixed = accent_free[_strip_accents(lowered)]
            else:
                continue
            result = _match_case(token, fixed)
            if result != token:
                corrections.append((token, result))
                return result
        return token

    return _WORD.sub(replace_token, text), corrections
