"""深色主题：配色常量 + Qt 样式表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ---------------- 配色 ----------------
BG = "#151517"
PANEL = "#1c1c20"
PANEL_ALT = "#232329"
CARD = "#1c1c20"
BORDER = "#2c2c34"
TEXT = "#e8e8ee"
MUTED = "#8a8a97"
ACCENT = "#4d8dff"
ACCENT_DARK = "#3b74e0"
RED = "#e05c5c"
AMBER = "#dda03f"
TEAL = "#3fb3a6"
PLAYHEAD = "#ff5a5a"
TRACK_BG = "#17171a"
TRACK_ALT = "#1a1a1f"

ASSETS = Path(__file__).resolve().parent / "assets"


def qc(name: str) -> QColor:
    return QColor(name)


_QSS = """
QWidget {
    color: @TEXT@;
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 12px;
}
QMainWindow, QDialog, #Root { background: @BG@; }

QToolTip {
    background: #2a2a33; color: #e8e8ee;
    border: 1px solid #3a3a46; padding: 4px 7px;
}

QFrame#Card {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
}
QLabel#CardTitle {
    color: #9b9bab;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#Muted { color: @MUTED@; }
QLabel#Tiny { color: @MUTED@; font-size: 11px; }
QLabel#Value { color: #cfd6e6; font-family: "Consolas", "Cascadia Mono", monospace; }
QLabel#Badge {
    background: #262631; color: #9b9bab;
    border-radius: 4px; padding: 1px 6px; font-size: 10px;
}
QLabel#BadgeRed {
    background: rgba(224,92,92,0.16); color: #f08a8a;
    border-radius: 4px; padding: 1px 6px; font-size: 10px;
}
QLabel#BadgeGreen {
    background: rgba(63,179,166,0.16); color: #63d6c8;
    border-radius: 4px; padding: 1px 6px; font-size: 10px;
}

QPushButton {
    background: #26262e;
    border: 1px solid #34343f;
    border-radius: 6px;
    padding: 6px 12px;
    color: #dcdce4;
}
QPushButton:hover { background: #2f2f3a; border-color: #45454f; }
QPushButton:pressed { background: #202027; }
QPushButton:disabled { color: #5a5a66; background: #1f1f25; border-color: #2a2a33; }
QPushButton#Accent {
    background: @ACCENT_DARK@;
    border: 1px solid @ACCENT@;
    color: #ffffff;
    font-weight: 600;
    padding: 7px 14px;
}
QPushButton#Accent:hover { background: @ACCENT@; }
QPushButton#Accent:disabled { background: #2a3550; border-color: #35456a; color: #7f8aa5; }
QPushButton#Ghost { background: transparent; border: 1px solid #34343f; }
QPushButton#Ghost:hover { background: #23232b; }
QPushButton#Danger { color: #f08a8a; border-color: #4a2f33; background: #2a2024; }
QPushButton#Danger:hover { background: #3a262b; border-color: #6d3b41; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 7px;
    color: #c9c9d4;
}
QToolButton:hover { background: #24242c; border-color: #34343f; }
QToolButton:pressed { background: #1f1f26; }
QToolButton:checked { background: #2b3f66; border-color: #3f5f9b; }
QToolButton:disabled { color: #55555f; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #131317;
    border: 1px solid #31313b;
    border-radius: 5px;
    padding: 4px 7px;
    color: #e2e2ea;
    selection-background-color: @ACCENT_DARK@;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: @ACCENT@;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #5c5c67; }
QComboBox::drop-down { width: 18px; border: none; }
QComboBox::down-arrow {
    image: url(@ASSETS@/arrow_down.png);
    width: 9px; height: 6px;
}
QComboBox QAbstractItemView {
    background: #1f1f26;
    border: 1px solid #3a3a46;
    selection-background-color: @ACCENT_DARK@;
    outline: none;
    padding: 3px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: #202028; border: none; width: 14px;
}
QSpinBox#Num { padding: 4px 2px 4px 6px; }
QSpinBox#Num::up-button, QSpinBox#Num::down-button { width: 12px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url(@ASSETS@/arrow_up.png); width: 7px; height: 5px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url(@ASSETS@/arrow_down.png); width: 7px; height: 5px;
}

QListWidget {
    background: #131317;
    border: 1px solid #2b2b34;
    border-radius: 6px;
    outline: none;
    padding: 2px;
}
QListWidget::item {
    padding: 5px 6px;
    border-radius: 4px;
    color: #d6d6de;
}
QListWidget::item:hover { background: #202029; }
QListWidget::item:selected { background: #2b4470; color: #ffffff; }

QSlider::groove:horizontal { height: 4px; background: #2d2d37; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 2px; }
QSlider::add-page:horizontal { background: #2d2d37; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #e9e9f0; width: 12px; margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #ffffff; }

QCheckBox { spacing: 6px; color: #cfcfd8; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border-radius: 3px;
    border: 1px solid #3d3d49;
    background: #131317;
}
QCheckBox::indicator:hover { border-color: #5a5a6b; }
QCheckBox::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
    image: url(@ASSETS@/check.png);
}

QProgressBar {
    background: #131317;
    border: 1px solid #2b2b34;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: @ACCENT@; border-radius: 3px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 2px 2px 0; }
QScrollBar::handle:vertical { background: #383843; border-radius: 4px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: #4a4a58; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0 2px 2px 2px; }
QScrollBar::handle:horizontal { background: #383843; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QMenu {
    background: #1f1f26;
    border: 1px solid #3a3a46;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item { padding: 6px 24px 6px 14px; border-radius: 4px; color: #dcdce4; }
QMenu::item:selected { background: #2b4470; }
QMenu::item:disabled { color: #5c5c67; }
QMenu::separator { height: 1px; background: #31313b; margin: 4px 8px; }

QSplitter::handle { background: #131317; }
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }

QStatusBar {
    background: #17171b;
    border-top: 1px solid #26262e;
    color: #8a8a97;
}
QStatusBar::item { border: none; }

QToolBar {
    background: #17171b;
    border: none;
    border-bottom: 1px solid #26262e;
    spacing: 4px;
    padding: 5px 10px;
}
QToolBar QToolButton {
    padding: 5px 9px;
    font-weight: 500;
}
QMenuBar { background: #17171b; color: #cfcfd8; }
QMenuBar::item { padding: 4px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: #26262e; }

QTabWidget::pane { border: 1px solid @BORDER@; border-radius: 6px; }
QTabBar::tab {
    background: #1f1f26; color: #9b9bab;
    padding: 6px 14px; border-top-left-radius: 5px; border-top-right-radius: 5px;
}
QTabBar::tab:selected { background: #2b4470; color: #ffffff; }

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


def apply(app: QApplication) -> None:
    """应用深色主题（含调色板兜底，避免原生控件闪白）。"""
    pal = QPalette()
    pal.setColor(QPalette.Window, qc(BG))
    pal.setColor(QPalette.WindowText, qc(TEXT))
    pal.setColor(QPalette.Base, qc("#131317"))
    pal.setColor(QPalette.AlternateBase, qc(PANEL_ALT))
    pal.setColor(QPalette.Text, qc(TEXT))
    pal.setColor(QPalette.Button, qc("#26262e"))
    pal.setColor(QPalette.ButtonText, qc(TEXT))
    pal.setColor(QPalette.Highlight, qc(ACCENT_DARK))
    pal.setColor(QPalette.HighlightedText, qc("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, qc("#2a2a33"))
    pal.setColor(QPalette.ToolTipText, qc(TEXT))
    pal.setColor(QPalette.Disabled, QPalette.Text, qc("#5c5c67"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, qc("#5c5c67"))
    app.setPalette(pal)

    qss = (
        _QSS.replace("@TEXT@", TEXT)
        .replace("@BG@", BG)
        .replace("@PANEL@", PANEL)
        .replace("@CARD@", CARD)
        .replace("@BORDER@", BORDER)
        .replace("@MUTED@", MUTED)
        .replace("@ACCENT@", ACCENT)
        .replace("@ACCENT_DARK@", ACCENT_DARK)
        .replace("@ASSETS@", ASSETS.as_posix())
    )
    app.setStyleSheet(qss)
