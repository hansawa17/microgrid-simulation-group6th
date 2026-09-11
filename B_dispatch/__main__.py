"""B / EMS package entry point."""
from __future__ import annotations

import argparse
import socket
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "runtime" / "ems.db"


def _build_parser():
    p = argparse.ArgumentParser(
        prog="python -m B_dispatch",
        description="B / EMS 主站启动入口",
    )
    s = p.add_subparsers(dest="command")
    g = s.add_parser("gui")
    g.add_argument("--monitor", action="store_true", help="三进程监视模式：不建立 TCP，仅监视与配置")
    s.add_parser(
        "wind-evaluation",
        help="兼容入口：单独打开风机执行评价（正式运行推荐从主 GUI 进入）",
    )
    s.add_parser("run")
    i = s.add_parser("init")
    i.add_argument("--db", type=Path, default=DEFAULT_DB)
    o = s.add_parser("io")
    o.add_argument("--db", type=Path, default=DEFAULT_DB)
    o.add_argument("--host")
    o.add_argument("--port", type=int)
    c = s.add_parser("compute")
    c.add_argument("--db", type=Path, default=DEFAULT_DB)
    return p


def _get_local_ip():
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
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "未获取"


def _embed_wind_evaluation(main_window, repository, QtCore, QtGui, QtWidgets):
    """Embed the wind execution evaluation into the main GUI as a tab.

    The evaluation view remains read-only and reuses the same repository. If
    the legacy GUI ever stops exposing a tab container, fall back to a dock
    inside the same main window rather than opening a second application window.
    """
    from .wind_execution_gui import WindExecutionWindow

    evaluation = WindExecutionWindow(repository)
    tab_widgets = main_window.findChildren(QtWidgets.QTabWidget)
    if tab_widgets:
        tabs = tab_widgets[0]
        tabs.addTab(evaluation, "风机执行评价")
        return evaluation

    dock = QtWidgets.QDockWidget("风机执行评价", main_window)
    dock.setObjectName("windExecutionEvaluationDock")
    dock.setWidget(evaluation)
    main_window.addDockWidget(
        QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
        dock,
    )
    dock.hide()

    action = QtGui.QAction("风机执行评价", main_window)
    action.triggered.connect(lambda: dock.show())
    main_window.menuBar().addAction(action)
    return evaluation


def _launch_gui(monitor=False):
    from PyQt6 import QtCore, QtGui, QtWidgets
    from . import gui_b
    from .repository import EMSRepository

    class NetworkAwareMainWindow(gui_b.MainWindow):
        def __init__(self):
            super().__init__(monitor=monitor)
            ip = _get_local_ip()
            if hasattr(self, "appSubtitle"):
                self.appSubtitle.setText(
                    f"PC-B · 能量管理与调度系统 · 闭环 EMS · 本机 IP：{ip}"
                )
            self.setWindowTitle(f"南极孤立微电网 EMS 主站 B · 本机 IP：{ip}")
            self._wind_eval_repo = EMSRepository(DEFAULT_DB)
            self._wind_eval_repo.initialize()
            self._wind_eval_widget = _embed_wind_evaluation(
                self,
                self._wind_eval_repo,
                QtCore,
                QtGui,
                QtWidgets,
            )
            self._wind_eval_widget.refresh()
            self.statusBar().addPermanentWidget(QtWidgets.QLabel(f"本机 IP：{ip}"))

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = NetworkAwareMainWindow()
    w.show()
    return app.exec()


def _launch_full_runtime():
    import subprocess
    import sys

    from .repository import EMSRepository

    EMSRepository(DEFAULT_DB).initialize()
    # 三进程模式：B_IO 负责 TCP 通信，compute 负责调度计算，GUI 仅监视与配置。
    # 关闭 GUI 后，B_IO 与 compute 作为独立进程继续运行。
    project_root = Path(__file__).resolve().parents[1]
    kwargs = {"cwd": str(project_root)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    db_arg = str(DEFAULT_DB)
    subprocess.Popen(
        [sys.executable, "-m", "B_dispatch", "compute", "--db", db_arg], **kwargs
    )
    subprocess.Popen(
        [sys.executable, "-m", "B_dispatch", "io", "--db", db_arg], **kwargs
    )
    return _launch_gui(monitor=True)


def _launch_wind_evaluation():
    """Backward-compatible standalone entry; formal use is the main GUI tab."""
    from PyQt6 import QtWidgets
    from .repository import EMSRepository
    from .wind_execution_gui import WindExecutionWindow

    repo = EMSRepository(DEFAULT_DB)
    repo.initialize()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = WindExecutionWindow(repo)
    w.show()
    return app.exec()


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.command in (None, "run"):
        return _launch_full_runtime()
    if args.command == "gui":
        return _launch_gui(monitor=args.monitor)
    if args.command == "wind-evaluation":
        return _launch_wind_evaluation()
    from .repository import EMSRepository

    repo = EMSRepository(args.db)
    repo.initialize()
    if args.command == "init":
        return 0
    if args.command == "compute":
        from .compute_service import EMSComputeService

        EMSComputeService(repo).run()
        return 0
    if args.command == "io":
        current = repo.get_communication_config()
        host = args.host if args.host is not None else str(current["host"])
        port = args.port if args.port is not None else int(current["port"])
        repo.set_communication_config(host=host, port=port, enabled=True)
        from .communication_service import EMSCommunicationService

        EMSCommunicationService(repo).run()
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
