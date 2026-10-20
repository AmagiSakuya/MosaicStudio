"""用 QPainter 现画矢量小图标，避免依赖外部图片资源。

顺带在首次运行时生成 QSS 需要的箭头 / 对勾 PNG（theme.ASSETS）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

_cache: dict[tuple[str, str, int], QIcon] = {}


# ---------------- 资源生成 ----------------
def _ensure_assets() -> None:
    theme.ASSETS.mkdir(parents=True, exist_ok=True)

    def make(name: str, w: int, h: int, draw) -> None:
        path = theme.ASSETS / name
        if path.exists():
            return
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        draw(p, w, h)
        p.end()
        img.save(str(path))

    def arrow(p: QPainter, w: int, h: int) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#b9b9c6"))
        p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w / 2, h)]))

    def arrow_up(p: QPainter, w: int, h: int) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#b9b9c6"))
        p.drawPolygon(QPolygonF([QPointF(0, h), QPointF(w, h), QPointF(w / 2, 0)]))

    def check(p: QPainter, w: int, h: int) -> None:
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(2, h * 0.55), QPointF(w * 0.42, h - 2), QPointF(w - 2, 2)]))

    make("arrow_down.png", 10, 6, arrow)
    make("arrow_up.png", 10, 6, arrow_up)
    make("check.png", 14, 14, check)


# ---------------- 图标绘制 ----------------
def _shape(name: str, p: QPainter, s: float) -> None:
    """所有图形按 18x18 的坐标系设计，s 为实际边长。"""
    p.scale(s / 18.0, s / 18.0)

    if name == "play":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(15, 9), QPointF(5, 15)]))
    elif name == "pause":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(4.5, 3.5, 3.2, 11), 1, 1)
        p.drawRoundedRect(QRectF(10.3, 3.5, 3.2, 11), 1, 1)
    elif name == "step_back":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(13, 4), QPointF(13, 14), QPointF(6, 9)]))
        p.drawRoundedRect(QRectF(4.2, 4, 1.8, 10), 1, 1)
    elif name == "step_fwd":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(5, 4), QPointF(5, 14), QPointF(12, 9)]))
        p.drawRoundedRect(QRectF(12, 4, 1.8, 10), 1, 1)
    elif name == "to_start":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(14, 4), QPointF(14, 14), QPointF(7, 9)]))
        p.drawRoundedRect(QRectF(3.4, 3.5, 2, 11), 1, 1)
    elif name == "to_end":
        p.setBrush(QColor("#e9e9f0"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(4, 4), QPointF(4, 14), QPointF(11, 9)]))
        p.drawRoundedRect(QRectF(12.6, 3.5, 2, 11), 1, 1)
    elif name == "plus":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(9, 3.5), QPointF(9, 14.5))
        p.drawLine(QPointF(3.5, 9), QPointF(14.5, 9))
    elif name == "trash":
        pen = QPen(QColor("#e0837f"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(3.5, 5), QPointF(14.5, 5))
        p.drawLine(QPointF(7, 5), QPointF(7.4, 3))
        p.drawLine(QPointF(11, 5), QPointF(10.6, 3))
        p.drawLine(QPointF(7.4, 3), QPointF(10.6, 3))
        path = QPainterPath()
        path.moveTo(5, 5.5)
        path.lineTo(5.8, 15)
        path.lineTo(12.2, 15)
        path.lineTo(13, 5.5)
        p.drawPath(path)
        p.drawLine(QPointF(8, 8), QPointF(8, 12.5))
        p.drawLine(QPointF(10, 8), QPointF(10, 12.5))
    elif name == "video":
        pen = QPen(QColor("#8fb6f0"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(2.5, 4.5, 10, 9), 1.6, 1.6)
        p.drawPolygon(QPolygonF([QPointF(12.5, 8), QPointF(15.5, 6.2), QPointF(15.5, 11.8), QPointF(12.5, 10)]))
    elif name == "image":
        pen = QPen(QColor("#63d6c8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(2.5, 3.5, 13, 11), 1.6, 1.6)
        p.setBrush(QColor("#63d6c8"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(6.4, 7.2), 1.5, 1.5)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(4, 13.5), QPointF(7.8, 9.5), QPointF(10.8, 12.6),
                                  QPointF(12.6, 10.8), QPointF(14.5, 13.5)]))
    elif name == "blur":
        p.setPen(Qt.NoPen)
        for i, (cx, cy, r) in enumerate([(6.5, 7.0, 3.4), (11.4, 9.4, 4.0), (7.6, 12.0, 2.6)]):
            c = QColor("#f08a8a")
            c.setAlpha(90 + i * 40)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy), r, r)
    elif name == "json":
        pen = QPen(QColor("#d9b46a"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(6.6, 3.2), QPointF(3.4, 3.2), QPointF(3.4, 14.8), QPointF(6.6, 14.8)]))
        p.drawPolyline(QPolygonF([QPointF(11.4, 3.2), QPointF(14.6, 3.2), QPointF(14.6, 14.8), QPointF(11.4, 14.8)]))
        p.setBrush(QColor("#d9b46a"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(9, 6.6), 0.9, 0.9)
        p.drawEllipse(QPointF(9, 11.4), 0.9, 0.9)
    elif name == "export":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(9, 12), QPointF(9, 3.5)]))
        p.drawPolyline(QPolygonF([QPointF(5.4, 7), QPointF(9, 3.4), QPointF(12.6, 7)]))
        path = QPainterPath()
        path.moveTo(3.6, 10.5)
        path.lineTo(3.6, 14.6)
        path.lineTo(14.4, 14.6)
        path.lineTo(14.4, 10.5)
        p.drawPath(path)
    elif name == "save":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(3, 3, 12, 12), 1.6, 1.6)
        p.drawRect(QRectF(6, 3, 6, 4.2))
        p.drawRect(QRectF(5.6, 10, 6.8, 5))
    elif name == "refresh":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(3.6, 3.6, 10.8, 10.8)
        p.drawArc(rect, 40 * 16, 280 * 16)
        p.setBrush(QColor("#cfcfd8"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(11.2, 2.4), QPointF(15.2, 5.4), QPointF(10.8, 6.6)]))
    elif name == "scan":
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(3, 3, 10, 10), 90 * 16, 360 * 16)
        p.drawLine(QPointF(12.4, 12.4), QPointF(15.4, 15.4))
    elif name == "folder":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(2.6, 13.6)
        path.lineTo(2.6, 5.0)
        path.lineTo(7.2, 5.0)
        path.lineTo(8.6, 6.8)
        path.lineTo(15.4, 6.8)
        path.lineTo(15.4, 13.6)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "eye":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(2.5, 9)
        path.quadTo(9, 2.6, 15.5, 9)
        path.quadTo(9, 15.4, 2.5, 9)
        p.drawPath(path)
        p.drawEllipse(QPointF(9, 9), 2.3, 2.3)
    elif name == "zoom_in" or name == "zoom_out":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawEllipse(QPointF(8, 8), 4.6, 4.6)
        p.drawLine(QPointF(11.4, 11.4), QPointF(15, 15))
        p.drawLine(QPointF(5.8, 8), QPointF(10.2, 8))
        if name == "zoom_in":
            p.drawLine(QPointF(8, 5.8), QPointF(8, 10.2))
    elif name == "project":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(4.6, 2.8)
        path.lineTo(10.4, 2.8)
        path.lineTo(14.2, 6.6)
        path.lineTo(14.2, 15.2)
        path.lineTo(4.6, 15.2)
        path.closeSubpath()
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(10.2, 2.9), QPointF(10.2, 6.7), QPointF(14.1, 6.7)]))
        p.drawLine(QPointF(6.9, 9.6), QPointF(11.8, 9.6))
        p.drawLine(QPointF(6.9, 12.2), QPointF(11.8, 12.2))
    elif name == "link":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.save()
        p.translate(9, 9)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-7.4, -3.1, 7.6, 6.2), 3.0, 3.0)
        p.drawRoundedRect(QRectF(-0.2, -3.1, 7.6, 6.2), 3.0, 3.0)
        p.restore()
    elif name == "sound":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#cfcfd8"))
        p.drawPolygon(QPolygonF([QPointF(3.2, 7.0), QPointF(6.2, 7.0), QPointF(9.4, 4.2),
                                 QPointF(9.4, 13.8), QPointF(6.2, 11.0), QPointF(3.2, 11.0)]))
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(8.4, 5.6, 5.0, 6.8), -60 * 16, 120 * 16)
        p.drawArc(QRectF(8.4, 3.4, 8.4, 11.2), -55 * 16, 110 * 16)
    elif name == "mute":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#8a8a97"))
        p.drawPolygon(QPolygonF([QPointF(2.4, 7.0), QPointF(5.4, 7.0), QPointF(8.6, 4.2),
                                 QPointF(8.6, 13.8), QPointF(5.4, 11.0), QPointF(2.4, 11.0)]))
        pen = QPen(QColor("#e0837f"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(11.0, 6.6), QPointF(15.4, 11.4))
        p.drawLine(QPointF(15.4, 6.6), QPointF(11.0, 11.4))
    elif name == "warning":
        pen = QPen(QColor("#e8b06a"))
        pen.setWidthF(1.5)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(9, 2.6), QPointF(15.8, 14.8), QPointF(2.2, 14.8)]))
        p.drawLine(QPointF(9, 6.8), QPointF(9, 10.6))
        p.setBrush(QColor("#e8b06a"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(9, 12.8), 0.85, 0.85)
    elif name == "fit":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(3, 6), QPointF(3, 3), QPointF(6, 3)]))
        p.drawPolyline(QPolygonF([QPointF(12, 3), QPointF(15, 3), QPointF(15, 6)]))
        p.drawPolyline(QPolygonF([QPointF(15, 12), QPointF(15, 15), QPointF(12, 15)]))
        p.drawPolyline(QPolygonF([QPointF(6, 15), QPointF(3, 15), QPointF(3, 12)]))
    elif name == "settings":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(9, 9), 4.9, 4.9)
        p.drawEllipse(QPointF(9, 9), 2.0, 2.0)
        p.save()
        p.translate(9, 9)
        for i in range(8):
            p.save()
            p.rotate(i * 45)
            p.drawLine(QPointF(0, -4.9), QPointF(0, -7.0))
            p.restore()
        p.restore()
    elif name == "download":
        pen = QPen(QColor("#cfcfd8"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(9, 3.2), QPointF(9, 11.2))
        p.drawPolyline(QPolygonF([QPointF(5.5, 7.9), QPointF(9, 11.5), QPointF(12.5, 7.9)]))
        path = QPainterPath()
        path.moveTo(3.6, 12.6)
        path.lineTo(3.6, 14.9)
        path.lineTo(14.4, 14.9)
        path.lineTo(14.4, 12.6)
        p.drawPath(path)
    elif name == "chip":
        pen = QPen(QColor("#8fb6f0"))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(4.4, 4.4, 9.2, 9.2), 1.6, 1.6)
        p.drawRoundedRect(QRectF(7.2, 7.2, 3.6, 3.6), 0.9, 0.9)
        for x in (6.6, 9.0, 11.4):
            p.drawLine(QPointF(x, 4.4), QPointF(x, 2.4))
            p.drawLine(QPointF(x, 13.6), QPointF(x, 15.6))
        for y in (6.6, 9.0, 11.4):
            p.drawLine(QPointF(4.4, y), QPointF(2.4, y))
            p.drawLine(QPointF(13.6, y), QPointF(15.6, y))


def icon(name: str, color: str = theme.TEXT, size: int = 18) -> QIcon:
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    _shape(name, p, float(size))
    p.end()
    ic = QIcon(pm)
    _cache[key] = ic
    return ic


def pixmap(name: str, color: str = theme.TEXT, size: int = 18) -> QPixmap:
    return icon(name, color, size).pixmap(size, size)


_ensure_assets()
