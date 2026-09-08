"""B / EMS package entry point.

默认启动 PyQt6 GUI，便于在项目根目录直接使用：
    python -m B_dispatch

也可以显式指定：
    python -m B_dispatch gui

当前 GUI 仍是本地演示/接入骨架，不主动建立 A/B/C TCP 连接。
"""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m B_dispatch",
        description="B / EMS 主站启动入口",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="启动 B 的 PyQt6 图形界面")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 不指定子命令时直接进入 GUI，保证 B 一条命令即可启动。
    if args.command in (None, "gui"):
        from .gui_b import main as gui_main

        return gui_main()

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
