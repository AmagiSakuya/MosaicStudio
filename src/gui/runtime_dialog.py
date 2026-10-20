"""运行时管理：检测模型的下载 / 删除 + GPU 运行环境检测与运行库安装。

发布版不带模型和 CUDA 运行库（体积太大），用户第一次使用时在这里按需下载：
  * 检测模型：从 HF 镜像（可换自定义镜像）下载到 model 目录
  * 运行环境：识别 NVIDIA / AMD / Intel 显卡与驱动，装对应的推理后端
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import models, net, runtime_info, runtime_pkgs
from ..paths import model_root, runtime_root
from . import icons, projects


# ---------------- 通用小件 ----------------
def _small(text: str = "", name: str = "Tiny") -> QLabel:
    lb = QLabel(text)
    lb.setObjectName(name)
    lb.setWordWrap(True)
    return lb


def _hsep() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:#2a2a33; border:none;")
    return f


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
            item.layout().deleteLater()


_BADGE = {
    "ok": "background:rgba(63,179,166,0.16);color:#63d6c8;",
    "warn": "background:rgba(221,160,63,0.16);color:#e8b06a;",
    "err": "background:rgba(224,92,92,0.16);color:#f08a8a;",
    "muted": "background:#262631;color:#8a8a97;",
    "info": "background:rgba(77,141,255,0.16);color:#9dc0f5;",
}


def _badge(label: QLabel, text: str, kind: str = "muted") -> None:
    label.setText(text)
    label.setStyleSheet(
        "border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600;" + _BADGE.get(kind, ""))


class _Card(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(13, 11, 13, 13)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        lb = QLabel(title)
        lb.setObjectName("CardTitle")
        head.addWidget(lb)
        head.addStretch(1)
        self.head = head
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body)


# ---------------- 后台任务 ----------------
class _Job(QThread):
    tick = Signal(float, str)
    log = Signal(str)
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn: Callable, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D401
        try:
            result = self._fn(
                lambda f, t: self.tick.emit(float(f), str(t)),
                lambda s: self.log.emit(str(s)),
                self._cancel,
            )
        except net.DownloadCancelled:
            self.fail.emit("已取消")
        except Exception as exc:  # noqa: BLE001 - 统一转成界面提示
            self.fail.emit(str(exc) or exc.__class__.__name__)
        else:
            self.ok.emit(result)


# ---------------- 模型行 ----------------
class _ModelRow(QFrame):
    downloadClicked = Signal(str)
    deleteClicked = Signal(str)

    def __init__(self, spec: models.ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("ModelRow")
        self.setStyleSheet(
            "QFrame#ModelRow{background:#202027;border:1px solid #2b2b34;border-radius:7px;}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(11, 9, 11, 9)
        h.setSpacing(10)

        ic = QLabel()
        ic.setPixmap(icons.pixmap("chip", size=22))
        ic.setFixedWidth(24)
        h.addWidget(ic)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel(spec.title)
        title.setStyleSheet("font-size:12px; font-weight:700; color:#ececf3;")
        col.addWidget(title)
        col.addWidget(_small(spec.tagline))
        col.addWidget(_small(f"{spec.size_mb} MB · 准确率 {spec.accuracy} · {spec.speed}"))
        h.addLayout(col, 1)

        self.badge = QLabel()
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedWidth(66)
        _badge(self.badge, "检查中", "muted")
        h.addWidget(self.badge)

        self.btn_dl = QPushButton("下载")
        self.btn_dl.setFixedWidth(78)
        self.btn_dl.clicked.connect(lambda: self.downloadClicked.emit(self.spec.key))
        h.addWidget(self.btn_dl)

        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.setFixedWidth(58)
        self.btn_del.clicked.connect(lambda: self.deleteClicked.emit(self.spec.key))
        h.addWidget(self.btn_del)

    def refresh(self, st: models.ModelState, busy: bool) -> None:
        if st.installed:
            _badge(self.badge, "已安装", "ok")
            self.btn_dl.setText("重新下载")
            self.btn_dl.setObjectName("Ghost")
            self.btn_del.setVisible(True)
            self.btn_del.setEnabled(not busy)
        elif st.partial:
            _badge(self.badge, "不完整", "warn")
            self.btn_dl.setText("继续下载")
            self.btn_dl.setObjectName("Accent")
            self.btn_del.setVisible(True)
            self.btn_del.setEnabled(not busy)
        else:
            _badge(self.badge, "未下载", "muted")
            self.btn_dl.setText("下载")
            self.btn_dl.setObjectName("Accent")
            self.btn_del.setVisible(False)
        self.btn_dl.setEnabled(not busy)
        for b in (self.btn_dl, self.btn_del):
            b.style().unpolish(b)
            b.style().polish(b)


# ---------------- 对话框 ----------------
class RuntimeDialog(QDialog):
    """模型 / 运行环境管理面板。"""

    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._job: Optional[_Job] = None
        self._busy = False
        self._quiet_fail = False
        self._info: dict = {}
        self._rows: dict[str, _ModelRow] = {}
        self._actions: list[QWidget] = []

        self.setWindowTitle("运行时管理")
        self.setModal(True)
        self.resize(980, 740)
        self.setMinimumSize(820, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("chip", size=28))
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t1 = QLabel("运行时管理")
        t1.setStyleSheet("font-size:16px; font-weight:800; letter-spacing:1px;")
        titles.addWidget(t1)
        sub = QLabel("检测模型 · 推理引擎 · GPU 运行库 —— 发布版不预置，按需下载")
        sub.setObjectName("Tiny")
        titles.addWidget(sub)
        head.addLayout(titles)
        head.addStretch(1)
        root.addLayout(head)
        root.addWidget(_hsep())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_models_tab(), "检测模型")
        self.tabs.addTab(self._build_env_tab(), "运行环境")
        root.addWidget(self.tabs, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFixedHeight(62)
        self.log.setPlaceholderText("日志：下载进度、镜像切换、安装结果会显示在这里")
        self.log.setStyleSheet("background:#121216;border:1px solid #2b2b34;border-radius:6px;"
                               "font-family:'Consolas','Cascadia Mono',monospace;font-size:11px;")
        root.addWidget(self.log)

        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setFixedWidth(64)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_job)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.btn_cancel)
        root.addLayout(prow)

        self.status = _small("就绪")
        root.addWidget(self.status)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_open_model = QPushButton("打开模型目录")
        self.btn_open_model.setObjectName("Ghost")
        self.btn_open_model.clicked.connect(lambda: self._reveal(model_root()))
        bar.addWidget(self.btn_open_model)
        self.btn_open_rt = QPushButton("打开运行库目录")
        self.btn_open_rt.setObjectName("Ghost")
        self.btn_open_rt.clicked.connect(lambda: self._reveal(runtime_root()))
        bar.addWidget(self.btn_open_rt)
        bar.addStretch(1)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        bar.addWidget(self.btn_close)
        root.addLayout(bar)

        self._restore_settings()
        self._refresh_models()
        QTimer.singleShot(80, self._detect_env)

    # ================= 模型页 =================
    def _build_models_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(_small("存储位置"))
        self.lb_model_path = QLabel(str(model_root()))
        self.lb_model_path.setObjectName("Muted")
        self.lb_model_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lb_model_path.setToolTip(str(model_root()))
        path_row.addWidget(self.lb_model_path, 1)
        self.lb_model_used = QLabel("")
        self.lb_model_used.setObjectName("Muted")
        path_row.addWidget(self.lb_model_used)
        v.addLayout(path_row)

        for key in ("ultra", "pro", "nano"):
            row = _ModelRow(models.MODELS[key])
            row.downloadClicked.connect(self._download_model)
            row.deleteClicked.connect(self._delete_model)
            self._rows[key] = row
            self._actions.append(row)
            v.addWidget(row)

        v.addSpacing(2)
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        src_row.addWidget(_small("下载源"))
        self.cmb_mirror = QComboBox()
        for name, url in models.MIRRORS:
            self.cmb_mirror.addItem(name, url)
        self.cmb_mirror.currentIndexChanged.connect(self._on_mirror_changed)
        src_row.addWidget(self.cmb_mirror, 1)
        self.ed_mirror = QLineEdit()
        self.ed_mirror.setPlaceholderText("https://your-mirror.example.com（自定义 HF 镜像）")
        self.ed_mirror.editingFinished.connect(self._save_settings)
        src_row.addWidget(self.ed_mirror, 1)
        v.addLayout(src_row)

        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self.btn_dl_all = QPushButton("下载全部未安装的模型")
        self.btn_dl_all.setObjectName("Accent")
        self.btn_dl_all.setIcon(icons.icon("download"))
        self.btn_dl_all.clicked.connect(self._download_all)
        act_row.addWidget(self.btn_dl_all)
        self.btn_verify = QPushButton("校验完整性")
        self.btn_verify.setObjectName("Ghost")
        self.btn_verify.clicked.connect(self._verify_all)
        act_row.addWidget(self.btn_verify)
        act_row.addStretch(1)
        v.addLayout(act_row)

        hint = QLabel("下载支持断点续传：中断后重新点「继续下载」会从断点接着下，不会从头再来。")
        hint.setObjectName("Tiny")
        v.addWidget(hint)

        self._actions.extend([self.btn_dl_all, self.btn_verify])
        v.addStretch(1)
        return w

    def _on_mirror_changed(self, _i: int = 0) -> None:
        custom = not self.cmb_mirror.currentData()
        self.ed_mirror.setVisible(custom)
        if custom:
            self.ed_mirror.setFocus()
        self._save_settings()

    def _mirror_url(self) -> str:
        data = str(self.cmb_mirror.currentData() or "").strip()
        if data:
            return data.rstrip("/")
        return self.ed_mirror.text().strip().rstrip("/")

    def _refresh_models(self) -> None:
        busy = self._busy
        for key, row in self._rows.items():
            row.refresh(models.state(key), busy)
        self.lb_model_used.setText(f"已占用 {net.human_size(models.used_bytes())}")

    def _download_model(self, key: str) -> None:
        mirror = self._mirror_url()
        if not mirror:
            QMessageBox.warning(self, "下载模型", "请先填写自定义镜像地址，或选择内置镜像")
            return
        self._quiet_fail = False

        def task(tick, log, cancel):
            return models.download(key, mirror, tick, cancel, log)

        self._run(task, lambda _r: self._after_download(key),
                  f"正在下载 {models.spec(key).title} …")

    def _after_download(self, key: str) -> None:
        ok, detail = models.verify(key)
        self._refresh_models()
        self._append_log(("✓ " if ok else "✗ ") + detail)
        self.status.setText(detail)
        if not ok:
            QMessageBox.warning(self, "下载完成但有异常", detail)

    def _download_all(self) -> None:
        mirror = self._mirror_url()
        if not mirror:
            QMessageBox.warning(self, "下载模型", "请先填写自定义镜像地址，或选择内置镜像")
            return
        missing = [k for k in models.MODELS if not models.state(k).installed]
        if not missing:
            QMessageBox.information(self, "下载模型", "所有档位的模型都已安装")
            return
        total_mb = sum(models.MODELS[k].size_mb for k in missing)
        ret = QMessageBox.question(
            self, "下载全部模型",
            f"将下载 {len(missing)} 个模型（约 {total_mb} MB）：\n"
            + "、".join(models.MODELS[k].title for k in missing)
            + "\n\n网络不好时可以只下 Ultra（最准），其余随时补下。继续吗？")
        if ret != QMessageBox.Yes:
            return
        self._quiet_fail = False

        def task(tick, log, cancel):
            for i, key in enumerate(missing):
                def sub(f, text, _i=i):
                    tick((_i + f) / len(missing), f"[{_i + 1}/{len(missing)}] {text}")
                models.download(key, mirror, sub, cancel, log)
            return missing

        self._run(task, self._after_download_all, "正在下载模型 …")

    def _after_download_all(self, keys) -> None:
        self._refresh_models()
        bad = [k for k in keys if not models.state(k).installed]
        if bad:
            self.status.setText("部分模型未完成，可稍后重试")
        else:
            self.status.setText(f"已安装 {len(keys)} 个模型")

    def _delete_model(self, key: str) -> None:
        s = models.spec(key)
        st = models.state(key)
        if not st.bytes:
            return
        ret = QMessageBox.question(
            self, "删除模型",
            f"删除「{s.title}」？\n将释放 {net.human_size(st.bytes)} 空间，"
            f"之后检测需要重新下载。\n\n（不会影响已有工程和检测结果）")
        if ret != QMessageBox.Yes:
            return
        models.remove(key)
        self._refresh_models()
        self._append_log(f"已删除 {s.title}")
        self.status.setText(f"已删除 {s.title}")

    def _verify_all(self) -> None:
        lines = []
        for key in models.MODELS:
            st = models.state(key)
            if not st.bytes:
                continue
            ok, detail = models.verify(key)
            lines.append(("✓ " if ok else "✗ ") + detail)
        if not lines:
            self._append_log("还没有已安装的模型")
            return
        for line in lines:
            self._append_log(line)
        self.status.setText(lines[0])

    # ================= 运行环境页 =================
    def _build_env_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(14, 12, 14, 10)
        v.setSpacing(8)

        # ---- 显卡 ----
        card_gpu = _Card("显卡与驱动")
        self.btn_driver = QPushButton("驱动下载页")
        self.btn_driver.setObjectName("Ghost")
        self.btn_driver.setFixedHeight(24)
        self.btn_driver.setToolTip("在浏览器里打开对应显卡厂商的驱动下载页")
        self.btn_driver.clicked.connect(self._open_driver_page)
        card_gpu.head.addWidget(self.btn_driver)
        self.btn_rescan = QPushButton("重新检测")
        self.btn_rescan.setObjectName("Ghost")
        self.btn_rescan.setFixedHeight(24)
        self.btn_rescan.clicked.connect(self._detect_env)
        card_gpu.head.addWidget(self.btn_rescan)
        self.gpu_body = QVBoxLayout()
        self.gpu_body.setSpacing(4)
        card_gpu.body.addLayout(self.gpu_body)
        v.addWidget(card_gpu)

        # ---- 推理引擎 ----
        card_eng = _Card("推理引擎（onnxruntime）")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        self.lb_engine_ver = QLabel("—")
        self.lb_engine_build = QLabel("—")
        self.lb_engine_providers = QLabel("—")
        self.lb_engine_providers.setWordWrap(True)
        self.lb_engine_mode = QLabel("—")
        for r, (name, w) in enumerate((("版本", self.lb_engine_ver),
                                       ("后端", self.lb_engine_build),
                                       ("可用执行器", self.lb_engine_providers),
                                       ("运行方式", self.lb_engine_mode))):
            lab = _small(name)
            lab.setFixedWidth(66)
            grid.addWidget(lab, r, 0, Qt.AlignTop)
            grid.addWidget(w, r, 1)
        card_eng.body.addLayout(grid)
        eng_row = QHBoxLayout()
        eng_row.setSpacing(8)
        self.btn_engine_cuda = QPushButton("安装 GPU 版引擎（CUDA）")
        self.btn_engine_cuda.clicked.connect(lambda: self._install_engine("cuda"))
        self.btn_engine_dml = QPushButton("安装 DirectML 版引擎（AMD / Intel）")
        self.btn_engine_dml.clicked.connect(lambda: self._install_engine("dml"))
        eng_row.addWidget(self.btn_engine_cuda)
        eng_row.addWidget(self.btn_engine_dml)
        eng_row.addStretch(1)
        card_eng.body.addLayout(eng_row)
        self.lb_engine_hint = _small("")
        card_eng.body.addWidget(self.lb_engine_hint)
        v.addWidget(card_eng)

        # ---- CUDA 运行库 ----
        card_cuda = _Card("CUDA / cuDNN 运行库（NVIDIA）")
        cgrid = QGridLayout()
        cgrid.setHorizontalSpacing(10)
        cgrid.setVerticalSpacing(5)
        cgrid.setColumnStretch(1, 1)
        self.lb_cublas = QLabel("—")
        self.lb_cudnn = QLabel("—")
        self.lb_cudart = QLabel("—")
        for r, (name, w) in enumerate((("cuBLAS", self.lb_cublas),
                                       ("cuDNN", self.lb_cudnn),
                                       ("Runtime", self.lb_cudart))):
            lab = _small(name)
            lab.setFixedWidth(66)
            cgrid.addWidget(lab, r, 0)
            cgrid.addWidget(w, r, 1)
        card_cuda.body.addLayout(cgrid)

        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        ctl.addWidget(_small("CUDA 版本"))
        self.cmb_cuda = QComboBox()
        self.cmb_cuda.addItem("CUDA 13（推荐）", 13)
        self.cmb_cuda.addItem("CUDA 12", 12)
        self.cmb_cuda.setFixedWidth(150)
        ctl.addWidget(self.cmb_cuda)
        ctl.addWidget(_small("下载源"))
        self.cmb_pypi = QComboBox()
        for name, url in runtime_pkgs.PYPI_MIRRORS:
            self.cmb_pypi.addItem(name, url)
        self.cmb_pypi.setFixedWidth(180)
        ctl.addWidget(self.cmb_pypi)
        ctl.addStretch(1)
        self.btn_cuda_dl = QPushButton("下载 / 修复运行库")
        self.btn_cuda_dl.setObjectName("Accent")
        self.btn_cuda_dl.setIcon(icons.icon("download"))
        self.btn_cuda_dl.clicked.connect(self._download_cuda)
        ctl.addWidget(self.btn_cuda_dl)
        card_cuda.body.addLayout(ctl)
        self.lb_cuda_hint = _small("")
        card_cuda.body.addWidget(self.lb_cuda_hint)
        v.addWidget(card_cuda)

        # ---- 建议 ----
        card_tip = _Card("环境建议")
        self.tips_body = QVBoxLayout()
        self.tips_body.setSpacing(3)
        card_tip.body.addLayout(self.tips_body)
        v.addWidget(card_tip)

        v.addStretch(1)
        self._actions.extend([self.btn_rescan, self.btn_driver, self.btn_engine_cuda,
                              self.btn_engine_dml, self.btn_cuda_dl])
        scroll.setWidget(inner)
        return scroll

    def _detect_env(self) -> None:
        self._quiet_fail = True
        self._run(lambda tick, log, cancel: runtime_info.summary(),
                  self._on_env, "正在检测运行环境 …")

    def _on_env(self, info: dict) -> None:
        self._info = info
        self._fill_gpus(info)
        ort = info.get("ort") or {}
        self.lb_engine_ver.setText(str(ort.get("version") or "未安装"))
        self.lb_engine_build.setText(str(ort.get("build") or "—"))
        providers = ort.get("providers") or []
        self.lb_engine_providers.setText("、".join(providers) if providers else "—")
        self.lb_engine_mode.setText("打包版（exe）" if info.get("frozen") else "源码运行（venv）")

        vendor = str(info.get("vendor") or "无")
        can_install, why = runtime_pkgs.engine_installable()
        show_cuda_btn = vendor == "NVIDIA" and not ort.get("cuda")
        show_dml_btn = vendor in ("AMD", "Intel") and not ort.get("dml")
        self.btn_engine_cuda.setVisible(show_cuda_btn)
        self.btn_engine_dml.setVisible(show_dml_btn)
        if (show_cuda_btn or show_dml_btn) and not can_install:
            self.lb_engine_hint.setText(f"⚠ {why}")
        elif ort.get("cuda"):
            self.lb_engine_hint.setText("✓ 已是 CUDA 版引擎，配合下方运行库即可 GPU 推理")
        elif ort.get("dml"):
            self.lb_engine_hint.setText("✓ 已是 DirectML 版引擎，检测时把「设备」选成 GPU · DirectML")
        else:
            self.lb_engine_hint.setText("当前为 CPU 版引擎：检测可用，但速度只有 GPU 的 1/15 左右")

        cuda = info.get("cuda") or {}
        libs = cuda.get("libs") or {}
        for label, key in ((self.lb_cublas, "cuBLAS"),
                           (self.lb_cudnn, "cuDNN"),
                           (self.lb_cudart, "CUDA Runtime")):
            path = libs.get(key)
            if path:
                label.setText(f"✓ 已就位　{os.path.basename(path)}")
                label.setStyleSheet("color:#63d6c8;")
                label.setToolTip(path)
            else:
                label.setText("✗ 未找到")
                label.setStyleSheet("color:#e08a7a;")
                label.setToolTip("")

        if vendor == "NVIDIA":
            self.lb_cuda_hint.setText(
                f"运行库会下载到 {runtime_info.install_dir()}（"
                f"{runtime_pkgs.cuda_runtime_size_hint(int(self.cmb_cuda.currentData() or 13))}），"
                "不用安装完整 CUDA Toolkit；装完重启程序生效。")
        else:
            self.lb_cuda_hint.setText("未检测到 NVIDIA 显卡，CUDA 运行库对当前机器无用。"
                                      "AMD / Intel 显卡请用 DirectML 后端。")

        _clear(self.tips_body)
        for tip in runtime_info.suggestion(info):
            self.tips_body.addWidget(_small("· " + tip))
        self._sync_env_buttons()
        self.status.setText(f"运行环境检测完成 · 显卡 {vendor} · 引擎 {ort.get('build') or '未知'}")

    def _sync_env_buttons(self) -> None:
        """按探测结果 + 当前是否忙，决定各按钮能不能点。"""
        info = self._info or {}
        if not info:
            return
        ort = info.get("ort") or {}
        vendor = str(info.get("vendor") or "无")
        can_install, _why = runtime_pkgs.engine_installable()
        idle = not self._busy
        self.btn_engine_cuda.setEnabled(idle and can_install and not ort.get("cuda"))
        self.btn_engine_dml.setEnabled(idle and can_install and not ort.get("dml"))
        self.btn_cuda_dl.setEnabled(idle and vendor == "NVIDIA")
        self.btn_rescan.setEnabled(idle)
        has_page = vendor in self._DRIVER_PAGE
        self.btn_driver.setEnabled(idle and has_page)
        if has_page:
            self.btn_driver.setToolTip(f"在浏览器里打开 {vendor} 官方驱动下载页")

    def _fill_gpus(self, info: dict) -> None:
        _clear(self.gpu_body)
        rank = {"NVIDIA": 0, "AMD": 1, "Intel": 2, "其它": 3, "虚拟": 4}
        all_gpus = sorted(info.get("gpus") or [], key=lambda g: rank.get(g.vendor, 3))
        real = [g for g in all_gpus if g.vendor != "虚拟"]
        gpus = real or all_gpus
        if not gpus:
            self.gpu_body.addWidget(_small("未检测到显卡信息（可能是远程桌面、虚拟机或 WMI 不可用）"))
            return
        for i, g in enumerate(gpus, 1):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            col = QVBoxLayout()
            col.setSpacing(0)
            name = QLabel(f"{i}. {g.name}")
            name.setStyleSheet("font-size:12px; font-weight:600;")
            col.addWidget(name)
            bits = []
            if g.driver:
                bits.append(f"驱动 {g.driver}")
            bits.append(f"显存 {g.vram_text}")
            if g.cuda_support:
                bits.append(f"驱动支持 CUDA {g.cuda_support}")
            col.addWidget(_small(" · ".join(bits)))
            h.addLayout(col, 1)
            badge = QLabel()
            kind = {"NVIDIA": "ok", "AMD": "warn", "Intel": "info"}.get(g.vendor, "muted")
            _badge(badge, g.vendor, kind)
            badge.setToolTip(f"探测方式：{g.source}")
            h.addWidget(badge)
            self.gpu_body.addWidget(row)
        hidden = len(all_gpus) - len(gpus)
        if hidden > 0:
            self.gpu_body.addWidget(_small(f"（已忽略 {hidden} 个虚拟显示器 / 远程桌面适配器）"))

    # ================= 任务控制 =================
    def _run(self, fn: Callable, on_ok: Callable, label: str) -> None:
        if self._job is not None and self._job.isRunning():
            return
        self._set_busy(True, label)
        job = _Job(fn, self)
        job.tick.connect(self._on_tick)
        job.log.connect(self._append_log)
        job.ok.connect(lambda r: self._on_ok(r, on_ok))
        job.fail.connect(self._on_fail)
        self._job = job
        job.start()

    def _on_ok(self, result, cb: Callable) -> None:
        self._set_busy(False, "完成")
        try:
            cb(result)
        except Exception as exc:  # noqa: BLE001 - 刷新失败不该弹崩溃
            self._append_log(f"界面刷新失败：{exc}")

    def _on_fail(self, msg: str) -> None:
        self._set_busy(False, msg)
        self._append_log(f"✗ {msg}")
        if msg != "已取消" and not self._quiet_fail:
            QMessageBox.warning(self, "运行时管理", msg)

    def _on_tick(self, frac: float, text: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        if text:
            self.status.setText(text)

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _cancel_job(self) -> None:
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self.btn_cancel.setEnabled(False)
            self.status.setText("正在取消 …")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.btn_cancel.setEnabled(busy)
        if not busy:
            self.progress.setValue(0)
        for w in self._actions:
            if isinstance(w, _ModelRow):
                continue
            w.setEnabled(not busy)
        self._refresh_models()
        self._sync_env_buttons()
        if text:
            self.status.setText(text)

    # ================= 安装动作 =================
    def _download_cuda(self) -> None:
        major = int(self.cmb_cuda.currentData() or 13)
        index = str(self.cmb_pypi.currentData() or runtime_pkgs.PYPI_MIRRORS[0][1])
        ret = QMessageBox.question(
            self, "下载 CUDA 运行库",
            f"将下载 CUDA {major} 的 cuBLAS / cuDNN / Runtime（"
            f"{runtime_pkgs.cuda_runtime_size_hint(major)}）并解压到程序目录。\n\n"
            "· 只下 DLL，不需要安装完整 CUDA Toolkit\n"
            "· 支持断点续传，可随时取消\n"
            "· 装完重启程序生效\n\n继续吗？")
        if ret != QMessageBox.Yes:
            return
        self._quiet_fail = False

        def task(tick, log, cancel):
            dest = runtime_pkgs.download_cuda_runtime(major, index, tick, log, cancel)
            return str(dest)

        self._run(task, self._after_cuda, f"正在下载 CUDA {major} 运行库 …")

    def _after_cuda(self, dest: str) -> None:
        try:
            from .. import gpu as gpu_mod
            gpu_mod.refresh()
        except Exception:  # noqa: BLE001
            pass
        self._append_log(f"运行库已解压：{dest}")
        QMessageBox.information(
            self, "运行库已就绪",
            "CUDA / cuDNN 运行库下载完成。\n\n"
            "请重启 Mosaic Studio，然后在「设备」里选「自动」或「GPU · CUDA」，"
            "检测就会走显卡。")
        self._detect_env()

    def _install_engine(self, kind: str) -> None:
        ok, why = runtime_pkgs.engine_installable()
        if not ok:
            QMessageBox.information(self, "无法在线安装", why)
            return
        name = "GPU 版（CUDA）" if kind == "cuda" else "DirectML 版"
        ret = QMessageBox.question(
            self, f"安装 {name} 推理引擎",
            f"将通过 pip 安装 {name} 引擎（会先卸载现有的 CPU / 其它 GPU 版本）。\n\n"
            "· 只影响当前 .venv，不动系统环境\n"
            "· 装完需要重启程序\n\n继续吗？")
        if ret != QMessageBox.Yes:
            return
        index = str(self.cmb_pypi.currentData() or runtime_pkgs.PYPI_MIRRORS[0][1])
        self._quiet_fail = False

        def task(tick, log, cancel):
            runtime_pkgs.install_engine(kind, index, tick, log, cancel)
            return kind

        self._run(task, self._after_engine, f"正在安装{name}引擎 …")

    def _after_engine(self, kind: str) -> None:
        QMessageBox.information(
            self, "安装完成",
            "推理引擎已切换完成，请重启 Mosaic Studio 生效。")
        self._detect_env()

    # ================= 杂项 =================
    _DRIVER_PAGE = {
        "NVIDIA": "https://www.nvidia.cn/geforce/drivers/",
        "AMD": "https://www.amd.com/zh-cn/support/download/drivers.html",
        "Intel": "https://www.intel.cn/content/www/cn/zh/download-center/home.html",
    }

    def _open_driver_page(self) -> None:
        vendor = str((self._info or {}).get("vendor") or "")
        url = self._DRIVER_PAGE.get(vendor)
        if not url:
            QMessageBox.information(self, "驱动下载",
                                    "没有检测到需要单独装驱动的独立显卡。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _reveal(self, path) -> None:
        try:
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))  # type: ignore[attr-defined]
        except OSError:
            pass

    def _restore_settings(self) -> None:
        mirror = str(self.settings.get("model_mirror") or models.MIRRORS[0][1])
        idx = self.cmb_mirror.findData(mirror)
        self.cmb_mirror.setCurrentIndex(idx if idx >= 0 else len(models.MIRRORS) - 1)
        self.ed_mirror.setText(str(self.settings.get("model_mirror_custom") or ""))
        self.ed_mirror.setVisible(not self.cmb_mirror.currentData())
        self._on_mirror_changed()

        pypi = str(self.settings.get("pypi_mirror") or runtime_pkgs.PYPI_MIRRORS[0][1])
        idx = self.cmb_pypi.findData(pypi)
        if idx >= 0:
            self.cmb_pypi.setCurrentIndex(idx)
        major = int(self.settings.get("cuda_major") or 13)
        idx = self.cmb_cuda.findData(major)
        if idx >= 0:
            self.cmb_cuda.setCurrentIndex(idx)

    def _save_settings(self) -> None:
        self.settings["model_mirror"] = str(self.cmb_mirror.currentData() or "")
        self.settings["model_mirror_custom"] = self.ed_mirror.text().strip()
        self.settings["pypi_mirror"] = str(self.cmb_pypi.currentData() or "")
        self.settings["cuda_major"] = int(self.cmb_cuda.currentData() or 13)
        projects.save_settings(self.settings)

    def closeEvent(self, e) -> None:
        self._save_settings()
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self._job.wait(2500)
        super().closeEvent(e)

    def accept(self) -> None:
        self._save_settings()
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self._job.wait(2500)
        super().accept()
