"""T2.5 — Post-correction contrôlée par lexique juridique/gestion.

Règle absolue : les références (numéros d'articles, décrets, dates, montants,
identifiants) ne sont JAMAIS corrigées. Seuls les mots alphabétiques hors
référence, proches d'un terme du lexique, sont remplacés.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import get_close_matches

# Termes fréquents des documents juridiques et de gestion (FR).
DEFAULT_LEXICON: tuple[str, ...] = (
    "décret", "arrêté", "loi", "article", "alinéa", "paragraphe", "chapitre",
    "titre", "section", "annexe", "préambule", "considérant", "visa",
    "ordonnance", "circulaire", "instruction", "directive", "règlement",
    "décision", "jugement", "arrêt", "ordonnance", "cour", "tribunal",
    "juridiction", "requête", "conclusions", "mémoire", "audience",
    "greffe", "greffier", "huissier", "notaire", "magistrat", "avocat",
    "procureur", "ministère", "secrétariat", "préfecture", "sous-préfecture",
    "mairie", "commune", "département", "région", "état", "république",
    "gouvernement", "ministre", "premier", "président", "conseil",
    "assemblée", "sénat", "député", "sénateur", "citoyen", "administré",
    "contrat", "facture", "devis", "bon", "commande", "livraison",
    "montant", "total", "sous-total", "tva", "taxe", "honoraires",
    "échéance", "règlement", "paiement", "virement", "iban", "bic",
    "siret", "siren", "client", "fournisseur", "prestataire", "société",
    "entreprise", "établissement", "signataire", "signature", "cachet",
    "mention", "clause", "condition", "obligation", "responsabilité",
    "pénalité", "intérêt", "litige", "contentieux", "recours", "appel",
    "cassation", "exécution", "notification", "publication", "journal",
    "officiel", "entrée", "vigueur", "abrogation", "modification",
)

# Jetons intouchables : chiffres, références d'articles, montants, dates,
# identifiants, numéros.
_PROTECTED_PATTERN = re.compile(
    r"""
    (?:
        \b(?:article|articles|alinéa|chapitre|section|titre|annexe|décret|arrêté|loi)\s+n?°?\s*[\dIVXLC.\-]+  # réf. explicite
      | \bn?°\s*[\dA-Za-z.\-]+                     # n° 2026-041
      | \b\d+[\d.,\s\-/]*\d*\b                     # nombres, montants, dates
      | \b[A-Z]{2}\d{2}[\s\d]{8,}\b                # IBAN / SIRET approx.
      | \b[\w.+-]+@[\w.-]+\.\w+\b                  # e-mail
      | \bhttps?://\S+\b                           # URL
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class CorrectionReport:
    text: str
    corrections: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    protected_tokens: tuple[str, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.corrections)


def _is_protected(token: str) -> bool:
    return bool(_PROTECTED_PATTERN.fullmatch(token)) or any(char.isdigit() for char in token)


def correct_text(
    text: str,
    lexicon: tuple[str, ...] | list[str] | None = None,
    *,
    cutoff: float = 0.82,
) -> CorrectionReport:
    """Corrige les mots proches du lexique, en laissant intactes les références."""
    vocabulary = tuple(lexicon) if lexicon is not None else DEFAULT_LEXICON
    corrections: list[tuple[str, str]] = []
    protected: list[str] = []

    # 1) Masquer tous les jetons protégés présents dans le texte (e-mails,
    #    URL, IBAN, références, nombres) avant la moindre correction de mot.
    masked: list[str] = []

    def mask(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        masked.append(match.group(0))
        return f"\x00{len(masked) - 1}\x00"

    working = _PROTECTED_PATTERN.sub(mask, text)

    # 2) Corriger uniquement les mots alphabétiques restants.
    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if len(token) < 4 or _is_protected(token):
            return token
        matches = get_close_matches(token, vocabulary, n=1, cutoff=cutoff)
        if not matches:
            return token
        candidate = matches[0]
        if candidate == token:
            return token
        corrections.append((token, candidate))
        return candidate

    corrected = re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]+", replace_token, working)

    # 3) Restaurer les jetons protégés à l'identique.
    def unmask(match: re.Match[str]) -> str:
        return masked[int(match.group(1))]

    corrected = re.sub("\x00(\\d+)\x00", unmask, corrected)
    return CorrectionReport(
        text=corrected,
        corrections=tuple(corrections),
        protected_tokens=tuple(protected),
    )
