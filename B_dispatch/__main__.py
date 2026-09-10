"""B / EMS package entry point.

默认启动 PyQt6 GUI，便于在项目根目录直接使用：
    python -m B_dispatch

也可以分别启动三个验收进程：
    python -m B_dispatch io --host 127.0.0.1 --port 5000
    python -m B_dispatch compute
    python -m B_dispatch gui

``io`` 独占 TCP，``compute`` 只读写 ems.db，GUI 负责操作和展示。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys


DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "runtime" / "ems.db"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m B_dispatch",
        description="B / EMS 主站启动入口",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="启动 B 的 PyQt6 图形界面")
    subparsers.add_parser("run", help="以三个独立进程启动 B_IO、B_COMPUTE 和 GUI")
    init_parser = subparsers.add_parser("init", help="初始化或迁移 ems.db")
    init_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    io_parser = subparsers.add_parser("io", help="启动独立 TCP/数据库通信进程")
    io_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    io_parser.add_argument("--host", help="A 服务端主机；省略时读取 ems.db")
    io_parser.add_argument("--port", type=int, help="A 服务端端口；省略时读取 ems.db")
    compute_parser = subparsers.add_parser("compute", help="启动独立数据库调度计算进程")
    compute_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
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
    """启动 GUI，并在页眉和状态栏显示本机网络 IPv4 地址。"""
    from PyQt6 import QtWidgets
    from . import gui_b

    class NetworkAwareMainWindow(gui_b.MainWindow):
        def __init__(self) -> None:
            super().__init__()
            ip = _get_local_ip()

            # 页眉是始终可见区域；把 IP 放在副标题中，避免窗口底部状态栏
            # 因系统缩放或窗口裁切而不可见。
            if hasattr(self, "appSubtitle"):
                self.appSubtitle.setText(
                    f"PC-B · 能量管理与调度系统 · 闭环 EMS · 本机 IP：{ip}"
                )

            # 窗口标题也同步显示 IP，便于最小化/切换窗口时确认当前地址。
            self.setWindowTitle(f"南极孤立微电网 EMS 主站 B · 本机 IP：{ip}")

            label = QtWidgets.QLabel(f"本机 IP：{ip}")
            label.setToolTip("B 主站当前电脑用于局域网通信的 IPv4 地址")
            self.statusBar().addPermanentWidget(label)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = NetworkAwareMainWindow()
    window.show()
    return app.exec()


def _launch_full_runtime() -> int:
    """Supervise three independent B processes while the GUI is open."""
    from .repository import EMSRepository

    EMSRepository(DEFAULT_DB).initialize()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    children = [
        subprocess.Popen(
            [sys.executable, "-m", "B_dispatch", "compute", "--db", str(DEFAULT_DB)],
            creationflags=creationflags,
        ),
        subprocess.Popen(
            [sys.executable, "-m", "B_dispatch", "io", "--db", str(DEFAULT_DB)],
            creationflags=creationflags,
        ),
    ]
    try:
        return _launch_gui()
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3.0)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 不指定子命令时启动完整三进程；仅调试界面时使用显式 gui。
    if args.command in (None, "run"):
        return _launch_full_runtime()
    if args.command == "gui":
        return _launch_gui()

    from .repository import EMSRepository

    repository = EMSRepository(args.db)
    repository.initialize()
    if args.command == "init":
        return 0
    if args.command == "compute":
        from .compute_service import EMSComputeService

        EMSComputeService(repository).run()
        return 0
    if args.command == "io":
        current = repository.get_communication_config()
        host = args.host if args.host is not None else str(current["host"])
        port = args.port if args.port is not None else int(current["port"])
        repository.set_communication_config(host=host, port=port, enabled=True)
        from .communication_service import EMSCommunicationService

        EMSCommunicationService(repository).run()
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
