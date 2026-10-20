"""Mosaic Studio 入口：python main_gui.py [--project xxx.vproj] [--video xxx.mp4]"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.gui.app import main  # noqa: E402


def _excepthook(exc_type, exc, tb) -> None:
    """打包成无控制台的 exe 后，异常要弹窗，否则会静默退出。"""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(text)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            QMessageBox.critical(None, "出错了", text[-2500:])
    except Exception:  # noqa: BLE001
        pass


sys.excepthook = _excepthook

if __name__ == "__main__":
    raise SystemExit(main())
