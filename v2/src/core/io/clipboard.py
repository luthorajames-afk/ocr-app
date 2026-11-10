"""T6.1 — Presse-papiers HTML riche (tableaux collables dans Word et Excel).

Pur Python (sans Qt) : construit le payload {html, text, markdown} depuis les
pages traitées. L'échappement HTML est systématique (aucune injection).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any


@dataclass(frozen=True, slots=True)
class ClipboardPayload:
    html: str
    text: str
    markdown: str


def _table_html(grid: list[list[Any]]) -> str:
    rows: list[str] = ["<table border='1' cellspacing='0' cellpadding='4'>"]
    for index, row in enumerate(grid):
        tag = "th" if index == 0 else "td"
        cells = "".join(f"<{tag}>{escape(str(cell or ''))}</{tag}>" for cell in row)
        rows.append(f"<tr>{cells}</tr>")
    rows.append("</table>")
    return "".join(rows)


def _table_markdown(grid: list[list[Any]]) -> str:
    if not grid:
        return ""
    header = "| " + " | ".join(str(cell or "") for cell in grid[0]) + " |"
    separator = "|" + "---|" * len(grid[0])
    body = [
        "| " + " | ".join(str(cell or "") for cell in row) + " |" for row in grid[1:]
    ]
    return "\n".join([header, separator, *body])


def page_payload(record: dict[str, Any], *, page_number: int | None = None) -> ClipboardPayload:
    """Payload d'une page : titres/paragraphes + tableaux éventuels."""
    number = page_number or int(record.get("page", 1) or 1)
    text = str(record.get("text", ""))
    html_parts: list[str] = [f"<h2>Page {number}</h2>"]
    markdown_parts: list[str] = [f"## Page {number}", ""]
    plain_parts: list[str] = []

    for line in text.splitlines():
        if line.strip():
            html_parts.append(f"<p>{escape(line)}</p>")
            plain_parts.append(line)

    for table in record.get("tables", []) or []:
        grid = table.get("grid") if isinstance(table, dict) else None
        if isinstance(grid, list) and grid:
            html_parts.append(_table_html(grid))
            markdown_parts.extend(["", _table_markdown(grid)])

    return ClipboardPayload(
        html="".join(html_parts) if html_parts else "<p>[page vide]</p>",
        text="\n".join(plain_parts),
        markdown="\n".join(markdown_parts).strip(),
    )


def document_payload(records: list[dict[str, Any]]) -> ClipboardPayload:
    """Payload du document complet (pages triées par numéro)."""
    ordered = sorted(records, key=lambda item: int(item.get("page", 1) or 1))
    payloads = [page_payload(record) for record in ordered]
    return ClipboardPayload(
        html="".join(payload.html for payload in payloads) or "<p>[document vide]</p>",
        text="\n\n".join(payload.text for payload in payloads if payload.text),
        markdown="\n\n".join(payload.markdown for payload in payloads if payload.markdown),
    )
