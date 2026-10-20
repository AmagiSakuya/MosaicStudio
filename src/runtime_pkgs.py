"""推理运行库的获取：CUDA/cuDNN DLL（NVIDIA）与 onnxruntime 引擎安装。

发布版不带这些东西（体积太大），用户在「运行时管理」里按需下载：
  * NVIDIA → 从 PyPI 镜像下载 nvidia-* wheel，把 DLL 抽到 runtime/cuda/bin
  * AMD / Intel → 安装 onnxruntime-directml（DirectML 后端）
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import net
from .paths import is_frozen, runtime_root

# ---------------- PyPI 镜像 ----------------
PYPI_MIRRORS: list[tuple[str, str]] = [
    ("清华 TUNA（国内推荐）", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云", "https://mirrors.aliyun.com/pypi/simple"),
    ("中科大 USTC", "https://mirrors.ustc.edu.cn/pypi/simple"),
    ("PyPI 官方", "https://pypi.org/simple"),
]

# CUDA 大版本 -> 需要的 pip 包（Windows wheel 里就是 DLL）
# 注意：CUDA 13 的 cublas / runtime / nvrtc 包名不带 cu13 后缀（13.x 起就是这么发布的）
CUDA_PACKAGES: dict[int, list[str]] = {
    13: ["nvidia-cublas", "nvidia-cudnn-cu13",
         "nvidia-cuda-runtime", "nvidia-cuda-nvrtc"],
    12: ["nvidia-cublas-cu12", "nvidia-cudnn-cu12",
         "nvidia-cuda-runtime-cu12", "nvidia-cuda-nvrtc-cu12"],
}

_HREF_RE = re.compile(r'href="([^"#]+\.whl[^"]*)"')


class InstallError(Exception):
    """安装失败（带用户能看懂的原因）。"""


def cuda_dll_dir() -> Path:
    return runtime_root() / "cuda" / "bin"


# ---------------- wheel 解析 ----------------
def _version_key(version: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", version)
    return tuple(int(x) for x in nums[:4]) or (0,)


def _split_wheel_name(filename: str) -> tuple[str, str] | None:
    """nvidia_cublas_cu13-13.0.0.19-py3-none-win_amd64.whl -> (包名, 版本)"""
    stem = filename[:-4] if filename.lower().endswith(".whl") else filename
    parts = stem.split("-")
    if len(parts) < 5:
        return None
    return parts[0], parts[1]


def resolve_wheel(index: str, package: str, log: Optional[Callable[[str], None]] = None) -> tuple[str, str, int]:
    """在镜像的 simple 索引里挑出该包最新的 Windows wheel。"""
    page = f"{index.rstrip('/')}/{package}/"
    html = net.fetch_text(page)
    want = package.replace("-", "_").lower()
    best: tuple[tuple[int, ...], str, str] | None = None
    for href in _HREF_RE.findall(html):
        name = urllib.parse.urlparse(href).path.rsplit("/", 1)[-1]
        if "win_amd64" not in name.lower():
            continue
        split = _split_wheel_name(name)
        if split is None or split[0].lower() != want:
            continue
        ver = split[1]
        if any(tag in ver.lower() for tag in ("rc", "dev", "a0", "b0")):
            continue
        key = _version_key(ver)
        if best is None or key > best[0]:
            best = (key, urllib.parse.urljoin(page, href), name)
    if best is None:
        raise InstallError(f"镜像 {index} 上没找到 {package} 的 Windows 安装包")
    size = net.content_length(best[1])
    if log:
        log(f"  {package} {best[2].split('-')[1]}  ({net.human_size(size) if size else '大小未知'})")
    return best[2], best[1], size


# ---------------- 解压 ----------------
def _extract_dlls(wheel: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if not info.filename.lower().endswith(".dll"):
                continue
            target = dest / Path(info.filename).name
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            count += 1
    return count


# ---------------- CUDA 运行库 ----------------
def download_cuda_runtime(
    major: int,
    index: str,
    progress: Optional[Callable[[float, str], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel=None,
) -> Path:
    """下载 CUDA/cuDNN 运行库并解压 DLL 到 runtime/cuda/bin。"""
    packages = CUDA_PACKAGES.get(int(major))
    if not packages:
        raise InstallError(f"不支持的 CUDA 版本：{major}")

    dest = cuda_dll_dir()
    if log:
        log(f"CUDA {major} 运行库 → {dest}")

    wheels: list[tuple[str, str, int]] = []
    for pkg in packages:
        wheels.append(resolve_wheel(index, pkg, log))

    total = sum(w[2] for w in wheels)
    done_bytes = 0
    dll_count = 0

    with tempfile.TemporaryDirectory(prefix="mosaic-cuda-") as td:
        for name, url, size in wheels:
            if log:
                log(f"下载 {name}")
            base = done_bytes

            def cb(done: int, tot: int, speed: float, _b=base) -> None:
                if not progress:
                    return
                overall = (_b + done) / total if total else 0.0
                text = (f"{net.human_size(_b + done)} / {net.human_size(total or _b + done)}"
                        f"{'  ' + net.human_speed(speed) if speed else ''}")
                progress(min(overall, 0.995), text)

            wheel = net.download(url, Path(td) / name, progress=cb, cancel=cancel, timeout=120)
            n = _extract_dlls(wheel, dest)
            dll_count += n
            if log:
                log(f"  解压 {n} 个 DLL")
            done_bytes += size or wheel.stat().st_size

    if progress:
        progress(1.0, f"完成：{dll_count} 个 DLL → {dest}")
    if log:
        log(f"CUDA 运行库就位（{dll_count} 个 DLL）。重启程序后生效。")
    return dest


# ---------------- onnxruntime 引擎 ----------------
ENGINE_PACKAGES = {
    "cuda": ["onnxruntime-gpu"],
    "dml": ["onnxruntime-directml"],
    "cpu": ["onnxruntime"],
}
ENGINE_CONFLICT = {"cuda": ["onnxruntime", "onnxruntime-directml"],
                   "dml": ["onnxruntime", "onnxruntime-gpu"],
                   "cpu": ["onnxruntime-gpu", "onnxruntime-directml"]}


def engine_installable() -> tuple[bool, str]:
    if is_frozen():
        return False, ("打包版不能在线替换推理引擎（DLL 已被占用）。"
                       "请使用带 GPU 的安装包，或改用源码方式运行。")
    return True, ""


def _pip(args: list[str], log: Optional[Callable[[str], None]], cancel=None) -> int:
    cmd = [sys.executable, "-m", "pip"] + args
    creation = 0x08000000 if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        errors="ignore", creationflags=creation,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            raise net.DownloadCancelled
        line = line.strip()
        if line and log:
            log(line[:160])
    return proc.wait()


def install_engine(
    kind: str,
    index: str,
    progress: Optional[Callable[[float, str], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel=None,
) -> None:
    ok, why = engine_installable()
    if not ok:
        raise InstallError(why)
    pkgs = ENGINE_PACKAGES.get(kind)
    if not pkgs:
        raise InstallError(f"未知引擎类型：{kind}")

    if log:
        log("先卸载冲突的引擎包（CPU / GPU / DirectML 三者不能共存）…")
    for pkg in ENGINE_CONFLICT.get(kind, []):
        _pip(["uninstall", "-y", pkg], log, cancel)

    if log:
        log(f"安装 {', '.join(pkgs)}（镜像：{index}）…")
    if progress:
        progress(0.15, "正在安装推理引擎…")
    code = _pip(["install", "--upgrade", "--no-warn-script-location",
                 "-i", index] + pkgs, log, cancel)
    if code != 0:
        raise InstallError(f"pip 安装失败（退出码 {code}），请检查网络或换镜像重试")
    if progress:
        progress(1.0, "安装完成，重启程序后生效")


def cuda_runtime_size_hint(major: int) -> str:
    return "约 0.9 GB" if int(major) == 13 else "约 1.3 GB"
