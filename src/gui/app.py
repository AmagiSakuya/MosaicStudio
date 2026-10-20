"""GUI 启动入口：初始化 Qt、主题，弹出「选择工程」启动页。

开发/自检用的隐藏参数：
  --screenshot out.png   离屏渲染截图（不弹启动页）
  --project path.vproj   直接打开指定工程
  --video path.mp4       用临时工程打开该视频（不落盘）
  --seek 12.5            跳转到指定秒
  --select 0             选中第 N 个模糊片段
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog

from . import theme
from .mainwindow import MainWindow
from .startup import StartupDialog


def _pop(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        val = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
        return val
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    shot = _pop(args, "--screenshot")
    project = _pop(args, "--project")
    video = _pop(args, "--video")
    _pop(args, "--json")          # 旧参数，检测结果已并入工程，忽略
    seek = _pop(args, "--seek")
    select = _pop(args, "--select")

    app = QApplication(args)
    app.setApplicationName("Mosaic Studio")
    app.setApplicationDisplayName("Mosaic Studio")
    font = QFont("Microsoft YaHei UI", 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    theme.apply(app)

    win = MainWindow()
    if shot:
        # 离屏截图模式：不真正弹窗，但仍使用系统字体数据库
        win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    if shot:
        win.resize(1660, 980)

    if project:
        win.open_project_file(project)
    elif video:
        win.open_scratch([video])
    elif not shot:
        # 启动页：选择要打开的工程（打开失败就回到启动页重新选）
        while True:
            dlg = StartupDialog(win.settings, win)
            if dlg.exec() != QDialog.Accepted or dlg.action == "quit":
                win.close()
                return 0
            ok = (win.new_project(dlg.path) if dlg.action == "new"
                  else win.open_project_file(dlg.path))
            if ok:
                break

    if seek:
        QTimer.singleShot(600, lambda: win._on_seek(float(seek)))
    if select:
        def pick() -> None:
            idx = int(select)
            if win.project and 0 <= idx < len(win.project.blurs):
                clip = win.project.blurs[idx]
                win.timeline.set_selected(clip)
                win.player.seek(clip.start + clip.duration / 2)
        QTimer.singleShot(700, pick)

    if shot:
        def grab() -> None:
            try:
                win.grab().save(shot)
            finally:
                app.quit()

        QTimer.singleShot(1800, grab)

    return app.exec()
