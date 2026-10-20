"""CUDA / cuDNN 运行库路径准备。

onnxruntime-gpu 的 CUDA provider 依赖 CUDA 13 + cuDNN 9 的 DLL。
除了系统安装，也可以用 pip 的 nvidia-* 包（DLL 落在 site-packages/nvidia 下），
或者由「运行时管理」把 DLL 下载到 <程序目录>/runtime/cuda/bin（发布版用户自行下载）。
这里在 import onnxruntime 之前把它们的目录加进 DLL 搜索路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import app_dir, runtime_root, user_data_dir

_handles: list = []
_added: set[str] = set()


def _candidate_dirs() -> list[Path]:
    roots: list[Path] = []

    # 1) 当前解释器 site-packages 下的 nvidia/*
    try:
        import site

        for sp in site.getsitepackages():
            roots.append(Path(sp) / "nvidia")
    except Exception:  # 某些嵌入式环境没有 site
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages" / "nvidia")
    roots.append(Path(sys.prefix) / "lib" / "site-packages" / "nvidia")

    # 2) 打包后：exe 同级的 cuda 目录（可选，放 CUDA DLL 用）
    base = app_dir()
    if getattr(sys, "frozen", False):
        roots.extend([base / "cuda", base / "_internal" / "cuda"])

    # 3) 「运行时管理」下载的运行库（程序目录 / 用户目录）
    for rt in (runtime_root(create=False), user_data_dir() / "runtime"):
        roots.extend([rt / "cuda", rt / "cuda" / "nvidia"])

    dirs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        # DLL 直接放在 cuda/ 下也认
        if any(root.glob("*.dll")):
            dirs.append(root)
        try:
            for p in root.rglob("*"):
                if p.is_dir() and p.name in ("bin", "x86_64"):
                    dirs.append(p)
        except OSError:
            continue

    seen: set[str] = set()
    uniq: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def prepare_cuda_dlls() -> list[str]:
    """把 CUDA/cuDNN 的 DLL 目录加入搜索路径，返回添加过的目录。"""
    added: list[str] = []
    if os.name != "nt":
        return added
    for d in _candidate_dirs():
        key = str(d).lower()
        if key in _added:
            added.append(str(d))
            continue
        try:
            _handles.append(os.add_dll_directory(str(d)))  # 句柄要保住，否则目录会被移除
            _added.add(key)
            added.append(str(d))
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    return added


def refresh() -> list[str]:
    """新下载了运行库后调用：重新扫一遍目录（已注册的不会重复加）。"""
    return prepare_cuda_dlls()


def _search_dirs() -> list[str]:
    dirs = [str(d) for d in _candidate_dirs()]
    dirs.extend(p for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    return dirs


def find_dll(prefix: str) -> str | None:
    """在已注册的 CUDA 目录和 PATH 里找一个以 prefix 开头的 DLL。"""
    prefix = prefix.lower()
    for d in _search_dirs():
        try:
            for p in Path(d).glob("*.dll"):
                if p.name.lower().startswith(prefix):
                    return str(p)
        except OSError:
            continue
    return None


def cuda_runtime_ok() -> tuple[bool, str]:
    """粗查 CUDA 运行库是否就位。

    只是为了避免"缺 DLL 时 ORT 刷一屏英文报错"，不是严格校验：
    有 cublas 和 cuDNN 就认为可以试，真跑不起来 ORT 会自己回退 CPU。
    """
    if os.name != "nt":
        return True, ""
    missing = []
    if not find_dll("cublas"):
        missing.append("cuBLAS")
    if not find_dll("cudnn"):
        missing.append("cuDNN")
    if missing:
        return False, "缺少 " + " / ".join(missing) + "（CUDA 13 + cuDNN 9 运行库）"
    return True, ""
