"""启动页（选择工程）与「素材缺失」提示对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import icons, projects, theme
from .model import LEGACY_SUFFIX, PROJECT_EXT, MediaAsset, Project
from .widgets import TwoLineDelegate

PROJECT_FILTER = f"Mosaic 工程 (*{PROJECT_EXT} *{LEGACY_SUFFIX});;所有文件 (*)"


def _small(text: str, name: str = "Tiny") -> QLabel:
    lb = QLabel(text)
    lb.setObjectName(name)
    return lb


class StartupDialog(QDialog):
    """启动时选择工程：从最近工程继续 / 新建 / 浏览。"""

    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.action = "quit"
        self.path = ""
        self._recents = projects.recent_projects(settings)

        self.setWindowTitle("Mosaic Studio — 选择工程")
        self.setModal(True)
        self.setMinimumSize(660, 470)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("blur", size=34))
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t1 = QLabel("MOSAIC STUDIO")
        t1.setStyleSheet("font-size:17px; font-weight:800; letter-spacing:3px;")
        titles.addWidget(t1)
        t2 = _small("选择要打开的工程 · 工程只记录素材引用，素材可以放在任意位置")
        titles.addWidget(t2)
        head.addLayout(titles)
        head.addStretch(1)
        root.addLayout(head)

        root.addWidget(self._hsep())

        row = QHBoxLayout()
        row.setSpacing(8)
        lab = _small("最近工程")
        row.addWidget(lab)
        row.addStretch(1)
        self.btn_forget = QPushButton("从列表移除")
        self.btn_forget.setObjectName("Ghost")
        self.btn_forget.setToolTip("只移除这条记录，不会删除工程文件")
        self.btn_forget.clicked.connect(self._forget)
        row.addWidget(self.btn_forget)
        root.addLayout(row)

        self.list = QListWidget()
        self.list.setItemDelegate(TwoLineDelegate(self.list, 50))
        self.list.setIconSize(QSize(18, 18))
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self._open_selected())
        self.list.currentItemChanged.connect(
            lambda *_: self.btn_open.setEnabled(self._current_path() is not None))
        root.addWidget(self.list, 1)

        self.lbl_hint = _small("")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

        root.addWidget(self._hsep())

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_new = QPushButton("新建工程…")
        self.btn_new.setIcon(icons.icon("plus"))
        self.btn_new.clicked.connect(self._new)
        bar.addWidget(self.btn_new)

        self.btn_browse = QPushButton("打开其他工程…")
        self.btn_browse.setIcon(icons.icon("folder"))
        self.btn_browse.clicked.connect(self._browse)
        bar.addWidget(self.btn_browse)
        bar.addStretch(1)

        self.btn_quit = QPushButton("退出")
        self.btn_quit.setObjectName("Ghost")
        self.btn_quit.clicked.connect(self.reject)
        bar.addWidget(self.btn_quit)

        self.btn_open = QPushButton("打开工程")
        self.btn_open.setObjectName("Accent")
        self.btn_open.setIcon(icons.icon("folder"))
        self.btn_open.setDefault(True)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_selected)
        bar.addWidget(self.btn_open)
        root.addLayout(bar)

        QShortcut(QKeySequence("Del"), self, activated=self._forget)

        self._fill()

    # ---------- 列表 ----------
    def _hsep(self) -> QWidget:
        from PySide6.QtWidgets import QFrame

        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet("background:#2a2a33; border:none;")
        return f

    def _fill(self) -> None:
        self.list.clear()
        for rec in self._recents:
            item = QListWidgetItem(icons.icon("project"), rec["name"])
            item.setData(Qt.UserRole, rec["path"])
            sub = f"{rec['videos']} 视频 · {rec['images']} 图片 · {projects.mtime_text(rec['mtime'])}"
            if rec.get("missing"):
                sub += f" · ⚠ 缺失 {rec['missing']} 个素材"
            if rec.get("error"):
                sub += " · 读取失败"
            item.setData(Qt.UserRole + 1, sub)
            item.setToolTip(rec["path"])
            if rec.get("missing") or rec.get("error"):
                item.setForeground(theme.qc("#d9a86a"))
            self.list.addItem(item)

        if self._recents:
            self.list.setCurrentRow(0)
            self.lbl_hint.setText(f"共 {len(self._recents)} 个最近工程 · 双击打开，Del 从列表移除")
        else:
            placeholder = QListWidgetItem("还没有工程记录，点「新建工程」开始")
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(theme.qc(theme.MUTED))
            self.list.addItem(placeholder)
            self.lbl_hint.setText("工程文件（*.vproj）记录素材引用与所有打码片段，下次打开可继续编辑")
        self.btn_forget.setEnabled(bool(self._recents))
        self.btn_open.setEnabled(self._current_path() is not None)

    def _current_path(self) -> str | None:
        item = self.list.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return None
        return str(item.data(Qt.UserRole))

    def _forget(self) -> None:
        path = self._current_path()
        if not path:
            return
        projects.forget_recent(self.settings, path)
        self._recents = [r for r in self._recents if r["path"] != path]
        self._fill()

    # ---------- 动作 ----------
    def _open_selected(self) -> None:
        path = self._current_path()
        if path:
            self.action = "open"
            self.path = path
            self.accept()

    def _browse(self) -> None:
        start = str(projects.default_project_dir())
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", start, PROJECT_FILTER)
        if path:
            self.action = "open"
            self.path = path
            self.accept()

    def _new(self) -> None:
        default = projects.unique_project_path(projects.default_project_dir(), "未命名工程")
        path, _ = QFileDialog.getSaveFileName(
            self, "新建工程（选择保存位置）", str(default), f"Mosaic 工程 (*{PROJECT_EXT})")
        if not path:
            return
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        self.action = "new"
        self.path = path
        self.accept()

    def reject(self) -> None:
        self.action = "quit"
        super().reject()


class MissingMediaDialog(QDialog):
    """工程里有素材引用找不到文件时的处理对话框。"""

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self._missing = list(project.missing_assets)
        self.relinked = 0

        self.setWindowTitle("素材缺失")
        self.setModal(True)
        self.setMinimumSize(660, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        title = QLabel("有素材找不到文件")
        title.setStyleSheet("font-size:15px; font-weight:700; color:#e8b06a;")
        root.addWidget(title)
        sub = _small("工程只记录引用，素材被移动/改名/删除后会失效。"
                     "可以逐个重新定位，或先保留引用稍后处理。")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self.list = QListWidget()
        self.list.setItemDelegate(TwoLineDelegate(self.list, 46))
        self.list.setIconSize(QSize(16, 16))
        self.list.itemDoubleClicked.connect(lambda _i: self._relocate())
        root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_relocate = QPushButton("重新定位…")
        self.btn_relocate.setObjectName("Accent")
        self.btn_relocate.setIcon(icons.icon("link"))
        self.btn_relocate.clicked.connect(self._relocate)
        bar.addWidget(self.btn_relocate)

        self.btn_remove = QPushButton("从工程移除")
        self.btn_remove.setObjectName("Danger")
        self.btn_remove.clicked.connect(self._remove)
        bar.addWidget(self.btn_remove)
        bar.addStretch(1)

        self.btn_skip = QPushButton("稍后处理")
        self.btn_skip.setToolTip("保留引用，素材库里会标红显示缺失")
        self.btn_skip.clicked.connect(self.accept)
        bar.addWidget(self.btn_skip)
        root.addLayout(bar)

        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for asset in self._missing:
            kind = "视频" if asset.is_video else "图片"
            item = QListWidgetItem(icons.icon("video" if asset.is_video else "image"),
                                   asset.name)
            item.setData(Qt.UserRole, asset.id)
            item.setData(Qt.UserRole + 1, f"{kind} · {asset.path or '（空路径）'}")
            item.setToolTip(asset.path)
            item.setForeground(theme.qc("#d9a86a"))
            self.list.addItem(item)
        if self._missing:
            self.list.setCurrentRow(0)
        has = bool(self._missing)
        self.btn_relocate.setEnabled(has)
        self.btn_remove.setEnabled(has)
        if not has:
            self.btn_skip.setText("关闭")

    def _current(self) -> MediaAsset | None:
        item = self.list.currentItem()
        if item is None:
            return None
        aid = item.data(Qt.UserRole)
        return next((a for a in self._missing if a.id == aid), None)

    def _relocate(self) -> None:
        asset = self._current()
        if asset is None:
            return
        old = Path(asset.path) if asset.path else None
        start_dir = str(old.parent) if old and old.parent.is_dir() else str(
            projects.default_project_dir())
        hint = "视频文件" if asset.is_video else "图片文件"
        path, _ = QFileDialog.getOpenFileName(
            self, f"重新定位「{asset.name}」", start_dir,
            f"{hint} (*.mp4 *.mkv *.avi *.mov *.flv *.webm *.ts *.m4v *.wmv *.png *.jpg *.jpeg *.webp *.bmp *.gif);;"
            "所有文件 (*)")
        if not path:
            return
        self.project.relink_asset(asset, path)
        self.relinked += 1
        self._missing = [a for a in self._missing if not a.exists]
        self._fill()
        if not self._missing:
            self.accept()

    def _remove(self) -> None:
        asset = self._current()
        if asset is None:
            return
        ret = QMessageBox.question(
            self, "从工程移除",
            f"从工程里移除「{asset.name}」？\n（只删除引用，不会动磁盘上的文件）")
        if ret != QMessageBox.Yes:
            return
        self.project.remove_asset(asset)
        self._missing = [a for a in self._missing if a is not asset]
        self._fill()
        if not self._missing:
            self.accept()
