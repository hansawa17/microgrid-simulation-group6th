"""B / EMS package exports and GUI startup helpers."""

from __future__ import annotations

import socket


def _get_local_ip() -> str:
    """获取本机当前用于局域网通信的 IPv4 地址。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        # 仅用于让操作系统选择本机出口网卡，不发送业务数据。
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


def _install_direct_gui_ip_display() -> None:
    """让直接运行 B_dispatch/gui_b.py 时也能显示本机 IP。

    gui_b.py 自己启动时不会经过 B_dispatch/__main__.py，因此这里在
    B 包被导入时给 B GUI 主窗口安装一个轻量的显示钩子。
    """
    try:
        from PyQt6 import QtWidgets
    except ImportError:
        return

    original_show = QtWidgets.QMainWindow.show
    if getattr(original_show, "_b_ip_hook", False):
        return

    def show_with_local_ip(self):
        try:
            if self.windowTitle() == "南极孤立微电网 EMS 主站 B":
                ip = _get_local_ip()
                self.setWindowTitle(f"南极孤立微电网 EMS 主站 B · 本机 IP：{ip}")

                subtitle = getattr(self, "appSubtitle", None)
                if subtitle is not None:
                    subtitle.setText(
                        f"PC-B · 能量管理与调度系统 · 闭环 EMS · 本机 IP：{ip}"
                    )

                status_bar = self.statusBar()
                if status_bar.findChild(QtWidgets.QLabel, "bLocalIpLabel") is None:
                    label = QtWidgets.QLabel(f"本机 IP：{ip}")
                    label.setObjectName("bLocalIpLabel")
                    label.setToolTip("B 主站当前电脑用于局域网通信的 IPv4 地址")
                    status_bar.addPermanentWidget(label)
        except Exception:
            # IP 展示失败不能阻止 EMS GUI 正常启动。
            pass
        return original_show(self)

    show_with_local_ip._b_ip_hook = True
    QtWidgets.QMainWindow.show = show_with_local_ip


_install_direct_gui_ip_display()

from .dispatch import DispatchError, calculate_dispatch
from .models import DispatchConfig, DispatchResult, GridState
from .operator_core import EMSCore, EMSDecision
from .runtime import EMSRuntime, RuntimeConfig
from .serviceB import EMSServiceB
from .tcpB import Ack, EMSTcpClient, JsonLineFramer, ProtocolError, encode_frame, validate_envelope

__all__ = [
    "Ack", "DispatchConfig", "DispatchError", "DispatchResult", "EMSCore", "EMSDecision",
    "EMSRuntime", "EMSServiceB", "EMSTcpClient", "GridState", "JsonLineFramer", "ProtocolError",
    "RuntimeConfig", "calculate_dispatch", "encode_frame", "validate_envelope",
]
