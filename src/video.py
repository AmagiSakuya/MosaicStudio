"""视频探测与按指定采样率抽帧。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass
class VideoInfo:
    path: Path
    fps: float
    frame_count: int
    duration: float
    width: int
    height: int


def probe(path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频（缺少解码器或文件损坏）：{path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 1e-3 or fps > 1000:
            fps = 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if frame_count > 0 else 0.0
    finally:
        cap.release()
    return VideoInfo(path=path, fps=fps, frame_count=frame_count,
                     duration=duration, width=width, height=height)


def iter_frames(
    info: VideoInfo,
    sample_fps: float = 2.0,
    start: float = 0.0,
    end: float | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """按 sample_fps 顺序抽帧，产出 (时间戳秒, BGR 帧)。

    用 grab() 跳过中间帧（只解码不转换），比逐帧 retrieve() 快得多，
    又比 seek 精确（不受关键帧限制）。
    """
    cap = cv2.VideoCapture(str(info.path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频：{info.path}")

    step = max(1, int(round(info.fps / max(sample_fps, 0.01))))
    first = max(0, int(round(start * info.fps)))
    if first > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)

    idx = first
    try:
        while True:
            if not cap.grab():
                break
            t = idx / info.fps
            if end is not None and t > end + 1e-6:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    break
                yield t, frame
            idx += 1
    finally:
        cap.release()


def count_samples(info: VideoInfo, sample_fps: float, start: float = 0.0,
                  end: float | None = None) -> int:
    """估算采样帧数，用于进度条。"""
    begin = max(0.0, start)
    stop = end if end is not None else info.duration
    if stop <= 0:
        stop = begin + 60.0
    return max(1, int(round((stop - begin) * max(sample_fps, 0.01))))


def grab_frame(path: Path, t: float) -> np.ndarray | None:
    """抓取指定时间点的单帧（用于生成预览图）。"""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t * fps)))
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
            ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def save_jpeg(frame: np.ndarray, dest: Path, quality: int = 92,
              max_width: int = 0) -> bool:
    """保存 JPEG。用 imencode 写字节，兼容中文路径（cv2.imwrite 在 Windows 上不支持）。"""
    img = frame
    if max_width and img.shape[1] > max_width:
        scale = max_width / img.shape[1]
        img = cv2.resize(img, (max_width, max(1, int(img.shape[0] * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.tobytes())
    return True
