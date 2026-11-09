"""Panneau de résultats : Rendu, Markdown / HTML et Texte brut.

Phase 5 : édition avant copie (R9). Les blocs de texte du rendu sont cliquables
(lien bloc ↔ texte) et surlignés quand ils sont actifs ; un double-clic (dans
le rendu ou le texte brut) ouvre un éditeur en surimpression qui met à jour le
modèle via le signal ``block_text_committed``.
"""

from __future__ import annotations

import html
import re
from typing import List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QTextCursor,
    QTextFrameFormat,
    QTextTable,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
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

# Types de blocs dont le texte est éditable avant copie (R9).
EDITABLE_TYPES = (
    BlockType.TITLE,
    BlockType.PARAGRAPH,
    BlockType.HEADER,
    BlockType.FOOTER,
)


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


def _plain_parts(page: Page) -> List[Tuple[int, str]]:
    """Parties texte brut : [(index de bloc, texte)], dans l'ordre de la page."""
    parts: List[Tuple[int, str]] = []
    for index, block in enumerate(page.blocks):
        if block.type == BlockType.TABLE:
            table = block.table
            if table is None or not table.grid:
                parts.append((index, "[tableau vide]"))
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
            parts.append((index, "\n".join(lines)))
        elif block.type == BlockType.IMAGE:
            parts.append((index, "[image]"))
        elif block.text.strip():
            parts.append((index, block.text.strip()))
    return parts


def page_to_text(page: Page) -> str:
    """Texte brut d'une page, tableaux alignés en colonnes."""
    return "\n\n".join(text for _index, text in _plain_parts(page))


def plain_text_line_ranges(page: Page) -> List[Tuple[int, int, int]]:
    """Lignes du texte brut → blocs : [(première ligne, après-dernière, bloc)].

    Sert au double-clic dans l'onglet « Texte brut » : la ligne visée est
    rattachée au bloc correspondant (les blocs vides ne produisent aucune
    ligne, le bloc le plus proche est alors retenu par la vue).
    """
    ranges: List[Tuple[int, int, int]] = []
    line = 0
    for index, text in _plain_parts(page):
        height = text.count("\n") + 1
        ranges.append((line, line + height, index))
        line += height + 1  # « \n\n » sépare les blocs d'une ligne vide
    return ranges


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


def _block_fragment(index: int, block: Block, link_blocks: bool) -> str:
    """Fragment HTML d'un bloc, avec ancre et lien cliquable optionnel."""
    anchor = f'<a name="block-{index}"></a>'

    def link(text: str) -> str:
        if not link_blocks or not text:
            return text
        return (
            f'<a href="block-{index}" '
            'style="color:#1a1d24; text-decoration:none;">'
            f"{text}</a>"
        )

    if block.type == BlockType.TITLE:
        return f"{anchor}<h4>{link(_escape(block.text))}</h4>"
    if block.type == BlockType.TABLE:
        return f"{anchor}{_table_html(block.table)}"
    if block.type == BlockType.IMAGE:
        return f'{anchor}<p style="color:#68707d;">[image]</p>'
    if block.text.strip():
        text = _escape(block.text).replace("\n", "<br>")
        return f"<p>{anchor}{link(text)}</p>"
    return ""


def page_to_html_fragment(
    page: Page,
    *,
    link_blocks: bool = False,
    active_block: Optional[int] = None,
) -> str:
    """Fragment HTML d'une page avec ancres bloc ↔ texte.

    ``link_blocks`` rend le texte des blocs cliquable (aperçu « Rendu »
    uniquement — l'export et la copie restent sans liens). ``active_block``
    surligne le bloc courant (fond accent doux, bord gauche accent).
    """
    parts: List[str] = []
    for index, block in enumerate(page.blocks):
        fragment = _block_fragment(index, block, link_blocks)
        if not fragment:
            continue
        if index == active_block:
            fragment = (
                '<div style="background-color:#eef2fb; '
                f"border-left:3px solid {theme.ACCENT}; "
                'padding:4px 8px; margin:4px 0;">'
                f"{fragment}</div>"
            )
        parts.append(fragment)
    return "\n".join(parts)


def page_to_rendered_html(
    page: Page, active_block: Optional[int] = None
) -> str:
    """Document HTML affiché dans l'onglet « Rendu » (lien + édition, R9)."""
    fragment = page_to_html_fragment(
        page, link_blocks=True, active_block=active_block
    )
    hint = (
        '<table width="100%" cellspacing="0" cellpadding="8" '
        'bgcolor="#eef2fb"><tr><td>'
        '<span style="font-size:11.5px; color:#1a1d24;">'
        "Cliquez sur un bloc de l'aperçu ou du rendu pour le lier — "
        "double-cliquez sur un bloc de texte pour l'éditer avant copie."
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


def _block_index_from_href(href: str) -> Optional[int]:
    """« block-12 » → 12 ; None pour tout autre lien."""
    prefix = "block-"
    if not href.startswith(prefix):
        return None
    try:
        return int(href[len(prefix):])
    except ValueError:
        return None


class RenderedBrowser(QTextBrowser):
    """Vue « Rendu » : clic → sélection de bloc, double-clic → édition (R9)."""

    block_clicked = Signal(int)
    block_edit_requested = Signal(int)

    def _block_index_at(self, position) -> Optional[int]:
        return _block_index_from_href(self.anchorAt(position))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._block_index_at(event.pos())
            if index is not None:
                self.block_clicked.emit(index)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._block_index_at(event.pos())
            if index is not None:
                self.block_edit_requested.emit(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class PlainView(QPlainTextEdit):
    """Vue « Texte brut » : clic / double-clic mappés sur les blocs."""

    block_clicked = Signal(int)
    block_edit_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_ranges: List[Tuple[int, int, int]] = []

    def set_line_ranges(self, ranges: List[Tuple[int, int, int]]) -> None:
        self._line_ranges = list(ranges)

    def _block_index_at(self, position) -> Optional[int]:
        if not self._line_ranges:
            return None
        line = self.cursorForPosition(position).blockNumber()
        best: Optional[int] = None
        best_distance: Optional[int] = None
        for start, end, index in self._line_ranges:
            if start <= line < end:
                return index
            distance = start - line if line < start else line - end + 1
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._block_index_at(event.pos())
            if index is not None:
                self.block_clicked.emit(index)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._block_index_at(event.pos())
            if index is not None:
                self.block_edit_requested.emit(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class BlockEditor(QPlainTextEdit):
    """Éditeur en surimpression : Ctrl+Entrée valide, Échap annule."""

    commit_requested = Signal()
    cancel_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.commit_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ResultsPanel(QWidget):
    """Onglets de résultat pour la page sélectionnée.

    Signaux (phase 5) :
    - ``block_clicked`` : un bloc de texte a été cliqué (rendu ou texte brut) ;
    - ``block_text_committed`` : édition validée (index de bloc, nouveau texte) ;
    - ``notice`` : message d'information transitoire (bloc non éditable…).
    """

    block_clicked = Signal(int)
    block_text_committed = Signal(int, str)
    editing_changed = Signal(bool)
    notice = Signal(str)

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
        self._active_block: Optional[int] = None
        self._editing_index: Optional[int] = None
        # Bloc actuellement surligné dans le HTML rendu (évite les
        # re-rendus complets quand le même bloc est visé plusieurs fois).
        self._rendered_active: Optional[int] = None

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

        self._rendered = RenderedBrowser(rendered_pane)
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

        self._plain = PlainView(plain_pane)
        self._plain.setObjectName("PlainView")
        self._plain.setReadOnly(True)
        self._plain.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._plain.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        plain_layout.addWidget(self._plain, 1)
        self._tabs.addTab(plain_pane, "Texte brut")

        # --- Éditeur en surimpression (R9) --------------------------------
        self._editor_card = QFrame(self)
        self._editor_card.setObjectName("BlockEditorCard")
        self._editor_card.setStyleSheet(
            "QFrame#BlockEditorCard {\n"
            " background: #ffffff;\n"
            f" border: 1px solid {theme.ACCENT};\n"
            " border-radius: 8px;\n"
            " }\n"
            "QLabel#BlockEditorHint {\n"
            f" color: {theme.MUTED}; font-size: 11px;\n"
            " }\n"
            "QPlainTextEdit#BlockEditor {\n"
            " background: #ffffff;\n"
            f" border: 1px solid {theme.BORDER};\n"
            " border-radius: 6px;\n"
            " font-size: 12px;\n"
            " selection-background-color: #dbe5fb;\n"
            " }"
        )
        editor_layout = QVBoxLayout(self._editor_card)
        editor_layout.setContentsMargins(8, 6, 8, 8)
        editor_layout.setSpacing(6)
        self._editor_hint = QLabel(
            "Édition du bloc — Ctrl+Entrée valide · Échap annule",
            self._editor_card,
        )
        self._editor_hint.setObjectName("BlockEditorHint")
        editor_layout.addWidget(self._editor_hint)
        self._editor = BlockEditor(self._editor_card)
        self._editor.setObjectName("BlockEditor")
        self._editor.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        editor_layout.addWidget(self._editor, 1)
        self._editor_card.hide()

        self._editor.commit_requested.connect(self._commit_editor)
        self._editor.cancel_requested.connect(self._cancel_editor)
        self._editor.installEventFilter(self)
        self._rendered.block_clicked.connect(self.block_clicked)
        self._rendered.block_edit_requested.connect(self._open_block_editor)
        self._plain.block_clicked.connect(self.block_clicked)
        self._plain.block_edit_requested.connect(self._open_block_editor)

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
    def set_page(
        self, page: Page, active_block: Optional[int] = None
    ) -> None:
        """Affiche une page (modèle à jour) ; les 3 onglets sont régénérés.

        Une édition en cours est d'abord validée (comme un Ctrl+Entrée) :
        l'arrivée d'une page livrée par le worker ou un changement de fichier
        ne perd plus la saisie.
        """
        self._flush_editor()
        self._page = page
        self._active_block = active_block
        self._markdown = page_to_markdown(page)
        self._html_document = page_to_html_document(page)
        self._rendered.setHtml(page_to_rendered_html(page, active_block))
        self._rendered_active = active_block
        self._style_tables()
        self._plain.setPlainText(page_to_text(page))
        self._plain.set_line_ranges(plain_text_line_ranges(page))
        self._update_code()
        if active_block is not None:
            self._rendered.scrollToAnchor(f"block-{active_block}")
        else:
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
        self._flush_editor()
        self._page = None
        self._markdown = ""
        self._html_document = ""
        self._active_block = None
        self._rendered_active = None
        self._rendered.setHtml(_empty_html(message))
        self._code.setPlainText("")
        self._plain.setPlainText("")
        self._plain.set_line_ranges([])

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
        """Sélectionne l'onglet Rendu, défile et surligne le bloc cliqué."""
        self.set_active_block(index, reveal=True)

    def set_active_block(self, index: Optional[int], reveal: bool = False) -> None:
        """Surligne le bloc actif dans le rendu et y fait défiler la vue.

        ``reveal`` sélectionne en plus l'onglet « Rendu » (sélection venant de
        l'aperçu). Sans bloc actif, la position de défilement est conservée.
        Si le bloc visé est déjà celui affiché, aucun re-rendu n'a lieu : les
        clics répétés (aperçu + résultats, re-sélection après commit) ne
        régénèrent pas le HTML à chaque fois.
        """
        self._active_block = index
        if self._page is None:
            return
        if index == self._rendered_active:
            if reveal:
                self._tabs.setCurrentIndex(0)
            return
        scroll = self._rendered.verticalScrollBar().value()
        self._rendered.setHtml(
            page_to_rendered_html(self._page, index)
        )
        self._rendered_active = index
        self._style_tables()
        if index is not None:
            self._rendered.scrollToAnchor(f"block-{index}")
        else:
            self._rendered.verticalScrollBar().setValue(scroll)
        if reveal:
            self._tabs.setCurrentIndex(0)

    def current_tab_index(self) -> int:
        return self._tabs.currentIndex()

    # --- Édition inline (R9) ----------------------------------------------
    def _editor_geometry(self) -> QRect:
        """Rectangle au-dessus de la vue courante où poser la carte d'édition."""
        host = self._plain if self._tabs.currentIndex() == 2 else self._rendered
        viewport = host.viewport()
        top_left = viewport.mapTo(self, QPoint(0, 0))
        area = QRect(top_left, viewport.size()).adjusted(6, 6, -6, -6)
        if area.width() < 120 or area.height() < 80:
            area = self.rect().adjusted(12, 12, -12, -12)
        return area

    def _open_block_editor(self, index: int) -> None:
        page = self._page
        if page is None or not 0 <= index < len(page.blocks):
            return
        block = page.blocks[index]
        if block.type not in EDITABLE_TYPES:
            self.notice.emit(
                "L'édition des tableaux et des images n'est pas disponible "
                "dans cette phase — seuls les blocs de texte sont modifiables."
            )
            return

        self._editing_index = index
        self._editor.setPlainText(block.text)
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(cursor)
        self._editor_card.setGeometry(self._editor_geometry())
        self._editor_card.show()
        self._editor_card.raise_()
        self._editor.setFocus()
        self.editing_changed.emit(True)

    def _commit_editor(self) -> None:
        if self._editing_index is None:
            return
        index = self._editing_index
        text = self._editor.toPlainText()
        self._close_editor()
        page = self._page
        if page is None or not 0 <= index < len(page.blocks):
            return
        if text != page.blocks[index].text:
            self.block_text_committed.emit(index, text)

    def _cancel_editor(self) -> None:
        self._close_editor()

    def _flush_editor(self) -> None:
        """Valide l'édition en cours avant un changement de contenu.

        Appelé par ``set_page`` et ``clear`` : la saisie non validée part
        vers le modèle (comme un Ctrl+Entrée) au lieu d'être perdue quand
        une page arrive du worker ou que le fichier affiché change.
        """
        if self._editing_index is None:
            return
        self._commit_editor()

    def _close_editor(self) -> None:
        was_open = self._editing_index is not None
        self._editing_index = None
        if self._editor_card.isVisible():
            self._editor_card.hide()
        if was_open:
            self.editing_changed.emit(False)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API Qt
        # Sortie de l'éditeur (clic ailleurs) : l'édition est validée,
        # sauf annulation déjà traitée (la carte est alors déjà fermée).
        if (
            obj is self._editor
            and event.type() == QEvent.Type.FocusOut
            and self._editing_index is not None
        ):
            self._commit_editor()
            return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # La carte d'édition est positionnée manuellement : elle suit la
        # géométrie de la vue pendant un redimensionnement de la fenêtre.
        if self._editing_index is not None and self._editor_card.isVisible():
            self._editor_card.setGeometry(self._editor_geometry())

    def show_tab(self, index: int) -> None:
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)
