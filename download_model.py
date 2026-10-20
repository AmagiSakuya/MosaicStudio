"""模型下载脚本（只用标准库，装依赖之前也能跑）。

用法：
    python download_model.py            # 下载默认的 ultra + pro
    python download_model.py --only nano
    python download_model.py --mirror https://hf-mirror.com
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "deepghs/anime_rating"
DEFAULT_MIRROR = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

# 档位 -> 仓库内子目录
VARIANTS = {
    "nano": "mobilenetv3_sce",      # 约 17MB，极快，但擦边内容容易漏
    "pro": "caformer_s36_plus",     # 约 150MB，准确率 74.3%
    "ultra": "caformer_b36",        # 约 366MB，准确率 88.5%，最强（默认）
}
FILES = ("model.onnx", "meta.json")
UA = {"User-Agent": "video-nsfw-scanner/1.0"}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as fp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fp.write(chunk)
            done += len(chunk)
            if total:
                pct = done / total * 100
                sys.stdout.write(f"\r    {pct:5.1f}%  {human(done)} / {human(total)}   ")
            else:
                sys.stdout.write(f"\r    {human(done)}   ")
            sys.stdout.flush()
    sys.stdout.write("\r" + " " * 50 + "\r")
    tmp.replace(dest)


def download_variant(variant: str, mirror: str, root: Path, force: bool) -> bool:
    sub = VARIANTS[variant]
    out_dir = root / "anime_rating" / sub
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in FILES:
        dest = out_dir / name
        if dest.is_file() and dest.stat().st_size > 1024 and not force:
            print(f"  ✓ {variant}/{name} 已存在（{human(dest.stat().st_size)}），跳过")
            continue
        url = f"{mirror.rstrip('/')}/{REPO}/resolve/main/{sub}/{name}"
        print(f"  ↓ {variant}/{name}")
        try:
            fetch(url, dest)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"  ✗ 下载失败：{exc}", file=sys.stderr)
            print(f"    可手动下载：{url}", file=sys.stderr)
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载动漫 NSFW 检测模型")
    parser.add_argument("--only", choices=list(VARIANTS), default=None,
                        help="只下载某一档（默认两档都下）")
    parser.add_argument("--mirror", default=DEFAULT_MIRROR, help="模型镜像站地址")
    parser.add_argument("--force", action="store_true", help="强制重新下载")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent / "model"
    root.mkdir(parents=True, exist_ok=True)
    targets = [args.only] if args.only else ["ultra", "pro"]  # nano 体积小，需要时 --only nano

    print(f"模型仓库：{REPO}   镜像：{args.mirror}")
    print(f"保存到：{root}\n")
    results = [download_variant(v, args.mirror, root, args.force) for v in targets]
    if all(results):
        print("\n全部完成，可以启动编辑器了：run_gui.bat")
        return 0
    print("\n部分文件下载失败，请检查网络或换镜像重试。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
