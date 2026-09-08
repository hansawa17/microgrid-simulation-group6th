"""B / EMS package entry point.

默认启动 PyQt6 GUI，便于在项目根目录直接使用：
    python -m B_dispatch

也可以显式指定：
    python -m B_dispatch gui

当前 GUI 仍是本地演示/接入骨架，不主动建立 A/B/C TCP 连接。
"""

from __future__ import annotations

import argparse
import socket


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m B_dispatch",
        description="B / EMS 主站启动入口",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="启动 B 的 PyQt6 图形界面")
    return parser


def _get_local_ip() -> str:
    """获取本机当前用于局域网通信的 IPv4 地址。

    使用 UDP connect 仅让操作系统选择本机出口网卡，不会实际发送业务数据。
    如果当前网络不可用，则回退到主机名解析；最终无法确定时显示“未获取”。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip:
            return ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        candidates = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in candidates:
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "未获取"


def _launch_gui() -> int:
    """启动 GUI，并在 B 界面状态栏显示本机网络 IPv4 地址。"""
    from PyQt6 import QtWidgets
    from . import gui_b

    class NetworkAwareMainWindow(gui_b.MainWindow):
        def __init__(self) -> None:
            super().__init__()
            ip = _get_local_ip()
            label = QtWidgets.QLabel(f"本机 IP：{ip}")
            label.setToolTip("B 主站当前电脑用于局域网通信的 IPv4 地址")
            self.statusBar().addPermanentWidget(label)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = NetworkAwareMainWindow()
    window.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 不指定子命令时直接进入 GUI，保证 B 一条命令即可启动。
    if args.command in (None, "gui"):
        return _launch_gui()

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
