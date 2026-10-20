"""Mosaic Studio —— 检测 + 时间线编辑器 GUI。"""

__all__ = ["run"]


def run(argv: list[str] | None = None) -> int:
    from .app import main

    return main(argv)
