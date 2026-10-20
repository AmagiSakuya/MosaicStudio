"""轻量提示条（Toast）：保存成功、下载完成这类反馈不打断操作。

比 QMessageBox 温和：自动淡出、不抢焦点、点不到（鼠标穿透），
但足够显眼 —— 居中显示在主窗口底部。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import theme

_KIND_COLOR = {
    "success": "#2fa36b",
    "info": "#4d8dff",
    "warn": "#dda03f",
    "error": "#e05c5c",
}


class Toast(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._text = ""
        self._kind = "success"
        self._font = QFont()
        self._font.setPointSizeF(9.4)
        self._font.setWeight(QFont.DemiBold)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._fading = False
        self.hide()

    # ---------- 对外 ----------
    def show_message(self, text: str, kind: str = "success", msec: int = 2600) -> None:
        self._text = str(text)
        self._kind = kind if kind in _KIND_COLOR else "info"
        self._fading = False
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(max(800, msec))

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        w = max(self.sizeHint().width(), 160)
        h = self.sizeHint().height()
        self.resize(w, h)
        x = max(8, (parent.width() - w) // 2)
        y = max(8, parent.height() - h - 26)
        self.move(x, y)

    # ---------- 绘制 ----------
    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(self._text)
        return QSize(text_w + 62, max(38, fm.height() + 20))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        p.setPen(QPen(QColor("#3b3b47"), 1))
        p.setBrush(QColor(28, 28, 34, 244))
        p.drawRoundedRect(r, 10, 10)

        accent = QColor(_KIND_COLOR.get(self._kind, "#4d8dff"))
        cx, cy = 20.0, r.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QPointF(cx, cy), 8.5, 8.5)

        p.setPen(QPen(QColor("#ffffff"), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([
            QPointF(cx - 3.7, cy + 0.2), QPointF(cx - 1.0, cy + 2.9), QPointF(cx + 3.9, cy - 3.0),
        ]))

        p.setFont(self._font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(36, 0, r.width() - 46, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)

    # ---------- 动画 ----------
    def _fade_out(self) -> None:
        self._fading = True
        self._anim.stop()
        self._anim.setDuration(240)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_done(self) -> None:
        if self._fading:
            self._fading = False
            self.hide()
