"""工程文件的存放、最近工程记录、工程摘要读取（供启动页展示）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..paths import app_dir
from .model import LEGACY_SUFFIX, PROJECT_EXT, Project

MAX_RECENT = 12
SETTINGS_FILE = ".editor-settings.json"


# ---------------- 配置文件 ----------------
def settings_path() -> Path:
    return app_dir() / SETTINGS_FILE


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    try:
        settings_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------- 工程目录 ----------------
def default_project_dir() -> Path:
    d = app_dir() / "projects"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def is_project_file(path: str | Path) -> bool:
    p = Path(path)
    name = p.name.lower()
    return name.endswith(PROJECT_EXT) or name.endswith(LEGACY_SUFFIX)


def unique_project_path(directory: Path, stem: str) -> Path:
    """在目录里找一个不冲突的工程文件名。"""
    base = directory / f"{stem or '未命名工程'}{PROJECT_EXT}"
    if not base.exists():
        return base
    for i in range(2, 999):
        cand = directory / f"{stem or '未命名工程'} ({i}){PROJECT_EXT}"
        if not cand.exists():
            return cand
    return base


def project_summary(path: str | Path) -> dict[str, Any]:
    """读工程文件，给启动页 / 最近工程列表用的轻量摘要。"""
    p = Path(path)
    summary: dict[str, Any] = {
        "path": str(p), "name": p.stem, "videos": 0, "images": 0,
        "missing": 0, "mtime": 0.0, "exists": p.is_file(), "error": "",
    }
    try:
        summary["mtime"] = p.stat().st_mtime
    except OSError:
        pass
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("格式不正确")
        assets = data.get("assets")
        if assets is None and "video" in data:            # 老工程
            assets = [data.get("video") or {}]
        assets = assets or []
        summary["name"] = str(data.get("name") or p.stem)
        videos = images = missing = 0
        for a in assets:
            if not isinstance(a, dict):
                continue
            kind = str(a.get("kind") or "video")
            if kind == "image":
                images += 1
            else:
                videos += 1
            ref = str(a.get("path") or "")
            if not ref or not Path(ref).is_file():
                missing += 1
        summary.update(videos=videos, images=images, missing=missing,
                       assets=len(assets))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        summary["error"] = str(exc)
    return summary


def mtime_text(ts: float) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "—"


# ---------------- 最近工程 ----------------
def recent_projects(settings: dict) -> list[dict]:
    """返回仍然存在的最近工程摘要，顺带清掉失效记录。"""
    raw = settings.get("recent_projects") or []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        path = str((item or {}).get("path") or "")
        if not path or path in seen:
            continue
        p = Path(path)
        if not p.is_file():
            continue
        seen.add(path)
        s = project_summary(p)
        s["opened_at"] = str((item or {}).get("opened_at") or "")
        out.append(s)
    return out[:MAX_RECENT]


def touch_recent(settings: dict, path: str | Path) -> None:
    """把工程提到最近列表最前面。"""
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = str(path)
    items = [i for i in (settings.get("recent_projects") or [])
             if str((i or {}).get("path") or "") != resolved]
    items.insert(0, {"path": resolved,
                     "opened_at": datetime.now().isoformat(timespec="seconds")})
    settings["recent_projects"] = items[:MAX_RECENT]
    settings["last_project"] = resolved
    save_settings(settings)


def forget_recent(settings: dict, path: str) -> None:
    items = [i for i in (settings.get("recent_projects") or [])
             if str((i or {}).get("path") or "") != str(path)]
    settings["recent_projects"] = items
    if str(settings.get("last_project") or "") == str(path):
        settings["last_project"] = ""
    save_settings(settings)


def create_project(path: str | Path) -> Project:
    """新建空工程并立即落盘（保证启动页里能立刻看到它）。"""
    p = Path(path)
    if p.suffix.lower() != PROJECT_EXT:
        p = p.with_suffix(PROJECT_EXT)
    proj = Project(name=p.stem, path=str(p))
    proj.save(p)
    return proj
