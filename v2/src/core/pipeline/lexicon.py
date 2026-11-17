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
    "exercice", "exercices", "dotation", "dotations", "autorisation",
    "engagement", "engagements", "ordonnancement", "liquidation",
    "recettes", "dépenses", "trésorerie", "fiscalité", "impôt", "impôts",
    "douane", "douanes", "subvention", "subventions", "emprunt", "emprunts",
    "dette", "dettes", "garantie", "garanties", "échéancier", "échéance",
    "privatisation", "restructuration", "assainissement", "régulation",
    "nomenclature", "comptabilité", "comptable", "ordonnateur",
    "contrôleur", "perception", "recouvrement", "décaissement",
    "ordonnance", "ordonnances", "arrêté", "arrêtés", "décret", "décrets",
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
    "exercise", "appropriation", "appropriations", "allocation", "endowment",
    "commitment", "commitments", "disbursement", "settlement", "clearance",
    "treasury", "taxation", "customs", "duty", "duties", "subsidy",
    "subsidies", "loan", "loans", "debt", "debts", "guarantee", "guarantees",
    "repayment", "maturity", "privatization", "restructuring", "sanitation",
    "regulation", "nomenclature", "accounting", "accountant", "authorizing",
    "controller", "collection", "recovery", "forecast", "outturn",
)

# Mots français courants des documents administratifs (correcteur machine à
# écrire : les substitutions ne sont validées que contre ce vocabulaire).
COMMON_FRENCH: tuple[str, ...] = (
    "travaillé", "travaillés", "modifié", "modifiée", "modifiés", "modifiées",
    "noté", "notés", "notée", "notées", "notation", "période", "périodes",
    "année", "années", "annuelle", "annuelles", "annuel", "supérieure",
    "supérieurs", "supérieur", "générales", "général", "générale", "prévues",
    "prévue", "prévu", "prévus", "arrêté", "arrêtés", "arrêtée", "candidature",
    "candidatures", "administration", "administrations", "administrative",
    "fonctionnaire", "fonctionnaires", "fonction", "fonctions", "détachement",
    "retraite", "salaires", "salaire", "hiérarchique", "hiérarchiques",
    "autorités", "autorité", "dispositions", "disposition", "susvisé",
    "susvisée", "susvisés", "alinéa", "alinéas", "application", "appliquée",
    "appliqué", "composition", "attributions", "modalités", "modalité",
    "avancement", "avancements", "commission", "commissions", "personnel",
    "personnels", "représentants", "représentant", "délégués", "délégué",
    "bénéficiaire", "bénéficiaires", "bénéfice", "provisoire", "provisoirement",
    "définitif", "définitive", "définitivement", "temporairement", "temporaire",
    "intéressé", "intéressée", "intéressés", "suppression", "supprimé",
    "remplacé", "remplacement", "assumé", "assumés", "effective", "effectif",
    "effectuée", "effectué", "situation", "situations", "postérieur",
    "postérieure", "antérieure", "antérieur", "postérieurement",
    "conformément", "respectivement", "immédiatement", "ultérieurement",
    "actuellement", "nécessaire", "nécessaires", "nécessairement",
    "respecter", "respecté", "respectée", "observation", "conditions",
    "condition", "régime", "régimes", "réglementaire", "réglementaires",
    "réglementation", "organisées", "organisé", "organisation", "constaté",
    "constatée", "exercice", "exercices", "trimestre", "trimestres",
    " mensuel", "mensuelle", "mensuelles", "quotient", "dépenses", "dépense",
    "recettes", "recette", "montant", "montants", "paiement", "paiements",
    "versement", "versements", "liquidation", "liquidations", "retenues",
    "retenue", "pensions", "pension", "prestation", "prestations",
    "allocation", "allocations", "indemnité", "indemnités", "indemnité",
    "prorogation", "prolongation", "prolongations", "réorganisation",
    "déconcentration", "décentralisation", "entreprise", "entreprises",
    "établissements", "établissement", "exploitation", "exploitations",
    "installations", "installation", "télécommunications", "capitaux",
    "capital", "infrastructure", "infrastructures", "opération",
    "opérations", "acquisition", "acquisitions", "gestion", "direction",
    "développement", "aménagement", "exécution", "exécution des budgets",
    "exercer", "exerce", "exercé", "exercés", "posséder", "possède",
    "création", "créée", "créé", "supprimées", "caractéristiques",
    "matériel", "matériels", "supplies", "quantité", "qualité", "qualités",
    "délai", "délais", "durée", "durées", "fixée", "fixé", "fixées", "fixés",
    "déterminé", "déterminée", "déterminées", "déterminant", "déterminants",
    "calculé", "calculée", "estimé", "estimée", "évaluée", "évalués",
    "validée", "validé", "approuvée", "approuvé", "autorisée", "autorisé",
    "autorisations", "habilitation", "habilité", "habilités", "habilitée",
    "signataire", "signataires", "signature", "enregistré", "enregistrée",
    "publié", "publiée", "publication", "diffusion", "transmission",
    "notification", "notifiée", "décision", "décisions", "décidée", "décidé",
    "jugement", "jugements", "jugée", "jugé", "audience", "audiences",
    "auditoire", "requérant", "requérante", "défendeur", "défenderesse",
    "demandeur", "demanderesse", "demande", "demandes", "conclusion",
    "conclusions", "récipient", "réception", "reçoit", "reçu", "adresse",
    "adressée", "adressé", "transmise", "transmis", "dossier", "dossiers",
    "pièces", "pièce", "copie", "copies", "exemplaire", "exemplaires",
    "originale", "original", "originaux", "bureau", "bureaux", "service",
    "services", "division", "divisions", "cellule", "cellules",
    "responsable", "responsables", "chargé", "chargée", "chargés",
    "désigné", "désignée", "désignés", "nommé", "nommée", "nommés",
    "ministre", "ministres", "ministère", "ministères", "secrétaire",
    "secrétaires", "secrétariat", "secrétariats", "général", "trésor",
    "trésorerie", "trésorier", "comptable", "comptables", "comptabilité",
    "ordonnateur", "ordonnateurs", "ordonnance", "ordonnances",
    "contrôleur", "contrôleurs", "contrôle", "contrôles", "vérification",
    "vérifié", "vérifiée", "certifié", "certifiée", "attesté", "attestation",
    "côté", "côtés", "côte", "côtes", "hôtel", "hôtels", "hôpital",
    "hôpitaux", "tôt", "bientôt", "plutôt", "château", "châteaux",
    "dépôt", "dépôts", "impôt", "impôts", "rôle", "rôles", "contrôle",
    "contrôles", "contrôler", "drôle", "diplôme", "diplômes", "pôle",
    "pôles", "entrepôt", "entrepôts", "trône", "dôme", "arôme", "allô",
    "nôtre", "vôtre", "très", "près", "après", "dès", "progrès", "accès",
    "succès", "excès", "décès", "prévoir", "travaux", "exécuter", "exécutés", "exécuté", "réunis",
    "réuni", "réunion", "comité", "comités", "payé", "payés", "payée",
    "payées", "versé", "versés", "acheté", "achetés", "imprimé", "imprimés",
    "rédigé", "rédigés", "adoption", "adopté", "adoptée", "adoptés",
    "procédure", "procédures", "étape", "étapes", "phase", "phases",
    "opérationnel", "opérationnels", "technique", "techniques",
    "administratif", "administratifs", "financier", "financiers",
    "financière", "financières", "budgétaire", "budgétaires",
    "mandataire", "mandataires", "mandat", "mandats", "trimestre",
)

# Vocabulaire juridique/administratif anglais extrait du corpus Library
# (lois camerounaises EN, accords OMC, décrets) — 117 documents analysés.
ENGLISH_LEGAL_TERMS: tuple[str, ...] = (
    "republic", "decree", "decrees", "cameroon", "president", "public",
    "section", "hereby", "service", "services", "national", "government",
    "provisions", "agreement", "trade", "development", "december", "general",
    "officer", "regulations", "official", "members", "follows", "financial",
    "registered", "according", "constitution", "state", "procedure", "french",
    "published", "july", "association", "urgency", "english", "amended",
    "gazette", "inserted", "rules", "fatherland", "supplemented", "minister",
    "cultural", "yaounde", "finance", "research", "adopted", "ministry",
    "relating", "products", "april", "regional", "signed", "conditions",
    "administrative", "activity", "salary", "march", "duties", "mining",
    "charge", "loan", "following", "organize", "peace", "council",
    "activities", "board", "artistic", "member", "ratify", "agency",
    "authority", "associations", "country", "personnel", "authorize", "year",
    "technical", "amount", "legal", "project", "presidence", "parliament",
    "union", "june", "million", "referred", "enacts", "office", "economy",
    "production", "sector", "forest", "provided", "date", "governing",
    "authorized", "period", "local", "planning", "system", "subject",
    "region", "approximately", "information", "force", "policy", "january",
    "product", "effect", "management", "deliberated", "representative",
    "senior", "private", "administration", "natural", "sign", "cfaf",
    "economic", "authorities", "code", "division", "billion", "customs",
    "particular", "international", "enterprises", "consular", "transport",
    "documents", "apply", "person", "chapter", "assistant", "orders",
    "purpose", "applicable", "implementation", "contract", "investment",
    "replacement", "domestic", "goods", "resources", "directors", "visa",
    "bank", "approval", "application", "foreign", "imports", "special",
    "signature", "credit", "divisional", "accordance", "ensure", "civil",
    "appointed", "head", "organization", "rural", "cnsc", "order", "costs",
    "required", "practice", "appoint", "review", "governed", "export",
    "electrification", "powers", "previously", "financing", "ratified",
    "concerned", "value", "price", "labour", "security", "contracts",
    "transferred", "constitutional", "functioning", "paragraph", "report",
    "degree", "rate", "protection", "form", "companies", "convention",
    "including", "term", "military", "engaged", "territorial", "wildlife",
    "family", "archives", "case", "implementing", "commercial", "unions",
    "corporations", "supervisory", "cooperation", "safety", "necessary",
    "unit", "respect", "measures", "corporation", "territory", "instruments",
    "medical", "framework", "index", "african", "culture", "practices",
    "years", "gatt", "increase", "material", "related", "business",
    "persons", "areas", "water", "requirements", "degrees", "property",
    "federation", "electoral", "document", "secretariat", "appropriate",
    "purposes", "staff", "right", "issued", "responsible", "definitions",
    "land", "prime", "establishments", "holders", "passports", "granted",
    "budget", "impact", "relevant", "environment", "basic", "provide",
    "market", "taxes", "process", "november", "remuneration", "laws",
    "social", "facilities", "presidency", "delegation", "exemption", "area",
)

BILINGUAL_LEXICON: tuple[str, ...] = (
    DEFAULT_LEXICON + ENGLISH_LEXICON + ENGLISH_LEGAL_TERMS + COMMON_FRENCH
)


# Confusions OCR / couche texte fréquentes (mots entiers, minuscules).
ARTIFACT_FIXES_FR: dict[str, str] = {
    "decret": "décret",
    "arrete": "arrêté",
    "republlque": "république",
    "rbfublique": "république",
    "gouvemement": "gouvernement",
    "paragraphe": "paragraphe",
    "alinées": "alinéas",
    "entrafner": "entraîner",
    "cendidature": "candidature",
    "nowore": "nombre",
    "fonctionna": "fonctionnaire",
    "forction": "fonction",
    "natwe": "nature",
    "mement": "moment",
    "camerci": "cameroun",
    "determinattsa": "déterminant sa",
    "rrete": "arrêté",
    "jer": "1er",
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
    "agreernent": "agreement",
    "cornmittee": "committee",
    "governrnent": "government",
    "parliarnent": "parliament",
    "departrnent": "department",
    "docurnent": "document",
    "payrnent": "payment",
    "staturnent": "statement",
    "natlonal": "national",
    "flnanclal": "financial",
    "sectlon": "section",
    "provlslons": "provisions",
    "regulatrons": "regulations",
    "admlnlstratlon": "administration",
    "publlcatlon": "publication",
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
    fuzzy: bool = True,
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
        if len(token) < 4 or not fuzzy:
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
