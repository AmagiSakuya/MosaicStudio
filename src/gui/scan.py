"""给 GUI 复用的检测流程（带进度回调 / 可取消）。

检测结果不再落盘成 JSON + 预览图，而是直接返回结构化 payload，
由主窗口写进工程的对应素材里。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..detector import RatingDetector
from ..segments import build_segments
from ..util import hms
from ..video import count_samples, iter_frames, probe

ProgressFn = Callable[[float, str], None]


def scan_video(
    video: Path,
    detector: RatingDetector,
    *,
    level: str = "all",
    threshold: float = 0.5,
    fps: float = 0.0,
    min_duration: float = 0.5,
    gap: float = 0.75,
    smooth: int = 3,
    batch: int = 8,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """扫描视频并返回检测结果（不写任何文件）。"""
    video = Path(video)
    info = probe(video)
    if fps <= 0:
        fps = 4.0 if detector.info.device.startswith("GPU") else 2.0

    total = count_samples(info, fps, 0.0, info.duration or None)
    times: list[float] = []
    probs: list[np.ndarray] = []
    buf: list[np.ndarray] = []
    buf_t: list[float] = []
    hits = 0
    t0 = time.perf_counter()
    safe_idx = detector.info.labels.index("safe") if "safe" in detector.info.labels else 0

    def flush() -> None:
        nonlocal hits
        if not buf:
            return
        p = detector.predict(buf)
        probs.extend(p)
        times.extend(buf_t)
        hits += int((1.0 - p[:, safe_idx] >= threshold).sum())
        buf.clear()
        buf_t.clear()

    for t, frame in iter_frames(info, fps, 0.0, None):
        if should_cancel is not None and should_cancel():
            raise InterruptedError("已取消")
        buf.append(frame)
        buf_t.append(t)
        if len(buf) >= batch:
            flush()
            if progress is not None:
                frac = len(times) / max(total, 1) * 0.95
                elapsed = time.perf_counter() - t0
                eta = elapsed / max(len(times), 1) * max(total - len(times), 0)
                progress(min(frac, 0.95),
                         f"检测中 {len(times)}/{total} 帧 · 命中 {hits} · "
                         f"已用 {elapsed:.0f}s · 剩余约 {eta:.0f}s")
    flush()

    times_arr = np.asarray(times, dtype=np.float64)
    probs_arr = np.vstack(probs) if probs else np.zeros((0, len(detector.info.labels)))

    segments = build_segments(
        times_arr, probs_arr, detector.info.labels,
        threshold=threshold, min_duration=min_duration, gap=gap,
        smooth_window=smooth, duration=info.duration,
        sample_step=1.0 / max(fps, 0.01), level=level,
    )

    nsfw_seconds = sum(s.duration for s in segments)
    payload = {
        "video": {
            "path": str(video.resolve()),
            "name": video.name,
            "duration": round(info.duration, 3),
            "fps": round(info.fps, 3),
            "resolution": [info.width, info.height],
            "sampled_frames": int(times_arr.size),
            "sample_fps": fps,
        },
        "model": detector.info.as_dict(),
        "settings": {
            "level": level,
            "level_name": "R18 + R15" if level == "all" else "仅 R18",
            "threshold": threshold,
            "min_duration": min_duration,
            "gap": gap,
            "smooth_window": smooth,
            "fit": detector.fit,
        },
        "summary": {
            "has_nsfw": bool(segments),
            "segment_count": len(segments),
            "nsfw_seconds": round(nsfw_seconds, 3),
            "nsfw_ratio": round(nsfw_seconds / info.duration, 4) if info.duration else 0.0,
            "max_score": round(max((s.peak for s in segments), default=0.0), 4),
            "elapsed_seconds": round(time.perf_counter() - t0, 2),
        },
        "segments": [
            {
                "index": i + 1,
                "start": s.start,
                "end": s.end,
                "start_hms": hms(s.start),
                "end_hms": hms(s.end),
                "duration": round(s.duration, 3),
                "label": s.label,
                "score": s.score,
                "peak": s.peak,
                "probs": s.probs,
                "samples": s.samples,
            }
            for i, s in enumerate(segments)
        ],
    }

    if progress is not None:
        progress(1.0, f"完成，命中 {len(segments)} 段")
    return payload
