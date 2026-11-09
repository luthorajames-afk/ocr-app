"""Icônes vectorielles légères, dessinées avec QPainter.

Aucun fichier d'assets n'est nécessaire : les icônes reprennent les traits du
mockup (style « lucide ») et sont rendues à la volée, en haute densité.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from ocr_app.ui import theme

_ICON_CACHE: Dict[Tuple[str, str, int], QIcon] = {}


def _new_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def _stroke(painter: QPainter, color: str, width: float = 1.6) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _paint(name: str, painter: QPainter, color: str) -> None:
    """Dessine l'icône dans un repère logique de 20 × 20."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _stroke(painter, color)

    if name == "plus":
        painter.drawLine(QPointF(10, 4.5), QPointF(10, 15.5))
        painter.drawLine(QPointF(4.5, 10), QPointF(15.5, 10))
    elif name == "minus":
        painter.drawLine(QPointF(4.5, 10), QPointF(15.5, 10))
    elif name == "close":
        painter.drawLine(QPointF(5.5, 5.5), QPointF(14.5, 14.5))
        painter.drawLine(QPointF(14.5, 5.5), QPointF(5.5, 14.5))
    elif name == "settings":
        center = QPointF(10, 10)
        for index in range(8):
            angle = math.radians(index * 45.0)
            inner = QPointF(
                center.x() + math.cos(angle) * 5.3,
                center.y() + math.sin(angle) * 5.3,
            )
            outer = QPointF(
                center.x() + math.cos(angle) * 7.4,
                center.y() + math.sin(angle) * 7.4,
            )
            painter.drawLine(inner, outer)
        painter.drawEllipse(center, 4.1, 4.1)
        painter.drawEllipse(center, 1.3, 1.3)
    elif name == "globe":
        painter.drawEllipse(QRectF(4, 4, 12, 12))
        painter.drawLine(QPointF(4, 10), QPointF(16, 10))
        painter.drawEllipse(QRectF(7, 4, 6, 12))
    elif name == "pause":
        _stroke(painter, color, 3.0)
        painter.drawLine(QPointF(7.3, 5.0), QPointF(7.3, 15.0))
        painter.drawLine(QPointF(12.7, 5.0), QPointF(12.7, 15.0))
    elif name == "play":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        triangle = QPainterPath()
        triangle.moveTo(7.0, 4.8)
        triangle.lineTo(15.2, 10.0)
        triangle.lineTo(7.0, 15.2)
        triangle.closeSubpath()
        painter.drawPath(triangle)
    elif name == "copy":
        painter.drawRoundedRect(QRectF(3.5, 7.0, 9.0, 9.5), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(7.5, 3.5, 9.0, 9.5), 1.5, 1.5)
    elif name == "download":
        painter.drawLine(QPointF(10, 3.5), QPointF(10, 12.5))
        painter.drawLine(QPointF(6.5, 9.0), QPointF(10, 12.5))
        painter.drawLine(QPointF(13.5, 9.0), QPointF(10, 12.5))
        painter.drawLine(QPointF(4.5, 16.2), QPointF(15.5, 16.2))
    elif name == "check-circle":
        painter.drawEllipse(QRectF(2.5, 2.5, 15, 15))
        painter.drawLine(QPointF(6.2, 10.2), QPointF(8.9, 12.9))
        painter.drawLine(QPointF(8.9, 12.9), QPointF(13.8, 7.4))
    elif name == "circle-dashed":
        pen = QPen(QColor(color))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([2.2, 2.2])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(3.2, 3.2, 13.6, 13.6))
    elif name == "file":
        path = QPainterPath()
        path.moveTo(5.0, 2.5)
        path.lineTo(11.8, 2.5)
        path.lineTo(15.0, 5.7)
        path.lineTo(15.0, 17.5)
        path.lineTo(5.0, 17.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(11.8, 2.5), QPointF(11.8, 5.7))
        painter.drawLine(QPointF(11.8, 5.7), QPointF(15.0, 5.7))
        painter.drawLine(QPointF(7.5, 9.2), QPointF(12.5, 9.2))
        painter.drawLine(QPointF(7.5, 12.2), QPointF(12.5, 12.2))
        painter.drawLine(QPointF(7.5, 15.2), QPointF(10.8, 15.2))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(2.8, 5.2)
        path.lineTo(8.0, 5.2)
        path.lineTo(9.4, 7.0)
        path.lineTo(17.2, 7.0)
        path.lineTo(17.2, 15.2)
        path.lineTo(2.8, 15.2)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "chevron-down":
        painter.drawLine(QPointF(5.5, 8.0), QPointF(10.0, 12.5))
        painter.drawLine(QPointF(10.0, 12.5), QPointF(14.5, 8.0))
    elif name == "image":
        painter.drawRoundedRect(QRectF(3, 4, 14, 12), 1.5, 1.5)
        painter.drawEllipse(QRectF(6.2, 7.0, 2.6, 2.6))
        path = QPainterPath()
        path.moveTo(4.2, 14.5)
        path.lineTo(8.4, 10.2)
        path.lineTo(11.6, 13.2)
        path.lineTo(14.2, 10.8)
        path.lineTo(16.2, 13.0)
        painter.drawPath(path)
    else:
        # Repli : petit carré, pour ne jamais produire une icône vide.
        painter.drawRoundedRect(QRectF(4, 4, 12, 12), 2, 2)


def make_icon(name: str, color: str = theme.TEXT, size: int = 16) -> QIcon:
    """Retourne une QIcon (mise en cache) dessinée à la couleur demandée."""
    key = (name, color, int(size))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = _new_pixmap(int(size))
    painter = QPainter(pixmap)
    try:
        painter.scale(size / 20.0, size / 20.0)
        _paint(name, painter, color)
    finally:
        painter.end()

    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


def app_icon_pixmap(size: int = 64) -> QPixmap:
    """Icône de l'application : carré arrondi dégradé avec un « A » blanc."""
    pixmap = _new_pixmap(int(size))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / 64.0, size / 64.0)
        gradient = QLinearGradient(0, 0, 64, 64)
        gradient.setColorAt(0.0, QColor(theme.ACCENT))
        gradient.setColorAt(1.0, QColor(theme.ACCENT_2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15)

        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(36)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "A")
    finally:
        painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon
