"""Structure juridique : rôles des blocs, niveaux de titre, plan du document."""

from __future__ import annotations

from core.pipeline.interfaces import OcrBlock
from core.pipeline.legal_structure import (
    ROLE_ALINEA,
    ROLE_ANNEXE,
    ROLE_ARTICLE,
    ROLE_CONSIDERANT,
    ROLE_DISPOSITIF,
    ROLE_META,
    ROLE_SECTION,
    ROLE_SIGNATURE,
    ROLE_TITRE,
    ROLE_VISA,
    assign_roles,
    build_outline,
    classify,
    role_breaks_paragraph,
    role_heading_level,
)


def block(text: str, kind: str = "line") -> OcrBlock:
    return OcrBlock(text=text, bbox=(40.0, 100.0, 500.0, 112.0), confidence=1.0, kind=kind)


def test_article_variants() -> None:
    for text in (
        "ARTICLE 1er.- Le présent arrêté porte application des dispositions",
        "ARTICLE_10.- Sauf cas de force majeur",
        "Article 2.- (1) CAMTEL a pour objet de posséder",
        "ARTICLE 2— 1 La commission paritaire d'avancement",
        "ARTICLES 3 et 4 sont abrogés",
    ):
        assert classify(text) == ROLE_ARTICLE, text


def test_titles_sections_and_annexes() -> None:
    assert classify("TITRE I :") == ROLE_TITRE
    assert classify("CHAPTER ONE:") == ROLE_TITRE
    assert classify("SECTION EIGHTY-ONE: The amounts") == ROLE_TITRE
    assert classify("A-COMPOSITION") == ROLE_SECTION
    assert classify("I-POUR L'AVANCEMENT DES AGENTS") == ROLE_SECTION
    assert classify("ANNEXE 1 : répartition des crédits") == ROLE_ANNEXE


def test_visa_dispositif_signature() -> None:
    assert classify("VU la Constitution du 02 juin 1972 ;") == ROLE_VISA
    assert classify("CONSIDÉRANT que la situation appelle des mesures ;") == ROLE_CONSIDERANT
    assert classify("ATTENDU que le demandeur soutient que ;") == ROLE_CONSIDERANT
    assert classify("ARRETE") == ROLE_DISPOSITIF
    assert classify("A R R Ê T E :") == ROLE_DISPOSITIF
    assert classify("PAR CES MOTIFS") == ROLE_DISPOSITIF
    assert classify("LE MINISTRE DE LA FONCTION PUBLIQUE,") == ROLE_SIGNATURE
    assert classify("Fait à Yaoundé, le 5 MARS 1975") == ROLE_SIGNATURE


def test_header_with_reference_is_meta_not_dispositif() -> None:
    assert classify("ARRETE N°3277/ MFP/DP DU 27 OCTOBRE 1977") == ROLE_META
    assert classify("DECRET N° 98/198 DU 08 SEP 1998") == ROLE_META


def test_alinea_and_plain_line() -> None:
    assert classify("2°/ - Cette notation n'est valable que pour la période") == ROLE_ALINEA
    assert classify("Les fonctionnaires en position de détachement") == ""


def test_assign_roles_preserves_tables() -> None:
    table = OcrBlock(
        text="CODE\tITEM",
        bbox=(40.0, 100.0, 500.0, 200.0),
        confidence=1.0,
        kind="table",
        grid=(("CODE", "ITEM"),),
    )
    blocks = assign_roles([block("ARTICLE 1er.- Texte du premier article."), table])
    assert blocks[0].role == ROLE_ARTICLE
    assert blocks[1].role == ""


def test_role_breaks_paragraph_and_heading_levels() -> None:
    assert role_breaks_paragraph(ROLE_ARTICLE)
    assert role_breaks_paragraph(ROLE_ALINEA)
    assert not role_breaks_paragraph(ROLE_VISA)
    assert role_heading_level(ROLE_TITRE) == 2
    assert role_heading_level(ROLE_ARTICLE) == 3
    assert role_heading_level(ROLE_VISA) == 0


def test_build_outline_lists_titles_and_articles() -> None:
    pages = [
        (
            1,
            [
                block("VU la Constitution du 02 juin 1972 ;"),
                block("ARRETE"),
                block("TITRE I : DISPOSITIONS GENERALES"),
                block("ARTICLE 1er.- Le présent arrêté fixe la composition."),
            ],
        ),
        (2, [block("ARTICLE 2.- Les attributions de la commission."), block("corps")]),
    ]
    outline = build_outline(pages)
    roles = [entry.role for entry in outline]
    assert roles == [ROLE_DISPOSITIF, ROLE_TITRE, ROLE_ARTICLE, ROLE_ARTICLE]
    assert outline[0].page == 1 and outline[-1].page == 2


def test_english_judgment_structure() -> None:
    assert classify("ARTICLE I") == ROLE_ARTICLE
    assert classify("ARTICLE IV") == ROLE_ARTICLE
    assert classify("WHEREAS the Parties have agreed as follows:") == ROLE_CONSIDERANT
    assert classify("NOW, THEREFORE, the Parties hereby agree:") == ROLE_DISPOSITIF
    assert classify("IT IS HEREBY ORDERED that the application is dismissed") == ROLE_DISPOSITIF
    assert classify("HAVE AGREED as follows:") == ROLE_DISPOSITIF
    assert classify("IN WITNESS WHEREOF, the parties have signed") == ROLE_SIGNATURE
    assert classify("DONE at Yaounde, this 5th day of March 2024") == ROLE_SIGNATURE
    assert classify("JUDGMENT") == ROLE_TITRE
    assert classify("DECISION") == ROLE_TITRE
    assert classify("the order was served on the defendant") == ""
