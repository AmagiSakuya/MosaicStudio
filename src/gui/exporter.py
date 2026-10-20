"""导出：优先用 ffmpeg（滤镜链打高斯模糊 + 图片叠加），没有 ffmpeg 时退回 OpenCV。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QProcess, QThread, Signal

from ..paths import app_dir
from .model import Project
from .player import _composite, _fast_blur, load_rgba

_encoder_cache: dict[str, set[str]] = {}
_version_cache: dict[str, tuple[int, int]] = {}
_version_text: dict[str, str] = {}

ENCODERS = {
    "libx264": "H.264 · x264（CPU，兼容最好）",
    "h264_nvenc": "H.264 · NVENC（N 卡硬件编码，最快）",
    "libx265": "H.265 · x265（CPU，体积更小）",
    "hevc_nvenc": "H.265 · NVENC（N 卡硬件编码）",
}
PRESETS_CPU = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
PRESETS_GPU = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
PRESETS_GPU_LEGACY = ["default", "slow", "medium", "fast", "hp", "hq", "bd", "llhq", "llhp"]


# ---------------- ffmpeg 定位 ----------------
def _candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("FFMPEG_BINARY")
    if env:
        out.append(Path(env))
    # 程序内置的 ffmpeg 优先（bin/ffmpeg.exe，由 download_ffmpeg.py 准备）
    for base in (app_dir(), Path.cwd()):
        out.extend([base / "bin" / "ffmpeg.exe", base / "ffmpeg.exe"])
    which = shutil.which("ffmpeg")
    if which:
        out.append(Path(which))
    roots = [
        Path("C:/ffmpeg/bin"), Path("C:/Program Files/ffmpeg/bin"),
        Path("D:/ffmpeg/bin"), Path("E:/ffmpeg/bin"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links",
    ]
    for r in roots:
        out.append(r / "ffmpeg.exe")
    return out


def find_ffmpeg(hint: str | None = None) -> Path | None:
    if hint and Path(hint).is_file():
        return Path(hint)
    for c in _candidates():
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


_SKIP_DIRS = {"windows", "winsxs", "system32", "node_modules", ".git", "$recycle.bin",
              "temp", "tmp", "appdata", "programdata", "windowsapps", "assembly"}


def search_ffmpeg(roots: list[str], max_depth: int = 3, budget: float = 12.0) -> Path | None:
    """在若干根目录里浅层搜索 ffmpeg.exe，多个候选取「有 libx264 且版本最新」的。"""
    deadline = time.time() + budget
    found: list[Path] = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for dirpath, dirnames, filenames in os.walk(base):
            if time.time() > deadline:
                break
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fn.lower() == "ffmpeg.exe":
                    found.append(Path(dirpath) / fn)
            if len(found) >= 8:
                break
        if len(found) >= 8 or time.time() > deadline:
            break
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    best, best_score = found[0], None
    for p in found:
        enc = probe_encoders(p)
        score = (1 if "libx264" in enc else 0,
                 ffmpeg_version(p),
                 1 if ("h264_nvenc" in enc or "libx265" in enc) else 0)
        if best_score is None or score > best_score:
            best, best_score = p, score
    return best


def probe_encoders(ffmpeg: Path) -> set[str]:
    key = str(ffmpeg)
    if key in _encoder_cache:
        return _encoder_cache[key]
    try:
        res = subprocess.run([key, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    _encoder_cache[key] = names
    return names


def ffmpeg_version(ffmpeg: Path | str | None) -> tuple[int, int]:
    """解析 ffmpeg 主版本号，解析不出按「很老」处理。"""
    if not ffmpeg:
        return (0, 0)
    key = str(ffmpeg)
    if key in _version_cache:
        return _version_cache[key]
    first = _version_line(key)
    m = re.search(r"version\s+n?(\d+)\.(\d+)", first)
    ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    _version_cache[key] = ver
    return ver


def ffmpeg_version_text(ffmpeg: Path | str | None) -> str:
    """给界面显示的版本串，比如 N-92511 或 6.1.1。"""
    if not ffmpeg:
        return "?"
    first = _version_line(str(ffmpeg))
    m = re.search(r"version\s+(\S+)", first)
    return m.group(1) if m else "?"


def _version_line(path: str) -> str:
    if path in _version_text:
        return _version_text[path]
    try:
        res = subprocess.run([path, "-version"], capture_output=True, text=True,
                             timeout=15, creationflags=_no_window())
        first = (res.stdout.splitlines() or [""])[0]
    except (OSError, subprocess.SubprocessError):
        first = ""
    _version_text[path] = first
    return first


def presets_for(encoder: str, ffmpeg: Path | str | None) -> list[str]:
    if not encoder.endswith("nvenc"):
        return PRESETS_CPU
    return PRESETS_GPU if ffmpeg_version(ffmpeg) >= (5, 0) else PRESETS_GPU_LEGACY


def default_preset(encoder: str, ffmpeg: Path | str | None) -> str:
    if not encoder.endswith("nvenc"):
        return "medium"
    return "p5" if ffmpeg_version(ffmpeg) >= (5, 0) else "medium"


def nvenc_usable(ffmpeg: Path | str | None) -> bool:
    """FFmpeg 5 之前的 NVENC 接口太旧，新显卡上基本初始化不了。"""
    return ffmpeg_version(ffmpeg) >= (5, 0)


def _no_window() -> int:
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


# ---------------- 导出参数 ----------------
@dataclass
class ExportSettings:
    ffmpeg: str = ""
    encoder: str = "libx264"
    quality: int = 18
    preset: str = "medium"
    container: str = "mp4"
    audio: str = "copy"          # copy / aac / none
    height: int = 0              # 0 = 原始分辨率
    use_ffmpeg: bool = True
    extra: list[str] = field(default_factory=list)


def default_output(project: Project) -> str:
    if project.has_video:
        p = Path(project.video_path)
        return str(p.with_name(f"{p.stem}_blurred.mp4"))
    return str(Path.cwd() / "output.mp4")


def build_command(s: ExportSettings, project: Project, out_path: str) -> list[str]:
    args = [s.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    args += ["-i", project.video_path]

    blurs = [c for c in project.blurs if c.enabled and c.end - c.start > 0.01]
    images = [c for c in project.images if c.enabled and c.path and c.end - c.start > 0.01]
    for im in images:
        args += ["-loop", "1", "-framerate", f"{project.fps or 30.0:.6f}",
                 "-t", f"{max(project.duration, 0.1):.3f}", "-i", im.path]

    parts: list[str] = []
    cur = "0:v"
    if s.height:
        parts.append(f"[0:v]scale=-2:{int(s.height)}:flags=lanczos[vres]")
        cur = "vres"
    for i, b in enumerate(blurs):
        out = f"vb{i}"
        parts.append(
            f"[{cur}]gblur=sigma={max(0.1, float(b.radius)):.2f}:"
            f"enable='between(t,{b.start:.3f},{b.end:.3f})'[{out}]")
        cur = out
    for j, im in enumerate(images):
        idx = j + 1
        base_h = s.height or project.height or 720
        th = int(round(base_h * float(im.scale)))
        th = max(2, th - th % 2)
        parts.append(f"[{idx}:v]format=rgba,scale=-2:{th},"
                     f"colorchannelmixer=aa={max(0.0, min(1.0, im.opacity)):.3f}[im{j}]")
        out = f"vo{j}"
        parts.append(
            f"[{cur}][im{j}]overlay=x=(W-w)*{im.x:.4f}:y=(H-h)*{im.y:.4f}:"
            f"enable='between(t,{im.start:.3f},{im.end:.3f})'[{out}]")
        cur = out
    if cur != "vout":
        parts.append(f"[{cur}]null[vout]")

    args += ["-filter_complex", ";".join(parts)]
    args += ["-map", "[vout]", "-map", "0:a?"]

    q = str(int(s.quality))
    if s.encoder == "libx264":
        args += ["-c:v", "libx264", "-preset", s.preset, "-crf", q, "-pix_fmt", "yuv420p"]
    elif s.encoder == "libx265":
        args += ["-c:v", "libx265", "-preset", s.preset, "-crf", q, "-pix_fmt", "yuv420p",
                 "-tag:v", "hvc1"]
    elif s.encoder == "h264_nvenc":
        args += ["-c:v", "h264_nvenc", "-preset", s.preset, "-rc", "vbr", "-cq", q,
                 "-b:v", "0", "-pix_fmt", "yuv420p"]
    elif s.encoder == "hevc_nvenc":
        args += ["-c:v", "hevc_nvenc", "-preset", s.preset, "-rc", "vbr", "-cq", q,
                 "-b:v", "0", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "libx264", "-crf", q, "-preset", "medium", "-pix_fmt", "yuv420p"]

    if s.audio == "none":
        args += ["-an"]
    elif s.audio == "aac":
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c:a", "copy"]

    if s.container in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]

    args += ["-progress", "pipe:1", "-nostats", out_path]
    return args


# ---------------- ffmpeg 任务 ----------------
class ExportJob(QObject):
    progress = Signal(float, str)
    logLine = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, cmd: list[str], duration: float, parent=None) -> None:
        super().__init__(parent)
        self.cmd = cmd
        self.duration = max(0.1, duration)
        self._buf = ""
        self._last = 0.0
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self._cancelled = False

    def start(self) -> None:
        self.proc.start(self.cmd[0], self.cmd[1:])

    def cancel(self) -> None:
        self._cancelled = True
        if self.proc.state() != QProcess.NotRunning:
            self.proc.kill()

    def _read_out(self) -> None:
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            key, _, val = line.strip().partition("=")
            if key == "out_time_us" and val.isdigit():
                t = int(val) / 1_000_000.0
                if t - self._last > 0.2:
                    self._last = t
                    self.progress.emit(min(0.99, t / self.duration),
                                       f"编码中 {t:.1f}s / {self.duration:.1f}s")
            elif key == "progress" and val == "end":
                self.progress.emit(1.0, "收尾中…")

    def _read_err(self) -> None:
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            for line in text.splitlines()[-6:]:
                self.logLine.emit(line)

    def _on_finished(self, code: int, _status) -> None:
        if self._cancelled:
            self.failed.emit("已取消导出")
            return
        if code == 0:
            self.finished_ok.emit(self.cmd[-1])
        else:
            self.failed.emit(f"ffmpeg 退出码 {code}")

    def _on_error(self, err) -> None:
        if err == QProcess.FailedToStart:
            self.failed.emit("无法启动 ffmpeg，请检查路径是否正确")


# ---------------- OpenCV 兜底 ----------------
class CvExportThread(QThread):
    progress = Signal(float, str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, out_path: str, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.out_path = out_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        pr = self.project
        cap = cv2.VideoCapture(pr.video_path)
        if not cap.isOpened():
            self.failed.emit("无法打开源视频")
            return
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(self.out_path, fourcc, fps, (w, h))
        if not writer.isOpened():
            cap.release()
            self.failed.emit("无法创建输出文件（缺少 mp4v 编码器）")
            return

        blurs = [(c.start, c.end, c.radius) for c in pr.blurs if c.enabled]
        overlays = []
        for c in pr.images:
            if not c.enabled:
                continue
            img = load_rgba(c.path)
            if img is not None:
                overlays.append({"start": c.start, "end": c.end, "img": img,
                                 "scale": c.scale, "x": c.x, "y": c.y, "opacity": c.opacity})

        idx = 0
        try:
            while True:
                if self._cancel:
                    self.failed.emit("已取消导出")
                    return
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                t = idx / fps
                img = frame
                for s, e, r in blurs:
                    if s <= t < e:
                        img = _fast_blur(img, r)
                for ov in overlays:
                    if ov["start"] <= t < ov["end"]:
                        img = _composite(img, ov)
                writer.write(img)
                idx += 1
                if idx % 10 == 0:
                    self.progress.emit(min(0.99, idx / max(total, 1)),
                                       f"逐帧渲染 {idx}/{total}")
        finally:
            cap.release()
            writer.release()
        self.progress.emit(1.0, "完成")
        self.finished_ok.emit(self.out_path)
