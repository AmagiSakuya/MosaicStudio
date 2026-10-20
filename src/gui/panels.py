"""左侧素材库 / 检测面板、右侧片段与属性面板、导出对话框。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import exporter, icons, theme
from .exporter import ExportSettings
from .model import BlurClip, Clip, ImageClip, MediaAsset, Project, fmt_short, fmt_tc
from .widgets import TwoLineDelegate


# ---------------- 基础卡片 ----------------
class Card(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(10, 8, 10, 10)
        self._lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title.upper() if title.isascii() else title)
        self.title_label.setObjectName("CardTitle")
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.head = head
        self._lay.addLayout(head)

    def body(self) -> QVBoxLayout:
        return self._lay


def _small(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Tiny")
    return lb


def _hsep() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:#2a2a33; border:none;")
    return f


def _icon_button(name: str, tip: str, slot) -> QPushButton:
    btn = QPushButton()
    btn.setIcon(icons.icon(name))
    btn.setIconSize(QSize(14, 14))
    btn.setFixedSize(26, 24)
    btn.setToolTip(tip)
    btn.clicked.connect(slot)
    return btn


# ---------------- 素材库 ----------------
class AssetLibrary(Card):
    """工程素材库：视频与图片的引用列表（路径可任意）。"""

    activateRequested = Signal(str)      # 视频=加载到工作区，图片=加到时间线
    importRequested = Signal()
    removeRequested = Signal(str)
    relinkRequested = Signal(str)
    refreshRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("素材库 · 工程引用", parent)
        self.project: Optional[Project] = None

        self.head.addWidget(_icon_button("plus", "导入视频 / 图片素材", self.importRequested.emit))
        self.head.addWidget(_icon_button("refresh", "刷新素材状态（检查文件是否还在）",
                                        self.refreshRequested.emit))

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索素材名…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.body().addWidget(self.search)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(False)
        self.list.setItemDelegate(TwoLineDelegate(self.list))
        self.list.setIconSize(QSize(16, 16))
        self.list.itemDoubleClicked.connect(self._on_double)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.body().addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_import = QPushButton("导入素材")
        self.btn_import.setObjectName("Accent")
        self.btn_import.setIcon(icons.icon("plus"))
        self.btn_import.setToolTip("把视频或图片加入本工程（可多选，文件放哪都行）")
        self.btn_import.clicked.connect(self.importRequested.emit)
        row.addWidget(self.btn_import, 1)

        self.btn_remove = QPushButton("移除")
        self.btn_remove.setObjectName("Ghost")
        self.btn_remove.setToolTip("只从工程移除引用，不删除磁盘文件 (Del)")
        self.btn_remove.clicked.connect(self._on_remove)
        row.addWidget(self.btn_remove)
        self.body().addLayout(row)

        self.count = _small("")
        self.count.setWordWrap(True)
        self.body().addWidget(self.count)

    # ---------- 数据 ----------
    def bind_project(self, project: Optional[Project]) -> None:
        self.project = project
        self.rebuild()

    def rebuild(self) -> None:
        self.list.clear()
        self.btn_remove.setEnabled(False)
        if self.project is None:
            self._placeholder("先新建或打开一个工程")
            self.count.setText("")
            return

        assets = self.project.assets
        if not assets:
            self._placeholder("还没有素材：点「导入素材」把视频/图片加进来")
        active = self.project.active
        for asset in assets:
            missing = not asset.exists
            kind = "视频" if asset.is_video else "图片"
            if missing:
                sub = f"{kind} · ⚠ 文件缺失"
            elif asset.is_video:
                bits = [kind, fmt_short(asset.duration)]
                if asset.width:
                    bits.append(f"{asset.width}×{asset.height}")
                if asset.detected:
                    n = len([b for b in asset.blurs if b.source == "detect"])
                    bits.append(f"已检测 {n} 段" if n else "已检测 · 无命中")
                sub = " · ".join(bits)
            else:
                sub = f"{kind} · {asset.width}×{asset.height}" if asset.width else kind

            name = asset.name
            if active is not None and asset.id == active.id:
                name = f"▶ {name}"
            item = QListWidgetItem(icons.icon("video" if asset.is_video else "image"), name)
            item.setData(Qt.UserRole, asset.id)
            item.setData(Qt.UserRole + 1, sub)
            item.setToolTip(f"{asset.path}\n双击{'加载' if asset.is_video else '添加到时间线'}")
            if missing:
                item.setForeground(theme.qc("#e08a7a"))
            elif active is not None and asset.id == active.id:
                item.setForeground(theme.qc("#9dc0f5"))
            self.list.addItem(item)
            if active is not None and asset.id == active.id:
                self.list.setCurrentItem(item)

        videos = len(self.project.video_assets)
        images = len(self.project.image_assets)
        missing_n = len(self.project.missing_assets)
        text = f"共 {len(assets)} 个素材 · 视频 {videos} · 图片 {images}"
        if missing_n:
            text += f" · ⚠ 缺失 {missing_n}"
        self.count.setText(text)
        self._filter(self.search.text())
        self._sync_buttons()

    def _placeholder(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        item.setForeground(theme.qc(theme.MUTED))
        self.list.addItem(item)

    def _filter(self, text: str) -> None:
        text = (text or "").strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.data(Qt.UserRole):
                continue
            item.setHidden(bool(text) and text not in item.text().lower())

    def _current_id(self) -> Optional[str]:
        item = self.list.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return None
        return str(item.data(Qt.UserRole))

    def _current_asset(self) -> Optional[MediaAsset]:
        if self.project is None:
            return None
        aid = self._current_id()
        return self.project.asset_by_id(aid) if aid else None

    def _sync_buttons(self) -> None:
        self.btn_remove.setEnabled(self._current_id() is not None)

    # ---------- 交互 ----------
    def _on_double(self, _item) -> None:
        aid = self._current_id()
        if aid:
            self.activateRequested.emit(aid)

    def _on_remove(self) -> None:
        aid = self._current_id()
        if aid:
            self.removeRequested.emit(aid)

    def _menu(self, pos) -> None:
        asset = self._current_asset()
        if asset is None or self.project is None:
            return
        menu = QMenu(self)
        act_open = menu.addAction("加载到工作区" if asset.is_video else "添加到时间线")
        act_relink = menu.addAction("重新定位…")
        act_reveal = menu.addAction("在资源管理器中显示")
        menu.addSeparator()
        act_remove = menu.addAction("从工程移除")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is act_open:
            self.activateRequested.emit(asset.id)
        elif chosen is act_relink:
            self.relinkRequested.emit(asset.id)
        elif chosen is act_reveal:
            try:
                if asset.exists:
                    os.startfile(str(Path(asset.path).parent))  # type: ignore[attr-defined]
            except OSError:
                pass
        elif chosen is act_remove:
            self.removeRequested.emit(asset.id)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Delete:
            self._on_remove()
            return
        super().keyPressEvent(e)


# ---------------- 检测设置 ----------------
class DetectPanel(Card):
    detectRequested = Signal(dict)
    cancelRequested = Signal()
    manageRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("模型配置 · 检测", parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._busy = False
        self._target_ok = False

        self.head.addWidget(_icon_button("settings", "运行时管理：模型下载 / GPU 环境 / 运行库",
                                         self.manageRequested.emit))

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        self._model_labels = {
            "ultra": "Ultra · b36（最准）",
            "pro": "Pro · s36（均衡）",
            "nano": "Nano · 极快",
        }
        self.model = QComboBox()
        self.model.addItem(self._model_labels["ultra"], "ultra")
        self.model.addItem(self._model_labels["pro"], "pro")
        self.model.addItem(self._model_labels["nano"], "nano")

        self.device = QComboBox()
        self.device.addItem("自动", "auto")
        self.device.addItem("GPU · CUDA", "cuda")
        self.device.addItem("GPU · DirectML", "dml")
        self.device.addItem("CPU", "cpu")

        self.level = QComboBox()
        self.level.addItem("R18 + R15", "all")
        self.level.addItem("仅 R18", "r18")

        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(10, 95)
        self.thr.setValue(50)
        self.thr_lb = QLabel("0.50")
        self.thr_lb.setObjectName("Value")
        self.thr_lb.setFixedWidth(30)
        self.thr.valueChanged.connect(lambda v: self.thr_lb.setText(f"{v / 100:.2f}"))
        thr_box = QHBoxLayout()
        thr_box.setSpacing(4)
        thr_box.addWidget(self.thr, 1)
        thr_box.addWidget(self.thr_lb)

        self.fps = QDoubleSpinBox()
        self.fps.setRange(0, 30)
        self.fps.setSingleStep(0.5)
        self.fps.setDecimals(1)
        self.fps.setValue(0)
        self.fps.setSpecialValueText("自动")
        self.fps.setToolTip("每秒采样帧数，0=自动（GPU 4 / CPU 2）")

        self.min_dur = QDoubleSpinBox()
        self.min_dur.setRange(0.1, 20.0)
        self.min_dur.setSingleStep(0.1)
        self.min_dur.setDecimals(1)
        self.min_dur.setValue(0.5)

        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.0, 20.0)
        self.gap.setSingleStep(0.25)
        self.gap.setDecimals(2)
        self.gap.setValue(0.75)

        self.smooth = QDoubleSpinBox()
        self.smooth.setRange(1, 15)
        self.smooth.setSingleStep(2)
        self.smooth.setDecimals(0)
        self.smooth.setValue(3)

        for w in (self.model, self.device, self.level, self.fps, self.min_dur, self.gap, self.smooth):
            w.setMinimumWidth(0)

        # 每个参数的含义（鼠标悬停显示）
        tips: dict[str, tuple[str, str]] = {
            "模型": ("检测模型档位",
                     "Ultra · caformer_b36：最准（官方 88.5%），24 分钟番剧约 2 分钟<br>"
                     "Pro · caformer_s36_plus：快一倍，准确率 74.3%<br>"
                     "Nano：极快，但漏检多，只适合粗筛"),
            "设备": ("推理设备",
                     "自动：有 CUDA 就走 CUDA，其次 DirectML，最后 CPU<br>"
                     "GPU · CUDA：NVIDIA 显卡，需要 GPU 版引擎 + CUDA/cuDNN 运行库，约 CPU 的 15 倍<br>"
                     "GPU · DirectML：AMD / Intel 显卡用的后端，任何 DX12 显卡都能跑<br>"
                     "CPU：兼容性最好但慢<br>"
                     "点右上角齿轮可打开「运行时管理」下载模型 / 运行库"),
            "等级": ("检测等级",
                     "R18 + R15：含擦边 / 暗示性画面，分数 = 1 - P(safe)<br>"
                     "仅 R18：只看露骨内容，误报更少（阈值自动降到 0.4）"),
            "阈值": ("判定阈值 0 ~ 1",
                     "逐帧分数 ≥ 阈值才算命中。<br>"
                     "越低越敏感（漏检少、误报多），越高越保守。<br>"
                     "默认 0.5；误报多调到 0.65，漏检多降到 0.35 ~ 0.4。"),
            "采样": ("采样帧率（每秒抽几帧送模型）",
                     "0 = 自动（GPU 4 fps / CPU 2 fps）。<br>"
                     "调高能抓到更短的镜头，但耗时线性增加。<br>"
                     "30fps 视频 4fps ≈ 每 7.5 帧看 1 帧。"),
            "最短": ("最短片段时长（秒）",
                     "短于该长度的片段直接丢弃，用来过滤一闪而过的误判。<br>默认 0.5。"),
            "合并": ("合并间隔（秒）",
                     "两段命中间隔小于该值时合并成一段，避免同一画面被切成碎片。<br>"
                     "默认 0.75；切得太碎可以加到 1.5。"),
            "去噪": ("去噪窗口（帧数）",
                     "邻域多数投票：窗口内过半帧命中才算命中，用来否决孤立的单帧误判。<br>"
                     "默认 3（奇数）；越大越保守，但可能吃掉很短的真片段。"),
        }

        def lab(key: str) -> QLabel:
            lb = _small(key)
            title, body = tips[key]
            lb.setToolTip(f"<b>{title}</b><br>{body}")
            lb.setCursor(Qt.WhatsThisCursor)
            return lb

        for key, widget in (("模型", self.model), ("设备", self.device), ("等级", self.level),
                            ("采样", self.fps), ("最短", self.min_dur),
                            ("合并", self.gap), ("去噪", self.smooth)):
            widget.setToolTip(f"<b>{tips[key][0]}</b><br>{tips[key][1]}")
        thr_tip = f"<b>{tips['阈值'][0]}</b><br>{tips['阈值'][1]}"
        self.thr.setToolTip(thr_tip)
        self.thr_lb.setToolTip(thr_tip)

        grid.addWidget(lab("模型"), 0, 0)
        grid.addWidget(self.model, 0, 1)
        grid.addWidget(lab("设备"), 0, 2)
        grid.addWidget(self.device, 0, 3)
        grid.addWidget(lab("等级"), 1, 0)
        grid.addWidget(self.level, 1, 1)
        grid.addWidget(lab("阈值"), 1, 2)
        grid.addLayout(thr_box, 1, 3)
        grid.addWidget(lab("采样"), 2, 0)
        grid.addWidget(self.fps, 2, 1)
        grid.addWidget(lab("最短"), 2, 2)
        grid.addWidget(self.min_dur, 2, 3)
        grid.addWidget(lab("合并"), 3, 0)
        grid.addWidget(self.gap, 3, 1)
        grid.addWidget(lab("去噪"), 3, 2)
        grid.addWidget(self.smooth, 3, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.body().addLayout(grid)

        self.model_hint = _small("")
        self.model_hint.setStyleSheet("color:#e8b06a;")
        self.model_hint.setVisible(False)
        self.body().addWidget(self.model_hint)
        self.model.currentIndexChanged.connect(lambda _i: self._sync_model_hint())

        self.btn_detect = QPushButton("开始检测")
        self.btn_detect.setObjectName("Accent")
        self.btn_detect.setIcon(icons.icon("scan"))
        self.btn_detect.setToolTip("扫描当前视频素材，结果直接写入工程（不产生 JSON 文件）")
        self.btn_detect.clicked.connect(self._on_button)
        self.btn_detect.setEnabled(False)
        self.body().addWidget(self.btn_detect)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.body().addWidget(self.progress)

        self.status = _small("先导入并加载一个视频素材")
        self.status.setWordWrap(True)
        self.body().addWidget(self.status)

    def _on_button(self) -> None:
        if self._busy:
            self.cancelRequested.emit()
            self.btn_detect.setEnabled(False)
            self.status.setText("正在取消…")
        else:
            self.detectRequested.emit(self.params())

    def params(self) -> dict:
        level = self.level.currentData()
        return {
            "variant": self.model.currentData(),
            "device": self.device.currentData(),
            "level": level,
            "threshold": self.thr.value() / 100.0,
            "fps": float(self.fps.value()),
            "min_duration": float(self.min_dur.value()),
            "gap": float(self.gap.value()),
            "smooth": int(self.smooth.value()),
        }

    def set_target(self, text: str, enabled: bool) -> None:
        """由主窗口告知当前检测目标（当前素材）。"""
        self._target_ok = enabled
        self.status.setText(text)
        self.btn_detect.setEnabled(enabled and not self._busy)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.btn_detect.setEnabled(self._target_ok or busy)
        self.btn_detect.setText("取消检测" if busy else "开始检测")
        self.btn_detect.setObjectName("Danger" if busy else "Accent")
        self.btn_detect.setIcon(icons.icon("trash" if busy else "scan"))
        self.btn_detect.style().unpolish(self.btn_detect)
        self.btn_detect.style().polish(self.btn_detect)
        if text:
            self.status.setText(text)
        if not busy:
            self.progress.setValue(0)

    def set_progress(self, frac: float, text: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.status.setText(text)

    # ---------- 运行环境 ----------
    def refresh_models(self) -> None:
        """下拉项带 tooltip 标出是否已安装，选中未安装的模型时在下方给黄色提示。"""
        from .. import models as model_repo

        for i in range(self.model.count()):
            key = str(self.model.itemData(i) or "")
            item = self.model.model().item(i)
            if item is None or not key:
                continue
            st = model_repo.state(key)
            s = model_repo.spec(key)
            item.setToolTip(
                f"已安装（{st.size_text}）" if st.installed
                else f"未下载 · 约 {s.size_mb} MB，点右上角齿轮下载")
        self._sync_model_hint()

    def _sync_model_hint(self) -> None:
        from .. import models as model_repo

        key = str(self.model.currentData() or "")
        st = model_repo.state(key) if key else None
        missing = st is not None and not st.installed
        self.model_hint.setVisible(missing)
        if missing:
            s = model_repo.spec(key)
            self.model_hint.setText(f"⚠ 该档位还没下载（约 {s.size_mb} MB），点右上角齿轮下载")

    def apply_runtime(self, info: dict) -> None:
        """按探测结果把不可用的设备选项灰掉，避免选了之后报错。"""
        ort = info.get("ort") or {}
        usable = {"auto": True, "cuda": bool(ort.get("cuda")),
                  "dml": bool(ort.get("dml")), "cpu": True}
        model = self.device.model()
        for i in range(self.device.count()):
            key = str(self.device.itemData(i) or "")
            item = model.item(i)
            if item is None:
                continue
            ok = usable.get(key, True)
            item.setEnabled(ok)
            label = {"auto": "自动", "cuda": "GPU · CUDA", "dml": "GPU · DirectML",
                     "cpu": "CPU"}.get(key, key)
            item.setText(label if ok else f"{label}（不可用）")
        cur = str(self.device.currentData() or "auto")
        if not usable.get(cur, True):
            self.device.setCurrentIndex(0)


# ---------------- 片段列表 ----------------
class ClipList(Card):
    selectRequested = Signal(object)
    toggleRequested = Signal(object)
    deleteRequested = Signal(object)
    addBlur = Signal()
    addImage = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("时间线片段", parent)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_click)
        self.list.itemChanged.connect(self._on_check)
        self.body().addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        b_blur = QPushButton("+ 模糊")
        b_blur.setToolTip("在播放头处添加模糊片段 (B)")
        b_blur.clicked.connect(self.addBlur.emit)
        b_img = QPushButton("+ 图片")
        b_img.setToolTip("把图片加入素材库并叠加到时间线 (I)")
        b_img.clicked.connect(self.addImage.emit)
        row.addWidget(b_blur, 1)
        row.addWidget(b_img, 1)
        self.body().addLayout(row)

    def rebuild(self, project: Optional[Project]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        if project is not None:
            for clip in sorted(project.all_clips(), key=lambda c: (c.start, c.kind)):
                if isinstance(clip, BlurClip):
                    text = (f"{fmt_short(clip.start)} → {fmt_short(clip.end)}  "
                            f"{clip.label.upper()} · σ{int(clip.radius)}")
                    icon = icons.icon("blur")
                else:
                    text = f"{fmt_short(clip.start)} → {fmt_short(clip.end)}  图片 · {clip.title}"
                    icon = icons.icon("image")
                item = QListWidgetItem(icon, text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if clip.enabled else Qt.Unchecked)
                item.setData(Qt.UserRole, clip.id)
                item.setSizeHint(QSize(0, 26))
                if not clip.enabled:
                    item.setForeground(theme.qc("#77777f"))
                self.list.addItem(item)
        if self.list.count() == 0:
            item = QListWidgetItem("还没有片段：检测视频，或在模糊轨拖拽新建")
            item.setFlags(Qt.NoItemFlags)
            item.setForeground(theme.qc(theme.MUTED))
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _clip_by_id(self, item: QListWidgetItem) -> Clip | None:
        cid = item.data(Qt.UserRole)
        if not cid or self._project is None:
            return None
        return self._project.find(cid)

    _project: Optional[Project] = None

    def bind_project(self, project: Optional[Project]) -> None:
        self._project = project

    def _on_click(self, item: QListWidgetItem) -> None:
        clip = self._clip_by_id(item)
        if clip is not None:
            self.selectRequested.emit(clip)

    def _on_check(self, item: QListWidgetItem) -> None:
        clip = self._clip_by_id(item)
        if clip is not None:
            clip.enabled = item.checkState() == Qt.Checked
            self.toggleRequested.emit(clip)


# ---------------- 属性检查器 ----------------
class Inspector(Card):
    changed = Signal()
    deleteRequested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__("属性", parent)
        self.clip: Clip | None = None
        self.project: Project | None = None
        self._content = QWidget()
        self._form = QVBoxLayout(self._content)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(8)
        self.body().addWidget(self._content)
        self.body().addStretch(1)
        self._empty = _small("在时间线上选中片段后可微调")
        self.body().insertWidget(1, self._empty)

    def set_clip(self, clip: Optional[Clip], project: Optional[Project]) -> None:
        self.clip = clip
        self.project = project
        self._clear()
        self._empty.setVisible(clip is None)
        if clip is None or project is None:
            return

        if isinstance(clip, BlurClip):
            self._build_blur(clip, project)
        else:
            self._build_image(clip, project)

    def _clear(self) -> None:
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
                lay.deleteLater()

    def _row(self, label: str, widget: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lb = _small(label)
        lb.setFixedWidth(38)
        h.addWidget(lb)
        h.addWidget(widget, 1)
        return w

    def _time_row(self, clip: Clip) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        dur = self.project.duration if self.project else 60.0

        self.sp_start = QDoubleSpinBox()
        self.sp_end = QDoubleSpinBox()
        for sp, val in ((self.sp_start, clip.start), (self.sp_end, clip.end)):
            sp.setRange(0.0, max(1.0, dur))
            sp.setDecimals(3)
            sp.setSingleStep(0.05)
            sp.setValue(val)
            sp.setSuffix(" s")
        grid.addWidget(_small("起点"), 0, 0)
        grid.addWidget(self.sp_start, 0, 1)
        grid.addWidget(_small("终点"), 1, 0)
        grid.addWidget(self.sp_end, 1, 1)
        self.sp_start.valueChanged.connect(self._on_time)
        self.sp_end.valueChanged.connect(self._on_time)
        return w

    def _on_time(self) -> None:
        if self.clip is None:
            return
        start = self.sp_start.value()
        end = self.sp_end.value()
        if end - start < 0.03:
            return
        self.clip.start = round(start, 3)
        self.clip.end = round(end, 3)
        if isinstance(self.clip, BlurClip) and self.project is not None:
            self.project.blurs.sort(key=lambda c: c.start)
        self.changed.emit()

    def _slider_row(self, label: str, lo: int, hi: int, value: int,
                    on_change, suffix: str = "") -> QWidget:
        """滑块 + 可直接输入的数值框（两边同步）。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lb = _small(label)
        lb.setFixedWidth(38)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        sp = QSpinBox()
        sp.setObjectName("Num")
        sp.setRange(lo, hi)
        sp.setValue(value)
        sp.setSuffix(suffix)
        sp.setFixedWidth(62)
        sp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sp.setKeyboardTracking(False)
        sp.setToolTip("可以直接输入数字，回车生效")

        def from_slider(v: int) -> None:
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
            on_change(v)

        def from_spin(v: int) -> None:
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
            on_change(v)

        s.valueChanged.connect(from_slider)
        sp.valueChanged.connect(from_spin)
        h.addWidget(lb)
        h.addWidget(s, 1)
        h.addWidget(sp)
        return w

    def _build_blur(self, clip: BlurClip, project: Project) -> None:
        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(6)
        badge = QLabel("模糊片段")
        badge.setObjectName("BadgeRed")
        hh.addWidget(badge)
        lab = QLabel(clip.label.upper())
        lab.setObjectName("Badge")
        hh.addWidget(lab)
        hh.addStretch(1)
        src = QLabel("检测" if clip.source == "detect" else "手动")
        src.setObjectName("Tiny")
        hh.addWidget(src)
        self._form.addWidget(head)

        self._form.addWidget(self._time_row(clip))

        def set_radius(v: int) -> None:
            clip.radius = float(v)
            self.changed.emit()

        self._form.addWidget(self._slider_row("模糊 σ", 1, 150, int(clip.radius), set_radius))

        en = QCheckBox("启用该片段")
        en.setChecked(clip.enabled)
        en.toggled.connect(lambda v: (setattr(clip, "enabled", v), self.changed.emit()))
        self._form.addWidget(en)

        if clip.score or clip.peak:
            info = _small(f"平均分 {clip.score:.2f} · 峰值 {clip.peak:.2f}")
            self._form.addWidget(info)

        det = (project.active.detect if project.active else {}) or {}
        meta = det.get("model") or {}
        if clip.source == "detect" and meta.get("name"):
            self._form.addWidget(_small(f"来源：{meta.get('name')}"))

        del_btn = QPushButton("删除该片段")
        del_btn.setObjectName("Danger")
        del_btn.setIcon(icons.icon("trash"))
        del_btn.clicked.connect(lambda: self.deleteRequested.emit(clip))
        self._form.addWidget(del_btn)

    def _build_image(self, clip: ImageClip, project: Project) -> None:
        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(6)
        badge = QLabel("图片片段")
        badge.setObjectName("BadgeGreen")
        hh.addWidget(badge)
        hh.addStretch(1)
        self._form.addWidget(head)

        name = QLabel(Path(clip.path).name if clip.path else "（未选择）")
        name.setObjectName("Tiny")
        name.setWordWrap(True)
        name.setToolTip(clip.path)
        self._form.addWidget(name)
        if clip.path and not Path(clip.path).is_file():
            warn = _small("⚠ 图片文件缺失，请到素材库重新定位")
            warn.setStyleSheet("color:#e08a7a;")
            self._form.addWidget(warn)

        self._form.addWidget(self._time_row(clip))

        def setter(attr: str, scale: float):
            def fn(v: int) -> None:
                setattr(clip, attr, v / scale)
                self.changed.emit()
            return fn

        self._form.addWidget(self._slider_row("缩放", 10, 300, int(clip.scale * 100),
                                              setter("scale", 100.0), suffix="%"))
        self._form.addWidget(self._slider_row("水平", 0, 100, int(clip.x * 100),
                                              setter("x", 100.0), suffix="%"))
        self._form.addWidget(self._slider_row("垂直", 0, 100, int(clip.y * 100),
                                              setter("y", 100.0), suffix="%"))
        self._form.addWidget(self._slider_row("不透明", 0, 100, int(clip.opacity * 100),
                                              setter("opacity", 100.0), suffix="%"))

        en = QCheckBox("启用该片段")
        en.setChecked(clip.enabled)
        en.toggled.connect(lambda v: (setattr(clip, "enabled", v), self.changed.emit()))
        self._form.addWidget(en)

        if clip.path and Path(clip.path).is_file():
            pm = QPixmap(clip.path)
            if not pm.isNull():
                thumb = QLabel()
                thumb.setPixmap(pm.scaledToWidth(250, Qt.SmoothTransformation))
                thumb.setAlignment(Qt.AlignCenter)
                self._form.addWidget(thumb)

        del_btn = QPushButton("删除该片段")
        del_btn.setObjectName("Danger")
        del_btn.setIcon(icons.icon("trash"))
        del_btn.clicked.connect(lambda: self.deleteRequested.emit(clip))
        self._form.addWidget(del_btn)


# ---------------- 导出对话框 ----------------
class _SearchThread(QThread):
    found = Signal(str)

    def __init__(self, roots: list[str], parent=None) -> None:
        super().__init__(parent)
        self.roots = roots

    def run(self) -> None:
        p = exporter.search_ffmpeg(self.roots)
        self.found.emit(str(p) if p else "")


class ExportDialog(QDialog):
    def __init__(self, project: Project, settings_store: dict, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.store = settings_store
        self.job: exporter.ExportJob | None = None
        self.cv_job: exporter.CvExportThread | None = None
        self._search: _SearchThread | None = None
        self._encoders: set[str] = set()

        self.setWindowTitle("导出视频")
        self.setMinimumWidth(560)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("导出视频")
        title.setStyleSheet("font-size:15px; font-weight:700;")
        root.addWidget(title)
        sub = _small(f"素材：{project.active_name}  ·  {fmt_tc(project.duration, project.fps)}  ·  "
                     f"{len([c for c in project.blurs if c.enabled])} 个模糊片段  ·  "
                     f"{len([c for c in project.images if c.enabled])} 个图片")
        root.addWidget(sub)
        root.addWidget(_hsep())

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        out_row = QWidget()
        oh = QHBoxLayout(out_row)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.setSpacing(6)
        stored = settings_store.get("last_output") or ""
        default_out = exporter.default_output(project)
        stem = Path(project.video_path).stem if project.video_path else ""
        out_path = stored if (stored and stem and Path(stored).stem.startswith(stem)) else default_out
        self.out_edit = QLineEdit(out_path)
        self.out_edit.setCursorPosition(0)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_out)
        oh.addWidget(self.out_edit, 1)
        oh.addWidget(btn_browse)
        form.addRow("输出文件", out_row)

        self.cmb_encoder = QComboBox()
        for key, label in exporter.ENCODERS.items():
            self.cmb_encoder.addItem(label, key)
        self.cmb_encoder.currentIndexChanged.connect(self._on_encoder)
        form.addRow("编码器", self.cmb_encoder)

        self.cmb_preset = QComboBox()
        form.addRow("预设", self.cmb_preset)

        q_row = QWidget()
        qh = QHBoxLayout(q_row)
        qh.setContentsMargins(0, 0, 0, 0)
        qh.setSpacing(8)
        self.sld_quality = QSlider(Qt.Horizontal)
        self.sld_quality.setRange(0, 51)
        self.sld_quality.setValue(int(settings_store.get("quality", 18)))
        self.lbl_quality = QLabel(str(self.sld_quality.value()))
        self.lbl_quality.setObjectName("Value")
        self.lbl_quality.setFixedWidth(24)
        self.sld_quality.valueChanged.connect(lambda v: self.lbl_quality.setText(str(v)))
        qh.addWidget(self.sld_quality, 1)
        qh.addWidget(self.lbl_quality)
        qh.addWidget(_small("数值越小画质越高"))
        form.addRow("质量 CRF", q_row)

        self.cmb_container = QComboBox()
        for c in ("mp4", "mkv", "mov"):
            self.cmb_container.addItem(c.upper(), c)
        form.addRow("容器", self.cmb_container)

        self.cmb_audio = QComboBox()
        self.cmb_audio.addItem("直接复制（最快）", "copy")
        self.cmb_audio.addItem("AAC 192k", "aac")
        self.cmb_audio.addItem("不要音轨", "none")
        form.addRow("音频", self.cmb_audio)

        self.cmb_height = QComboBox()
        self.cmb_height.addItem("原始分辨率", 0)
        self.cmb_height.addItem("1080p", 1080)
        self.cmb_height.addItem("720p", 720)
        self.cmb_height.addItem("480p", 480)
        form.addRow("分辨率", self.cmb_height)

        ff_row = QWidget()
        fh = QHBoxLayout(ff_row)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(6)
        self.ff_edit = QLineEdit(settings_store.get("ffmpeg", ""))
        self.ff_edit.setPlaceholderText("ffmpeg.exe 路径（留空自动查找）")
        btn_ff = QPushButton("浏览…")
        btn_ff.clicked.connect(self._browse_ff)
        btn_auto = QPushButton("自动搜索")
        btn_auto.clicked.connect(self._auto_search)
        fh.addWidget(self.ff_edit, 1)
        fh.addWidget(btn_ff)
        fh.addWidget(btn_auto)
        form.addRow("ffmpeg", ff_row)

        root.addLayout(form)

        self.lbl_ff = _small("")
        root.addWidget(self.lbl_ff)
        root.addWidget(_hsep())

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.lbl_status = _small("准备就绪")
        root.addWidget(self.lbl_status)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_open = QPushButton("打开输出目录")
        self.btn_open.setObjectName("Ghost")
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setVisible(False)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_start = QPushButton("开始导出")
        self.btn_start.setObjectName("Accent")
        self.btn_start.setIcon(icons.icon("export"))
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_start)
        root.addLayout(row)

        # 恢复上次设置
        self.cmb_container.setCurrentIndex(max(0, self.cmb_container.findData(
            settings_store.get("container", "mp4"))))
        self.cmb_audio.setCurrentIndex(max(0, self.cmb_audio.findData(
            settings_store.get("audio", "copy"))))
        self.cmb_height.setCurrentIndex(max(0, self.cmb_height.findData(
            int(settings_store.get("height", 0)))))
        self.cmb_encoder.setCurrentIndex(max(0, self.cmb_encoder.findData(
            settings_store.get("encoder", "libx264"))))
        self._on_encoder()
        self._detect_ffmpeg()
        # 没存过偏好时，有可用 NVENC 就默认用它（比 CPU 快很多）
        if not settings_store.get("encoder"):
            idx = self.cmb_encoder.findData("h264_nvenc")
            item = self.cmb_encoder.model().item(idx) if idx >= 0 else None
            if item is not None and item.isEnabled():
                self.cmb_encoder.setCurrentIndex(idx)

    # ---- ffmpeg ----
    def _detect_ffmpeg(self) -> None:
        path = exporter.find_ffmpeg(self.ff_edit.text().strip() or None)
        if path is None:
            self.lbl_ff.setText("未找到 ffmpeg：将使用内置逐帧渲染（无音频、仅 mp4v）")
            self.lbl_ff.setStyleSheet("color:#e0a35c;")
            return
        self.ff_edit.setText(str(path))
        self.ff_edit.setCursorPosition(0)
        self._encoders = exporter.probe_encoders(path)
        ver = exporter.ffmpeg_version_text(path)
        gpu_ok = exporter.nvenc_usable(path)
        ok = [k for k in exporter.ENCODERS if k in self._encoders and
              (gpu_ok or not k.endswith("nvenc"))]
        tip = f"已找到 ffmpeg {ver}：{path}  ·  可用编码器 {len(ok)} 个"
        if not gpu_ok and any(k.endswith("nvenc") for k in self._encoders):
            tip += "  ·  ffmpeg 版本较旧，NVENC 已禁用（请用 x264 / x265）"
        self.lbl_ff.setText(tip)
        self.lbl_ff.setStyleSheet(f"color:{theme.MUTED};")

        model = self.cmb_encoder.model()
        for i in range(self.cmb_encoder.count()):
            key = self.cmb_encoder.itemData(i)
            item = model.item(i)
            if item is None:
                continue
            enabled = key in self._encoders and (gpu_ok or not key.endswith("nvenc"))
            item.setEnabled(enabled)
            item.setText(exporter.ENCODERS[key] + ("" if enabled else "（不可用）"))
        cur_idx = self.cmb_encoder.currentIndex()
        cur_item = model.item(cur_idx) if cur_idx >= 0 else None
        if cur_item is None or not cur_item.isEnabled():
            idx = self.cmb_encoder.findData("libx264")
            if idx >= 0 and model.item(idx) is not None and model.item(idx).isEnabled():
                self.cmb_encoder.setCurrentIndex(idx)
        self._on_encoder()

    def _browse_ff(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 ffmpeg.exe", "", "可执行文件 (*.exe)")
        if path:
            self.ff_edit.setText(path)
            self._detect_ffmpeg()

    def _auto_search(self) -> None:
        self.lbl_ff.setText("正在搜索 ffmpeg.exe（C:/ D:/ E:/ 浅层扫描，最多 12 秒）…")
        self._search = _SearchThread(["C:/", "D:/", "E:/"], self)
        self._search.found.connect(self._on_search)
        self._search.start()

    def _on_search(self, path: str) -> None:
        if path:
            self.ff_edit.setText(path)
            self._detect_ffmpeg()
        else:
            self.lbl_ff.setText("没有搜索到 ffmpeg.exe，请手动指定或使用内置渲染")
            self.lbl_ff.setStyleSheet("color:#e0a35c;")

    def _on_encoder(self, *_a) -> None:
        key = self.cmb_encoder.currentData() or "libx264"
        ff = self.ff_edit.text().strip() or None
        presets = exporter.presets_for(key, ff)
        cur = self.cmb_preset.currentText()
        self.cmb_preset.clear()
        self.cmb_preset.addItems(presets)
        self.cmb_preset.setCurrentText(
            cur if cur in presets else exporter.default_preset(key, ff))

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出到", self.out_edit.text(), "视频文件 (*.mp4 *.mkv *.mov)")
        if path:
            self.out_edit.setText(path)

    # ---- 导出 ----
    def _settings(self) -> ExportSettings:
        return ExportSettings(
            ffmpeg=self.ff_edit.text().strip(),
            encoder=self.cmb_encoder.currentData() or "libx264",
            quality=int(self.sld_quality.value()),
            preset=self.cmb_preset.currentText() or "medium",
            container=self.cmb_container.currentData() or "mp4",
            audio=self.cmb_audio.currentData() or "copy",
            height=int(self.cmb_height.currentData() or 0),
        )

    def _start(self) -> None:
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "导出", "请先选择输出文件路径")
            return
        try:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "导出", f"输出目录不可用：{exc}")
            return

        s = self._settings()
        self.store.update({"last_output": out, "encoder": s.encoder, "quality": s.quality,
                           "container": s.container, "audio": s.audio, "height": s.height,
                           "ffmpeg": s.ffmpeg})

        ff = exporter.find_ffmpeg(s.ffmpeg or None)
        if ff is not None:
            s.ffmpeg = str(ff)
            cmd = exporter.build_command(s, self.project, out)
            self.job = exporter.ExportJob(cmd, self.project.duration, self)
            self.job.progress.connect(self._on_progress)
            self.job.logLine.connect(lambda line: self.lbl_status.setText(line[:120]))
            self.job.finished_ok.connect(self._on_done)
            self.job.failed.connect(self._on_fail)
            self.job.start()
        else:
            self.cv_job = exporter.CvExportThread(self.project, out, self)
            self.cv_job.progress.connect(self._on_progress)
            self.cv_job.finished_ok.connect(self._on_done)
            self.cv_job.failed.connect(self._on_fail)
            self.cv_job.start()
            self.lbl_status.setText("未找到 ffmpeg，使用内置逐帧渲染（无音频）…")

        self._busy(True)

    def _busy(self, busy: bool) -> None:
        self.btn_start.setEnabled(not busy)
        self.btn_start.setText("导出中…" if busy else "开始导出")
        self.btn_cancel.setText("停止" if busy else "取消")
        for w in (self.cmb_encoder, self.cmb_preset, self.sld_quality, self.cmb_container,
                  self.cmb_audio, self.cmb_height, self.out_edit, self.ff_edit):
            w.setEnabled(not busy)

    def _on_progress(self, frac: float, text: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.lbl_status.setText(text)

    def _on_done(self, path: str) -> None:
        self._busy(False)
        self.progress.setValue(1000)
        self.lbl_status.setText(f"完成：{path}")
        self.btn_open.setVisible(True)
        self.btn_open.setProperty("out", path)

    def _on_fail(self, msg: str) -> None:
        self._busy(False)
        self.lbl_status.setText(f"失败：{msg}")
        self.progress.setValue(0)

    def _open_folder(self) -> None:
        path = self.btn_open.property("out") or self.out_edit.text()
        try:
            os.startfile(str(Path(path).parent))  # type: ignore[attr-defined]
        except OSError:
            pass

    def _cancel(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self.job = None
            self._busy(False)
        elif self.cv_job is not None:
            self.cv_job.cancel()
            self.cv_job = None
            self._busy(False)
        else:
            self.reject()

    def closeEvent(self, e) -> None:
        if self.job is not None or self.cv_job is not None:
            self._cancel()
        super().closeEvent(e)
