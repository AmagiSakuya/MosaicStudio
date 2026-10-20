"""下载并内置 ffmpeg 到 bin/ffmpeg.exe（带 libx264 / libx265 / NVENC）。

用法：
    python download_ffmpeg.py            # 已有可用版本就跳过
    python download_ffmpeg.py --force    # 强制重新下载
    python download_ffmpeg.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple

优先走 pip 的 imageio-ffmpeg（快、镜像多），失败再直接下 gyan.dev 的静态构建。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "bin" / "ffmpeg.exe"
NEEDED = ("libx264", "h264_nvenc")


def _run(cmd: list[str]) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (res.stdout or "") + (res.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def check(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "文件不存在"
    text = _run([str(path), "-version"])
    if not text:
        return False, "无法执行"
    first = text.splitlines()[0]
    enc = _run([str(path), "-hide_banner", "-encoders"])
    missing = [k for k in NEEDED if k not in enc]
    if missing:
        return False, f"缺少编码器 {', '.join(missing)}"
    return True, first.strip()


def install_from_pip(mirror: str | None) -> Path | None:
    cmd = [sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"]
    if mirror:
        cmd += ["-i", mirror]
    print("· 正在通过 pip 获取 imageio-ffmpeg（内置 ffmpeg 二进制）…")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.SubprocessError as exc:
        print(f"  pip 安装失败：{exc}")
        return None
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # noqa: BLE001
        print(f"  无法定位 imageio-ffmpeg 的二进制：{exc}")
        return None


def install_from_url(url: str) -> Path | None:
    print(f"· 正在下载 {url}")
    tmp = Path(tempfile.mkdtemp(prefix="ffmpeg_"))
    zip_path = tmp / "ffmpeg.zip"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(zip_path, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        print()
    except Exception as exc:  # noqa: BLE001
        print(f"  下载失败：{exc}")
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name.lower().endswith("bin/ffmpeg.exe"):
                    out = tmp / "ffmpeg.exe"
                    with zf.open(name) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return out
    except (zipfile.BadZipFile, OSError) as exc:
        print(f"  解压失败：{exc}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="下载并内置 ffmpeg")
    ap.add_argument("--force", action="store_true", help="已有也重新下载")
    ap.add_argument("--mirror", default="https://pypi.tuna.tsinghua.edu.cn/simple",
                    help="pip 镜像地址")
    args = ap.parse_args()

    if not args.force:
        ok, info = check(TARGET)
        if ok:
            print(f"✓ 已有可用的 ffmpeg：{TARGET}")
            print(f"  {info}")
            return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    src = install_from_pip(args.mirror)
    if src is None:
        src = install_from_url("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip")
    if src is None:
        print("✗ 下载失败。可以手动下载 ffmpeg 后放到：")
        print(f"  {TARGET}")
        return 1

    shutil.copyfile(src, TARGET)
    ok, info = check(TARGET)
    if not ok:
        print(f"✗ 复制后的文件不可用：{info}")
        return 1
    print(f"✓ 完成：{TARGET}")
    print(f"  {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
