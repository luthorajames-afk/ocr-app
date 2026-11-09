"""Panneau de résultats : Rendu, Markdown / HTML et Texte brut."""

from __future__ import annotations

import html
import re
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QTextFrameFormat, QTextTable
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.data_model import Block, BlockType, Page, Table
from ocr_app.ui import theme

_NUMERIC_RE = re.compile(r"^[-+0-9  .,%€$]+$")


def _looks_numeric(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and _NUMERIC_RE.match(stripped) is not None


def _escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def _table_markdown(table: Optional[Table]) -> str:
    if table is None or not table.grid:
        return "[tableau vide]"
    rows = table.grid
    cols = max(len(row) for row in rows)
    lines: List[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for col_index in range(cols):
            cell = row[col_index] if col_index < len(row) else None
            text = (cell.text if cell is not None else "").replace("|", "\\|")
            cells.append(text.strip())
        lines.append("| " + " | ".join(cells) + " |")
        if row_index == 0:
            lines.append("|" + "---|" * cols)
    return "\n".join(lines)


def page_to_markdown(page: Page) -> str:
    """Markdown d'une page : titre, paragraphes et tableaux."""
    parts: List[str] = []
    for block in page.blocks:
        if block.type == BlockType.TITLE:
            parts.append(f"# {block.text.strip()}")
        elif block.type == BlockType.TABLE:
            parts.append(_table_markdown(block.table))
        elif block.type == BlockType.IMAGE:
            parts.append("[image]")
        elif block.text.strip():
            parts.append(block.text.strip())
    return "\n\n".join(parts) + ("\n" if parts else "")


def page_to_text(page: Page) -> str:
    """Texte brut d'une page, tableaux alignés en colonnes."""
    parts: List[str] = []
    for block in page.blocks:
        if block.type == BlockType.TABLE:
            table = block.table
            if table is None or not table.grid:
                parts.append("[tableau vide]")
                continue
            rows = [
                [
                    (cell.text if cell is not None else "").strip()
                    for cell in row
                ]
                for row in table.grid
            ]
            cols = max((len(row) for row in rows), default=0)
            widths = [
                max(
                    (len(row[col]) if col < len(row) else 0 for row in rows),
                    default=0,
                )
                for col in range(cols)
            ]
            lines = []
            for row in rows:
                cells = [
                    (row[col] if col < len(row) else "").ljust(widths[col])
                    for col in range(cols)
                ]
                lines.append("   ".join(cells).rstrip())
            parts.append("\n".join(lines))
        elif block.type == BlockType.IMAGE:
            parts.append("[image]")
        elif block.text.strip():
            parts.append(block.text.strip())
    return "\n\n".join(parts)


def _table_html(table: Optional[Table]) -> str:
    if table is None or not table.grid:
        return '<p style="color:#68707d;">[tableau vide]</p>'
    rows = table.grid
    cols = max(len(row) for row in rows)
    lines = [
        '<table width="100%" border="1" cellspacing="0" cellpadding="5" '
        'bordercolor="#dfe3ea">'
    ]
    for row_index, row in enumerate(rows):
        lines.append("<tr>")
        for col_index in range(cols):
            cell = row[col_index] if col_index < len(row) else None
            text = _escape(cell.text if cell is not None else "")
            if not text:
                text = "&nbsp;"
            if row_index == 0:
                lines.append(
                    f'<td bgcolor="#f2f4f7"><b>{text}</b></td>'
                )
            else:
                align = ' align="right"' if _looks_numeric(cell.text if cell else "") else ""
                lines.append(f"<td{align}>{text}</td>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def page_to_html_fragment(page: Page) -> str:
    """Fragment HTML d'une page avec ancres bloc ↔ texte."""
    parts: List[str] = []
    for index, block in enumerate(page.blocks):
        anchor = f'<a name="block-{index}"></a>'
        if block.type == BlockType.TITLE:
            parts.append(f"{anchor}<h4>{_escape(block.text)}</h4>")
        elif block.type == BlockType.TABLE:
            parts.append(f"{anchor}{_table_html(block.table)}")
        elif block.type == BlockType.IMAGE:
            parts.append(f'{anchor}<p style="color:#68707d;">[image]</p>')
        elif block.text.strip():
            text = _escape(block.text).replace("\n", "<br>")
            parts.append(f"<p>{anchor}{text}</p>")
    return "\n".join(parts)


def page_to_rendered_html(page: Page) -> str:
    """Document HTML affiché dans l'onglet « Rendu » (avec l'aide R9)."""
    fragment = page_to_html_fragment(page)
    hint = (
        '<table width="100%" cellspacing="0" cellpadding="8" '
        'bgcolor="#eef2fb"><tr><td>'
        '<span style="font-size:11.5px; color:#1a1d24;">'
        "Cliquez sur un bloc de l'aperçu pour le modifier avant copie — "
        "le lien bloc ↔ texte est conservé."
        "</span></td></tr></table>"
    )
    return (
        "<html><body style=\"font-family:'Segoe UI'; font-size:12.5px; "
        "color:#1a1d24; background-color:#ffffff;\">"
        f"{fragment}"
        '<div style="margin-top:11px;">'
        f"{hint}"
        "</div>"
        "</body></html>"
    )


def page_to_html_document(page: Page) -> str:
    """Document HTML autonome pour l'onglet « Markdown / HTML »."""
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        "</head>\n<body>\n"
        f"{page_to_html_fragment(page)}\n"
        "</body>\n</html>\n"
    )


def _empty_html(message: str) -> str:
    return (
        "<html><body style=\"font-family:'Segoe UI'; font-size:12.5px; "
        "color:#9aa1ad; background-color:#ffffff;\">"
        '<p align="center" style="margin-top:48px;">'
        f"{_escape(message)}</p></body></html>"
    )


class ResultsPanel(QWidget):
    """Onglets de résultat pour la page sélectionnée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(320)
        self.setMaximumWidth(520)

        self._page: Optional[Page] = None
        self._markdown = ""
        self._html_document = ""
        self._code_mode = "markdown"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("ResultsTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setUsesScrollButtons(False)
        layout.addWidget(self._tabs)

        # --- Onglet Rendu -------------------------------------------------
        rendered_pane = QWidget(self)
        rendered_pane.setObjectName("ResultsPane")
        rendered_pane.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        rendered_layout = QVBoxLayout(rendered_pane)
        rendered_layout.setContentsMargins(0, 0, 0, 0)
        rendered_layout.setSpacing(0)

        self._rendered = QTextBrowser(rendered_pane)
        self._rendered.setObjectName("RenderedView")
        self._rendered.setOpenLinks(False)
        self._rendered.setOpenExternalLinks(False)
        self._rendered.document().setDocumentMargin(14)
        rendered_layout.addWidget(self._rendered, 1)
        self._tabs.addTab(rendered_pane, "Rendu")

        # --- Onglet Markdown / HTML --------------------------------------
        code_pane = QWidget(self)
        code_pane.setObjectName("CodePane")
        code_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        code_layout = QVBoxLayout(code_pane)
        code_layout.setContentsMargins(12, 10, 12, 12)
        code_layout.setSpacing(8)

        code_head = QHBoxLayout()
        code_head.setContentsMargins(0, 0, 0, 0)
        code_head.setSpacing(4)
        code_head.addWidget(self._muted_label("Format", code_pane))

        self._format_group = QButtonGroup(self)
        self._format_group.setExclusive(True)
        self._markdown_button = self._sub_mode_button("Markdown", code_pane)
        self._html_button = self._sub_mode_button("HTML", code_pane)
        self._format_group.addButton(self._markdown_button, 0)
        self._format_group.addButton(self._html_button, 1)
        self._markdown_button.setChecked(True)
        self._markdown_button.clicked.connect(
            lambda: self.set_code_mode("markdown")
        )
        self._html_button.clicked.connect(lambda: self.set_code_mode("html"))
        code_head.addWidget(self._markdown_button)
        code_head.addWidget(self._html_button)
        code_head.addStretch(1)
        code_layout.addLayout(code_head)

        self._code = QPlainTextEdit(code_pane)
        self._code.setObjectName("CodeView")
        self._code.setReadOnly(True)
        self._code.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._code.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        code_layout.addWidget(self._code, 1)
        self._tabs.addTab(code_pane, "Markdown / HTML")

        # --- Onglet Texte brut -------------------------------------------
        plain_pane = QWidget(self)
        plain_pane.setObjectName("CodePane")
        plain_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        plain_layout = QVBoxLayout(plain_pane)
        plain_layout.setContentsMargins(12, 12, 12, 12)
        plain_layout.setSpacing(0)

        self._plain = QPlainTextEdit(plain_pane)
        self._plain.setObjectName("PlainView")
        self._plain.setReadOnly(True)
        self._plain.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._plain.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        plain_layout.addWidget(self._plain, 1)
        self._tabs.addTab(plain_pane, "Texte brut")

        self.clear()

    @staticmethod
    def _muted_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setStyleSheet(f"color:{theme.MUTED}; font-size:11.5px;")
        return label

    @staticmethod
    def _sub_mode_button(text: str, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("SubModeButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    # --- Contenu ----------------------------------------------------------
    def set_page(self, page: Page) -> None:
        self._page = page
        self._markdown = page_to_markdown(page)
        self._html_document = page_to_html_document(page)
        self._rendered.setHtml(page_to_rendered_html(page))
        self._style_tables()
        self._plain.setPlainText(page_to_text(page))
        self._update_code()
        self._rendered.verticalScrollBar().setValue(0)

    def _style_tables(self) -> None:
        """Applique des bordures claires aux tableaux rendus (comme le mockup)."""
        document = self._rendered.document()
        stack = [document.rootFrame()]
        tables: List[QTextTable] = []
        while stack:
            frame = stack.pop()
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    tables.append(child)
                stack.append(child)

        brush = QBrush(QColor(theme.BORDER))
        style = QTextFrameFormat.BorderStyle.BorderStyle_Solid
        for table in tables:
            # Le bandeau d'aide n'a pas de bordure dans le mockup.
            cell_background = (
                table.cellAt(0, 0).format().background().color().name()
                if table.rows() and table.columns()
                else ""
            )
            if cell_background.lower() == theme.ACCENT_SOFT.lower():
                continue

            table_format = table.format()
            table_format.setBorder(1.0)
            table_format.setBorderBrush(brush)
            table_format.setBorderStyle(style)
            table.setFormat(table_format)

            for row in range(table.rows()):
                for col in range(table.columns()):
                    cell = table.cellAt(row, col)
                    cell_format = cell.format().toTableCellFormat()
                    cell_format.setLeftBorder(1.0)
                    cell_format.setRightBorder(1.0)
                    cell_format.setTopBorder(1.0)
                    cell_format.setBottomBorder(1.0)
                    cell_format.setLeftBorderBrush(brush)
                    cell_format.setRightBorderBrush(brush)
                    cell_format.setTopBorderBrush(brush)
                    cell_format.setBottomBorderBrush(brush)
                    cell_format.setLeftBorderStyle(style)
                    cell_format.setRightBorderStyle(style)
                    cell_format.setTopBorderStyle(style)
                    cell_format.setBottomBorderStyle(style)
                    cell.setFormat(cell_format)

    def clear(self, message: str = "Aucun résultat à afficher.") -> None:
        self._page = None
        self._markdown = ""
        self._html_document = ""
        self._rendered.setHtml(_empty_html(message))
        self._code.setPlainText("")
        self._plain.setPlainText("")

    def has_page(self) -> bool:
        return self._page is not None

    def current_page(self) -> Optional[Page]:
        return self._page

    def set_code_mode(self, mode: str) -> None:
        self._code_mode = "html" if mode == "html" else "markdown"
        self._markdown_button.setChecked(self._code_mode == "markdown")
        self._html_button.setChecked(self._code_mode == "html")
        self._update_code()

    def _update_code(self) -> None:
        if self._page is None:
            self._code.setPlainText("")
            return
        content = (
            self._html_document
            if self._code_mode == "html"
            else self._markdown
        )
        self._code.setPlainText(content)

    def scroll_to_block(self, index: int) -> None:
        """Sélectionne l'onglet Rendu et fait défiler jusqu'au bloc cliqué."""
        self._tabs.setCurrentIndex(0)
        self._rendered.scrollToAnchor(f"block-{index}")

    def show_tab(self, index: int) -> None:
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)
