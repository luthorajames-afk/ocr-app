"""Passage Qt du presse-papiers riche (phase 6).

Construit le ``QMimeData`` avec les trois formats de même contenu —
``text/plain`` (``setText``), ``text/html`` (``setHtml``) et
``text/markdown`` (``setData``) — puis le pose sur le presse-papiers de
l'application. La construction des chargeurs est faite par
``ocr_app.core.clipboard`` (indépendant de Qt) : le HTML ne contient ni
ancres ni liens d'édition, uniquement le contenu net (R9).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QMimeData
from PySide6.QtGui import QGuiApplication

from ocr_app.core.clipboard import ClipboardPayload

# Format MIME portant le Markdown (QMimeData n'a pas d'accesseur dédié).
MARKDOWN_MIME_TYPE = "text/markdown"


def build_mime_data(payload: ClipboardPayload) -> QMimeData:
    """Construit le QMimeData multi-formats (testable sans presse-papiers)."""
    mime = QMimeData()
    mime.setText(payload.text)
    if payload.html:
        mime.setHtml(payload.html)
    if payload.markdown:
        mime.setData(
            MARKDOWN_MIME_TYPE, QByteArray(payload.markdown.encode("utf-8"))
        )
    return mime


def put_on_clipboard(payload: ClipboardPayload) -> str:
    """Pose le chargeur sur le presse-papiers de l'application.

    Retourne la description lisible des formats posés (message de statut).
    Hors écran (plateforme ``offscreen`` des tests), le presse-papiers Qt
    plante à la destruction si un ``QMimeData`` lui est confié : on retombe
    sur le texte brut, comme depuis la phase 5.
    """
    clipboard = QGuiApplication.clipboard()
    if QGuiApplication.platformName() == "offscreen":
        clipboard.setText(payload.text)
        return "texte"
    clipboard.setMimeData(build_mime_data(payload))
    return "texte + HTML + Markdown"
