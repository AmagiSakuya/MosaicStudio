"""运行环境探测：显卡型号 / 厂商 / 驱动、onnxruntime 后端、CUDA 运行库状态。

全部只读探测，不安装任何东西；安装动作在 runtime_pkgs.py。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import gpu as gpu_mod
from .paths import is_frozen, runtime_root

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

VENDOR_LABEL = {
    "NVIDIA": "NVIDIA",
    "AMD": "AMD",
    "Intel": "Intel",
    "虚拟": "虚拟显卡",
    "其它": "其它",
}


def _run(cmd: list[str], timeout: float = 10.0) -> str:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
        return (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class GpuInfo:
    name: str
    vendor: str = "其它"
    driver: str = ""
    vram_mb: int = 0
    cuda_support: str = ""       # 驱动支持的最高 CUDA 版本（仅 NVIDIA）
    source: str = "wmi"

    @property
    def is_discrete(self) -> bool:
        return self.vendor in ("NVIDIA", "AMD") and "基本" not in self.name

    @property
    def vram_text(self) -> str:
        if self.vram_mb <= 0:
            return "—"
        gb = self.vram_mb / 1024.0
        return f"{gb:.1f} GB" if gb >= 1 else f"{self.vram_mb} MB"


def classify_vendor(name: str, compat: str = "") -> str:
    s = f"{name} {compat}".lower()
    if any(k in s for k in ("nvidia", "geforce", "quadro", "rtx", "gtx", "tesla")):
        return "NVIDIA"
    if any(k in s for k in ("amd", "radeon", "advanced micro", "ati ", "firepro")):
        return "AMD"
    if any(k in s for k in ("intel", "arc ", "iris", "uhd graphics", "hd graphics")):
        return "Intel"
    if any(k in s for k in ("microsoft", "remote", "parsec", "vmware", "virtual", "basic display")):
        return "虚拟"
    return "其它"


def _gpus_from_wmi() -> list[GpuInfo]:
    if os.name != "nt":
        return []
    script = ("Get-CimInstance Win32_VideoController | "
              "Select-Object Name,DriverVersion,AdapterRAM,AdapterCompatibility | "
              "ConvertTo-Json -Compress")
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 15)
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    gpus: list[GpuInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        compat = str(item.get("AdapterCompatibility") or "")
        try:
            ram = int(item.get("AdapterRAM") or 0)
        except (TypeError, ValueError):
            ram = 0
        # WMI 的 AdapterRAM 是 32 位，>4GB 会溢出成负数 / 0xFFFFFFFF，不可信
        vram = 0 if ram <= 0 or ram >= 0xFFFFF000 else int(ram / (1024 * 1024))
        gpus.append(GpuInfo(name=name, vendor=classify_vendor(name, compat),
                            driver=str(item.get("DriverVersion") or ""), vram_mb=vram))
    return gpus


def nvidia_info() -> dict:
    """nvidia-smi：驱动版本、驱动支持的 CUDA 版本、每卡显存。"""
    info: dict = {"available": False, "cuda": "", "driver": "", "gpus": []}
    raw = _run(["nvidia-smi"], 8)
    if not raw:
        return info
    info["available"] = True
    # 新版驱动写 "CUDA UMD Version: 13.4"，老版写 "CUDA Version: 12.4"
    m = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([\d.]+)", raw)
    if m:
        info["cuda"] = m.group(1)
    if not info["cuda"]:
        m = re.search(r"CUDA\s+([\d.]+)", raw)
        if m:
            info["cuda"] = m.group(1)
    rows = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                 "--format=csv,noheader,nounits"], 8)
    for line in rows.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            mem = int(float(parts[2]))
        except ValueError:
            mem = 0
        info["gpus"].append({"name": parts[0], "driver": parts[1], "vram_mb": mem})
    if info["gpus"]:
        info["driver"] = info["gpus"][0]["driver"]
    return info


def detect_gpus() -> list[GpuInfo]:
    """WMI 列表 + nvidia-smi 补充显存/CUDA 版本（nvidia-smi 的数字更准）。"""
    gpus = _gpus_from_wmi()
    nv = nvidia_info()
    if not nv["available"] and not gpus:
        return []
    if nv["available"]:
        smi = nv["gpus"]
        for i, g in enumerate(gpus):
            if g.vendor != "NVIDIA":
                continue
            match = smi[i] if i < len(smi) else None
            if match is None:
                match = next((s for s in smi if s["name"].split()[-1] in g.name), None)
            if match:
                g.vram_mb = match.get("vram_mb") or g.vram_mb
                g.driver = match.get("driver") or g.driver
                g.source = "nvidia-smi"
            g.cuda_support = nv["cuda"]
        if not gpus:      # WMI 拿不到时用 nvidia-smi 兜底
            for s in smi:
                gpus.append(GpuInfo(name=s["name"], vendor="NVIDIA", driver=s["driver"],
                                    vram_mb=s.get("vram_mb") or 0,
                                    cuda_support=nv["cuda"], source="nvidia-smi"))
    return gpus


def primary_vendor(gpus: list[GpuInfo]) -> str:
    for g in gpus:
        if g.vendor in ("NVIDIA", "AMD", "Intel") and g.is_discrete:
            return g.vendor
    for g in gpus:
        if g.vendor in ("NVIDIA", "AMD", "Intel"):
            return g.vendor
    return "无"


# ---------------- onnxruntime ----------------
def onnxruntime_info() -> dict:
    info = {
        "version": "", "providers": [], "cuda": False, "dml": False,
        "tensorrt": False, "error": "", "build": "未知",
    }
    try:
        import onnxruntime as ort
    except Exception as exc:      # noqa: BLE001 - 缺包 / DLL 加载失败都要兜住
        info["error"] = str(exc)
        return info
    info["version"] = getattr(ort, "__version__", "")
    try:
        info["providers"] = list(ort.get_available_providers())
    except Exception as exc:      # noqa: BLE001
        info["error"] = str(exc)
    info["cuda"] = "CUDAExecutionProvider" in info["providers"]
    info["dml"] = "DmlExecutionProvider" in info["providers"]
    info["tensorrt"] = "TensorrtExecutionProvider" in info["providers"]
    if info["cuda"]:
        info["build"] = "GPU 版（CUDA）"
    elif info["dml"]:
        info["build"] = "GPU 版（DirectML）"
    elif info["providers"]:
        info["build"] = "CPU 版"
    return info


def cuda_dll_status() -> dict:
    """CUDA / cuDNN 运行库是否就位（DLL 级别粗查）。"""
    ok, reason = gpu_mod.cuda_runtime_ok()
    libs = {
        "cuBLAS": gpu_mod.find_dll("cublas"),
        "cuDNN": gpu_mod.find_dll("cudnn"),
        "CUDA Runtime": gpu_mod.find_dll("cudart"),
    }
    return {"ok": ok, "reason": reason, "libs": libs}


def runtime_dirs() -> list[str]:
    dirs = [str(d) for d in gpu_mod._candidate_dirs()]      # noqa: SLF001 - 内部工具
    return dirs


def install_dir() -> Path:
    return runtime_root(create=False) / "cuda" / "bin"


def summary() -> dict:
    """一次性把界面需要的信息都取回来（在后台线程里调用）。"""
    gpus = detect_gpus()
    nv = nvidia_info()
    return {
        "gpus": gpus,
        "vendor": primary_vendor(gpus),
        "nvidia": nv,
        "ort": onnxruntime_info(),
        "cuda": cuda_dll_status(),
        "frozen": is_frozen(),
        "dll_dir": install_dir(),
        "dll_dirs": runtime_dirs(),
    }


def suggestion(info: dict) -> list[str]:
    """按当前环境给出建议文案（纯文本，界面里逐条显示）。"""
    vendor = info.get("vendor") or "无"
    ort = info.get("ort") or {}
    cuda = info.get("cuda") or {}
    tips: list[str] = []

    if vendor == "NVIDIA":
        tips.append("检测到 NVIDIA 显卡：推理走 CUDA，速度约是 CPU 的 15 倍。")
        nv = info.get("nvidia") or {}
        cuda_ver = nv.get("cuda") or ""
        if cuda_ver:
            major = cuda_ver.split(".")[0]
            tips.append(f"显卡驱动最高支持 CUDA {cuda_ver}，"
                        f"建议下载 CUDA {major} 对应的运行库（cuBLAS + cuDNN + Runtime）。")
        if not ort.get("cuda"):
            tips.append("当前推理引擎是 CPU 版，装了运行库也不会走显卡；"
                        "需要 GPU 版 onnxruntime（源码运行可在此一键安装）。")
        if not cuda.get("ok"):
            tips.append("CUDA 运行库不完整，点下方「下载 / 修复运行库」即可补齐，无需安装完整 CUDA Toolkit。")
        else:
            tips.append("CUDA 运行库已就位，检测时「设备」保持「自动」就会用显卡。")
    elif vendor == "AMD":
        tips.append("检测到 AMD 显卡：Windows 上 onnxruntime 不支持 ROCm，"
                    "请使用 DirectML 后端（onnxruntime-directml），任何 DX12 显卡都能跑。")
        if not ort.get("dml"):
            tips.append("当前引擎没有 DirectML 支持，可在下方安装 DirectML 版引擎。")
        else:
            tips.append("DirectML 已可用，检测时把「设备」选成「GPU · DirectML」。")
        tips.append("AMD 显卡驱动请从官网更新到最新版 Adrenalin（DirectML 依赖 DX12 驱动）。")
    elif vendor == "Intel":
        tips.append("检测到 Intel 显卡：建议使用 DirectML 后端（onnxruntime-directml），"
                    "核显也能加速，但提升有限。")
        if not ort.get("dml"):
            tips.append("当前引擎没有 DirectML 支持，可在下方安装 DirectML 版引擎。")
    else:
        tips.append("没有检测到可加速的独立显卡，将使用 CPU 推理（慢但兼容性最好）。")
        tips.append("CPU 推理建议把「采样」降到 2、模型选 Pro，能明显缩短耗时。")

    if ort.get("error"):
        tips.append(f"推理引擎加载异常：{ort['error']}")
    return tips
