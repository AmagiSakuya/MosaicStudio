"""工程数据模型：工程 / 素材 / 模糊片段 / 图片片段，以及工程文件读写。

设计要点（v2）：
- 工程（Project）只记录**素材引用**，素材可以是任意位置的视频或图片，不再限定 input 目录；
- 每个视频素材自己带时间线数据（模糊片段 + 图片叠加片段）和最近一次检测记录；
- 检测结果（含模型、参数、摘要）直接存在工程里，不再落 output/*.json 和预览图。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_EXT = ".vproj"
LEGACY_SUFFIX = ".vproj.json"
PROJECT_FORMAT = "mosaic-project"
PROJECT_VERSION = 2

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm",
    ".wmv", ".ts", ".m4v", ".mpg", ".mpeg", ".rmvb",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def media_kind(path: str | Path) -> str:
    """返回 'video' / 'image' / ''（不支持的扩展名）。"""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return ""


def is_media(path: str | Path) -> bool:
    return bool(media_kind(path))


def probe_media(path: str | Path) -> dict[str, Any]:
    """探测素材信息：视频读时长/帧率/分辨率，图片读分辨率。"""
    p = Path(path)
    kind = media_kind(p)
    info: dict[str, Any] = {"kind": kind, "duration": 0.0, "fps": 30.0,
                            "width": 0, "height": 0}
    if kind == "video":
        try:
            import cv2

            cap = cv2.VideoCapture(str(p))
            if cap.isOpened():
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                if fps <= 1e-3 or fps > 1000:
                    fps = 30.0
                count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                info["fps"] = fps
                info["duration"] = count / fps if count > 0 else 0.0
                info["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                info["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                cap.release()
        except Exception:  # noqa: BLE001 - 探测失败不应该阻断导入
            pass
    elif kind == "image":
        try:
            from PySide6.QtGui import QImageReader

            size = QImageReader(str(p)).size()
            if size.isValid():
                info["width"], info["height"] = size.width(), size.height()
        except Exception:  # noqa: BLE001
            pass
    return info


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def fmt_tc(seconds: float, fps: float = 30.0) -> str:
    """00:00:12:07（时:分:秒:帧）"""
    fps = fps if fps and fps > 0 else 30.0
    total = max(0, int(round(float(seconds) * fps)))
    f = total % int(round(fps))
    s = total // int(round(fps))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}:{f:02d}"


def fmt_short(seconds: float) -> str:
    """00:12.267"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if h:
        return f"{h:d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


# ---------------- 片段 ----------------
@dataclass
class BlurClip:
    """一段需要高斯模糊的区间。"""

    start: float = 0.0
    end: float = 1.0
    label: str = "r18"
    score: float = 0.0
    peak: float = 0.0
    radius: float = 32.0
    enabled: bool = True
    source: str = "detect"          # detect / manual
    id: str = field(default_factory=lambda: _uid("b"))

    kind = "blur"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def title(self) -> str:
        return "模糊"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "start": self.start, "end": self.end,
            "label": self.label, "score": self.score, "peak": self.peak,
            "radius": self.radius, "enabled": self.enabled, "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlurClip":
        return cls(
            start=float(d.get("start", 0.0)), end=float(d.get("end", 1.0)),
            label=str(d.get("label", "r18")), score=float(d.get("score", 0.0)),
            peak=float(d.get("peak", 0.0)), radius=float(d.get("radius", 32.0)),
            enabled=bool(d.get("enabled", True)), source=str(d.get("source", "detect")),
            id=str(d.get("id") or _uid("b")),
        )


@dataclass
class ImageClip:
    """叠加到画面上的图片（贴图 / 遮挡物），引用素材库里的图片素材。"""

    path: str = ""
    start: float = 0.0
    end: float = 3.0
    scale: float = 1.0          # 相对视频高度
    x: float = 0.5              # 0~1 相对位置
    y: float = 0.5
    opacity: float = 1.0
    enabled: bool = True
    asset_id: str = ""          # 指向素材库里的图片素材
    id: str = field(default_factory=lambda: _uid("i"))

    kind = "image"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def title(self) -> str:
        return Path(self.path).name if self.path else "图片"

    @property
    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "asset_id": self.asset_id,
            "start": self.start, "end": self.end,
            "scale": self.scale, "x": self.x, "y": self.y,
            "opacity": self.opacity, "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ImageClip":
        return cls(
            path=str(d.get("path", "")), start=float(d.get("start", 0.0)),
            end=float(d.get("end", 3.0)), scale=float(d.get("scale", 1.0)),
            x=float(d.get("x", 0.5)), y=float(d.get("y", 0.5)),
            opacity=float(d.get("opacity", 1.0)), enabled=bool(d.get("enabled", True)),
            asset_id=str(d.get("asset_id", "") or ""),
            id=str(d.get("id") or _uid("i")),
        )


Clip = BlurClip | ImageClip


# ---------------- 素材 ----------------
@dataclass
class MediaAsset:
    """工程里的一个素材引用（视频或图片）。视频素材自带时间线数据。"""

    path: str = ""
    kind: str = "video"
    id: str = field(default_factory=lambda: _uid("m"))
    duration: float = 0.0
    fps: float = 30.0
    width: int = 0
    height: int = 0
    blurs: list[BlurClip] = field(default_factory=list)
    images: list[ImageClip] = field(default_factory=list)
    detect: dict[str, Any] = field(default_factory=dict)   # 最近一次检测记录
    added_at: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name if self.path else "（空引用）"

    @property
    def stem(self) -> str:
        return Path(self.path).stem if self.path else ""

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()

    @property
    def detected(self) -> bool:
        return bool(self.detect)

    def probe(self) -> None:
        """重新探测时长 / 分辨率（文件还在时才有效）。"""
        if not self.exists:
            return
        info = probe_media(self.path)
        self.kind = info["kind"] or self.kind
        self.duration = float(info["duration"]) or self.duration
        self.fps = float(info["fps"]) or self.fps
        self.width = int(info["width"]) or self.width
        self.height = int(info["height"]) or self.height

    def all_clips(self) -> list[Clip]:
        return [*self.blurs, *self.images]

    def find(self, clip_id: str) -> Clip | None:
        for c in self.all_clips():
            if c.id == clip_id:
                return c
        return None

    # ---------- 序列化 ----------
    def to_dict(self, base: Path | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "path": self.path,
            "duration": round(float(self.duration), 3), "fps": round(float(self.fps), 3),
            "width": int(self.width), "height": int(self.height),
            "added_at": self.added_at,
            "detect": self.detect or {},
            "blurs": [c.to_dict() for c in self.blurs],
            "images": [c.to_dict() for c in self.images],
        }
        if base is not None and self.path:
            try:
                data["rel"] = Path(self.path).resolve().relative_to(base.resolve()).as_posix()
            except (ValueError, OSError):
                pass
        return data

    @classmethod
    def from_dict(cls, d: dict, base: Path | None = None) -> "MediaAsset":
        path = str(d.get("path", "") or "")
        rel = str(d.get("rel", "") or "")
        # 引用路径失效时，尝试用相对工程目录的路径找回（工程整体搬家的场景）
        if base is not None and rel and (not path or not Path(path).is_file()):
            cand = (base / rel)
            if cand.is_file():
                path = str(cand)
        kind = str(d.get("kind", "") or "") or media_kind(path) or "video"
        return cls(
            path=path, kind=kind,
            id=str(d.get("id") or _uid("m")),
            duration=float(d.get("duration", 0.0) or 0.0),
            fps=float(d.get("fps", 30.0) or 30.0),
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            blurs=[BlurClip.from_dict(x) for x in d.get("blurs", [])],
            images=[ImageClip.from_dict(x) for x in d.get("images", [])],
            detect=dict(d.get("detect") or {}),
            added_at=str(d.get("added_at", "") or ""),
        )


# ---------------- 工程 ----------------
@dataclass
class Project:
    name: str = "未命名工程"
    path: str = ""                     # 工程文件路径
    assets: list[MediaAsset] = field(default_factory=list)
    active_id: str = ""
    dirty: bool = False

    # ---------- 基本属性 ----------
    @property
    def active(self) -> MediaAsset | None:
        """当前正在编辑的素材：优先 active_id，其次第一个视频，再次第一个素材。"""
        if self.active_id:
            for a in self.assets:
                if a.id == self.active_id:
                    return a
        videos = [a for a in self.assets if a.is_video]
        if videos:
            return videos[0]
        return self.assets[0] if self.assets else None

    @property
    def active_name(self) -> str:
        a = self.active
        return a.name if a else "未加载素材"

    def activate(self, asset: "MediaAsset | str | None") -> MediaAsset | None:
        if isinstance(asset, str):
            asset = self.asset_by_id(asset)
        if asset is not None:
            self.active_id = asset.id
        return self.active

    def asset_by_id(self, asset_id: str) -> MediaAsset | None:
        for a in self.assets:
            if a.id == asset_id:
                return a
        return None

    def asset_by_path(self, path: str) -> MediaAsset | None:
        try:
            target = Path(path).resolve()
        except OSError:
            return None
        for a in self.assets:
            try:
                if Path(a.path).resolve() == target:
                    return a
            except OSError:
                continue
        return None

    @property
    def video_assets(self) -> list[MediaAsset]:
        return [a for a in self.assets if a.is_video]

    @property
    def image_assets(self) -> list[MediaAsset]:
        return [a for a in self.assets if not a.is_video]

    @property
    def missing_assets(self) -> list[MediaAsset]:
        return [a for a in self.assets if not a.exists]

    # ---------- 当前素材的代理属性（时间线 / 播放器 / 导出沿用旧接口） ----------
    @property
    def video_path(self) -> str:
        a = self.active
        return a.path if a and a.is_video else ""

    @property
    def duration(self) -> float:
        a = self.active
        return float(a.duration) if a and a.duration else 60.0

    @duration.setter
    def duration(self, v: float) -> None:
        a = self.active
        if a is not None:
            a.duration = float(v)

    @property
    def fps(self) -> float:
        a = self.active
        return float(a.fps) if a and a.fps else 30.0

    @fps.setter
    def fps(self, v: float) -> None:
        a = self.active
        if a is not None:
            a.fps = float(v)

    @property
    def width(self) -> int:
        a = self.active
        return int(a.width) if a else 0

    @property
    def height(self) -> int:
        a = self.active
        return int(a.height) if a else 0

    @property
    def has_video(self) -> bool:
        a = self.active
        return bool(a and a.is_video and a.exists)

    @property
    def blurs(self) -> list[BlurClip]:
        a = self.active
        return a.blurs if a else []

    @blurs.setter
    def blurs(self, value: list[BlurClip]) -> None:
        a = self.active
        if a is not None:
            a.blurs = value

    @property
    def images(self) -> list[ImageClip]:
        a = self.active
        return a.images if a else []

    @images.setter
    def images(self, value: list[ImageClip]) -> None:
        a = self.active
        if a is not None:
            a.images = value

    def all_clips(self) -> list[Clip]:
        a = self.active
        return a.all_clips() if a else []

    def find(self, clip_id: str) -> Clip | None:
        a = self.active
        return a.find(clip_id) if a else None

    def blur_seconds(self) -> float:
        return sum(c.duration for c in self.blurs if c.enabled)

    # ---------- 素材操作 ----------
    def add_asset(self, path: str, kind: str = "", activate: bool = False) -> MediaAsset:
        """把素材加入工程（同一路径不重复添加）。"""
        resolved = str(Path(path).resolve())
        exist = self.asset_by_path(resolved)
        if exist is not None:
            if activate:
                self.active_id = exist.id
            return exist
        k = kind or media_kind(resolved) or "video"
        asset = MediaAsset(path=resolved, kind=k,
                           added_at=datetime.now().isoformat(timespec="seconds"))
        asset.probe()
        self.assets.append(asset)
        if activate or not self.active_id:
            self.active_id = asset.id
        self.dirty = True
        return asset

    def remove_asset(self, asset: MediaAsset) -> None:
        self.assets = [a for a in self.assets if a is not asset]
        if self.active_id == asset.id:
            nxt = next((a for a in self.assets if a.is_video), None) or (
                self.assets[0] if self.assets else None)
            self.active_id = nxt.id if nxt else ""
        self.dirty = True

    def relink_asset(self, asset: MediaAsset, new_path: str) -> None:
        """素材文件被移动后重新指向新路径，同时修正引用它的图片片段。"""
        asset.path = str(Path(new_path).resolve())
        asset.kind = media_kind(asset.path) or asset.kind
        asset.probe()
        for a in self.assets:
            for clip in a.images:
                if clip.asset_id == asset.id or (
                        not clip.asset_id and Path(clip.path).name == Path(asset.path).name):
                    clip.asset_id = asset.id
                    clip.path = asset.path
        self.dirty = True

    # ---------- 片段操作 ----------
    def add_blur(self, start: float, end: float, **kw) -> BlurClip | None:
        a = self.active
        if a is None:
            return None
        clip = BlurClip(start=round(start, 3), end=round(end, 3), **kw)
        a.blurs.append(clip)
        a.blurs.sort(key=lambda c: c.start)
        self.dirty = True
        return clip

    def add_image(self, path: str, start: float, end: float,
                  asset_id: str = "") -> ImageClip | None:
        a = self.active
        if a is None:
            return None
        clip = ImageClip(path=path, start=round(start, 3), end=round(end, 3),
                         asset_id=asset_id)
        a.images.append(clip)
        self.dirty = True
        return clip

    def remove(self, clip: Clip) -> None:
        a = self.active
        if a is None:
            return
        if isinstance(clip, BlurClip):
            a.blurs = [c for c in a.blurs if c is not clip]
        else:
            a.images = [c for c in a.images if c is not clip]
        self.dirty = True

    # ---------- 检测结果 ----------
    def apply_detection(self, payload: dict, asset: MediaAsset | None = None) -> int:
        """把一次检测结果写入素材：替换检测产生的模糊片段，并记录检测元信息。"""
        asset = asset or self.active
        if asset is None:
            return 0
        asset.blurs = [c for c in asset.blurs if c.source != "detect"]
        segments = payload.get("segments") or []
        for seg in segments:
            try:
                asset.blurs.append(BlurClip(
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    label=str(seg.get("label", "r18")),
                    score=float(seg.get("score", 0.0)),
                    peak=float(seg.get("peak", 0.0)),
                    source="detect",
                ))
            except (KeyError, TypeError, ValueError):
                continue
        asset.blurs.sort(key=lambda c: c.start)
        asset.detect = {
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "video": payload.get("video") or {},
            "model": payload.get("model") or {},
            "settings": payload.get("settings") or {},
            "summary": payload.get("summary") or {},
        }
        if payload.get("video"):
            info = payload["video"]
            asset.duration = float(info.get("duration") or asset.duration)
            asset.fps = float(info.get("fps") or asset.fps)
            res = info.get("resolution") or []
            if len(res) == 2:
                asset.width, asset.height = int(res[0]), int(res[1])
        self.dirty = True
        return len(segments)

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        base = Path(self.path).parent if self.path else None
        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "name": self.name,
            "active_id": self.active_id,
            "assets": [a.to_dict(base) for a in self.assets],
        }

    @classmethod
    def from_dict(cls, d: dict, path: str = "") -> "Project":
        if "assets" not in d and "video" in d:      # v1 老工程：单视频 + json_path
            return cls._from_legacy(d, path)
        base = Path(path).parent if path else None
        assets = [MediaAsset.from_dict(x, base) for x in d.get("assets", [])]
        name = str(d.get("name") or "")
        if not name:
            name = Path(path).stem if path else "未命名工程"
        return cls(
            name=name, path=path, assets=assets,
            active_id=str(d.get("active_id", "") or ""),
        )

    @classmethod
    def _from_legacy(cls, d: dict, path: str) -> "Project":
        v = d.get("video") or {}
        asset = MediaAsset(
            path=str(v.get("path", "") or ""), kind="video",
            duration=float(v.get("duration", 0.0) or 0.0),
            fps=float(v.get("fps", 30.0) or 30.0),
            width=int(v.get("width", 0) or 0),
            height=int(v.get("height", 0) or 0),
            blurs=[BlurClip.from_dict(x) for x in d.get("blurs", [])],
            images=[ImageClip.from_dict(x) for x in d.get("images", [])],
        )
        legacy_json = str(d.get("json_path", "") or "")
        if legacy_json:
            asset.detect = {"legacy_json": legacy_json}
        return cls(name=Path(path).stem if path else "未命名工程", path=path,
                   assets=[asset], active_id=asset.id)

    def save(self, path: str | Path | None = None) -> str:
        target = Path(path) if path else (Path(self.path) if self.path else None)
        if target is None:
            raise ValueError("没有指定工程文件路径")
        if target.suffix.lower() != PROJECT_EXT:
            target = target.with_suffix(PROJECT_EXT)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(target)
        if not self.name or self.name == "未命名工程":
            self.name = target.stem
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                          encoding="utf-8")
        self.dirty = False
        return self.path

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("工程文件格式不正确")
        proj = cls.from_dict(data, str(p))
        if not proj.name:
            proj.name = p.stem
        return proj
