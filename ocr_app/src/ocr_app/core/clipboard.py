"""Presse-papiers riche (phase 6) — construction des chargeurs utiles.

Ce module est volontairement indépendant de Qt : il assemble, depuis les
``Page`` / ``Document`` du modèle, un chargeur ``{html, text, markdown}`` prêt
à poser sur le presse-papiers. Le passage réel à Qt (``QMimeData`` avec
``text/plain`` + ``text/html`` + ``text/markdown``) est fait par
``ocr_app.ui.clipboard_qt``.

R9 : les ``Page`` sont partagées avec l'``EditSession`` — le HTML/texte
générés ici reflètent donc toujours les éditions en cours. Le HTML ne
contient ni ancres ni liens d'édition : uniquement le contenu net, avec de
vrais ``<table>`` (collable dans Word ET Excel en conservant la structure des
tableaux, ``colspan``/``rowspan`` pour les fusions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ocr_app.core.data_model import BlockType, Document, Page
from ocr_app.core.exports import (
    clean_document_html,
    clean_page_html,
    document_markdown,
    document_plain_text,
    html_document,
    page_markdown,
    page_plain_text,
)


@dataclass(frozen=True)
class ClipboardPayload:
    """Chargeur prêt pour le presse-papiers : trois formats de même contenu."""

    html: str
    text: str
    markdown: str


def _page_title(page: Page) -> str:
    """Titre lisible pour ``<title>`` : premier bloc titre, sinon la page."""
    for block in page.blocks:
        if block.type == BlockType.TITLE and block.text.strip():
            return block.text.strip()
    return f"Page {page.number}"


def page_payload(page: Page) -> ClipboardPayload:
    """Chargeur pour la page affichée (contenu net, sans bandeau de page)."""
    return ClipboardPayload(
        html=html_document(_page_title(page), clean_page_html(page)),
        text=page_plain_text(page),
        markdown=page_markdown(page),
    )


def document_payload(document: Optional[Document]) -> ClipboardPayload:
    """Chargeur pour tout le document (titre + une section par page)."""
    if document is None:
        return ClipboardPayload(html="", text="", markdown="")
    # Vue triée par numéro, sans muter le document partagé avec l'interface ;
    # clean_document_html place le titre du document en <h1>.
    view = Document(
        title=document.title,
        pages=sorted(document.pages, key=lambda page: page.number),
        source_path=document.source_path,
    )
    return ClipboardPayload(
        html=html_document(view.title, clean_document_html(view)),
        text=document_plain_text(view),
        markdown=document_markdown(view),
    )
