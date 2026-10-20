"""下载工具：断点续传、进度回调、可取消、镜像重试（只用标准库）。

设计目标：模型 / CUDA 运行库动辄几百 MB，用户网络差、中途断线是常态，
所以必须支持 Range 续传（.part 临时文件）、取消、以及失败后换镜像重试。
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

UA = {"User-Agent": "MosaicStudio/1.0 (Windows)"}
CHUNK = 1 << 20
Progress = Callable[[int, int, float], None]   # (已下载, 总大小, 速度 B/s)


class DownloadCancelled(Exception):
    """用户取消下载。"""


class DownloadError(Exception):
    """网络错误（已重试仍失败）。"""


# ---------------- 格式化 ----------------
def human_size(n: float) -> str:
    n = float(n or 0)
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}TB"


def human_speed(bps: float) -> str:
    return f"{human_size(bps)}/s"


def url_basename(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


# ---------------- HTTP ----------------
def _request(url: str, headers: Optional[dict] = None) -> urllib.request.Request:
    h = dict(UA)
    if headers:
        h.update(headers)
    return urllib.request.Request(url, headers=h)


def fetch_text(url: str, timeout: float = 25, retries: int = 2) -> str:
    """取一段文本（用于 PyPI 索引页）。失败会重试，最终抛 DownloadError。"""
    last = ""
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = str(getattr(exc, "reason", exc))
            if i < retries:
                time.sleep(0.8 * (i + 1))
    raise DownloadError(f"无法访问 {url}（{last}）")


def content_length(url: str, timeout: float = 20) -> int:
    """HEAD 取文件大小，失败返回 0（只用于显示进度，不致命）。"""
    try:
        with urllib.request.urlopen(_request(url, {"Range": "bytes=0-0"}), timeout=timeout) as resp:
            rng = resp.headers.get("Content-Range") or ""
            m = re.search(r"/(\d+)", rng)
            if m:
                return int(m.group(1))
            return int(resp.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return 0


def download(
    url: str,
    dest: Path,
    progress: Optional[Progress] = None,
    cancel=None,
    timeout: float = 60,
    retries: int = 2,
) -> Path:
    """下载 url 到 dest（支持续传）。

    dest 旁边的 `dest.part` 是未完成的临时文件；成功后才原子改名，
    所以中途失败不会留下一个看起来"完整"的坏文件。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempt = 0
    last_err = ""

    while True:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with urllib.request.urlopen(_request(url, headers), timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if have and status != 206:      # 服务器不支持续传，从头来
                    have = 0
                total = int(resp.headers.get("Content-Length") or 0) + have
                mode = "ab" if have and status == 206 else "wb"
                done = have
                last_t, last_d, speed = time.time(), done, 0.0
                with part.open(mode) as fp:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fp.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last_t >= 0.35:
                            speed = (done - last_d) / (now - last_t)
                            last_t, last_d = now, done
                            if progress:
                                progress(done, total or done, speed)
                if progress:
                    progress(done, total or done, 0.0)
            os.replace(part, dest)
            return dest
        except DownloadCancelled:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            last_err = str(getattr(exc, "reason", exc))
            attempt += 1
            if attempt > retries:
                raise DownloadError(f"{url_basename(url)} 下载失败：{last_err}") from exc
            time.sleep(min(3.0, 0.8 * attempt))


def download_with_mirrors(
    mirrors: list[str],
    relative_path: str,
    dest: Path,
    progress: Optional[Progress] = None,
    cancel=None,
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 60,
) -> Path:
    """依次尝试多个镜像（同一个相对路径），全部失败才报错。"""
    errors: list[str] = []
    for i, base in enumerate(mirrors):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled
        url = f"{base.rstrip('/')}/{relative_path.lstrip('/')}"
        if log:
            log(f"  ← {base.split('//')[-1]}")
        try:
            return download(url, dest, progress=progress, cancel=cancel, timeout=timeout)
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            errors.append(str(exc))
            if i < len(mirrors) - 1 and log:
                log("  该镜像不可用，换下一个…")
    raise DownloadError("；".join(errors) or "下载失败")
