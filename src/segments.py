"""把逐帧概率整理成连续的时间段。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Segment:
    start: float
    end: float
    label: str
    score: float
    peak: float
    probs: dict[str, float]
    samples: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def majority_vote(hits: np.ndarray, window: int) -> np.ndarray:
    """邻域多数投票：窗口内过半帧命中，才认为该帧命中。

    比直接平滑分数更稳：孤立的单帧误判会被投票否决，连续几帧命中则保留。
    """
    n = hits.size
    if n == 0 or window <= 1:
        return hits.copy()
    window = window if window % 2 == 1 else window + 1
    radius = window // 2
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n, i + radius + 1)
        out[i] = int(hits[lo:hi].sum()) * 2 >= (hi - lo)
    return out


def build_segments(
    times: np.ndarray,
    probs: np.ndarray,
    labels: list[str],
    threshold: float = 0.5,
    min_duration: float = 0.5,
    gap: float = 0.75,
    smooth_window: int = 5,
    duration: float | None = None,
    sample_step: float = 0.5,
    level: str = "all",
) -> list[Segment]:
    """按 NSFW 分数阈值切分片段，并做合并/过滤。

    level="all"  ：分数 = P(r15) + P(r18) = 1 - P(safe)，擦边画面也算
    level="r18"  ：分数 = P(r18)，只看露骨内容，误报更少
    smooth_window：多数投票窗口（帧数），用于压制孤立误判
    """
    if times.size == 0 or probs.size == 0:
        return []

    if level == "r18" and "r18" in labels:
        risk_idx = [labels.index("r18")]
    else:
        risk_idx = [i for i, name in enumerate(labels) if name != "safe"]
    if not risk_idx:
        return []

    raw_scores = probs[:, risk_idx].sum(axis=1)
    hits = raw_scores >= threshold
    # 先按阈值逐帧判定，再用邻域投票否决孤立单帧（转场、闪白之类的误判）
    mask = hits & majority_vote(hits, smooth_window)
    if not mask.any():
        return []

    # 找出连续命中区间
    runs: list[list[int]] = []
    start_idx: int | None = None
    for i, hit in enumerate(mask):
        if hit and start_idx is None:
            start_idx = i
        elif not hit and start_idx is not None:
            runs.append([start_idx, i - 1])
            start_idx = None
    if start_idx is not None:
        runs.append([start_idx, len(mask) - 1])

    # 间隔太短的区间先合并，避免一段画面被切成碎片
    merged: list[list[int]] = []
    for run in runs:
        if merged and times[run[0]] - times[merged[-1][1]] <= gap:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    segments: list[Segment] = []
    for first, last in merged:
        seg_start = float(times[first])
        seg_end = float(times[last] + sample_step)
        if duration and duration > 0:
            seg_end = min(seg_end, duration)
        seg_dur = seg_end - seg_start
        if seg_dur + 1e-9 < min_duration:
            continue

        seg_probs = probs[first:last + 1].mean(axis=0)
        seg_scores = raw_scores[first:last + 1]
        label = labels[max(risk_idx, key=lambda i: seg_probs[i])]

        segments.append(
            Segment(
                start=round(seg_start, 3),
                end=round(seg_end, 3),
                label=label,
                score=round(float(seg_scores.mean()), 4),
                peak=round(float(seg_scores.max()), 4),
                probs={name: round(float(seg_probs[i]), 4) for i, name in enumerate(labels)},
                samples=int(last - first + 1),
            )
        )
    return segments
