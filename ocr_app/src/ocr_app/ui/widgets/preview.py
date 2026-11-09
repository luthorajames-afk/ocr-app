"""Aperçu de la page : image réelle du document + surcouches de blocs.

Plan v1 : le worker écrit ``page_NNN.json`` ET ``page_NNN.png`` (rendu 300 DPI)
dans le dossier de travail de la tâche. L'aperçu charge l'image comme fond
réel de la page et pose les surcouches de blocs par-dessus, à l'échelle
correcte : les bboxes des blocs sont en points PDF (1 pt = 1/72 po) et
l'image est rendue à ``render_dpi``, d'où ``scale = px_image / largeur_pt``.
En l'absence d'image (mode démo, anciens dossiers, échec d'écriture), repli
sur le rendu vectoriel historique (placeholder ou blocs dessinés).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.data_model import Block, BlockType, Page, Table
from ocr_app.ui import theme
from ocr_app.ui.icons import make_icon

# Page A4 affichée à 430 px de large (comme le mockup).
PAGE_W = 430.0
A4_W = 595.0
PAGE_SCALE = PAGE_W / A4_W
PAGE_H = 842.0 * PAGE_SCALE

# Borne mémoire de l'aperçu (R7) : une image plus large que cela (scan très
# haute résolution) est réduite avant affichage. A4 à 300 DPI = 2 481 px.
_MAX_PREVIEW_PX = 3500

# Attribut transient posé par ui/task_runner.py sur chaque page chargée
# depuis un résultat : dossier de travail contenant page_NNN.png.
_PAGE_WORK_DIR_ATTR = "work_dir"


BLOCK_LABELS = {
    BlockType.TITLE: "titre",
    BlockType.PARAGRAPH: "paragraphe",
    BlockType.TABLE: "tableau",
    BlockType.IMAGE: "image",
    BlockType.HEADER: "en-tête",
    BlockType.FOOTER: "pied de page",
}

_NUMERIC_RE = re.compile(r"^[-+0-9  .,%€$]+$")


def _page_image_path(page: Page, image_dir: Optional[str] = None) -> Optional[Path]:
    """Chemin de ``page_NNN.png`` pour la page, si le dossier est connu.

    Le dossier de travail voyage avec la page (attribut transient posé par
    le ``TaskRunner``) ou est passé explicitement (``image_dir``, tests).
    """
    directory = image_dir or getattr(page, _PAGE_WORK_DIR_ATTR, None)
    if not directory:
        return None
    return Path(str(directory)) / f"page_{page.number:03d}.png"


def _load_page_pixmap(path: Optional[Path]) -> Optional[QPixmap]:
    """Charge l'image de page, bornée en taille (None si indisponible)."""
    pixmap, _point_scale = _load_page_background(path)
    return pixmap


def _load_page_background(path: Optional[Path]) -> Tuple[Optional[QPixmap], float]:
    """Charge ``page_NNN.png`` et retourne ``(pixmap, échelle points→scène)``.

    L'échelle des bboxes (points PDF) est dérivée du DPI enregistré dans le
    PNG (métadonnée pHYs écrite par le pipeline) : la page mesure
    ``largeur_px × 72 / dpi`` points, d'où ``scale = PAGE_W / largeur_pt``.
    Le calcul utilise la largeur AVANT la réduction d'affichage
    (``_MAX_PREVIEW_PX``) : le DPI décrit l'image d'origine. Ce calcul rend
    les surcouches justes pour toute taille de page (paysage, Letter…),
    pas seulement l'A4 portrait. Sans métadonnée fiable (30..2400 DPI),
    repli sur ``PAGE_SCALE`` (hypothèse A4 historique).
    """
    if path is None:
        return None, PAGE_SCALE
    try:
        if not path.is_file():
            return None, PAGE_SCALE
    except OSError:  # pragma: no cover - chemin illisible
        return None, PAGE_SCALE
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None, PAGE_SCALE
    dpm = image.dotsPerMeterX()
    dpi = dpm * 0.0254 if dpm > 0 else 0.0
    if 30.0 <= dpi <= 2400.0:
        point_scale = PAGE_W * dpi / (float(image.width()) * 72.0)
    else:
        point_scale = PAGE_SCALE
    if image.width() > _MAX_PREVIEW_PX:
        scaled = image.scaledToWidth(
            _MAX_PREVIEW_PX, Qt.TransformationMode.SmoothTransformation
        )
        if not scaled.isNull():
            image = scaled
    return QPixmap.fromImage(image), point_scale


def block_scene_rect(block: Block, scale: float = PAGE_SCALE) -> QRectF:
    """Rect scène d'un bloc : bbox points PDF (origine haut-gauche) → scène."""
    x0, y0, x1, y1 = block.bbox
    return QRectF(
        x0 * scale,
        y0 * scale,
        max(0.0, (x1 - x0) * scale),
        max(0.0, (y1 - y0) * scale),
    )


def _font(pixel_size: int, bold: bool = False) -> QFont:
    font = QFont(theme.FONT_FAMILY)
    font.setPixelSize(max(6, int(pixel_size)))
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


def _looks_numeric(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and _NUMERIC_RE.match(stripped) is not None


class PageContentItem(QGraphicsItem):
    """Dessine le contenu placeholder ou les blocs réels d'une page."""

    def __init__(self, page: Optional[Page], placeholder: bool) -> None:
        super().__init__()
        self._page = page
        self._placeholder = placeholder
        self.setZValue(1)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return QRectF(0, 0, PAGE_W, PAGE_H)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setClipRect(self.boundingRect())
        if self._placeholder:
            self._paint_placeholder(painter)
        elif self._page is not None:
            self._paint_page(painter, self._page)
        painter.restore()

    # --- Placeholder ------------------------------------------------------
    def _paint_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QColor(theme.PAPER_INK))
        painter.setFont(_font(16, bold=True))
        painter.drawText(
            QRectF(30, 34, 370, 30),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Aperçu de la page",
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.PAPER_LINE))
        for rect in (
            QRectF(30, 82, 300, 7),
            QRectF(30, 96, 250, 7),
            QRectF(30, 110, 360, 7),
            QRectF(30, 124, 210, 7),
        ):
            painter.drawRoundedRect(rect, 2, 2)

        table_rect = QRectF(30, 180, 370, 160)
        self._paint_empty_table(painter, table_rect, rows=5, cols=4)

        painter.setBrush(QColor(theme.PAPER_LINE))
        for rect in (
            QRectF(30, 372, 330, 7),
            QRectF(30, 386, 290, 7),
            QRectF(30, 400, 350, 7),
        ):
            painter.drawRoundedRect(rect, 2, 2)

    def _paint_empty_table(
        self, painter: QPainter, rect: QRectF, rows: int, cols: int
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.PAPER_TABLE_HEAD))
        painter.drawRect(
            QRectF(rect.left(), rect.top(), rect.width(), rect.height() / rows)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.PAPER_TABLE_BORDER), 0.8))
        painter.drawRect(rect)
        for row in range(1, rows):
            y = rect.top() + rect.height() * row / rows
            painter.drawLine(rect.left(), y, rect.right(), y)
        for col in range(1, cols):
            x = rect.left() + rect.width() * col / cols
            painter.drawLine(x, rect.top(), x, rect.bottom())

    # --- Page réelle ------------------------------------------------------
    def _paint_page(self, painter: QPainter, page: Page) -> None:
        if not page.blocks:
            painter.setPen(QColor(theme.MUTED))
            painter.setFont(_font(11))
            painter.drawText(
                QRectF(30, 30, PAGE_W - 60, PAGE_H - 60),
                Qt.AlignmentFlag.AlignCenter,
                f"Page {page.number} — aucun bloc détecté",
            )
            return

        for block in page.blocks:
            rect = self._block_rect(block)
            if rect.width() <= 0 or rect.height() <= 0:
                continue

            if block.type == BlockType.TITLE:
                painter.setPen(QColor(theme.PAPER_INK))
                painter.setFont(_font(15, bold=True))
                painter.drawText(
                    rect.adjusted(2, 1, -2, -1),
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignTop
                    | Qt.TextFlag.TextWordWrap,
                    block.text,
                )
            elif block.type == BlockType.TABLE:
                self._paint_table(painter, block.table, rect)
            elif block.type == BlockType.IMAGE:
                painter.setPen(QPen(QColor(theme.PAPER_TABLE_BORDER), 1))
                painter.setBrush(QColor(theme.PANEL_2))
                painter.drawRoundedRect(rect, 2, 2)
                painter.setPen(QColor(theme.MUTED))
                painter.setFont(_font(8))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "[image]")
            else:
                color = (
                    theme.MUTED
                    if block.type in (BlockType.HEADER, BlockType.FOOTER)
                    else "#34383f"
                )
                painter.setPen(QColor(color))
                painter.setFont(
                    _font(7 if block.type == BlockType.FOOTER else 8)
                )
                painter.drawText(
                    rect.adjusted(2, 1, -2, -1),
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignTop
                    | Qt.TextFlag.TextWordWrap,
                    block.text,
                )

    @staticmethod
    def _block_rect(block: Block, scale: float = PAGE_SCALE) -> QRectF:
        return block_scene_rect(block, scale)

    def _paint_table(
        self, painter: QPainter, table: Optional[Table], rect: QRectF
    ) -> None:
        if table is None or not table.grid:
            painter.setPen(QPen(QColor(theme.PAPER_TABLE_BORDER), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            painter.setPen(QColor(theme.MUTED))
            painter.setFont(_font(8))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "[tableau]")
            return

        rows = len(table.grid)
        cols = max((len(row) for row in table.grid), default=1)
        if rows == 0 or cols == 0:
            return
        row_height = rect.height() / rows

        # Largeurs proportionnelles au contenu : la colonne « Désignation »
        # reste lisible, les colonnes numériques restent compactes.
        weights = []
        for col_index in range(cols):
            longest = max(
                (
                    len(row[col_index].text)
                    for row in table.grid
                    if col_index < len(row) and row[col_index] is not None
                ),
                default=1,
            )
            weights.append(max(4, min(18, longest)))
        total_weight = sum(weights) or 1
        col_widths = [
            rect.width() * weight / total_weight for weight in weights
        ]
        col_offsets = [rect.left()]
        for width in col_widths[:-1]:
            col_offsets.append(col_offsets[-1] + width)

        header = QRectF(rect.left(), rect.top(), rect.width(), row_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.PAPER_TABLE_HEAD))
        painter.drawRect(header)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.PAPER_TABLE_BORDER), 0.8))
        painter.drawRect(rect)
        for row in range(1, rows):
            y = rect.top() + row_height * row
            painter.drawLine(rect.left(), y, rect.right(), y)
        for x in col_offsets[1:]:
            painter.drawLine(x, rect.top(), x, rect.bottom())

        font = _font(max(6, min(8, int(row_height * 0.42))))
        for row_index, row in enumerate(table.grid):
            for col_index, cell in enumerate(row):
                if cell is None or not cell.text:
                    continue
                cell_width = col_widths[col_index]
                cell_rect = QRectF(
                    col_offsets[col_index] + 2.5,
                    rect.top() + row_index * row_height + 1,
                    cell_width - 5,
                    row_height - 2,
                )
                painter.save()
                painter.setClipRect(cell_rect)
                painter.setPen(QColor(theme.PAPER_INK))
                painter.setFont(font)
                align = Qt.AlignmentFlag.AlignVCenter
                if row_index > 0 and _looks_numeric(cell.text):
                    align |= Qt.AlignmentFlag.AlignRight
                else:
                    align |= Qt.AlignmentFlag.AlignLeft
                painter.drawText(cell_rect, align, cell.text)
                painter.restore()


class BlockOverlayItem(QGraphicsRectItem):
    """Rectangle de détection avec étiquette « type · confiance »."""

    def __init__(
        self,
        rect: QRectF,
        label: str,
        index: int,
        tooltip: str = "",
    ) -> None:
        super().__init__(rect)
        self.index = index
        self._label = label
        self._hover = False
        self._active = False
        self.setZValue(10)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        if tooltip:
            self.setToolTip(tooltip)

        font = _font(9, bold=True)
        metrics = QFontMetricsF(font)
        self._tag_size = QRectF(
            0,
            0,
            metrics.horizontalAdvance(label) + 10,
            metrics.height() + 2,
        )

    def _tag_rect(self) -> QRectF:
        rect = self.rect()
        height = self._tag_size.height()
        y = rect.top() - height - 1
        if y < 0:
            y = rect.top() + 1
        return QRectF(rect.left() - 1, y, self._tag_size.width(), height)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return self.rect().united(self._tag_rect()).adjusted(-4, -4, 4, 4)

    def shape(self) -> QPainterPath:  # noqa: N802 - API Qt
        path = QPainterPath()
        path.addRect(self.rect())
        path.addRect(self._tag_rect())
        return path

    def set_active(self, active: bool) -> None:
        """Bloc correspondant au texte sélectionné dans les résultats."""
        if self._active != active:
            self._active = active
            self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()
        if self._active:
            alpha, width = 85, 2.8
        elif selected:
            alpha, width = 55, 2.4
        elif self._hover:
            alpha, width = 40, 1.5
        else:
            alpha, width = 26, 1.5
        painter.setPen(QPen(QColor(theme.ACCENT), width))
        painter.setBrush(QColor(47, 91, 215, alpha))
        painter.drawRect(self.rect())

        tag = self._tag_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(tag, 2, 2)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(_font(9, bold=True))
        painter.drawText(
            tag.adjusted(5, 0, -5, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._label,
        )

    def hoverEnterEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)


class PreviewView(QGraphicsView):
    """Vue graphique de la page avec zoom et surcouches cliquables."""

    block_selected = Signal(int)
    zoom_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewView")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(theme.PANEL_2))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._zoom = 1.0
        self._overlays: List[BlockOverlayItem] = []
        self._placeholder = True
        self._programmatic = False
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self.set_zoom(1.0, emit=False)

    # --- Contenu ----------------------------------------------------------
    def _reset_scene(self, page_h: float = PAGE_H) -> None:
        self._scene.clear()
        self._overlays = []
        self._scene.setSceneRect(
            QRectF(-20, -20, PAGE_W + 40, page_h + 40)
        )

    def _add_page_item(self, page_h: float = PAGE_H) -> None:
        page_item = QGraphicsRectItem(0, 0, PAGE_W, page_h)
        page_item.setZValue(0)
        page_item.setPen(QPen(QColor(theme.BORDER), 1))
        page_item.setBrush(QColor(theme.PAPER))
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(15, 23, 42, 70))
        page_item.setGraphicsEffect(shadow)
        self._scene.addItem(page_item)

    def show_placeholder(self) -> None:
        self._reset_scene()
        self._add_page_item()
        self._scene.addItem(PageContentItem(None, placeholder=True))
        specs = (
            (QRectF(30, 36, 370, 32), "titre · 98 %", "titre — confiance 98 %"),
            (
                QRectF(30, 80, 370, 80),
                "paragraphe · 96 %",
                "paragraphe — confiance 96 %",
            ),
            (
                QRectF(30, 180, 370, 160),
                "tableau · 94 %",
                "tableau — confiance 94 %",
            ),
        )
        for index, (rect, label, tooltip) in enumerate(specs):
            overlay = BlockOverlayItem(rect, label, index, tooltip)
            self._scene.addItem(overlay)
            self._overlays.append(overlay)
        self._placeholder = True

    def show_page(self, page: Page, image_dir: Optional[str] = None) -> None:
        """Affiche une page : image réelle en fond si disponible, sinon dessin.

        ``image_dir`` (optionnel) est le dossier de travail contenant
        ``page_NNN.png`` ; à défaut, l'attribut transient posé par le
        ``TaskRunner`` sur la page est utilisé. Sans image (mode démo,
        anciens dossiers, échec d'écriture), repli sur le rendu vectoriel
        des blocs (comportement historique).

        Échelle : deux espaces distincts, à ne pas confondre —

        - les **blocs** ont leur bbox en points PDF (origine haut-gauche).
        Leur échelle vers la scène (``PAGE_W`` de large) est dérivée du DPI
        enregistré dans ``page_NNN.png`` (cf. ``_load_page_background``) :
        juste pour toute taille de page (paysage, Letter…), avec repli
        ``PAGE_SCALE`` (A4 portrait) si le DPI est absent ;
        - l'**image** occupe le même rect de page : son échelle propre est
        ``PAGE_W / largeur_px_image`` (px scène par px image), utilisée
        uniquement par l'item pixmap de fond.

        Appliquer l'échelle image aux bboxes en points comprimerait les
        surcouches d'un facteur ``dpi/72`` (300/72 ≈ 4,2) dans le coin
        haut-gauche.
        """
        pixmap, point_scale = _load_page_background(
            _page_image_path(page, image_dir)
        )
        if pixmap is not None and pixmap.width() > 0:
            image_scale = PAGE_W / float(pixmap.width())
            page_h = float(pixmap.height()) * image_scale
        else:
            pixmap = None
            point_scale = PAGE_SCALE
            page_h = PAGE_H

        self._reset_scene(page_h)
        self._add_page_item(page_h)
        if pixmap is not None:
            background = QGraphicsPixmapItem(pixmap)
            background.setPos(0.0, 0.0)
            background.setScale(image_scale)
            background.setTransformationMode(
                Qt.TransformationMode.SmoothTransformation
            )
            background.setZValue(0.5)
            self._scene.addItem(background)
        else:
            # Sans fond réel : les blocs sont dessinés (texte) sur la page.
            self._scene.addItem(PageContentItem(page, placeholder=False))

        for index, block in enumerate(page.blocks):
            rect = PageContentItem._block_rect(block, point_scale)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            type_label = BLOCK_LABELS.get(block.type, block.type.value)
            if block.confidence > 0:
                confidence = f"{round(block.confidence * 100)} %"
            else:
                confidence = "—"
            label = f"{type_label} · {confidence}"
            details = block.text.strip().replace("\n", " ")
            if block.table is not None:
                details = f"{block.table.rows} × {block.table.cols} cellules"
            tooltip = f"{type_label} — confiance {confidence}"
            if details:
                tooltip += f"\n{details[:300]}"
            overlay = BlockOverlayItem(rect, label, index, tooltip)
            self._scene.addItem(overlay)
            self._overlays.append(overlay)

        self._placeholder = False

    # --- Zoom -------------------------------------------------------------
    def zoom_percent(self) -> int:
        return int(round(self._zoom * 100))

    def set_zoom(self, value: float, emit: bool = True) -> None:
        zoom = max(0.5, min(2.5, float(value)))
        self._zoom = zoom
        self.setTransform(QTransform().scale(zoom, zoom))
        if emit:
            self.zoom_changed.emit(self.zoom_percent())

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.15)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.15)

    def wheelEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    # --- Sélection --------------------------------------------------------
    def select_block(self, index: int, emit: bool = False) -> bool:
        """Sélectionne la surcouche d'un bloc (lien texte → aperçu).

        La sélection est programmatique : aucun signal n'est émis sauf si
        ``emit`` est vrai, pour éviter toute boucle avec le panneau résultat.
        """
        overlay = next(
            (item for item in self._overlays if item.index == index), None
        )
        if overlay is None:
            return False
        self._programmatic = True
        try:
            for item in self._overlays:
                item.setSelected(item is overlay)
            overlay.set_active(True)
            self.ensureVisible(overlay, 60, 60)
        finally:
            self._programmatic = False
        if emit:
            self.block_selected.emit(index)
        return True

    def set_active_block(self, index: Optional[int]) -> None:
        """Met en évidence la surcouche du bloc actif (sans la sélectionner)."""
        for item in self._overlays:
            item.set_active(item.index == index)

    def _on_selection_changed(self) -> None:
        if self._programmatic:
            return
        for item in self._scene.selectedItems():
            if isinstance(item, BlockOverlayItem):
                self.block_selected.emit(item.index)
                return

    def block_count(self) -> int:
        return len(self._overlays)

    def is_placeholder(self) -> bool:
        return self._placeholder


class PreviewPanel(QWidget):
    """En-tête (fichier, page, zoom) + vue graphique de la page."""

    block_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QWidget(self)
        head.setObjectName("PreviewHead")
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head.setFixedHeight(44)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(14, 8, 14, 8)
        head_layout.setSpacing(10)

        self._title = QLabel("Aucun fichier sélectionné", head)
        self._title.setObjectName("PreviewTitle")
        head_layout.addWidget(self._title)

        self._info = QLabel("Aperçu placeholder · 300 DPI", head)
        self._info.setObjectName("PreviewInfo")
        head_layout.addWidget(self._info)
        head_layout.addStretch(1)

        zoom_box = QWidget(head)
        zoom_layout = QHBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(6)

        zoom_out = QPushButton(zoom_box)
        zoom_out.setObjectName("ZoomButton")
        zoom_out.setIcon(make_icon("minus", theme.MUTED, 14))
        zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        zoom_out.setToolTip("Zoom arrière")
        zoom_out.setAccessibleName("Zoom arrière")
        zoom_out.clicked.connect(self.zoom_out)
        zoom_layout.addWidget(zoom_out)

        self._zoom_label = QLabel("100 %", zoom_box)
        self._zoom_label.setObjectName("ZoomLabel")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(42)
        zoom_layout.addWidget(self._zoom_label)

        zoom_in = QPushButton(zoom_box)
        zoom_in.setObjectName("ZoomButton")
        zoom_in.setIcon(make_icon("plus", theme.MUTED, 14))
        zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        zoom_in.setToolTip("Zoom avant")
        zoom_in.setAccessibleName("Zoom avant")
        zoom_in.clicked.connect(self.zoom_in)
        zoom_layout.addWidget(zoom_in)

        head_layout.addWidget(zoom_box, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(head)

        self.view = PreviewView(self)
        self.view.zoom_changed.connect(self._on_zoom_changed)
        self.view.block_selected.connect(self.block_selected)
        layout.addWidget(self.view, 1)

    def _on_zoom_changed(self, percent: int) -> None:
        self._zoom_label.setText(f"{percent} %")

    def zoom_in(self) -> None:
        self.view.zoom_in()

    def zoom_out(self) -> None:
        self.view.zoom_out()

    def set_title_info(self, title: str, info: str) -> None:
        self._title.setText(title)
        self._info.setText(info)

    def set_info(self, info: str) -> None:
        self._info.setText(info)

    def show_placeholder(
        self,
        title: str = "Aucun fichier sélectionné",
        info: str = "Aperçu placeholder · 300 DPI",
    ) -> None:
        self.set_title_info(title, info)
        self.view.show_placeholder()

    def show_page(self, page: Page, image_dir: Optional[str] = None) -> None:
        """Affiche la page (image réelle en fond si disponible, cf. vue)."""
        self.view.show_page(page, image_dir=image_dir)

    def select_block(self, index: int, emit: bool = False) -> bool:
        """Sélectionne la surcouche du bloc donné et la rend visible."""
        return self.view.select_block(index, emit=emit)

    def set_active_block(self, index: Optional[int]) -> None:
        """Met en évidence la surcouche du bloc actif."""
        self.view.set_active_block(index)
