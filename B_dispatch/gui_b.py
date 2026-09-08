# -*- coding: utf-8 -*-
"""南极孤立微电网 EMS 主站 B —— PyQt6 GUI。

B 是 TCP client，A 是 TCP server。本文件可以直接运行：
    python B_dispatch/gui_b.py

GUI 允许操作员填写 A 的公网/局域网可达 IPv4 地址和 TCP 端口，主动连接 A，
接收 state，并在收到有效状态后执行 B 的闭环调度。B 只发送 dispatch target / enable，
不发送 pitch，也不修改 A-owned actual 值。C 的保护/控制优先级由 B_dispatch.dispatch 保证。
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from B_dispatch.models import DispatchConfig, GridState
    from B_dispatch.operator_core import EMSCore
    from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError
except ImportError:
    from .models import DispatchConfig, GridState
    from .operator_core import EMSCore
    from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError


QSS = """
QMainWindow { background: #e9eff6; }
QWidget { font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI"; color: #274056; }
#header { background: #ffffff; border-bottom: 1px solid #d7e2ed; }
#title { color: #16324f; font-size: 21px; font-weight: 700; }
#subtitle { color: #7f91a3; font-size: 12px; }
#serverCard { background: #ffffff; border: 1px solid #cbdbe8; border-radius: 8px; }
#serverTitle { color: #16324f; font-size: 14px; font-weight: 700; }
QLineEdit, QSpinBox, QDoubleSpinBox { background: #ffffff; border: 1px solid #c7d7e5; border-radius: 5px; padding: 7px; min-height: 20px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #2f6fd6; }
QPushButton { border-radius: 6px; padding: 8px 16px; font-weight: 600; }
QPushButton[role="primary"] { background: #2f6fd6; color: white; border: none; }
QPushButton[role="success"] { background: #1fa15a; color: white; border: none; }
QPushButton[role="danger"] { background: #d64545; color: white; border: none; }
QPushButton[role="secondary"] { background: #eef3f8; color: #33516d; border: 1px solid #d2dfea; }
QPushButton:disabled { background: #cbd5df; color: #728396; }
#statusGood { color: #1b8d50; font-weight: 700; }
#statusWarn { color: #c7830b; font-weight: 700; }
#statusBad { color: #c63c3c; font-weight: 700; }
QFrame[role="card"] { background: #ffffff; border: 1px solid #d7e2ed; border-radius: 8px; }
QLabel[role="cardTitle"] { color: #45607a; font-size: 13px; font-weight: 600; }
QLabel[role="value"] { color: #123a5f; font-size: 25px; font-weight: 700; }
QLabel[role="unit"] { color: #6a8094; font-size: 12px; }
QLabel[role="section"] { color: #16324f; font-size: 19px; font-weight: 700; }
QTableWidget { background: white; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; }
QHeaderView::section { background: #edf3f9; color: #33516d; font-weight: 700; padding: 7px; border: none; }
QStatusBar { background: #e6edf5; color: #5e7891; }
"""


class ValueCard(QtWidgets.QFrame):
    def __init__(self, title: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("role", "cardTitle")
        self.value = QtWidgets.QLabel("--")
        self.value.setProperty("role", "value")
        unit_label = QtWidgets.QLabel(unit)
        unit_label.setProperty("role", "unit")
        layout.addWidget(title_label)
        layout.addWidget(self.value)
        layout.addWidget(unit_label)

    def set_value(self, value: object) -> None:
        self.value.setText(str(value))


class MainWindow(QtWidgets.QMainWindow):
    """B EMS GUI. Direct execution is supported and does not depend on __main__.py."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("南极孤立微电网 EMS 主站 B")
        self.resize(1500, 900)
        self.setMinimumSize(1200, 720)
        self.setStyleSheet(QSS)

        self.client: EMSTcpClient | None = None
        self.state: GridState | None = None
        self.core: EMSCore | None = None
        self.last_dispatch = None
        self._events: list[str] = []

        self._build_ui()
        self._build_timer()
        self._set_connection_state("服务器未连接", "warn")

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("header")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(22, 14, 22, 14)
        title_box = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("南极孤立微电网 EMS 主站 B")
        title.setObjectName("title")
        self.subtitle = QtWidgets.QLabel("B / EMS 闭环调度 · TCP Client")
        self.subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        self.clock = QtWidgets.QLabel()
        header_layout.addWidget(self.clock)
        root.addWidget(header)

        server = QtWidgets.QFrame()
        server.setObjectName("serverCard")
        sl = QtWidgets.QGridLayout(server)
        sl.setContentsMargins(18, 12, 18, 12)
        sl.setHorizontalSpacing(10)
        server_title = QtWidgets.QLabel("A 服务器连接")
        server_title.setObjectName("serverTitle")
        sl.addWidget(server_title, 0, 0)
        sl.addWidget(QtWidgets.QLabel("A 公网/可达 IP"), 1, 0)
        self.host_edit = QtWidgets.QLineEdit("127.0.0.1")
        self.host_edit.setPlaceholderText("例如 203.0.113.10")
        self.host_edit.setToolTip("填写 A（微电网模拟器）服务器的公网或局域网可达 IPv4 地址")
        self.host_edit.setMinimumWidth(250)
        sl.addWidget(self.host_edit, 1, 1)
        sl.addWidget(QtWidgets.QLabel("TCP 端口"), 1, 2)
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5000)
        self.port_spin.setFixedWidth(100)
        sl.addWidget(self.port_spin, 1, 3)
        self.connect_btn = QtWidgets.QPushButton("连接 A 服务器")
        self.connect_btn.setProperty("role", "success")
        self.connect_btn.clicked.connect(self._toggle_connection)
        sl.addWidget(self.connect_btn, 1, 4)
        self.connection_label = QtWidgets.QLabel("服务器未连接")
        self.connection_label.setObjectName("statusWarn")
        sl.addWidget(self.connection_label, 1, 5)
        self.local_hint = QtWidgets.QLabel(f"本机网络：{self._local_ip()}")
        self.local_hint.setStyleSheet("color:#7f91a3; font-size:11px;")
        sl.addWidget(self.local_hint, 0, 5, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        root.addWidget(server)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_monitor(), "实时监控")
        tabs.addTab(self._build_dispatch(), "EMS 调度")
        tabs.addTab(self._build_events(), "通信/事件")
        root.addWidget(tabs, 1)
        self.statusBar().showMessage("B GUI 已启动；请输入 A 服务器地址后连接")

    def _build_monitor(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        title = QtWidgets.QLabel("实时运行状态")
        title.setProperty("role", "section")
        root.addWidget(title)

        grid = QtWidgets.QGridLayout()
        self.cards: dict[str, ValueCard] = {}
        specs = [
            ("load", "负荷", "kW"),
            ("wind_avail", "风电 Available", "kW"),
            ("wind_limit", "风电 Operating Limit", "kW"),
            ("wind_actual", "风电 Actual", "kW"),
            ("diesel_actual", "柴油 Actual", "kW"),
            ("wind_target", "风电 Target", "kW"),
            ("diesel_target", "柴油 Target", "kW"),
            ("wind_speed", "风速", "m/s"),
        ]
        for i, (key, name, unit) in enumerate(specs):
            card = ValueCard(name, unit)
            self.cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        root.addLayout(grid)

        info = QtWidgets.QFrame()
        info.setProperty("role", "card")
        il = QtWidgets.QFormLayout(info)
        self.session_label = QtWidgets.QLabel("--")
        self.step_label = QtWidgets.QLabel("--")
        self.time_label = QtWidgets.QLabel("--")
        self.state_label = QtWidgets.QLabel("等待 A 状态")
        self.state_label.setObjectName("statusWarn")
        self.reason_label = QtWidgets.QLabel("--")
        il.addRow("Session / Step", self.session_label)
        il.addRow("Simulation Time", self.time_label)
        il.addRow("风机运行 / Fault", self.state_label)
        il.addRow("最近调度结果", self.reason_label)
        root.addWidget(info)
        root.addStretch()
        return page

    def _build_dispatch(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        title = QtWidgets.QLabel("闭环 EMS 调度")
        title.setProperty("role", "section")
        root.addWidget(title)

        form = QtWidgets.QFrame()
        form.setProperty("role", "card")
        fl = QtWidgets.QFormLayout(form)
        self.wind_max = QtWidgets.QDoubleSpinBox()
        self.wind_max.setRange(0, 100000)
        self.wind_max.setValue(100)
        self.diesel_max = QtWidgets.QDoubleSpinBox()
        self.diesel_max.setRange(10, 100000)
        self.diesel_max.setValue(120)
        self.reserve = QtWidgets.QDoubleSpinBox()
        self.reserve.setRange(10, 100000)
        self.reserve.setValue(10)
        self.max_age = QtWidgets.QDoubleSpinBox()
        self.max_age.setRange(0.01, 3600)
        self.max_age.setValue(2.0)
        fl.addRow("风电额定上限 (kW)", self.wind_max)
        fl.addRow("柴油额定上限 (kW)", self.diesel_max)
        fl.addRow("柴油 Reserve (kW)", self.reserve)
        fl.addRow("状态最大年龄 (s)", self.max_age)
        root.addWidget(form)

        buttons = QtWidgets.QHBoxLayout()
        self.request_btn = QtWidgets.QPushButton("立即请求 A 状态")
        self.request_btn.setProperty("role", "secondary")
        self.request_btn.clicked.connect(self._request_state)
        self.dispatch_btn = QtWidgets.QPushButton("计算并发送 B Dispatch")
        self.dispatch_btn.setProperty("role", "primary")
        self.dispatch_btn.clicked.connect(self._dispatch)
        buttons.addWidget(self.request_btn)
        buttons.addWidget(self.dispatch_btn)
        buttons.addStretch()
        root.addLayout(buttons)

        result = QtWidgets.QFrame()
        result.setProperty("role", "card")
        rl = QtWidgets.QFormLayout(result)
        self.result_wind = QtWidgets.QLabel("--")
        self.result_diesel = QtWidgets.QLabel("--")
        self.result_unserved = QtWidgets.QLabel("--")
        self.result_reason = QtWidgets.QLabel("--")
        rl.addRow("Wind Target", self.result_wind)
        rl.addRow("Diesel Target", self.result_diesel)
        rl.addRow("Target Unserved", self.result_unserved)
        rl.addRow("Reason", self.result_reason)
        root.addWidget(result)
        root.addStretch()
        return page

    def _build_events(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        title = QtWidgets.QLabel("通信与事件")
        title.setProperty("role", "section")
        layout.addWidget(title)
        self.events = QtWidgets.QTableWidget(0, 2)
        self.events.setHorizontalHeaderLabels(["时间", "事件"])
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.events)
        return page

    def _build_timer(self) -> None:
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll_socket)
        self.timer.start()
        clock_timer = QtCore.QTimer(self)
        clock_timer.setInterval(1000)
        clock_timer.timeout.connect(self._update_clock)
        clock_timer.start()
        self._clock_timer = clock_timer
        self._update_clock()

    @staticmethod
    def _local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "未知"

    def _update_clock(self) -> None:
        self.clock.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _set_connection_state(self, text: str, level: str) -> None:
        self.connection_label.setText(text)
        self.connection_label.setObjectName({"good": "statusGood", "warn": "statusWarn", "bad": "statusBad"}.get(level, "statusWarn"))
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)

    def _log(self, text: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        row = self.events.rowCount()
        self.events.insertRow(row)
        self.events.setItem(row, 0, QtWidgets.QTableWidgetItem(now))
        self.events.setItem(row, 1, QtWidgets.QTableWidgetItem(text))
        self.events.scrollToBottom()
        self.statusBar().showMessage(text)

    def _toggle_connection(self) -> None:
        if self.client is not None and self.client.connected:
            self._disconnect_server()
        else:
            self._connect_server()

    def _connect_server(self) -> None:
        host = self.host_edit.text().strip()
        port = int(self.port_spin.value())
        if not host:
            self._set_connection_state("请输入 A 服务器 IP", "bad")
            self.host_edit.setFocus()
            return
        if host == "0.0.0.0":
            self._set_connection_state("A 客户端地址不能使用 0.0.0.0", "bad")
            return
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("连接中…")
        QtWidgets.QApplication.processEvents()
        try:
            self.client = EMSTcpClient(host, port, timeout_s=2.0)
            self.client.connect()
            self._set_connection_state(f"已连接 {host}:{port}", "good")
            self.connect_btn.setText("断开 A 服务器")
            self._log(f"已建立 B → A TCP 连接：{host}:{port}；已发送 state_request")
        except Exception as exc:
            self.client = None
            self._set_connection_state(f"连接失败：{exc}", "bad")
            self.connect_btn.setText("连接 A 服务器")
            self._log(f"连接 A 失败：{exc}")
        finally:
            self.connect_btn.setEnabled(True)

    def _disconnect_server(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.state = None
        self._set_connection_state("服务器未连接", "warn")
        self.connect_btn.setText("连接 A 服务器")
        self._log("已断开 A 服务器")

    def _request_state(self) -> None:
        if self.client is None or not self.client.connected:
            self._set_connection_state("请先连接 A 服务器", "bad")
            return
        try:
            self.client.request_state(full=self.client.needs_full_sync)
            self._log("已发送 state_request")
        except Exception as exc:
            self._log(f"请求状态失败：{exc}")
            self._set_connection_state(f"通信错误：{exc}", "bad")

    def _poll_socket(self) -> None:
        client = self.client
        if client is None or not client.connected:
            return
        try:
            results = client.receive_once()
        except socket.timeout:
            if client._pending_state_request_seq is None:
                try:
                    client.request_state(full=client.needs_full_sync)
                except Exception as exc:
                    self._log(f"state_request 失败：{exc}")
            return
        except (ConnectionError, OSError, ProtocolError) as exc:
            self._set_connection_state(f"通信中断：{exc}", "bad")
            self._log(f"A 通信中断：{exc}")
            try:
                client.close()
            except Exception:
                pass
            self.connect_btn.setText("连接 A 服务器")
            return

        for item in results:
            if isinstance(item, GridState):
                self._apply_state(item)
            elif isinstance(item, Ack):
                self._log(f"收到 ACK seq={item.ack_seq} accepted={item.accepted} reason={item.reason}")

        if client._pending_state_request_seq is None and client._pending_ack_seq is None:
            try:
                client.request_state(full=False)
            except Exception:
                pass

    def _apply_state(self, state: GridState) -> None:
        self.state = state
        self.cards["load"].set_value(f"{state.load_power_kw:.2f}")
        self.cards["wind_avail"].set_value(f"{state.wind_available_kw:.2f}")
        self.cards["wind_limit"].set_value(f"{state.wind_operating_limit_kw:.2f}")
        self.cards["wind_actual"].set_value(f"{state.wind_actual_kw:.2f}")
        self.cards["diesel_actual"].set_value(f"{state.diesel_actual_kw:.2f}")
        self.cards["wind_target"].set_value(f"{state.wind_target_kw:.2f}")
        self.cards["diesel_target"].set_value("--")
        self.cards["wind_speed"].set_value(f"{state.wind_speed_mps:.2f}")
        self.session_label.setText(f"{state.session_id} / {state.step}")
        self.time_label.setText(f"{state.sim_time_s:.3f} s · age {state.received_age_s:.3f} s")
        if state.fault:
            self.state_label.setText("FAULT · C 优先")
            self.state_label.setObjectName("statusBad")
        else:
            self.state_label.setText("RUNNING" if state.wind_running else "STOPPED")
            self.state_label.setObjectName("statusGood" if state.wind_running else "statusWarn")
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self._set_connection_state(f"已连接 {self.client.host}:{self.client.port} · state seq={self.client._last_incoming_seq}", "good")

    def _make_core(self) -> EMSCore:
        config = DispatchConfig(
            wind_max_kw=float(self.wind_max.value()),
            diesel_max_kw=float(self.diesel_max.value()),
            reserve_kw=float(self.reserve.value()),
            max_state_age_s=float(self.max_age.value()),
            c_has_control_priority=True,
        )
        return EMSCore(config)

    def _dispatch(self) -> None:
        if self.state is None or self.client is None or not self.client.connected:
            self._log("不能调度：尚未收到 A 的有效 state")
            return
        try:
            self.core = self._make_core()
            decision = self.core.decide(self.state)
            result = decision.result
            self.result_wind.setText(f"{result.wind_target_kw:.2f} kW")
            self.result_diesel.setText(f"{result.diesel_target_kw:.2f} kW")
            self.result_unserved.setText(f"{result.target_unserved_kw:.2f} kW")
            self.result_reason.setText(result.reason)
            self.reason_label.setText(result.reason)
            self.dispatch_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()
            seq = self.client.send_dispatch(decision)
            ack = self.client.get_ack(seq)
            if ack is None:
                self._log(f"Dispatch seq={seq} 已发送，但 ACK 未建立")
            elif ack.accepted:
                self.cards["diesel_target"].set_value(f"{result.diesel_target_kw:.2f}")
                self._log(f"Dispatch seq={seq} ACK accepted；目标将在后续 A state 中体现")
            else:
                self._log(f"Dispatch seq={seq} 被 A 拒绝：{ack.reason}")
        except DispatchDeliveryUnknown as exc:
            self._log(f"Dispatch 结果未知，禁止盲目重发：{exc}")
        except Exception as exc:
            self._log(f"Dispatch 失败：{exc}")
        finally:
            self.dispatch_btn.setEnabled(True)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        event.accept()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
