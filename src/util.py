"""通用小工具：时间格式化。"""

from __future__ import annotations


def hms(seconds: float) -> str:
    """00:45.000 / 1:02:03.500"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


def file_ts(seconds: float) -> str:
    """文件名里用的时间戳：00-19-500。"""
    return hms(seconds).replace(":", "-").replace(".", "-")
