"""B / EMS package exports and GUI startup helpers."""

from __future__ import annotations

import socket


def _install_direct_gui_server_controls() -> None:
    """让直接运行 gui_b.py 时提供 A 服务器公网地址输入和连接控制。

    B 在通信架构中是 TCP client，A 才是 TCP server。因此这里的“服务器 IP”
    是 A 服务器的公网/可达 IPv4 地址，B 使用它主动连接 A，而不是让 B 监听该地址。
    """
    try:
        from PyQt6 import QtCore, QtWidgets
        from .tcpB import EMSTcpClient
    except ImportError:
        return

    original_show = QtWidgets.QMainWindow.show
    if getattr(original_show, "_b_server_hook", False):
        return

    def show_with_server_controls(self):
        try:
            if self.windowTitle() == "南极孤立微电网 EMS 主站 B":
                status_bar = self.statusBar()

                if status_bar.findChild(QtWidgets.QLineEdit, "bServerHostInput") is None:
                    title = QtWidgets.QLabel("A 服务器：")
                    title.setObjectName("bServerTitle")
                    title.setToolTip("输入 A / 微电网模拟器服务器的公网或局域网可达 IPv4 地址")

                    host_input = QtWidgets.QLineEdit()
                    host_input.setObjectName("bServerHostInput")
                    host_input.setPlaceholderText("服务器公网 IP，例如 203.0.113.10")
                    host_input.setText("127.0.0.1")
                    host_input.setFixedWidth(190)
                    host_input.setToolTip("A 服务器 IP；B 将主动连接该地址")

                    port_input = QtWidgets.QSpinBox()
                    port_input.setObjectName("bServerPortInput")
                    port_input.setRange(1, 65535)
                    port_input.setValue(5000)
                    port_input.setFixedWidth(82)
                    port_input.setToolTip("A TCP 服务器端口，默认 5000")

                    connect_button = QtWidgets.QPushButton("连接服务器")
                    connect_button.setObjectName("bServerConnectButton")
                    connect_button.setProperty("role", "success")
                    connect_button.setToolTip("使用输入的服务器 IP 和端口主动连接 A")

                    server_state = QtWidgets.QLabel("服务器未连接")
                    server_state.setObjectName("bServerStateLabel")

                    status_bar.addPermanentWidget(title)
                    status_bar.addPermanentWidget(host_input)
                    status_bar.addPermanentWidget(QtWidgets.QLabel("端口"))
                    status_bar.addPermanentWidget(port_input)
                    status_bar.addPermanentWidget(connect_button)
                    status_bar.addPermanentWidget(server_state)

                    client_holder = {"client": None}
                    self._b_server_client_holder = client_holder

                    def set_status(text: str, ok: bool = False) -> None:
                        server_state.setText(text)
                        server_state.setProperty("state", "good" if ok else "warn")
                        server_state.style().unpolish(server_state)
                        server_state.style().polish(server_state)

                    def connect_server() -> None:
                        host = host_input.text().strip()
                        port = int(port_input.value())
                        if not host:
                            set_status("请输入服务器 IP")
                            host_input.setFocus()
                            return

                        connect_button.setEnabled(False)
                        connect_button.setText("连接中…")
                        QtWidgets.QApplication.processEvents()

                        old_client = client_holder.get("client")
                        if old_client is not None:
                            try:
                                old_client.close()
                            except Exception:
                                pass

                        try:
                            client = EMSTcpClient(host, port, timeout_s=2.0)
                            client.connect()
                            client_holder["client"] = client
                            self._b_server_client = client
                            set_status(f"已连接 {host}:{port}", ok=True)
                            connect_button.setText("断开服务器")
                        except Exception as exc:
                            client_holder["client"] = None
                            self._b_server_client = None
                            set_status(f"连接失败：{exc}")
                            connect_button.setText("连接服务器")
                        finally:
                            connect_button.setEnabled(True)

                    def disconnect_server() -> None:
                        client = client_holder.get("client")
                        if client is not None:
                            try:
                                client.close()
                            except Exception:
                                pass
                        client_holder["client"] = None
                        self._b_server_client = None
                        set_status("服务器未连接")
                        connect_button.setText("连接服务器")

                    def toggle_connection() -> None:
                        client = client_holder.get("client")
                        if client is not None and client.connected:
                            disconnect_server()
                        else:
                            connect_server()

                    connect_button.clicked.connect(toggle_connection)
                    host_input.returnPressed.connect(connect_server)
                    self._b_server_connect = connect_server
                    self._b_server_disconnect = disconnect_server

                    def cleanup_client() -> None:
                        client = client_holder.get("client")
                        if client is not None:
                            try:
                                client.close()
                            except Exception:
                                pass
                            client_holder["client"] = None

                    self.destroyed.connect(cleanup_client)

                    # 将服务器设置也保存在窗口对象中，后续 service/runtime 接入时可直接复用。
                    self._b_server_host_input = host_input
                    self._b_server_port_input = port_input
                    self._b_server_state_label = server_state
                    set_status("服务器未连接")
        except Exception:
            # 服务器控件初始化失败不能阻止 EMS GUI 正常启动。
            pass
        return original_show(self)

    show_with_server_controls._b_server_hook = True
    QtWidgets.QMainWindow.show = show_with_server_controls


_install_direct_gui_server_controls()

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
