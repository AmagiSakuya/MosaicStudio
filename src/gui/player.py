"""视频预览：解码线程 + 播放器控件（实时叠加模糊 / 图片 + 声音播放）。"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import (
    QMutex, QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import icons, theme
from .model import Project, fmt_tc

# 声音预览依赖 QtMultimedia（pyside6-addons）。缺失时静默降级为无声预览。
try:  # pragma: no cover - 取决于运行环境
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    AUDIO_AVAILABLE = True
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]
    AUDIO_AVAILABLE = False


# ---------------- 图像处理 ----------------
def _fast_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """大 sigma 时先降采样再模糊，视觉几乎一致但快很多。"""
    if sigma <= 0.4:
        return img
    if sigma > 8:
        k = max(2, int(sigma / 4))
        small = cv2.resize(img, (max(1, img.shape[1] // k), max(1, img.shape[0] // k)),
                           interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigma / k)
        return cv2.resize(small, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(img, (0, 0), sigma)


def _composite(img: np.ndarray, ov: dict) -> np.ndarray:
    src = ov["img"]
    H, W = img.shape[:2]
    th = max(1, int(round(H * float(ov["scale"]))))
    tw = max(1, int(round(src.shape[1] * th / max(1, src.shape[0]))))
    if (tw, th) != (src.shape[1], src.shape[0]):
        src = cv2.resize(src, (tw, th), interpolation=cv2.INTER_AREA)
    cx = int((W - tw) * float(ov["x"]))
    cy = int((H - th) * float(ov["y"]))
    x0, y0 = max(0, cx), max(0, cy)
    x1, y1 = min(W, cx + tw), min(H, cy + th)
    if x1 <= x0 or y1 <= y0:
        return img
    sub = src[y0 - cy:y1 - cy, x0 - cx:x1 - cx]
    alpha = (sub[:, :, 3:4].astype(np.float32) / 255.0) * float(ov["opacity"])
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (sub[:, :, :3].astype(np.float32) * alpha + roi * (1.0 - alpha)).astype(np.uint8)
    return img


def to_qimage(bgr: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format_RGB888).copy()


def load_rgba(path: str) -> np.ndarray | None:
    """读图片为 BGRA（兼容中文路径）。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    except (OSError, ValueError):
        return None
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


# ---------------- 解码线程 ----------------
class DecodeWorker(QThread):
    frameReady = Signal(QImage, float)
    infoReady = Signal(float, float, int, int)
    failed = Signal(str)
    playbackFinished = Signal()

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = str(path)
        self._mtx = QMutex()
        self._seek_to: float | None = None
        self._playing = False
        self._stop = False
        self._redraw = False
        self._fps = 30.0
        self._pos = 0.0
        self._blurs: list[tuple[float, float, float]] = []
        self._overlays: list[dict] = []
        self._preview = True
        self._last_raw: np.ndarray | None = None

    # ---- 外部控制 ----
    def set_playing(self, on: bool) -> None:
        self._mtx.lock()
        self._playing = on
        self._mtx.unlock()

    def seek(self, t: float) -> None:
        self._mtx.lock()
        self._seek_to = max(0.0, float(t))
        self._mtx.unlock()

    def request_redraw(self) -> None:
        self._mtx.lock()
        self._redraw = True
        self._mtx.unlock()

    def stop(self) -> None:
        self._mtx.lock()
        self._stop = True
        self._mtx.unlock()

    def set_effects(self, blurs, overlays, preview: bool = True) -> None:
        self._mtx.lock()
        self._blurs = blurs
        self._overlays = overlays
        self._preview = preview
        self._redraw = True
        self._mtx.unlock()

    # ---- 主循环 ----
    def run(self) -> None:  # noqa: C901
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            self.failed.emit(f"无法打开视频：{self._path}")
            return
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 1e-3 or fps > 1000:
            fps = 30.0
        self._fps = fps
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = count / fps if count > 0 else 0.0
        self.infoReady.emit(duration, fps, w, h)

        idx = 0
        ok, frame = cap.read()
        if ok and frame is not None:
            self._last_raw = frame
            self._emit(frame, 0.0)
        idx = 1

        try:
            while True:
                self._mtx.lock()
                if self._stop:
                    self._mtx.unlock()
                    break
                seek_to = self._seek_to
                self._seek_to = None
                playing = self._playing
                redraw = self._redraw
                self._redraw = False
                self._mtx.unlock()

                if seek_to is not None:
                    target = int(round(seek_to * fps))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        idx = target + 1
                        self._last_raw = frame
                        self._pos = idx / fps
                        self._emit(frame, idx / fps)
                    continue

                if redraw:
                    if self._last_raw is not None:
                        self._emit(self._last_raw, self._pos, force=True)
                    continue

                if not playing:
                    self.msleep(6)
                    continue

                t0 = time.perf_counter()
                ok, frame = cap.read()
                if not ok or frame is None:
                    self._mtx.lock()
                    self._playing = False
                    self._mtx.unlock()
                    self.playbackFinished.emit()
                    continue
                idx += 1
                self._last_raw = frame
                self._pos = (idx - 1) / fps
                self._emit(frame, self._pos)

                period = 1.0 / fps
                spent = time.perf_counter() - t0
                if spent < period:
                    self.msleep(max(1, int((period - spent) * 1000)))
        finally:
            cap.release()

    def _emit(self, frame: np.ndarray, t: float, force: bool = False) -> None:
        img = frame
        self._mtx.lock()
        blurs = list(self._blurs)
        overlays = list(self._overlays)
        preview = self._preview
        self._mtx.unlock()
        if preview:
            try:
                for s, e, r in blurs:
                    if s <= t < e:
                        img = _fast_blur(img, r)
                for ov in overlays:
                    if ov["start"] <= t < ov["end"]:
                        img = _composite(img, ov)
            except (cv2.error, ValueError, KeyError):
                img = frame
        self.frameReady.emit(to_qimage(img), t)


# ---------------- 胶片缩略图 ----------------
class FilmstripWorker(QThread):
    ready = Signal(str, object)

    def __init__(self, path: str, duration: float, count: int = 14, height: int = 80) -> None:
        super().__init__()
        self._path, self._duration = path, duration
        self._count, self._height = count, height

    def run(self) -> None:
        images: list[tuple[float, QImage]] = []
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            dur = self._duration or (int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps)
            for i in range(self._count):
                t = dur * (i + 0.5) / self._count
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t * fps)))
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                h = self._height
                w = max(1, int(frame.shape[1] * h / max(1, frame.shape[0])))
                small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
                images.append((t, to_qimage(small)))
        finally:
            cap.release()
        if images:
            self.ready.emit(self._path, images)


# ---------------- 预览画面 ----------------
class Viewer(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self._hint = "从左侧素材库双击视频素材加载"
        self.setMinimumSize(420, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_frame(self, img: QImage) -> None:
        self._pix = QPixmap.fromImage(img)
        self.update()

    def clear(self, hint: str = "") -> None:
        self._pix = None
        if hint:
            self._hint = hint
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0d0f"))
        if self._pix is None or self._pix.isNull():
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self._hint)
            return
        size: QSize = self._pix.size().scaled(self.size() - QSize(20, 20), Qt.KeepAspectRatio)
        target = QRect(QPoint(0, 0), size)
        target.moveCenter(self.rect().center())
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(target, self._pix)
        p.setPen(QColor(255, 255, 255, 16))
        p.drawRect(target.adjusted(0, 0, -1, -1))


# ---------------- 播放器 ----------------
class Player(QWidget):
    positionChanged = Signal(float)
    playStateChanged = Signal(bool)
    videoOpened = Signal(float, float, int, int)   # duration, fps, w, h

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self._worker: DecodeWorker | None = None
        self._duration = 0.0
        self._fps = 30.0
        self._pos = 0.0
        self._playing = False
        self._image_cache: dict[str, np.ndarray] = {}
        self._audio = None
        self._audio_out = None
        self._path = ""

        self.viewer = Viewer(self)

        # 传输控制条
        bar = QWidget(self)
        bar.setFixedHeight(40)
        bar.setStyleSheet("background:#1a1a1f; border-top:1px solid #26262e;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(4)

        self.tc = QLabel("00:00:00:00")
        self.tc.setStyleSheet(
            "color:#e8e8ee; font-family:Consolas,'Cascadia Mono',monospace;"
            "font-size:13px; letter-spacing:0.5px;")
        self.tc.setToolTip("当前时间码 / 总时长")
        h.addWidget(self.tc)

        h.addStretch(1)
        self.btn_start = self._tb("to_start", "回到开头 (Home)")
        self.btn_prev = self._tb("step_back", "上一帧 (←)")
        self.btn_play = self._tb("play", "播放 / 暂停 (空格)", big=True)
        self.btn_next = self._tb("step_fwd", "下一帧 (→)")
        self.btn_end = self._tb("to_end", "跳到结尾 (End)")
        for b in (self.btn_start, self.btn_prev, self.btn_play, self.btn_next, self.btn_end):
            h.addWidget(b)
        h.addStretch(1)

        self.chk_preview = QCheckBox("模糊预览")
        self.chk_preview.setChecked(True)
        self.chk_preview.setToolTip("在预览里实时显示模糊与图片叠加效果")
        self.chk_preview.toggled.connect(lambda _v: self.refresh_effects())
        h.addWidget(self.chk_preview)

        self.chk_sound = QCheckBox("声音")
        self.chk_sound.setChecked(True)
        self.chk_sound.setToolTip("预览时播放视频原声（不影响导出）")
        self.chk_sound.toggled.connect(self._apply_mute)
        if not AUDIO_AVAILABLE:
            self.chk_sound.setChecked(False)
            self.chk_sound.setEnabled(False)
            self.chk_sound.setToolTip("当前环境缺少 QtMultimedia 组件（pip install pyside6-addons），"
                                      "只能无声预览")
        h.addWidget(self.chk_sound)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.viewer, 1)
        lay.addWidget(bar)

        self.btn_start.clicked.connect(lambda: self.seek(0.0))
        self.btn_end.clicked.connect(lambda: self.seek(max(0.0, self._duration - 0.04)))
        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.btn_play.clicked.connect(self.toggle_play)

        # 音画同步：解码线程是画面主时钟，音频每 600ms 校一次，偏差过大就重新对齐
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(600)
        self._sync_timer.timeout.connect(self._sync_audio)
        self._sync_timer.start()

    def _tb(self, name: str, tip: str, big: bool = False) -> QToolButton:
        b = QToolButton(self)
        b.setIcon(icons.icon(name))
        b.setIconSize(QSize(20 if big else 16, 20 if big else 16))
        b.setToolTip(tip)
        b.setFixedSize(34 if big else 28, 28)
        return b

    # ---------- 打开 / 关闭 ----------
    def open_video(self, path: str) -> bool:
        self.close_video()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return False
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 1e-3 or fps > 1000:
            fps = 30.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        duration = count / fps if count else 0.0

        self._fps = fps
        self._duration = duration
        self._pos = 0.0
        self._path = str(path)
        self._image_cache.clear()

        self._worker = DecodeWorker(str(path), self)
        self._worker.frameReady.connect(self._on_frame)
        self._worker.failed.connect(lambda msg: self.viewer.clear(msg))
        self._worker.playbackFinished.connect(self._on_finished)
        self._worker.start()
        self._open_audio(str(path))

        self.videoOpened.emit(duration, fps, w, h)
        self._update_tc()
        self.refresh_effects()
        return True

    def close_video(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(1500)
            self._worker = None
        self._close_audio()
        self._playing = False
        self._path = ""
        self.viewer.clear("从左侧素材库双击视频素材加载")
        self._duration = 0.0
        self._update_tc()

    # ---------- 声音 ----------
    def _open_audio(self, path: str) -> None:
        self._close_audio()
        if not AUDIO_AVAILABLE:
            return
        try:
            self._audio_out = QAudioOutput(self)          # type: ignore[misc]
            self._audio_out.setVolume(0.9)
            self._audio = QMediaPlayer(self)              # type: ignore[misc]
            self._audio.setAudioOutput(self._audio_out)
            self._audio.setSource(QUrl.fromLocalFile(path))
            self._apply_mute(self.chk_sound.isChecked())
        except Exception:  # noqa: BLE001 - 没有音频设备时也不该影响预览
            self._audio = None
            self._audio_out = None

    def _close_audio(self) -> None:
        if self._audio is not None:
            try:
                self._audio.stop()
                self._audio.setSource(QUrl())
            except Exception:  # noqa: BLE001
                pass
        self._audio = None
        self._audio_out = None

    def _apply_mute(self, on: bool) -> None:
        if self._audio_out is not None:
            try:
                self._audio_out.setMuted(not on)
            except Exception:  # noqa: BLE001
                pass

    def _audio_seek(self, t: float) -> None:
        if self._audio is None:
            return
        try:
            self._audio.setPosition(int(max(0.0, t) * 1000))
        except Exception:  # noqa: BLE001
            pass

    def _sync_audio(self) -> None:
        """画面是主时钟：音频落后/超前太多就把它拽回来。"""
        if self._audio is None or not self._playing:
            return
        try:
            if self._audio.playbackState() != QMediaPlayer.PlayingState:  # type: ignore[union-attr]
                return
            ap = self._audio.position() / 1000.0
        except Exception:  # noqa: BLE001
            return
        if abs(ap - self._pos) > 0.4:
            self._audio_seek(self._pos)

    # ---------- 播放控制 ----------
    def toggle_play(self) -> None:
        self.set_playing(not self._playing)

    def set_playing(self, on: bool) -> None:
        if self._worker is None:
            return
        if on and self._duration and self._pos >= self._duration - 0.06:
            self.seek(0.0)
        self._playing = on
        self._worker.set_playing(on)
        if self._audio is not None:
            try:
                if on:
                    self._audio_seek(self._pos)
                    self._audio.play()
                else:
                    self._audio.pause()
            except Exception:  # noqa: BLE001
                pass
        self.btn_play.setIcon(icons.icon("pause" if on else "play"))
        self.playStateChanged.emit(on)

    def seek(self, t: float) -> None:
        t = max(0.0, min(t, self._duration or 0.0))
        self._pos = t
        if self._worker is not None:
            self._worker.seek(t)
        self._audio_seek(t)
        self._update_tc()
        self.positionChanged.emit(t)

    def step(self, frames: int) -> None:
        self.set_playing(False)
        self.seek(self._pos + frames / max(self._fps, 1.0))

    @property
    def current_path(self) -> str:
        return self._path

    @property
    def position(self) -> float:
        return self._pos

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def is_playing(self) -> bool:
        return self._playing

    # ---------- 效果 ----------
    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.refresh_effects()

    def refresh_effects(self) -> None:
        if self._worker is None:
            return
        blurs: list[tuple[float, float, float]] = []
        overlays: list[dict] = []
        if self.project is not None:
            for c in self.project.blurs:
                if c.enabled:
                    blurs.append((c.start, c.end, float(c.radius)))
            for c in self.project.images:
                if not c.enabled or not c.path:
                    continue
                img = self._image_cache.get(c.path)
                if img is None:
                    img = load_rgba(c.path)
                    if img is None:
                        continue
                    self._image_cache[c.path] = img
                overlays.append({
                    "start": c.start, "end": c.end, "img": img,
                    "scale": c.scale, "x": c.x, "y": c.y, "opacity": c.opacity,
                })
        self._worker.set_effects(blurs, overlays, self.chk_preview.isChecked())

    def invalidate_images(self) -> None:
        self._image_cache.clear()

    # ---------- 回调 ----------
    def _on_frame(self, img: QImage, t: float) -> None:
        self.viewer.set_frame(img)
        if self._playing:
            self._pos = t
            self._update_tc()
            self.positionChanged.emit(t)

    def _on_finished(self) -> None:
        self._playing = False
        if self._audio is not None:
            try:
                self._audio.pause()
            except Exception:  # noqa: BLE001
                pass
        self.btn_play.setIcon(icons.icon("play"))
        self.playStateChanged.emit(False)

    def _update_tc(self) -> None:
        self.tc.setText(f"{fmt_tc(self._pos, self._fps)}  /  {fmt_tc(self._duration, self._fps)}")
