"""检测模型的清单、状态检查、下载与删除。

发布版不预置模型，用户第一次使用时在「运行时管理」里按需下载：
  <model 目录>/anime_rating/<子目录>/{model.onnx, meta.json}
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import net
from .paths import model_root

REPO = "deepghs/anime_rating"
FILES = ("model.onnx", "meta.json")
MIN_ONNX_BYTES = 1 << 20          # 小于 1MB 认为没下完（nano 也有 17MB）

# 镜像站（顺序即界面里的顺序，第一个为默认）
MIRRORS: list[tuple[str, str]] = [
    ("HF 镜像 · hf-mirror（国内推荐）", "https://hf-mirror.com"),
    ("HuggingFace 官方", "https://huggingface.co"),
    ("自定义…", ""),
]


@dataclass(frozen=True)
class ModelSpec:
    key: str
    subdir: str
    title: str
    tagline: str
    size_mb: int
    accuracy: str
    speed: str

    @property
    def repo_path(self) -> str:
        return f"{REPO}/resolve/main/{self.subdir}"


MODELS: dict[str, ModelSpec] = {
    "ultra": ModelSpec(
        "ultra", "caformer_b36", "Ultra · caformer_b36",
        "最准，默认档位；24 分钟番剧 GPU 约 2 分钟",
        366, "88.5%", "~47 帧/秒"),
    "pro": ModelSpec(
        "pro", "caformer_s36_plus", "Pro · caformer_s36_plus",
        "速度约为 Ultra 的两倍，准确率略低",
        150, "74.3%", "~80 帧/秒"),
    "nano": ModelSpec(
        "nano", "mobilenetv3_sce", "Nano · mobilenetv3_sce",
        "极快但漏检多，只适合粗筛",
        17, "66.3%", "~87 帧/秒（CPU）"),
}

DEFAULT_VARIANT = "ultra"


def spec(key: str) -> ModelSpec:
    return MODELS.get(key) or MODELS[DEFAULT_VARIANT]


@dataclass
class ModelState:
    key: str
    installed: bool
    partial: bool
    bytes: int
    missing: tuple[str, ...]
    path: Path

    @property
    def size_text(self) -> str:
        return net.human_size(self.bytes) if self.bytes else "—"


def spec_dir(spec_: ModelSpec, root: Optional[Path] = None) -> Path:
    return Path(root or model_root()) / "anime_rating" / spec_.subdir


def state(key: str, root: Optional[Path] = None) -> ModelState:
    s = spec(key)
    d = spec_dir(s, root)
    total = 0
    missing: list[str] = []
    for name in FILES:
        p = d / name
        if p.is_file():
            size = p.stat().st_size
            if name == "model.onnx" and size < MIN_ONNX_BYTES:
                missing.append(name)
                total += size
            else:
                total += size
        else:
            missing.append(name)
    installed = not missing
    partial = bool(total) and not installed
    return ModelState(key=key, installed=installed, partial=partial,
                      bytes=total, missing=tuple(missing), path=d)


def installed_keys(root: Optional[Path] = None) -> list[str]:
    return [k for k in MODELS if state(k, root).installed]


def used_bytes(root: Optional[Path] = None) -> int:
    return sum(state(k, root).bytes for k in MODELS)


def download(
    key: str,
    mirror: str,
    progress: Optional[Callable[[float, str], None]] = None,
    cancel=None,
    log: Optional[Callable[[str], None]] = None,
    root: Optional[Path] = None,
) -> Path:
    """下载某个档位（已存在的文件会跳过，方便断点重来）。"""
    s = spec(key)
    dest_dir = spec_dir(s, root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    mirrors = [mirror] if mirror else [m for _n, m in MIRRORS if m]

    # 已知大小用来算总进度（meta.json 很小，给个估值）
    sizes = {"model.onnx": s.size_mb * 1024 * 1024, "meta.json": 2 * 1024}
    total = sum(sizes.values())
    base = 0.0

    if log:
        log(f"下载模型 {s.title}")
    for name in FILES:
        target = dest_dir / name
        if target.is_file() and (name != "model.onnx" or target.stat().st_size >= MIN_ONNX_BYTES):
            base += sizes[name]
            if log:
                log(f"  ✓ {name} 已存在，跳过")
            continue
        if progress:
            progress(base / total, f"{s.title} · {name}")

        def cb(done: int, tot: int, speed: float, _b=base, _n=name) -> None:
            if progress:
                frac = (_b + min(done, sizes[_n])) / total
                text = (f"{s.title} · {_n}  {net.human_size(done)}"
                        f"{' / ' + net.human_size(tot) if tot else ''}"
                        f"{'  ' + net.human_speed(speed) if speed else ''}")
                progress(min(frac, 0.999), text)

        net.download_with_mirrors(
            mirrors, f"{s.repo_path}/{name}", target,
            progress=cb, cancel=cancel, log=log)
        base += sizes[name]
        if log:
            log(f"  ✓ {name} 完成")

    if progress:
        progress(1.0, f"{s.title} 下载完成")
    return dest_dir


def remove(key: str, root: Optional[Path] = None) -> None:
    d = spec_dir(spec(key), root)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def verify(key: str, root: Optional[Path] = None) -> tuple[bool, str]:
    """粗校验：文件在、大小合理、meta.json 能解析。"""
    import json

    s = spec(key)
    st = state(key, root)
    if not st.installed:
        return False, "缺少 " + "、".join(st.missing)
    meta = st.path / "meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, "meta.json 格式异常"
    except (OSError, ValueError) as exc:
        return False, f"meta.json 读取失败：{exc}"
    if st.bytes < MIN_ONNX_BYTES:
        return False, "模型文件过小，可能没下载完整"
    return True, f"{s.title} · {net.human_size(st.bytes)}"
