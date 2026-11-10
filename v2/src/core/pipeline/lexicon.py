"""Correcteur lexical juridique bilingue (français + anglais).

Règle absolue : les références (numéros d'articles, décrets, dates, montants,
identifiants, codes) ne sont JAMAIS corrigées.
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
    "décision", "jugement", "arrêt", "cour", "tribunal", "juridiction",
    "requête", "conclusions", "mémoire", "audience", "greffe", "greffier",
    "huissier", "notaire", "magistrat", "avocat", "procureur", "ministère",
    "secrétariat", "préfecture", "mairie", "commune", "département",
    "région", "état", "république", "gouvernement", "ministre", "président",
    "conseil", "assemblée", "sénat", "député", "sénateur", "citoyen",
    "administré", "contrat", "facture", "devis", "commande", "livraison",
    "montant", "total", "taxe", "honoraires", "échéance", "règlement",
    "paiement", "virement", "client", "fournisseur", "prestataire",
    "société", "entreprise", "établissement", "signataire", "signature",
    "mention", "clause", "condition", "obligation", "responsabilité",
    "pénalité", "intérêt", "litige", "contentieux", "recours", "appel",
    "cassation", "exécution", "notification", "publication", "journal",
    "officiel", "vigueur", "abrogation", "modification", "budget",
    "dépense", "recette", "crédit", "financier", "fiscal", "trésor",
    "compte", "bilan", "financement", "autorisation", "programme", "tva",
)

# Termes juridiques et budgétaires anglais (documents type loi de finances).
ENGLISH_LEXICON: tuple[str, ...] = (
    "law", "act", "article", "section", "chapter", "part", "title",
    "paragraph", "subparagraph", "schedule", "annex", "appendix",
    "preamble", "provision", "amendment", "decree", "order", "regulation",
    "directive", "circular", "instruction", "decision", "judgment",
    "judgement", "ruling", "court", "tribunal", "jurisdiction", "appeal",
    "cassation", "execution", "notification", "publication", "gazette",
    "republic", "government", "ministry", "minister", "president",
    "parliament", "assembly", "senate", "council", "state", "citizen",
    "contract", "invoice", "quotation", "order", "delivery", "amount",
    "total", "subtotal", "tax", "fee", "fees", "payment", "transfer",
    "client", "supplier", "provider", "company", "enterprise",
    "establishment", "signatory", "signature", "clause", "condition",
    "obligation", "liability", "penalty", "interest", "dispute",
    "litigation", "remedy", "budget", "expenditure", "expense", "revenue",
    "credit", "financial", "fiscal", "treasury", "account", "balance",
    "financing", "authorization", "authorisation", "program", "programme",
    "objective", "indicator", "appropriation", "appropriations",
    "allocation", "endowment", "commitment", "authorisation",
    "investment", "subsidy", "loan", "debt", "borrowing", "treasury",
    "personnel", "equipment", "services", "goods", "supplies", "maintenance",
    "operating", "capital", "recurrent", "special", "general", "public",
    "national", "regional", "local", "authority", "department", "agency",
    "commission", "committee", "board", "office", "bureau", "directorate",
    "secretariat", "registry", "notary", "lawyer", "attorney", "judge",
    "magistrate", "prosecutor", "clerk", "bailiff", "hearing", "evidence",
    "witness", "pleading", "submission", "memorandum", "judgment",
)

BILINGUAL_LEXICON: tuple[str, ...] = DEFAULT_LEXICON + ENGLISH_LEXICON

# Confusions OCR / couche texte fréquentes (mots entiers, minuscules).
ARTIFACT_FIXES_FR: dict[str, str] = {
    "decret": "décret",
    "arrete": "arrêté",
    "republlque": "république",
    "gouvemement": "gouvernement",
    "paragraphe": "paragraphe",
}

ARTIFACT_FIXES_EN: dict[str, str] = {
    "govemment": "government",
    "recelved": "received",
    "recieved": "received",
    "publlc": "public",
    "publlcation": "publication",
    "expenditrue": "expenditure",
    "flnancial": "financial",
    "financiai": "financial",
    "sectionn": "section",
    "articie": "article",
}


def _artifact_fixes(language: str) -> dict[str, str]:
    if language == "fr":
        return ARTIFACT_FIXES_FR
    if language == "en":
        return ARTIFACT_FIXES_EN
    return {**ARTIFACT_FIXES_FR, **ARTIFACT_FIXES_EN}


_PROTECTED_PATTERN = re.compile(
    r"""
    (?:
        \b(?:article|articles|alinéa|chapitre|section|titre|annexe|décret|arrêté|loi
            |section|chapter|part|schedule|act)\s+n?°?\s*[\dIVXLC.\-]+
      | \bn?°\s*[\dA-Za-z.\-]+
      | \b\d+[\d.,\s\-/]*\d*\b
      | \b[A-Z]{2}\d{2}[\s\d]{8,}\b
      | \b[\w.+-]+@[\w.-]+\.\w+\b
      | \bhttps?://\S+\b
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


def _lexicon_for(language: str, explicit: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if explicit is not None:
        return tuple(explicit)
    if language == "fr":
        return DEFAULT_LEXICON
    if language == "en":
        return ENGLISH_LEXICON
    return BILINGUAL_LEXICON


def _match_case(original: str, candidate: str) -> str:
    if original.isupper() and len(original) > 1:
        return candidate.upper()
    if original[:1].isupper():
        return candidate[:1].upper() + candidate[1:]
    return candidate


def correct_text(
    text: str,
    lexicon: tuple[str, ...] | list[str] | None = None,
    *,
    cutoff: float = 0.84,
    language: str = "bilingual",
) -> CorrectionReport:
    """Corrige les mots proches du lexique, en laissant intactes les références."""
    vocabulary = _lexicon_for(language, lexicon)
    artifact_fixes = _artifact_fixes(language)
    corrections: list[tuple[str, str]] = []
    protected: list[str] = []
    masked: list[str] = []

    def mask(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        masked.append(match.group(0))
        return f"\x00{len(masked) - 1}\x00"

    working = _PROTECTED_PATTERN.sub(mask, text)

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if len(token) < 3 or _is_protected(token):
            return token
        lowered = token.lower()
        fixed = artifact_fixes.get(lowered)
        if fixed is not None:
            candidate = _match_case(token, fixed)
            corrections.append((token, candidate))
            return candidate
        if len(token) < 4:
            return token
        matches = get_close_matches(token, vocabulary, n=1, cutoff=cutoff)
        if not matches:
            return token
        # Ne pas singulariser un pluriel correct (loans -> loan, sections -> section).
        if token.lower().endswith("s") and matches[0].lower() == token.lower()[:-1]:
            return token
        candidate = _match_case(token, matches[0])
        if candidate == token:
            return token
        corrections.append((token, candidate))
        return candidate

    corrected = re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]+", replace_token, working)

    def unmask(match: re.Match[str]) -> str:
        return masked[int(match.group(1))]

    corrected = re.sub("\x00(\\d+)\x00", unmask, corrected)
    return CorrectionReport(
        text=corrected,
        corrections=tuple(corrections),
        protected_tokens=tuple(protected),
    )
