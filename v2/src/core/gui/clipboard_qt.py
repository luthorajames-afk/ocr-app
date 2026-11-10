"""T6.1 — Adaptation Qt : QMimeData {text/plain, text/html, text/markdown}."""

from __future__ import annotations

from PySide6.QtCore import QMimeData

from core.io.clipboard import ClipboardPayload

MARKDOWN_MIME = "text/markdown"


def build_mime_data(payload: ClipboardPayload) -> QMimeData:
    mime = QMimeData()
    mime.setText(payload.text)
    mime.setHtml(payload.html)
    mime.setData(MARKDOWN_MIME, payload.markdown.encode("utf-8"))
    return mime


def put_on_clipboard(payload: ClipboardPayload) -> None:
    from PySide6.QtWidgets import QApplication

    clipboard = QApplication.clipboard()
    clipboard.setMimeData(build_mime_data(payload))
