"""Structure juridique : articles, titres, visas, considérants, signatures.

Attribue un rôle à chaque bloc texte d'un décret, arrêté, loi ou décision de
justice. Les rôles pilotent le plan du document (navigation), les niveaux de
titres des exports DOCX/Markdown et la fusion de paragraphes (jamais à
cheval sur deux articles).

Les motifs couvrent les variantes rencontrées dans les corpus réels :
« ARTICLE 1er.- », « ARTICLE_10 », « 2°/— », « A-COMPOSITION », « I-POUR … »,
« VU la loi n°… », « CONSIDÉRANT que », « PAR CES MOTIFS », « Fait à Yaoundé »,
versions bilingues (« CHAPTER ONE », « TITLE II ») et textes espacés
(« A R R Ê T E : »).
"""

from __future__ import annotations

import re

from core.pipeline.interfaces import OcrBlock

# Rôles possibles (champ ``role`` des blocs).
ROLE_ARTICLE = "article"
ROLE_ALINEA = "alinea"
ROLE_TITRE = "titre"  # TITRE / CHAPITRE / SECTION / TITLE / CHAPTER
ROLE_SECTION = "section"  # sous-divisions non numérotées (A-COMPOSITION…)
ROLE_VISA = "visa"
ROLE_CONSIDERANT = "considerant"
ROLE_DISPOSITIF = "dispositif"
ROLE_ANNEXE = "annexe"
ROLE_SIGNATURE = "signature"
ROLE_META = "meta"  # en-tête officiel (RÉPUBLIQUE…, décret n°…)

_ROMAN = r"[IVXLCCD]+"
_DIGIT_WORD = r"(?:\d+|PREMIER|premier|1er|UN|un|ONE|one|TWO|two)"

_ARTICLE_RE = re.compile(
    r"^\s*(?:ARTICLE|Article|ARTICLES|Art\.?)[\s_.·-]*(?:N°|n°|Nº|No\.?)?\s*"
    r"(?:1er|1ᵉʳ|PREMIER|premier|\d+|première|Première)",
)
_ALINEA_RE = re.compile(r"^\s*(?:\d{1,2}\s*[°º]\s*/?|[a-hA-H]\s*\))\s*[-–—]?\s*")
_TITRE_RE = re.compile(
    r"^\s*(?:TITRE|Titre|TITLE|CHAPITRE|Chapitre|CHAPTER|SECTION|Section|PART|Partie|PARTIE)\s*"
    r"(?:N°|n°)?\s*(?:[IVXLCCD]+|\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN"
    r"|TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY"
    r"|(?:TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY)[- ](?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE)"
    r"|PREMIER|premier|UN|DEUX)\b",
)
_ROMAN_DASH_RE = re.compile(rf"^\s*{_ROMAN}\s*[-–—]\s*[A-ZÀ-Ü]")
_SECTION_LETTER_RE = re.compile(r"^\s*[A-Z]\s*[-–—]\s*[A-ZÀ-Ü]{3,}")
_VISA_RE = re.compile(
    r"^\s*(?:VU|VUE|VUS|Vus|Vu|Voyant|Sur\s+proposition|SUR\s+PROPOSITION)\b"
)
_CONSIDERANT_RE = re.compile(
    r"^\s*(?:CONSIDÉRANT|CONSIDERANT|Considérant|ATTENDU|Attendu|VU\s+QU'|Vu\s+qu')",
)
_DISPOSITIF_RE = re.compile(
    r"^\s*(?:A\s*R\s*R\s*Ê\s*T\s*E|A\s*R\s*R\s*E\s*T\s*E|ARRÊTE|ARRETE|Arrête|Arrête\b"
    r"|DÉCRÈTE|DECRETE|Décrète|DÉCIDE|DECIDE|Décide"
    r"|PAR\s+CES\s+MOTIFS|Par\s+ces\s+motifs|EN\s+CONCLUSION|DISPOSE|Dispose)",
)
_ANNEXE_RE = re.compile(r"^\s*(?:ANNEXE|Annexe|A\s*N\s*N\s*E\s*X\s*E)\b")
_SIGNATURE_RE = re.compile(
    r"^\s*(?:LE\s+PRÉSIDENT|LE\s+PRESIDENT|LA\s+PRÉSIDENTE|LA\s+PRESIDENTE"
    r"|LE\s+PREMIER\s+MINISTRE|LE\s+MINISTRE|LA\s+MINISTRE|LES\s+MINISTRES"
    r"|LE\s+GARDE\s+DES\s+SCEAUX|LE\s+PROCUREUR|LE\s+GREFFIER|LA\s+GREFFIÈRE"
    r"|Fait\s+à|FAIT\s+À|Fait\s+a\b|Pour\s+le\s+Président|Pour\s+le\s+Ministre"
    r"|Ampliations| Le\s+Président)",
    re.IGNORECASE,
)
# Ligne de fonction suivie d'un point final (« LE MINISTRE DE LA FONCTION PUBLIQUE, »).
_META_RE = re.compile(
    r"^\s*(?:RÉPUBLIQUE|RÉPUBLIQUE|REPUBLIQUE|PRESIDENCE|PRIMATURE|PAIX|CONSTITUTION"
    r"|LOI\s+N|LOI/D|DÉCRET\s+N|DÉCRET\s*n|DECRET\s+N|ARRÊTÉ\s+N|ORDONNANCE\s*N"
    r"|CIRCULAIRE\s+N|JOURNAL\s+OFFICIEL|MINISTÈRE|MINISTERE)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    """Normalise pour détection : espaces multiples, majuscules AccentIssue."""
    return re.sub(r"\s+", " ", text).strip()


def classify(text: str) -> str:
    """Rôle d'un bloc à partir de son texte (``""`` si aucun)."""
    line = _clean(text)
    if not line or len(line) > 400:
        return ""
    upper = line.upper()
    if _ARTICLE_RE.match(line):
        return ROLE_ARTICLE
    if _TITRE_RE.match(line):
        return ROLE_TITRE
    if _ANNEXE_RE.match(line):
        return ROLE_ANNEXE
    if _DISPOSITIF_RE.match(upper) and len(line) < 80:
        # Le dispositif est le mot seul (« ARRETE », « DÉCIDE ») : une ligne
        # avec numéro/référence (« ARRETE N°3277/… ») est un en-tête.
        rest = _DISPOSITIF_RE.sub("", upper).strip(" :;.,-–—")
        if "N°" not in rest and "NO." not in rest and len(rest) <= 12:
            return ROLE_DISPOSITIF
        return ROLE_META
    if _CONSIDERANT_RE.match(line):
        return ROLE_CONSIDERANT
    if _VISA_RE.match(line):
        return ROLE_VISA
    if _SIGNATURE_RE.match(line):
        return ROLE_SIGNATURE
    if _ALINEA_RE.match(line):
        return ROLE_ALINEA
    if _ROMAN_DASH_RE.match(line) or _SECTION_LETTER_RE.match(line):
        return ROLE_SECTION
    if _META_RE.match(line):
        return ROLE_META
    return ""


# Blocs qui ouvrent un nouveau paragraphe : la fusion ne doit jamais joindre
# la fin d'un paragraphe à un article, un alinéa ou une section.
_ROLE_PARAGRAPH_BREAKS = frozenset(
    {ROLE_ARTICLE, ROLE_TITRE, ROLE_SECTION, ROLE_ANNEXE, ROLE_ALINEA}
)

# Niveaux de titre par rôle pour les exports structurés.
_ROLE_HEADING_LEVELS = {
    ROLE_TITRE: 2,
    ROLE_SECTION: 2,
    ROLE_ARTICLE: 3,
    ROLE_ANNEXE: 2,
}


def assign_roles(blocks: list[OcrBlock]) -> list[OcrBlock]:
    """Complète ``role`` sur chaque bloc (texte), sans toucher les tableaux."""
    from dataclasses import replace

    result: list[OcrBlock] = []
    for block in blocks:
        if block.kind == "table":
            result.append(block)
            continue
        role = classify(block.text)
        result.append(replace(block, role=role))
    return result


def role_heading_level(role: str) -> int:
    """Niveau de titre DOCX/Markdown pour un rôle (0 = pas un titre)."""
    return _ROLE_HEADING_LEVELS.get(role, 0)


def role_breaks_paragraph(role: str) -> bool:
    """Vrai si un bloc de ce rôle doit démarrer un nouveau paragraphe."""
    return role in _ROLE_PARAGRAPH_BREAKS


class OutlineEntry:
    """Entrée du plan du document (rôle, texte court, page, index, boîte)."""

    __slots__ = ("bbox", "block_index", "page", "role", "text")

    def __init__(
        self,
        role: str,
        text: str,
        page: int,
        block_index: int,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.role = role
        self.text = text
        self.page = page
        self.block_index = block_index
        self.bbox = bbox


_OUTLINE_ROLES = frozenset(
    {ROLE_TITRE, ROLE_SECTION, ROLE_ARTICLE, ROLE_ANNEXE, ROLE_DISPOSITIF}
)


def build_outline(
    pages: list[tuple[int, list[OcrBlock]]],
) -> list[OutlineEntry]:
    """Plan du document : titres, sections, articles et annexes par page."""
    entries: list[OutlineEntry] = []
    for page_number, blocks in pages:
        for index, block in enumerate(blocks):
            if block.kind == "table":
                continue
            role = block.role or classify(block.text)
            if role not in _OUTLINE_ROLES:
                continue
            text = _clean(block.text)
            if not text:
                continue
            left, top, right, bottom = (float(value) for value in block.bbox)
            entries.append(OutlineEntry(role, text[:120], page_number, index, (left, top, right, bottom)))
    return entries
