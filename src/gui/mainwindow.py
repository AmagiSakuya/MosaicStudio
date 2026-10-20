"""主窗口：把工程、素材库、播放器、时间线、检测、导出串起来。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QScrollArea, QSplitter,
    QStatusBar, QToolButton, QVBoxLayout, QWidget,
)

from .. import models as model_repo
from .. import runtime_info
from ..detector import RatingDetector
from ..paths import app_dir, find_model_dir
from . import icons, projects
from .model import LEGACY_SUFFIX, PROJECT_EXT, MediaAsset, Project, fmt_tc, is_media
from .panels import AssetLibrary, ClipList, DetectPanel, ExportDialog, Inspector
from .player import FilmstripWorker, Player
from .runtime_dialog import RuntimeDialog
from .scan import scan_video
from .startup import MissingMediaDialog
from .timeline import Timeline
from .toast import Toast

APP_TITLE = "Mosaic Studio"
MEDIA_FILTER = (
    "所有支持的素材 (*.mp4 *.mkv *.avi *.mov *.flv *.webm *.wmv *.ts *.m4v *.mpg *.mpeg *.rmvb "
    "*.png *.jpg *.jpeg *.webp *.bmp *.gif);;"
    "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.webm *.wmv *.ts *.m4v *.mpg *.mpeg *.rmvb);;"
    "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)"
)


class ScanWorker(QThread):
    progress = Signal(float, str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, video: str, params: dict, parent=None) -> None:
        super().__init__(parent)
        self.video = video
        self.params = params
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            model_dir = find_model_dir()
            detector = RatingDetector(
                model_dir,
                variant=self.params["variant"],
                threads=0,
                fit="crop",
                device=self.params["device"],
            )
            payload = scan_video(
                Path(self.video), detector,
                level=self.params["level"],
                threshold=self.params["threshold"],
                fps=self.params["fps"],
                min_duration=self.params["min_duration"],
                gap=self.params["gap"],
                smooth=self.params["smooth"],
                batch=8,
                progress=lambda f, m: self.progress.emit(f, m),
                should_cancel=lambda: self._cancel,
            )
            self.done.emit(payload)
        except InterruptedError:
            self.failed.emit("已取消检测")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class RuntimeProbe(QThread):
    """后台探测显卡 / 推理引擎（PowerShell + nvidia-smi 要 1 秒左右，不能卡界面）。"""

    ready = Signal(dict)

    def run(self) -> None:
        try:
            self.ready.emit(runtime_info.summary())
        except Exception:  # noqa: BLE001 - 探测失败就当没检测到
            self.ready.emit({})


class TimelinePanel(QFrame):
    """时间线容器：标题栏 + 缩放按钮 + 时间线控件。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(34)
        bar.setStyleSheet("background:#1c1c22; border-top-left-radius:8px; border-top-right-radius:8px;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 8, 4)
        h.setSpacing(6)
        title = QLabel("时间线")
        title.setObjectName("CardTitle")
        h.addWidget(title)
        hint = QLabel("拖动移动 · 边缘裁剪 · 模糊轨空白处拖出新片段 · 图片轨 Alt+拖动复制 · Ctrl+滚轮缩放")
        hint.setObjectName("Tiny")
        h.addWidget(hint)
        h.addStretch(1)

        self.btn_zoom_out = self._tool("zoom_out", "缩小")
        self.btn_zoom_in = self._tool("zoom_in", "放大")
        self.btn_fit = self._tool("fit", "适应窗口")
        for b in (self.btn_zoom_out, self.btn_zoom_in, self.btn_fit):
            h.addWidget(b)

        self.timeline = Timeline()
        lay.addWidget(bar)
        lay.addWidget(self.timeline, 1)

        self.btn_zoom_out.clicked.connect(lambda: self.timeline.zoom(1 / 1.3))
        self.btn_zoom_in.clicked.connect(lambda: self.timeline.zoom(1.3))
        self.btn_fit.clicked.connect(self.timeline.fit)

    def _tool(self, name: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(name))
        b.setIconSize(QSize(15, 15))
        b.setToolTip(tip)
        b.setFixedSize(26, 24)
        return b


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.base = app_dir()
        self.settings = projects.load_settings()

        self.project: Optional[Project] = None
        self.project_path: Optional[str] = None
        self._strip_worker: Optional[FilmstripWorker] = None
        self._stale_workers: list[FilmstripWorker] = []
        self._scan: Optional[ScanWorker] = None
        self._probe: Optional[RuntimeProbe] = None
        self._import_dir = str(projects.default_project_dir())

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(icons.icon("blur", size=64))
        self.resize(1660, 980)
        self.setMinimumSize(1180, 760)

        self._build_ui()
        self._build_actions()
        self._build_menu()
        self._build_status()
        self._sync_project_ui()

        # 悬浮提示条（保存成功等反馈）
        self.toast = Toast(self.centralWidget())
        self.detect_panel.refresh_models()
        QTimer.singleShot(250, self._probe_runtime)

    # ---------------- 界面 ----------------
    def _build_ui(self) -> None:
        self.player = Player()
        self.player.positionChanged.connect(self._on_position)
        self.player.videoOpened.connect(self._on_video_opened)
        self.player.playStateChanged.connect(self._on_play_state)

        self.library = AssetLibrary()
        self.library.activateRequested.connect(self.activate_asset)
        self.library.importRequested.connect(lambda: self.import_assets())
        self.library.removeRequested.connect(self.remove_asset)
        self.library.relinkRequested.connect(self.relink_asset)
        self.library.refreshRequested.connect(self.refresh_assets)

        self.detect_panel = DetectPanel()
        self.detect_panel.detectRequested.connect(self._start_detect)
        self.detect_panel.cancelRequested.connect(self._cancel_detect)
        self.detect_panel.manageRequested.connect(self.open_runtime_manager)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(8)
        lv.addWidget(self.library, 1)
        lv.addWidget(self.detect_panel, 0)

        self.timeline_panel = TimelinePanel()
        self.timeline = self.timeline_panel.timeline
        self.timeline.seeked.connect(self._on_seek)
        self.timeline.selectionChanged.connect(self._on_selection)
        self.timeline.clipsChanged.connect(self._on_clips_changed)
        self.timeline.editFinished.connect(self._on_edit_finished)
        self.timeline.message.connect(self._status)
        self.timeline.filesDropped.connect(self._on_files_dropped)

        center = QSplitter(Qt.Vertical)
        center.setChildrenCollapsible(False)
        center.addWidget(self.player)
        center.addWidget(self.timeline_panel)
        center.setStretchFactor(0, 3)
        center.setStretchFactor(1, 2)
        center.setSizes([520, 330])

        self.clip_list = ClipList()
        self.clip_list.addBlur.connect(self.add_blur_at_playhead)
        self.clip_list.addImage.connect(self.add_image_at_playhead)
        self.clip_list.selectRequested.connect(self._select_clip)
        self.clip_list.toggleRequested.connect(self._on_clip_toggled)
        self.clip_list.bind_project(None)

        self.inspector = Inspector()
        self.inspector.changed.connect(self._on_inspector_changed)
        self.inspector.deleteRequested.connect(self._delete_clip)
        self.clip_list.rebuild(None)

        insp_scroll = QScrollArea()
        insp_scroll.setWidgetResizable(True)
        insp_scroll.setWidget(self.inspector)
        insp_scroll.setFrameShape(QFrame.NoFrame)
        insp_scroll.setMinimumHeight(180)

        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self.clip_list)
        right.addWidget(insp_scroll)
        right.setSizes([300, 420])
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)

        right_wrap = QWidget()
        rv = QVBoxLayout(right_wrap)
        rv.setContentsMargins(4, 8, 8, 8)
        rv.setSpacing(0)
        rv.addWidget(right)

        root = QSplitter(Qt.Horizontal)
        root.setChildrenCollapsible(False)
        root.addWidget(left)
        root.addWidget(center)
        root.addWidget(right_wrap)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        root.setSizes([320, 990, 350])

        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(self._build_toolbar())
        wl.addWidget(root, 1)
        self.setCentralWidget(wrap)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet("background:#17171b; border-bottom:1px solid #26262e;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 6, 12, 6)
        h.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(icons.pixmap("blur", size=26))
        h.addWidget(logo)

        name = QLabel("MOSAIC")
        name.setStyleSheet("font-size:15px; font-weight:800; letter-spacing:2px; color:#f0f0f5;")
        h.addWidget(name)
        sub = QLabel("STUDIO")
        sub.setStyleSheet("font-size:11px; font-weight:600; letter-spacing:2px; color:#6b6b7a;")
        h.addWidget(sub)

        h.addSpacing(18)
        self.btn_open_project = self._tool_button("folder", "打开工程", self.open_project)
        h.addWidget(self.btn_open_project)
        self.btn_save = self._tool_button("save", "保存工程", self.save_project)
        h.addWidget(self.btn_save)

        h.addStretch(1)

        self.lbl_project = QLabel("未打开工程")
        self.lbl_project.setObjectName("Muted")
        h.addWidget(self.lbl_project)

        h.addSpacing(12)
        self.btn_export = QToolButton()
        self.btn_export.setIcon(icons.icon("export"))
        self.btn_export.setText(" 导出视频")
        self.btn_export.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_export.setStyleSheet(
            "QToolButton{background:#3b74e0;border:1px solid #4d8dff;border-radius:6px;"
            "padding:6px 14px;color:#fff;font-weight:600;}"
            "QToolButton:hover{background:#4d8dff;}"
            "QToolButton:disabled{background:#2a3550;border-color:#35456a;color:#7f8aa5;}")
        self.btn_export.clicked.connect(self.export_video)
        self.btn_export.setEnabled(False)
        h.addWidget(self.btn_export)
        return bar

    def _tool_button(self, icon: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(icon))
        b.setText(f" {tip}")
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    def _build_actions(self) -> None:
        self.act_new = QAction("新建工程…", self, shortcut=QKeySequence("Ctrl+N"),
                               triggered=self.new_project)
        self.act_open_project = QAction("打开工程…", self, shortcut=QKeySequence("Ctrl+O"),
                                        triggered=self.open_project)
        self.act_save = QAction("保存工程", self, shortcut=QKeySequence("Ctrl+S"),
                                triggered=self.save_project)
        self.act_save_as = QAction("工程另存为…", self, shortcut=QKeySequence("Ctrl+Shift+S"),
                                   triggered=lambda: self.save_project(save_as=True))
        self.act_import = QAction("导入素材…", self, shortcut=QKeySequence("Ctrl+I"),
                                  triggered=lambda: self.import_assets())
        self.act_runtime = QAction("运行时与模型管理…", self, shortcut=QKeySequence("Ctrl+M"),
                                   triggered=self.open_runtime_manager)
        self.act_export = QAction("导出视频…", self, shortcut=QKeySequence("Ctrl+E"),
                                  triggered=self.export_video)
        self.act_quit = QAction("退出", self, shortcut=QKeySequence("Ctrl+Q"), triggered=self.close)
        self.act_add_blur = QAction("在播放头添加模糊片段", self, shortcut=QKeySequence("B"),
                                    triggered=self.add_blur_at_playhead)
        self.act_add_image = QAction("添加图片到时间线…", self, shortcut=QKeySequence("I"),
                                     triggered=self.add_image_at_playhead)
        self.act_fit = QAction("时间线适应窗口", self, shortcut=QKeySequence("F"),
                               triggered=lambda: self.timeline.fit())
        self.act_delete = QAction("删除选中片段", self, shortcut=QKeySequence("Del"),
                                  triggered=self.delete_selected)
        self.act_help = QAction("使用说明", self, triggered=self.show_help)
        for a in (self.act_new, self.act_open_project, self.act_save, self.act_save_as,
                  self.act_import, self.act_runtime, self.act_export, self.act_add_blur,
                  self.act_add_image, self.act_delete):
            self.addAction(a)

        self._shortcut("Space", self._toggle_play)
        self._shortcut("Left", lambda: self._nudge(-1))
        self._shortcut("Right", lambda: self._nudge(1))
        self._shortcut("Home", lambda: self.player.seek(0.0))
        self._shortcut("End", lambda: self.player.seek(max(0.0, self.player.duration - 0.04)))
        self._shortcut("Ctrl+=", lambda: self.timeline.zoom(1.3))
        self._shortcut("Ctrl+-", lambda: self.timeline.zoom(1 / 1.3))

    def _shortcut(self, key: str, fn) -> None:
        sc = QShortcut(QKeySequence(key), self)
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(fn)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("文件")
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_open_project)
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addSeparator()
        m_file.addAction(self.act_import)
        m_file.addSeparator()
        m_file.addAction(self.act_runtime)
        m_file.addSeparator()
        m_file.addAction(self.act_export)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu("编辑")
        m_edit.addAction(self.act_add_blur)
        m_edit.addAction(self.act_add_image)
        m_edit.addAction(self.act_delete)

        m_view = mb.addMenu("视图")
        m_view.addAction(self.act_fit)
        act_preview = QAction("显示模糊预览", self, checkable=True, checked=True)
        act_preview.toggled.connect(self._toggle_preview)
        m_view.addAction(act_preview)
        act_sound = QAction("播放声音", self, checkable=True, checked=True)
        act_sound.toggled.connect(self._toggle_sound)
        m_view.addAction(act_sound)

        m_help = mb.addMenu("帮助")
        m_help.addAction(self.act_help)

    def _build_status(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.lbl_msg = QLabel("就绪")
        self.lbl_msg.setObjectName("Muted")
        bar.addWidget(self.lbl_msg, 1)
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("Muted")
        bar.addPermanentWidget(self.lbl_info)

    # ---------------- 工程 ----------------
    def new_project(self, path: str | Path | None = None) -> bool:
        if not self._maybe_save():
            return False
        if path is None:
            default = projects.unique_project_path(projects.default_project_dir(), "未命名工程")
            chosen, _ = QFileDialog.getSaveFileName(
                self, "新建工程（选择保存位置）", str(default),
                f"Mosaic 工程 (*{PROJECT_EXT})")
            if not chosen:
                return False
            if not chosen.lower().endswith(PROJECT_EXT):
                chosen += PROJECT_EXT
            path = chosen
        try:
            proj = projects.create_project(path)
        except OSError as exc:
            QMessageBox.warning(self, "新建工程", f"创建失败：{exc}")
            return False
        self._adopt_project(proj)
        self._status(f"已新建工程：{proj.name}（下一步：导入视频/图片素材）")
        return True

    def open_project(self) -> bool:
        if not self._maybe_save():
            return False
        start = str(Path(self.project_path).parent) if self.project_path else str(
            projects.default_project_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", start,
            f"Mosaic 工程 (*{PROJECT_EXT} *{LEGACY_SUFFIX});;所有文件 (*)")
        if not path:
            return False
        return self.open_project_file(path)

    def open_project_file(self, path: str) -> bool:
        if self.project is not None and not self._maybe_save():
            return False
        try:
            proj = Project.load(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "打开工程", f"读取失败：{exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程", f"工程文件解析失败：{exc}")
            return False
        self._adopt_project(proj)
        return True

    def open_scratch(self, paths: list[str]) -> None:
        """命令行/自检用：不创建工程文件，直接把素材当临时工程打开。"""
        proj = Project(name="临时工程")
        self._adopt_project(proj)
        self.import_assets(paths)

    def _adopt_project(self, proj: Project) -> None:
        self._stop_scan()
        self.player.close_video()
        self.project = proj
        self.project_path = proj.path or None
        if self.project_path:
            projects.touch_recent(self.settings, self.project_path)

        self.library.bind_project(proj)
        self._sync_project_ui()
        self._status(f"已打开工程：{proj.name} · {len(proj.assets)} 个素材")

        missing = proj.missing_assets
        if missing:
            QTimer.singleShot(100, self._prompt_missing)

        active = proj.active
        if active is not None and active.is_video and active.exists:
            self._open_active_video()
        elif active is not None:
            self._status(f"当前素材：{active.name}（{len(proj.assets)} 个素材）")

    def _open_active_video(self) -> None:
        asset = self.project.active if self.project else None
        if asset is None or not asset.is_video or not asset.exists:
            return
        if not self.player.open_video(asset.path):
            self._status(f"无法解码：{asset.name}")
            QMessageBox.warning(self, "打开素材", f"无法解码该视频：\n{asset.path}")
            return
        self.player.set_project(self.project)
        self.player.invalidate_images()
        self.timeline.set_project(self.project)
        self.timeline.set_playhead(self.player.position)
        self.clip_list.bind_project(self.project)
        self.clip_list.rebuild(self.project)
        self.inspector.set_clip(None, self.project)
        self.btn_export.setEnabled(True)
        self._start_filmstrip()
        self._update_title()
        self._update_info()
        self._update_detect_target()

    def save_project(self, save_as: bool = False) -> bool:
        if self.project is None:
            return False
        target = self.project.path
        if save_as or not target:
            default = target or str(projects.unique_project_path(
                projects.default_project_dir(), self.project.name or "未命名工程"))
            chosen, _ = QFileDialog.getSaveFileName(
                self, "保存工程", default, f"Mosaic 工程 (*{PROJECT_EXT})")
            if not chosen:
                return False
            target = chosen
            self.project.name = Path(target).stem or self.project.name
        try:
            saved = self.project.save(target)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存工程", f"保存失败：{exc}")
            return False
        self.project_path = saved
        projects.touch_recent(self.settings, saved)
        self.library.rebuild()
        self._update_title()
        self._status(f"工程已保存：{Path(saved).name}")
        if getattr(self, "toast", None) is not None:
            self.toast.show_message(f"工程已保存 · {Path(saved).name}")
        return True

    def _autosave(self) -> None:
        if self.project is None or not self.project.path:
            return
        try:
            self.project.save()
        except OSError:
            return
        projects.touch_recent(self.settings, self.project.path)
        self._update_title()

    def _maybe_save(self) -> bool:
        """有未保存修改时询问；返回 False 表示用户取消。"""
        if self.project is None or not self.project.dirty:
            return True
        ret = QMessageBox.question(
            self, "保存工程",
            f"工程「{self.project.name}」有未保存的修改，要保存吗？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if ret == QMessageBox.Cancel:
            return False
        if ret == QMessageBox.Save:
            return self.save_project()
        return True

    # ---------------- 素材 ----------------
    def import_assets(self, paths: Optional[list[str]] = None,
                      activate: bool = True) -> list[MediaAsset]:
        if self.project is None:
            QMessageBox.information(self, "导入素材", "请先新建或打开一个工程")
            return []
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "导入视频 / 图片素材", self._import_dir, MEDIA_FILTER)
            if not paths:
                return []
            self._import_dir = str(Path(paths[0]).parent)

        added: list[MediaAsset] = []
        skipped: list[str] = []
        for p in paths:
            if not Path(p).is_file() or not is_media(p):
                skipped.append(Path(p).name)
                continue
            before = len(self.project.assets)
            asset = self.project.add_asset(p)
            if len(self.project.assets) > before:
                added.append(asset)

        self.library.rebuild()
        self._update_title()
        self._update_detect_target()

        if added:
            names = "、".join(a.name for a in added[:3])
            more = f" 等 {len(added)} 个" if len(added) > 3 else ""
            self._status(f"已导入素材：{names}{more}")
        if skipped:
            self._status(f"已跳过不支持的文件：{'、'.join(skipped[:3])}")

        # 工程里还没有可用视频时，自动加载第一个导入的视频（检测中不切素材）
        if activate and not (self._scan is not None and self._scan.isRunning()):
            first_video = next((a for a in added if a.is_video), None)
            active = self.project.active
            if first_video is not None and (active is None or not active.is_video):
                self.activate_asset(first_video.id)
            elif active is not None and active.is_video and self.player.current_path != active.path:
                self._open_active_video()
                self.library.rebuild()
        return added

    def activate_asset(self, asset_id: str) -> None:
        """双击素材：视频=加载到工作区，图片=添加到时间线。"""
        if self.project is None:
            return
        asset = self.project.asset_by_id(asset_id)
        if asset is None:
            return
        if not asset.exists:
            if self._ask_relink(asset):
                return
            return
        if not asset.is_video:
            self.add_image_asset(asset, self.timeline.playhead)
            return
        if self._scan is not None and self._scan.isRunning():
            QMessageBox.information(self, "检测中", "请等待当前检测完成或取消后再切换素材")
            return
        self.project.activate(asset)
        self._open_active_video()
        self.library.rebuild()
        self._status(f"已加载 {asset.name} · {fmt_tc(asset.duration, asset.fps)}")

    def add_image_asset(self, asset: MediaAsset, at: Optional[float] = None) -> None:
        if not self._require_video():
            return
        t = self.timeline.playhead if at is None else max(0.0, at)
        end = min(self.project.duration, t + 3.0)
        if end - t < 0.1:
            t = max(0.0, self.project.duration - 3.0)
            end = self.project.duration
        clip = self.project.add_image(asset.path, t, end, asset_id=asset.id)
        if clip is None:
            return
        self.player.invalidate_images()
        self.player.refresh_effects()
        self.timeline.set_selected(clip)
        self.timeline.clipsChanged.emit()
        self.timeline.editFinished.emit()
        self._status(f"已把图片「{asset.name}」加到时间线 {fmt_tc(t, self.project.fps)}")

    def remove_asset(self, asset_id: str) -> None:
        if self.project is None:
            return
        asset = self.project.asset_by_id(asset_id)
        if asset is None:
            return
        ret = QMessageBox.question(
            self, "移除素材",
            f"从工程里移除「{asset.name}」？\n"
            f"（只删除工程中的引用，磁盘文件不受影响；该素材上的 "
            f"{len(asset.blurs)} 个模糊片段 / {len(asset.images)} 个图片片段也会一起移除）")
        if ret != QMessageBox.Yes:
            return
        was_active = self.project.active is asset
        self.project.remove_asset(asset)
        self.library.rebuild()
        self._update_title()
        if was_active:
            self.player.close_video()
            self.btn_export.setEnabled(False)
            self._sync_project_ui()
            if self.project.active is not None and self.project.active.is_video:
                self._open_active_video()
        self._status(f"已移除素材：{asset.name}")

    def relink_asset(self, asset_id: str) -> None:
        if self.project is None:
            return
        asset = self.project.asset_by_id(asset_id)
        if asset is None:
            return
        self._ask_relink(asset)

    def _ask_relink(self, asset: MediaAsset) -> bool:
        """素材文件缺失时提示重新定位。返回 True 表示已处理（重定位或用户放弃）。"""
        old = Path(asset.path) if asset.path else None
        start = str(old.parent) if old and old.parent.is_dir() else self._import_dir
        ret = QMessageBox.question(
            self, "素材缺失",
            f"找不到素材文件：\n{asset.path or '（空路径）'}\n\n"
            "文件可能被移动、改名或删除。要重新定位吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return False
        kind = "视频" if asset.is_video else "图片"
        path, _ = QFileDialog.getOpenFileName(
            self, f"重新定位「{asset.name}」", start, f"{kind} (*);;所有文件 (*)")
        if not path:
            return False
        self.project.relink_asset(asset, path)
        self.library.rebuild()
        self._update_title()
        if self.project.active is asset and asset.is_video:
            self._open_active_video()
        self._status(f"已重新定位：{asset.name}")
        return True

    def refresh_assets(self) -> None:
        if self.project is None:
            return
        for asset in self.project.assets:
            if asset.exists:
                asset.probe()
        self.library.rebuild()
        self._update_title()
        self._update_detect_target()
        missing = len(self.project.missing_assets)
        if missing:
            self._status(f"刷新完成 · 仍有 {missing} 个素材文件缺失")
            self._prompt_missing()
        else:
            self._status("刷新完成 · 所有素材文件都在")

    def _prompt_missing(self) -> None:
        if self.project is None or not self.project.missing_assets:
            return
        dlg = MissingMediaDialog(self.project, self)
        dlg.exec()
        self.library.rebuild()
        self._update_title()
        if dlg.relinked:
            self._status(f"已重新定位 {dlg.relinked} 个素材")
            active = self.project.active
            if active is not None and active.is_video and active.exists:
                self._open_active_video()

    def _on_files_dropped(self, paths: list, t: float) -> None:
        if self.project is None:
            QMessageBox.information(self, "导入素材", "请先新建或打开一个工程")
            return
        added = self.import_assets([str(p) for p in paths], activate=True)
        for asset in added:
            if not asset.is_video:
                self.add_image_asset(asset, t)

    # ---------------- 播放 ----------------
    def _on_video_opened(self, duration: float, fps: float, w: int, h: int) -> None:
        asset = self.project.active if self.project else None
        if asset is None:
            return
        asset.duration = float(duration or asset.duration)
        asset.fps = float(fps or asset.fps)
        asset.width = int(w or asset.width)
        asset.height = int(h or asset.height)
        self._update_info()
        self._update_detect_target()

    def _start_filmstrip(self) -> None:
        if self.project is None or not self.project.has_video:
            return
        old = self._strip_worker
        if old is not None and old.isRunning():
            self._stale_workers.append(old)
            old.finished.connect(
                lambda o=old: self._stale_workers.remove(o) if o in self._stale_workers else None)
        self._strip_worker = FilmstripWorker(
            self.project.video_path, self.project.duration, count=16, height=90)
        self._strip_worker.ready.connect(lambda _p, imgs: self.timeline.set_filmstrip(imgs))
        self._strip_worker.start()

    # ---------------- 检测 ----------------
    def _update_detect_target(self) -> None:
        proj = self.project
        if proj is None:
            self.detect_panel.set_target("先新建或打开一个工程", False)
            return
        asset = proj.active
        if asset is None or not asset.is_video:
            self.detect_panel.set_target("当前没有视频素材（检测只针对视频）", False)
            return
        if not asset.exists:
            self.detect_panel.set_target(f"⚠ 素材文件缺失：{asset.name}", False)
            return
        info = f"目标：{asset.name} · {fmt_tc(asset.duration, asset.fps)}"
        if asset.detected:
            n = len([b for b in asset.blurs if b.source == "detect"])
            when = str((asset.detect or {}).get("scanned_at") or "")[:16].replace("T", " ")
            info += f" · 已检测 {n} 段"
            if when:
                info += f"（{when}）"
        self.detect_panel.set_target(info, True)

    def _start_detect(self, params: dict) -> None:
        if not self._require_video():
            return
        if self._scan is not None and self._scan.isRunning():
            return

        variant = str(params.get("variant") or "")
        st = model_repo.state(variant)
        if not st.installed:
            s = model_repo.spec(variant)
            ret = QMessageBox.question(
                self, "模型未下载",
                f"「{s.title}」还没下载（约 {s.size_mb} MB）。\n\n"
                "要现在打开「运行时管理」下载吗？（支持镜像站、断点续传）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.Yes:
                self.open_runtime_manager()
            return

        self.detect_panel.set_busy(True, "加载模型…")
        self._scan = ScanWorker(self.project.video_path, params, self)
        self._scan.progress.connect(self.detect_panel.set_progress)
        self._scan.done.connect(self._on_detect_done)
        self._scan.failed.connect(self._on_detect_failed)
        self._scan.start()
        self._status("正在检测…")

    def _on_detect_done(self, payload: dict) -> None:
        self.detect_panel.set_busy(False, "检测完成，结果已写入工程")
        if self.project is None:
            return
        count = self.project.apply_detection(payload)
        summary = payload.get("summary") or {}
        secs = float(summary.get("nsfw_seconds") or 0.0)
        self.player.set_project(self.project)
        self.player.invalidate_images()
        self.player.refresh_effects()
        self.timeline.set_project(self.project)
        self.timeline.set_playhead(self.player.position)
        self.clip_list.bind_project(self.project)
        self.clip_list.rebuild(self.project)
        self.inspector.set_clip(None, self.project)
        self.library.rebuild()
        self._update_info()
        self._update_detect_target()
        self._autosave()
        saved = "，工程已保存" if self.project.path else ""
        self._status(f"检测完成：{count} 段 · 共 {secs:.1f}s{saved}")

    def _cancel_detect(self) -> None:
        if self._scan is not None and self._scan.isRunning():
            self._scan.cancel()
            self._status("正在取消检测…")

    def _on_detect_failed(self, msg: str) -> None:
        self.detect_panel.set_busy(False, "已取消" if "取消" in msg else "检测失败")
        self._status(msg)
        if "取消" in msg:
            return
        QMessageBox.warning(self, "检测失败", msg)

    def _stop_scan(self) -> None:
        if self._scan is not None and self._scan.isRunning():
            self._scan.cancel()
            self._scan.wait(1500)
        self._scan = None

    # ---------------- 编辑 ----------------
    def add_blur_at_playhead(self) -> None:
        if not self._require_video():
            return
        t = self.timeline.playhead
        end = min(self.project.duration, t + 2.0)
        clip = self.project.add_blur(t, end, source="manual")
        if clip is None:
            return
        self.timeline.set_selected(clip)
        self.timeline.clipsChanged.emit()
        self.timeline.editFinished.emit()
        self._status(f"已在 {fmt_tc(t, self.project.fps)} 添加模糊片段")

    def add_image_at_playhead(self) -> None:
        if not self._require_video():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片（会同时加入素材库）", self._import_dir,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)")
        if not path:
            return
        self._import_dir = str(Path(path).parent)
        asset = self.project.add_asset(path)
        self.library.rebuild()
        self.add_image_asset(asset, self.timeline.playhead)

    def delete_selected(self) -> None:
        if self._text_focus() or self.project is None:
            return
        clip = self.timeline.selected
        if clip is None:
            return
        self._delete_clip(clip)

    def _delete_clip(self, clip) -> None:
        if self.project is None:
            return
        self.project.remove(clip)
        self.timeline.set_selected(None)
        self.timeline.clipsChanged.emit()
        self.timeline.editFinished.emit()
        self._status("已删除片段")

    def _select_clip(self, clip) -> None:
        self.timeline.set_selected(clip)
        self.player.seek(clip.start)
        self.timeline.set_playhead(clip.start)

    # ---------------- 事件 ----------------
    def _on_seek(self, t: float) -> None:
        self.player.seek(t)
        self.timeline.set_playhead(t)

    def _on_position(self, t: float) -> None:
        self.timeline.set_playhead(t, follow=self.player.is_playing)
        if self.project is not None:
            self._update_info()

    def _on_play_state(self, playing: bool) -> None:
        self._status("播放中…" if playing else "已暂停")

    def _on_selection(self, clip) -> None:
        self.inspector.set_clip(clip, self.project)
        if clip is not None and self.project is not None:
            self._status(f"选中 {clip.title} · {fmt_tc(clip.start, self.project.fps)} → "
                         f"{fmt_tc(clip.end, self.project.fps)}")

    def _on_clips_changed(self) -> None:
        if self.project is not None:
            self.project.dirty = True
            self._update_title()
        self.player.refresh_effects()
        self.timeline.update()
        self._update_info()

    def _on_edit_finished(self) -> None:
        self.clip_list.rebuild(self.project)
        self.inspector.set_clip(self.timeline.selected, self.project)

    def _on_inspector_changed(self) -> None:
        if self.project is not None:
            self.project.dirty = True
            self._update_title()
        self.player.refresh_effects()
        self.timeline.update()
        QTimer.singleShot(0, lambda: self.clip_list.rebuild(self.project))

    def _on_clip_toggled(self, _clip) -> None:
        if self.project is not None:
            self.project.dirty = True
            self._update_title()
        self.player.refresh_effects()
        self.timeline.update()
        QTimer.singleShot(0, lambda: self.clip_list.rebuild(self.project))

    def _toggle_preview(self, on: bool) -> None:
        self.player.chk_preview.setChecked(on)

    def _toggle_sound(self, on: bool) -> None:
        self.player.chk_sound.setChecked(on)

    def _toggle_play(self) -> None:
        if self._text_focus():
            return
        self.player.toggle_play()

    def _nudge(self, frames: int) -> None:
        if self._text_focus():
            return
        self.player.step(frames)

    # ---------------- 导出 ----------------
    def export_video(self) -> None:
        if not self._require_video():
            return
        self.player.set_playing(False)
        dlg = ExportDialog(self.project, self.settings, self)
        dlg.exec()
        projects.save_settings(self.settings)

    # ---------------- 运行时 / 模型 ----------------
    def _probe_runtime(self) -> None:
        if self._probe is not None and self._probe.isRunning():
            return
        self._probe = RuntimeProbe(self)
        self._probe.ready.connect(self._on_runtime_probe)
        self._probe.start()

    def _on_runtime_probe(self, info: dict) -> None:
        if not info:
            return
        self.detect_panel.apply_runtime(info)
        ort = info.get("ort") or {}
        if not ort.get("cuda") and not ort.get("dml"):
            self._status("运行环境：CPU 推理（可在「模型配置」右上角齿轮里启用 GPU 加速）")

    def open_runtime_manager(self) -> None:
        """模型下载 / GPU 运行环境管理。"""
        dlg = RuntimeDialog(self.settings, self)
        dlg.exec()
        projects.save_settings(self.settings)
        self.detect_panel.refresh_models()
        self._probe_runtime()
        self._status("运行时管理已关闭 · 配置已保存")

    # ---------------- 杂项 ----------------
    def _require_video(self) -> bool:
        if self.project is None:
            QMessageBox.information(self, "提示", "请先新建或打开一个工程")
            return False
        asset = self.project.active
        if asset is None or not asset.is_video:
            QMessageBox.information(self, "提示", "请先在素材库加载一个视频素材")
            return False
        if not asset.exists:
            QMessageBox.information(self, "素材缺失",
                                    f"视频文件不见了：\n{asset.path}\n\n请在素材库右键「重新定位」。")
            return False
        return True

    def _text_focus(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QAbstractSpinBox, QComboBox))

    def _status(self, msg: str) -> None:
        self.lbl_msg.setText(msg)

    def _sync_project_ui(self) -> None:
        """工程切换后统一刷新所有面板。"""
        proj = self.project
        self.library.bind_project(proj)
        self.clip_list.bind_project(proj)
        self.clip_list.rebuild(proj)
        self.inspector.set_clip(None, proj)
        self.timeline.set_project(proj)
        self.btn_export.setEnabled(bool(proj and proj.has_video))
        self._update_title()
        self._update_info()
        self._update_detect_target()

    def _update_title(self) -> None:
        if self.project is None:
            self.setWindowTitle(APP_TITLE)
            self.lbl_project.setText("未打开工程")
            return
        star = "*" if self.project.dirty else ""
        self.setWindowTitle(f"{self.project.name}{star} — {APP_TITLE}")
        path_hint = Path(self.project.path).name if self.project.path else "未保存"
        self.lbl_project.setText(f"{self.project.name}{star} · {path_hint}")

    def _update_info(self) -> None:
        if self.project is None:
            self.lbl_info.setText("")
            return
        p = self.project
        asset = p.active
        if asset is None:
            self.lbl_info.setText(f"{len(p.assets)} 个素材 · 未选择")
            return
        n = len([c for c in p.blurs if c.enabled])
        secs = p.blur_seconds()
        bits = [f"{len(p.assets)} 素材", asset.name]
        if p.width:
            bits.append(f"{p.width}×{p.height}")
        bits.append(f"{p.fps:.2f}fps")
        bits.append(f"模糊 {n} 段 / {secs:.1f}s")
        bits.append(fmt_tc(self.player.position, p.fps))
        self.lbl_info.setText(" · ".join(bits))

    def show_help(self) -> None:
        QMessageBox.information(
            self, "使用说明",
            "1. 启动时选择工程（或新建一个）。工程只记录素材引用，视频/图片放哪都行\n"
            "2. 左侧「导入素材」把视频、图片加入工程；双击视频加载到工作区，双击图片加到时间线\n"
            "3. 第一次用先点「模型配置」右上角的齿轮 →「运行时管理」下载模型（可按镜像下载）\n"
            "4. 视频素材可调模型参数后点「开始检测」，结果直接写入工程（不再生成 JSON / 预览图）\n"
            "5. 素材文件被移动或删除时，素材库会标红，右键「重新定位」即可恢复\n"
            "6. 时间线：拖动移动、拖两端裁剪、模糊轨空白处拖出新片段\n"
            "7. 选中片段后在右下「属性」里微调时间 / 模糊强度 / 图片位置\n"
            "8. 右上角「导出视频」→ 选择编码参数 → 开始导出\n\n"
            "「运行时管理」（Ctrl+M）里还能：检测 NVIDIA / AMD 显卡、下载 CUDA 运行库、"
            "删除不用的模型\n\n"
            "快捷键：空格播放/暂停 · ←/→ 逐帧 · B 加模糊 · I 加图片 · Delete 删除 · "
            "Ctrl+S 保存工程 · Ctrl+I 导入素材 · Ctrl+M 运行时管理 · Ctrl+滚轮 缩放时间线")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if getattr(self, "toast", None) is not None:
            self.toast.reposition()

    def closeEvent(self, e) -> None:
        if not self._maybe_save():
            e.ignore()
            return
        projects.save_settings(self.settings)
        self._stop_scan()
        if self._probe is not None and self._probe.isRunning():
            if not self._probe.wait(2500):
                self._probe.terminate()
                self._probe.wait(800)
        for w in [self._strip_worker, *self._stale_workers]:
            if w is not None and w.isRunning():
                w.wait(1500)
        self.player.close_video()
        super().closeEvent(e)
