"""路径解析：兼容源码运行与 PyInstaller 打包后的 exe 运行。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录（exe 所在目录 / 项目根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """程序目录不可写时的兜底位置（%LOCALAPPDATA%\\MosaicStudio）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "MosaicStudio"
    return Path.home() / ".mosaic-studio"


def _writable(directory: Path) -> bool:
    """试着在目录里建个临时文件，判断能不能写（Program Files 下常常不行）。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_bytes(b"0")
        probe.unlink()
        return True
    except OSError:
        return False


def _model_candidates() -> list[Path]:
    base = app_dir()
    return [
        base / "model",                             # exe 同级 / 项目根目录
        base.parent / "model",                      # exe 在 dist/ 下时，模型在项目根目录
        Path.cwd() / "model",                       # 当前工作目录
        base / "_internal" / "model",               # onedir 打包布局
        user_data_dir() / "model",                  # 运行时管理下载的兜底位置
    ]


def find_model_dir(explicit: str | os.PathLike | None = None) -> Path:
    """按优先级定位 model 目录，方便 exe 放在 dist/ 或项目根目录下都能找到模型。"""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"指定的模型目录不存在：{p}")
        return p

    env = os.environ.get("NSFW_MODEL_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()

    for c in _model_candidates():
        if (c / "anime_rating").is_dir():
            return c.resolve()

    raise FileNotFoundError(
        "还没有下载检测模型。\n"
        "请在「模型配置」右上角的设置按钮里打开「运行时管理」，下载需要的模型。"
    )


def model_root(create: bool = True) -> Path:
    """模型下载的落地目录：优先程序目录，不可写时退回用户目录。"""
    try:
        return find_model_dir()
    except FileNotFoundError:
        pass
    for cand in _model_candidates()[:1] + [user_data_dir() / "model"]:
        if not create:
            return cand
        if _writable(cand):
            return cand
    return user_data_dir() / "model"


def runtime_root(create: bool = True) -> Path:
    """GPU 运行库（CUDA / cuDNN 的 DLL）存放目录。"""
    for cand in (app_dir() / "runtime", user_data_dir() / "runtime"):
        if not create:
            return cand
        if _writable(cand):
            return cand
    return user_data_dir() / "runtime"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
