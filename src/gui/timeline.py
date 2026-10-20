"""时间线控件：轨道绘制、片段拖拽/裁剪/框选新建、缩放滚动。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QCursor, QFont, QLinearGradient, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import theme
from .model import BlurClip, Clip, ImageClip, Project, clamp, fmt_short

HEADER_W = 104
RULER_H = 26
TRACKS = [("video", 44), ("blur", 62), ("image", 62)]
GAP = 4
PAD_BOTTOM = 10

LABEL_STEPS = [0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]


class Timeline(QWidget):
    seeked = Signal(float)
    selectionChanged = Signal(object)      # Clip | None
    clipsChanged = Signal()                # 数据变了（需要重绘 / 刷新预览）
    editFinished = Signal()                # 一次拖拽结束
    message = Signal(str)
    filesDropped = Signal(list, float)   # 拖入的素材路径列表 + 落点时间

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.ppsec = 40.0
        self.offset = 0.0
        self.playhead = 0.0
        self.selected: Optional[Clip] = None
        self._strip: list[tuple[float, object]] = []
        self._drag: dict | None = None
        self._hover = None
        self._pending_fit = False

        self.setMinimumHeight(RULER_H + sum(h for _, h in TRACKS) + GAP * len(TRACKS) + PAD_BOTTOM)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setCursor(Qt.ArrowCursor)

    # ---------------- 数据 ----------------
    def set_project(self, project: Optional[Project]) -> None:
        self.project = project
        self.selected = None
        self.offset = 0.0
        self.playhead = 0.0
        self._strip = []
        if project is not None:
            self.fit()
            # 首次 set_project 时窗口布局可能还没定型，等布局完成再 fit 一次
            self._pending_fit = True
        self.update()

    def set_filmstrip(self, images: list) -> None:
        self._strip = images or []
        self.update()

    def set_playhead(self, t: float, follow: bool = False) -> None:
        self.playhead = max(0.0, float(t))
        if follow:
            span = self._span()
            x = self.x_of(self.playhead)
            if x > self.width() - 30:
                self.offset = max(0.0, self.playhead - span * 0.75)
            elif x < HEADER_W:
                self.offset = max(0.0, self.playhead - span * 0.1)
        self.update()

    def set_selected(self, clip: Optional[Clip]) -> None:
        if clip is not self.selected:
            self.selected = clip
            self.selectionChanged.emit(clip)
            self.update()

    # ---------------- 坐标换算 ----------------
    @property
    def duration(self) -> float:
        return max(1.0, self.project.duration if self.project else 60.0)

    def _span(self) -> float:
        return max(0.1, (self.width() - HEADER_W) / max(self.ppsec, 0.01))

    def x_of(self, t: float) -> float:
        return HEADER_W + (t - self.offset) * self.ppsec

    def t_of(self, x: float) -> float:
        return self.offset + (x - HEADER_W) / max(self.ppsec, 0.01)

    def _track_top(self, i: int) -> float:
        y = RULER_H
        for j in range(i):
            y += TRACKS[j][1] + GAP
        return y

    def _track_at(self, y: float) -> int:
        for i in range(len(TRACKS)):
            top = self._track_top(i)
            if top <= y <= top + TRACKS[i][1]:
                return i
        return -1

    def _clip_rect(self, track: int, start: float, end: float) -> QRectF:
        y = self._track_top(track)
        h = TRACKS[track][1]
        x1, x2 = self.x_of(start), self.x_of(end)
        return QRectF(x1, y + 6, max(4.0, x2 - x1), h - 12)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._pending_fit and self.width() > HEADER_W + 200:
            self._pending_fit = False
            self.fit()

    # ---------------- 缩放 ----------------
    def fit(self) -> None:
        span = self.duration * 1.02
        self.ppsec = clamp((self.width() - HEADER_W - 12) / max(span, 0.1), 2.0, 2000.0)
        self.offset = 0.0
        self.update()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        anchor_x = max(HEADER_W + 1, anchor_x)
        t = self.t_of(anchor_x)
        self.ppsec = clamp(self.ppsec * factor, 2.0, 4000.0)
        self.offset = max(0.0, t - (anchor_x - HEADER_W) / self.ppsec)
        self.update()

    def scroll_x(self, seconds: float) -> None:
        self.offset = max(0.0, self.offset + seconds)
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.BG))

        self._paint_ruler(p, w)
        self._paint_tracks(p, w, h)
        self._paint_clips(p, w)
        self._paint_header(p)
        self._paint_playhead(p, h)

    def _paint_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(0, 0, w, RULER_H, QColor("#1a1a1f"))
        p.setPen(QPen(QColor("#2a2a33"), 1))
        p.drawLine(0, RULER_H - 1, w, RULER_H - 1)

        if self.ppsec <= 0:
            return
        target = 84.0 / self.ppsec
        step = next((s for s in LABEL_STEPS if s >= target), LABEL_STEPS[-1])
        minor = step / 5.0 if step / 5.0 * self.ppsec >= 5 else step

        t = self.offset - (self.offset % minor)
        f = QFont(self.font())
        f.setPointSizeF(8.2)
        p.setFont(f)
        while True:
            x = self.x_of(t)
            if x > w:
                break
            if x >= HEADER_W:
                major = abs(t / step - round(t / step)) < 1e-6
                if major:
                    p.setPen(QPen(QColor("#3d3d49"), 1))
                    p.drawLine(QPointF(x, RULER_H - 10), QPointF(x, RULER_H))
                    p.setPen(QColor(theme.MUTED))
                    label = fmt_short(t) if step < 1 else fmt_short(t).split(".")[0]
                    p.drawText(QPointF(x + 4, 12), label)
                else:
                    p.setPen(QPen(QColor("#2e2e38"), 1))
                    p.drawLine(QPointF(x, RULER_H - 5), QPointF(x, RULER_H))
            t += minor

    def _paint_tracks(self, p: QPainter, w: int, h: int) -> None:
        # 竖向网格（与标尺对齐）
        if self.ppsec > 0:
            target = 84.0 / self.ppsec
            step = next((s for s in LABEL_STEPS if s >= target), LABEL_STEPS[-1])
            t = self.offset - (self.offset % step)
            p.setPen(QPen(QColor(255, 255, 255, 8), 1))
            while True:
                x = self.x_of(t)
                if x > w:
                    break
                if x >= HEADER_W:
                    p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
                t += step

        for i, (name, th) in enumerate(TRACKS):
            top = self._track_top(i)
            bg = QColor(theme.TRACK_BG) if i % 2 == 0 else QColor(theme.TRACK_ALT)
            p.fillRect(QRectF(HEADER_W, top, w - HEADER_W, th), bg)
            p.setPen(QPen(QColor("#232329"), 1))
            p.drawLine(QPointF(HEADER_W, top + th), QPointF(w, top + th))

    def _paint_header(self, p: QPainter) -> None:
        h = self.height()
        p.fillRect(0, 0, HEADER_W, h, QColor("#1c1c22"))
        p.setPen(QPen(QColor("#2a2a33"), 1))
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, h)

        f = QFont(self.font())
        f.setPointSizeF(8.4)
        p.setFont(f)
        for i, (name, th) in enumerate(TRACKS):
            top = self._track_top(i)
            label, color = {
                "video": ("视频", "#8fb6f0"),
                "blur": ("模糊", "#f08a8a"),
                "image": ("图片", "#63d6c8"),
            }[name]
            p.setPen(QColor(color))
            p.drawText(QRectF(12, top, HEADER_W - 20, th), Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setPen(QColor(theme.MUTED))
            count = 0
            if self.project is not None:
                count = len(self.project.blurs) if name == "blur" else (
                    len(self.project.images) if name == "image" else 1)
            if count:
                p.drawText(QRectF(12, top, HEADER_W - 24, th),
                           Qt.AlignVCenter | Qt.AlignRight, str(count))

    def _paint_clips(self, p: QPainter, w: int) -> None:
        if self.project is None:
            r = QRectF(HEADER_W + 4, self._track_top(0) + 6,
                       min(340, w - HEADER_W - 24), TRACKS[0][1] - 12)
            p.setPen(QPen(QColor("#3a3a46"), 1, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 6))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "拖入视频/图片素材，或从左侧素材库加载")
            return

        # 视频轨
        if self.project.has_video:
            r = self._clip_rect(0, 0, self.duration)
            p.save()
            p.setClipRect(r)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0, QColor("#243347"))
            grad.setColorAt(1, QColor("#1a2432"))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(r, 5, 5)
            if self._strip:
                self._paint_strip(p, r, self.duration)
            p.restore()
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#3a5a7d"), 1))
            p.drawRoundedRect(r, 5, 5)
            f = QFont(self.font())
            f.setPointSizeF(8.6)
            p.setFont(f)
            fm = p.fontMetrics()
            name = self.project.active_name
            tw = min(fm.horizontalAdvance(name) + 14, r.width() - 12)
            if tw > 20:
                bg = QRectF(r.left() + 5, r.center().y() - 9, tw, 18)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(8, 12, 18, 165))
                p.drawRoundedRect(bg, 4, 4)
            p.setPen(QColor("#dbe8f8"))
            p.drawText(r.adjusted(9, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, name)
        else:
            r = QRectF(HEADER_W + 2, self._track_top(0) + 6, min(320, w - HEADER_W - 20),
                       TRACKS[0][1] - 12)
            p.setPen(QPen(QColor("#3a3a46"), 1, Qt.DashLine))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "拖入视频/图片素材，或从素材库加载")

        for clip in self.project.blurs:
            self._paint_blur(p, clip)
        for clip in self.project.images:
            self._paint_image(p, clip)

    def _paint_strip(self, p: QPainter, r: QRectF, duration: float) -> None:
        n = len(self._strip)
        for i, (t, img) in enumerate(self._strip):
            t0 = duration * i / n
            t1 = duration * (i + 1) / n
            rr = QRectF(self.x_of(t0), r.top(), self.x_of(t1) - self.x_of(t0), r.height())
            if rr.right() < HEADER_W or rr.left() > self.width() or img is None:
                continue
            iw, ih = img.width(), img.height()
            if not iw or not ih:
                continue
            scale = max(rr.width() / iw, rr.height() / ih)
            dw, dh = iw * scale, ih * scale
            p.drawImage(QRectF(rr.center().x() - dw / 2, rr.center().y() - dh / 2, dw, dh), img)
        p.fillRect(r, QColor(10, 14, 20, 120))

    def _paint_blur(self, p: QPainter, clip: BlurClip) -> None:
        r = self._clip_rect(1, clip.start, clip.end)
        if r.right() < HEADER_W or r.left() > self.width():
            return
        base = QColor(theme.RED) if clip.label == "r18" else QColor(theme.AMBER)
        fill = QColor(base)
        fill.setAlpha(70 if clip.enabled else 28)
        border = QColor(base)
        border.setAlpha(210 if clip.enabled else 90)

        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.NoBrush)
        sel = clip is self.selected
        p.setPen(QPen(QColor("#ffffff") if sel else border, 1.8 if sel else 1.2))
        p.drawRoundedRect(r, 4, 4)

        # 左侧色条
        p.setPen(Qt.NoPen)
        p.setBrush(border)
        p.drawRoundedRect(QRectF(r.left() + 2, r.top() + 3, 2.5, r.height() - 6), 1.2, 1.2)

        if r.width() > 34:
            p.setClipRect(r.adjusted(4, 0, -2, 0))
            p.setPen(QColor("#ffffff") if clip.enabled else QColor("#9a9aa5"))
            f = QFont(self.font())
            f.setPointSizeF(8.0)
            p.setFont(f)
            text = f"{clip.label.upper()} · σ{int(clip.radius)}"
            if r.width() > 96:
                text = f"{fmt_short(clip.start)}  {text}"
            p.drawText(r.adjusted(8, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
            p.setClipping(False)

        if sel:
            self._paint_handles(p, r)

    def _paint_image(self, p: QPainter, clip: ImageClip) -> None:
        r = self._clip_rect(2, clip.start, clip.end)
        if r.right() < HEADER_W or r.left() > self.width():
            return
        base = QColor(theme.TEAL)
        fill = QColor(base)
        fill.setAlpha(60 if clip.enabled else 24)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.NoBrush)
        sel = clip is self.selected
        p.setPen(QPen(QColor("#ffffff") if sel else QColor(base.red(), base.green(), base.blue(), 200),
                      1.8 if sel else 1.2))
        p.drawRoundedRect(r, 4, 4)
        if r.width() > 34:
            p.setClipRect(r.adjusted(2, 0, -2, 0))
            p.setPen(QColor("#ffffff") if clip.enabled else QColor("#9a9aa5"))
            f = QFont(self.font())
            f.setPointSizeF(8.0)
            p.setFont(f)
            p.drawText(r.adjusted(7, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       f"图片 · {int(clip.scale * 100)}%")
            p.setClipping(False)
        if sel:
            self._paint_handles(p, r)

    def _paint_handles(self, p: QPainter, r: QRectF) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        for x in (r.left(), r.right()):
            p.drawRoundedRect(QRectF(x - 1.6, r.center().y() - 8, 3.2, 16), 1.6, 1.6)

    def _paint_playhead(self, p: QPainter, h: int) -> None:
        x = self.x_of(self.playhead)
        if x < HEADER_W - 1 or x > self.width():
            return
        p.setPen(QPen(QColor(theme.PLAYHEAD), 1.4))
        p.drawLine(QPointF(x, RULER_H - 2), QPointF(x, h))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PLAYHEAD))
        p.drawPolygon(QPolygonF([
            QPointF(x - 5.5, RULER_H - 12), QPointF(x + 5.5, RULER_H - 12),
            QPointF(x + 5.5, RULER_H - 5), QPointF(x, RULER_H),
            QPointF(x - 5.5, RULER_H - 5),
        ]))

    # ---------------- 命中测试 ----------------
    def _duplicate_image(self, src: ImageClip, at: float | None = None) -> ImageClip:
        """复制一个图片片段（新 id），可选把起点移到 at。"""
        dup = ImageClip.from_dict({**src.to_dict(), "id": ""})
        if at is not None:
            dur = max(0.05, src.duration)
            dup.start = round(clamp(at, 0.0, max(0.0, self.duration - dur)), 3)
            dup.end = round(dup.start + dur, 3)
        self.project.images.append(dup)
        self.clipsChanged.emit()
        self.message.emit("已复制图片片段（Alt + 拖动）")
        return dup

    def _hit(self, pos: QPointF) -> tuple[int, Optional[Clip], str]:
        """返回 (轨道, 片段, 区域) 区域 ∈ {'body','left','right'}"""
        track = self._track_at(pos.y())
        if track < 0 or self.project is None:
            return -1, None, ""
        clips: list[Clip] = []
        if track == 1:
            clips = self.project.blurs
        elif track == 2:
            clips = self.project.images
        for clip in reversed(clips):
            r = self._clip_rect(track, clip.start, clip.end)
            if r.adjusted(-3, -2, 3, 2).contains(pos):
                if abs(pos.x() - r.left()) <= 5:
                    return track, clip, "left"
                if abs(pos.x() - r.right()) <= 5:
                    return track, clip, "right"
                return track, clip, "body"
        return track, None, ""

    # ---------------- 鼠标 ----------------
    def mousePressEvent(self, e) -> None:
        pos = e.position()
        if self.project is None:
            return
        if pos.y() < RULER_H and pos.x() > HEADER_W:
            self._drag = {"kind": "playhead"}
            self.seeked.emit(clamp(self.t_of(pos.x()), 0, self.duration))
            return
        if pos.x() < HEADER_W:
            return

        track, clip, zone = self._hit(pos)
        if track < 0:
            return

        if clip is not None:
            # 图片轨：Alt + 拖动 = 复制出一个新片段（和 Pr 一样）
            if track == 2 and isinstance(clip, ImageClip) and (e.modifiers() & Qt.AltModifier):
                clip = self._duplicate_image(clip)
            self.set_selected(clip)
            self._drag = {
                "kind": "trim" if zone != "body" else "move",
                "zone": zone,
                "clip": clip,
                "press_x": pos.x(),
                "press_t": self.t_of(pos.x()),
                "start": clip.start,
                "end": clip.end,
            }
            return

        # 图片轨空白处 Alt + 拖动：复制最后一个图片片段并拖到松手位置
        if track == 2 and (e.modifiers() & Qt.AltModifier) and self.project.images:
            t = clamp(self.t_of(pos.x()), 0, self.duration)
            dup = self._duplicate_image(self.project.images[-1], at=t)
            self.set_selected(dup)
            self._drag = {
                "kind": "move", "zone": "body", "clip": dup,
                "press_x": pos.x(), "press_t": t,
                "start": dup.start, "end": dup.end,
            }
            return

        # 空白处：模糊轨拖拽新建片段，其它轨只移动播放头
        self.set_selected(None)
        t = clamp(self.t_of(pos.x()), 0, self.duration)
        self.seeked.emit(t)
        if track == 1:
            clip = BlurClip(start=t, end=min(self.duration, t + 0.2), source="manual")
            self._drag = {
                "kind": "create", "clip": clip, "added": False,
                "press_x": pos.x(), "press_t": t, "start": t, "end": clip.end,
            }

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._drag is None:
            track, clip, zone = self._hit(pos)
            if clip is not None and zone in ("left", "right"):
                self.setCursor(Qt.SizeHorCursor)
            elif clip is not None:
                self.setCursor(Qt.OpenHandCursor)
            elif pos.y() < RULER_H and pos.x() > HEADER_W:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        kind = self._drag["kind"]
        if kind == "playhead":
            self.seeked.emit(clamp(self.t_of(pos.x()), 0, self.duration))
            return

        clip = self._drag["clip"]
        dt = self.t_of(pos.x()) - self._drag["press_t"]
        self.setCursor(Qt.ClosedHandCursor if kind == "move" else Qt.SizeHorCursor)

        if kind == "move":
            dur = self._drag["end"] - self._drag["start"]
            new_start = clamp(self._drag["start"] + dt, 0.0, max(0.0, self.duration - dur))
            new_start += self._snap_delta(new_start, clip)
            new_start += self._snap_delta(new_start + dur, clip)
            new_start = clamp(new_start, 0.0, max(0.0, self.duration - dur))
            clip.start = round(new_start, 3)
            clip.end = round(new_start + dur, 3)
        elif kind == "trim":
            if self._drag["zone"] == "left":
                new_start = clamp(self._drag["start"] + dt, 0.0, clip.end - 0.05)
                new_start = clamp(new_start + self._snap_delta(new_start, clip), 0.0, clip.end - 0.05)
                clip.start = round(new_start, 3)
            else:
                new_end = clamp(self._drag["end"] + dt, clip.start + 0.05, self.duration)
                new_end = clamp(new_end + self._snap_delta(new_end, clip), clip.start + 0.05, self.duration)
                clip.end = round(new_end, 3)
        elif kind == "create":
            if not self._drag["added"]:
                if abs(pos.x() - self._drag["press_x"]) <= 4:
                    return
                self.project.blurs.append(clip)
                self._drag["added"] = True
                self.set_selected(clip)
            new_end = clamp(self.t_of(pos.x()), self._drag["start"] + 0.05, self.duration)
            new_end = clamp(new_end + self._snap_delta(new_end, clip), self._drag["start"] + 0.05, self.duration)
            clip.end = round(new_end, 3)

        if isinstance(clip, BlurClip):
            self.project.blurs.sort(key=lambda c: c.start)
        self.clipsChanged.emit()
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is None:
            return
        kind = self._drag["kind"]
        clip = self._drag.get("clip")
        added = self._drag.get("added", True)
        self._drag = None
        self.setCursor(Qt.ArrowCursor)
        if kind == "create" and isinstance(clip, BlurClip):
            if not added:
                self.update()
                return
            if clip.duration < 0.15:
                clip.end = min(self.duration, clip.start + 0.5)
        if kind != "playhead":
            self.editFinished.emit()
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        pos = e.position()
        track, clip, _zone = self._hit(pos)
        if clip is not None:
            self.set_selected(clip)
        elif self.project is not None and pos.y() < RULER_H and pos.x() > HEADER_W:
            self.fit()

    def wheelEvent(self, e) -> None:
        delta = e.angleDelta()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.18 if delta.y() > 0 else 1 / 1.18, e.position().x())
        elif e.modifiers() & Qt.ShiftModifier:
            self.scroll_x(-delta.y() / 120 * self._span() * 0.25)
        else:
            if delta.x():
                self.scroll_x(-delta.x() / 120 * self._span() * 0.15)
            else:
                self.scroll_x(-delta.y() / 120 * self._span() * 0.15)
        e.accept()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Delete and self.selected is not None and self.project is not None:
            self.project.remove(self.selected)
            self.set_selected(None)
            self.clipsChanged.emit()
            self.editFinished.emit()
            self.update()
            return
        super().keyPressEvent(e)

    # ---------------- 吸附 ----------------
    def _snap_delta(self, value: float, exclude: Clip | None) -> float:
        if self.project is None:
            return 0.0
        tol = 8.0 / max(self.ppsec, 0.01)
        targets = [0.0, self.duration, self.playhead]
        for c in self.project.all_clips():
            if c is exclude:
                continue
            targets.extend([c.start, c.end])
        best = None
        for t in targets:
            d = t - value
            if abs(d) <= tol and (best is None or abs(d) < abs(best)):
                best = d
        return best or 0.0

    # ---------------- 右键菜单 ----------------
    def contextMenuEvent(self, e) -> None:
        if self.project is None:
            return
        _track, clip, _zone = self._hit(QPointF(e.pos()))
        menu = self.build_context_menu(clip)
        menu.exec(e.globalPos())

    def build_context_menu(self, clip: Clip | None) -> QMenu:
        menu = QMenu(self)
        if clip is not None:
            self.set_selected(clip)
            act_toggle = QAction("禁用片段" if clip.enabled else "启用片段", menu)
            act_toggle.triggered.connect(lambda: self._toggle(clip))
            menu.addAction(act_toggle)
            if isinstance(clip, ImageClip):
                segs = self._align_targets()
                act_align = QAction(f"以此图片对齐所有模糊片段（{len(segs)} 段）", menu)
                act_align.setEnabled(bool(segs))
                act_align.setToolTip("清除其它图片片段，再按每个模糊片段的时间复制这张图")
                act_align.triggered.connect(lambda: self._align_image_to_blurs(clip))
                menu.addAction(act_align)
            if isinstance(clip, BlurClip):
                act_r = QAction("模糊强度重置为 32", menu)
                act_r.triggered.connect(lambda: self._set_radius(clip, 32.0))
                menu.addAction(act_r)
            menu.addSeparator()
            act_del = QAction(icons_trash(), "删除片段", menu)
            act_del.triggered.connect(lambda: self._delete(clip))
            menu.addAction(act_del)
            menu.addSeparator()
        act_fit = QAction("适应窗口", menu)
        act_fit.triggered.connect(self.fit)
        menu.addAction(act_fit)
        return menu

    def _toggle(self, clip: Clip) -> None:
        clip.enabled = not clip.enabled
        self.clipsChanged.emit()
        self.update()

    def _align_targets(self) -> list[BlurClip]:
        """对齐目标：启用的模糊片段（全禁用时退回全部）。"""
        if self.project is None:
            return []
        segs = [b for b in self.project.blurs if b.enabled]
        return sorted(segs or self.project.blurs, key=lambda b: b.start)

    def _align_image_to_blurs(self, clip: ImageClip) -> None:
        """用这张图铺满所有模糊片段：清掉其它图片，再按每段起止各复制一份。"""
        if self.project is None:
            return
        segs = self._align_targets()
        if not segs:
            self.message.emit("当前没有模糊片段，无法对齐")
            return
        template = clip.to_dict()
        clip.start = round(segs[0].start, 3)
        clip.end = round(segs[0].end, 3)
        clips: list[ImageClip] = [clip]
        for seg in segs[1:]:
            clips.append(ImageClip.from_dict({
                **template,
                "id": "",
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
            }))
        self.project.images = clips
        self.set_selected(clip)
        self.clipsChanged.emit()
        self.editFinished.emit()
        self.message.emit(f"已把该图片对齐到 {len(segs)} 个模糊片段（其它图片片段已清除）")

    def _set_radius(self, clip: BlurClip, radius: float) -> None:
        clip.radius = radius
        self.clipsChanged.emit()
        self.editFinished.emit()
        self.update()

    def _delete(self, clip: Clip) -> None:
        self.project.remove(clip)
        if clip is self.selected:
            self.set_selected(None)
        self.clipsChanged.emit()
        self.editFinished.emit()
        self.update()

    # ---------------- 拖放 ----------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        if self.project is None:
            return
        t = clamp(self.t_of(e.position().x()), 0, self.duration)
        paths: list[str] = []
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path:
                paths.append(path)
        if paths:
            self.filesDropped.emit(paths, t)
        self.update()


def icons_trash():
    from . import icons
    return icons.icon("trash")
