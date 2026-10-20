"""通用小部件（素材库和启动页共用）。"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)

from . import theme


class TwoLineDelegate(QStyledItemDelegate):
    """两行列表项：主标题 + 灰色副标题（QListWidget 默认会把换行吃掉）。"""

    def __init__(self, parent=None, row_height: int = 42) -> None:
        super().__init__(parent)
        self.sub_color = QColor(theme.MUTED)
        self.row_height = row_height

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        painter.save()
        r = opt.rect.adjusted(8, 4, -8, -4)
        has_icon = index.data(Qt.DecorationRole) is not None
        if has_icon:
            r.setLeft(r.left() + (opt.decorationSize.width() or 16) + 6)
        main = str(index.data(Qt.DisplayRole) or "")
        sub = str(index.data(Qt.UserRole + 1) or "")
        selected = bool(opt.state & QStyle.State_Selected)

        f = QFont(opt.font)
        f.setPointSizeF(9.2)
        painter.setFont(f)
        painter.setPen(QColor("#ffffff") if selected else QColor(theme.TEXT))
        fm = painter.fontMetrics()
        top = QRect(r.left(), r.top(), r.width(), r.height() // 2)
        painter.drawText(top, Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(main, Qt.ElideMiddle, r.width()))

        if sub:
            f.setPointSizeF(8.0)
            painter.setFont(f)
            painter.setPen(QColor("#c7d6f0") if selected else self.sub_color)
            fm2 = painter.fontMetrics()
            bottom = QRect(r.left(), r.top() + r.height() // 2, r.width(), r.height() // 2)
            painter.drawText(bottom, Qt.AlignLeft | Qt.AlignVCenter,
                             fm2.elidedText(sub, Qt.ElideRight, r.width()))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.row_height)
