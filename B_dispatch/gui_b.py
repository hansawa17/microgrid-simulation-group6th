# -*- coding: utf-8 -*-
"""南极孤立微电网 EMS 主站 B —— PyQt6 GUI。

本版本只更新界面视觉与页面组织，保留 B GUI 原有业务功能：
- A TCP server 地址/端口输入、连接/断开、实时本机 IP 显示；
- A state 接收、EMS 闭环调度、dispatch 发送与 ACK/unknown 处理；
- 运行监控、实时曲线、参数设置、EMS 调度、历史数据、报警与通信六个页面。

视觉规范参考 C_controller/host/gui.py：浅蓝灰 SCADA 背景、白色卡片、顶部状态胶囊、
左侧导航、蓝色主色和统一字体层级。界面风格统一不等于复制 C 的风机控制职责；B
仍只发送 target/enable，不发送 pitch。
"""

from __future__ import annotations

import math
import socket
import sys
from datetime import datetime
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from B_dispatch.models import DispatchConfig, GridState
    from B_dispatch.operator_core import EMSCore
    from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError
except ImportError:
    from .models import DispatchConfig, GridState
    from .operator_core import EMSCore
    from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError


COLORS = {
    "bg": "#e9eff6", "surface": "#ffffff", "border": "#d7e2ed",
    "header_bg": "#ffffff", "nav_bg": "#f6f9fc", "primary": "#2f6fd6",
    "primary_dark": "#16324f", "text": "#274056", "text_soft": "#45607a",
    "text_muted": "#8b9caf", "good": "#1fa15a", "warn": "#e19a1a",
    "bad": "#d64545", "wind": "#2f6fd6", "avail": "#1fa15a",
    "limit": "#2b93b5", "target": "#e19a1a", "actual": "#6b4fd8",
    "diesel": "#8a5a2b", "load": "#355a78",
}


_QSS = f"""
QMainWindow {{ background: {COLORS['bg']}; }}
QWidget {{ font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI"; color: {COLORS['text']}; }}
#headerBar {{ background: {COLORS['header_bg']}; border-bottom: 1px solid {COLORS['border']}; }}
#logoBadge {{ background: {COLORS['primary']}; border-radius: 6px; }}
#logoGlyph {{ color: #ffffff; font-size: 19px; font-weight: 800; }}
#appTitle {{ color: {COLORS['primary_dark']}; font-size: 20px; font-weight: 700; }}
#appSubtitle {{ color: {COLORS['text_muted']}; font-size: 12px; }}
#clockLabel {{ color: #2b4a68; font-size: 14px; font-weight: 600; }}
QFrame[role="pill"] {{ background: #eef4fa; border: 1px solid #dfe8f1; border-radius: 14px; }}
QLabel[role="pillText"] {{ color: #33516d; font-size: 12px; font-weight: 600; }}
#navPanel {{ background: {COLORS['nav_bg']}; border-right: 1px solid #dbe5ef; }}
QLabel[role="navCaption"] {{ color: {COLORS['text_muted']}; font-size: 12px; font-weight: 700; padding: 4px 18px; }}
QPushButton[role="nav"] {{ background: transparent; border: none; text-align: left; border-radius: 7px; padding: 12px 18px; color: #33516d; font-size: 15px; font-weight: 600; }}
QPushButton[role="nav"]:hover {{ background: #e8f0f8; }}
QPushButton[role="nav"]:checked {{ background: {COLORS['primary']}; color: #ffffff; }}
QLabel[role="siteLabel"] {{ color: #9aabba; font-size: 11px; padding: 8px 18px; }}
QLabel[role="sectionTitle"] {{ color: {COLORS['primary_dark']}; font-size: 19px; font-weight: 700; }}
QFrame[role="card"] {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}
QLabel[role="cardTitle"] {{ color: {COLORS['text_soft']}; font-size: 13px; font-weight: 600; }}
QLabel[role="bigValue"] {{ color: #123a5f; font-size: 28px; font-weight: 700; }}
QLabel[role="unit"] {{ color: #5c758c; font-size: 13px; font-weight: 600; }}
QLabel[role="source"] {{ color: {COLORS['text_muted']}; font-size: 11px; }}
QLabel[role="hint"] {{ color: #a7b6c6; font-size: 12px; }}
QLabel[state="good"] {{ color: {COLORS['good']}; font-size: 16px; font-weight: 700; }}
QLabel[state="warn"] {{ color: {COLORS['warn']}; font-size: 16px; font-weight: 700; }}
QLabel[state="bad"] {{ color: {COLORS['bad']}; font-size: 16px; font-weight: 700; }}
QPushButton[role="primary"] {{ background: {COLORS['primary']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #285fb8; }}
QPushButton[role="success"] {{ background: {COLORS['good']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="success"]:hover {{ background: #1a8a4d; }}
QPushButton[role="danger"] {{ background: {COLORS['bad']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="secondary"] {{ background: #eef3f8; color: #33516d; border: 1px solid #d2dfea; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="secondary"]:hover {{ background: #e2ebf3; }}
QPushButton:disabled {{ background: #cbd5df; color: #728396; }}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QDateTimeEdit {{ background: #ffffff; border: 1px solid #c9d8e6; border-radius: 5px; padding: 6px 8px; color: {COLORS['text']}; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 1px solid {COLORS['primary']}; }}
QTableWidget {{ background: #ffffff; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; color: #2c4359; alternate-background-color: #f7fafd; }}
QTableWidget::item {{ padding: 4px; }}
QHeaderView::section {{ background: #edf3f9; color: #33516d; font-weight: 700; border: none; border-bottom: 1px solid #d5e2ed; padding: 8px; }}
QTabWidget::pane {{ border: 1px solid #d5e2ed; background: #ffffff; border-radius: 6px; }}
QTabBar::tab {{ background: #edf3f9; color: {COLORS['text_soft']}; padding: 9px 22px; border: 1px solid #d5e2ed; border-bottom: none; font-weight: 600; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {COLORS['primary']}; color: #ffffff; }}
QStatusBar {{ background: #e6edf5; color: #5e7891; border-top: 1px solid #d4e1ed; }}
"""


class ValueCard(QtWidgets.QFrame):
    def __init__(self, title: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        t = QtWidgets.QLabel(title); t.setProperty("role", "cardTitle")
        self.value = QtWidgets.QLabel("--"); self.value.setProperty("role", "bigValue")
        u = QtWidgets.QLabel(unit); u.setProperty("role", "unit")
        lay.addWidget(t); lay.addWidget(self.value); lay.addWidget(u)

    def set_value(self, value: object) -> None:
        self.value.setText(str(value))


class TrendChart(QtWidgets.QWidget):
    """轻量趋势图，保持 B 无 pyqtgraph 硬依赖。"""
    def __init__(self, series: Iterable[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.names = [name for name, _ in series]
        self.data = {name: [] for name in self.names}
        self.setMinimumHeight(270)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

    def push(self, values: dict[str, float], max_points: int = 60) -> None:
        for name in self.names:
            self.data[name].append(float(values.get(name, math.nan)))
            if len(self.data[name]) > max_points:
                del self.data[name][:-max_points]
        self.update()

    def clear(self) -> None:
        for values in self.data.values(): values.clear()
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(58, 18, -20, -42); p.fillRect(self.rect(), QtGui.QColor("#f7fafd"))
        p.setPen(QtGui.QPen(QtGui.QColor("#dde6ef"), 1))
        for i in range(6):
            y = r.top() + r.height() * i / 5; p.drawLine(r.left(), int(y), r.right(), int(y))
        for i in range(7):
            x = r.left() + r.width() * i / 6; p.drawLine(int(x), r.top(), int(x), r.bottom())
        values = [v for arr in self.data.values() for v in arr if math.isfinite(v)]
        if not values:
            p.setPen(QtGui.QColor(COLORS["text_muted"])); p.drawText(r, QtCore.Qt.AlignmentFlag.AlignCenter, "等待 EMS 状态数据")
            self._legend(p); return
        lo, hi = min(values), max(values)
        if abs(hi - lo) < 1e-9: lo -= 1; hi += 1
        palette = [COLORS["load"], COLORS["avail"], COLORS["limit"], COLORS["target"], COLORS["actual"], COLORS["diesel"], COLORS["wind"]]
        for idx, name in enumerate(self.names):
            pts = []
            for i, v in enumerate(self.data[name]):
                if not math.isfinite(v): continue
                x = r.left() + r.width() * i / max(1, len(self.data[name]) - 1)
                y = r.bottom() - r.height() * (v - lo) / (hi - lo); pts.append(QtCore.QPointF(x, y))
            if len(pts) >= 2:
                p.setPen(QtGui.QPen(QtGui.QColor(palette[idx % len(palette)]), 2)); p.drawPolyline(QtGui.QPolygonF(pts))
        p.setPen(QtGui.QColor(COLORS["text_muted"])); p.drawText(5, r.top(), 45, 18, QtCore.Qt.AlignmentFlag.AlignRight, f"{hi:.0f}")
        p.drawText(5, r.bottom()-18, 45, 18, QtCore.Qt.AlignmentFlag.AlignRight, f"{lo:.0f}"); self._legend(p)

    def _legend(self, p: QtGui.QPainter) -> None:
        palette = [COLORS["load"], COLORS["avail"], COLORS["limit"], COLORS["target"], COLORS["actual"], COLORS["diesel"], COLORS["wind"]]
        x, y = 62, self.height()-20
        for i, name in enumerate(self.names):
            p.setPen(QtGui.QPen(QtGui.QColor(palette[i % len(palette)]), 3)); p.drawLine(x, y, x+14, y)
            p.setPen(QtGui.QColor(COLORS["text_soft"])); p.drawText(x+18, y+4, name); x += max(78, len(name)*10+38)


class MainWindow(QtWidgets.QMainWindow):
    """B EMS GUI：直接运行和 python -m B_dispatch 均可启动。"""
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("南极孤立微电网 EMS 主站 B")
        self.resize(1920, 1080); self.setMinimumSize(1400, 800)
        self.setStyleSheet(_QSS)
        self.client: EMSTcpClient | None = None
        self.state: GridState | None = None
        self.core: EMSCore | None = None
        self.last_dispatch = None
        self._history_rows: list[list[str]] = []
        self._event_rows: list[list[str]] = []
        self._params = {"wind_min_kw": 0.0, "wind_max_kw": 100.0, "diesel_max_kw": 120.0, "reserve_kw": 10.0, "max_age_s": 2.0}
        self._build_ui(); self._build_timers(); self._set_connection_state("A 服务器未连接", "warn")

    # ------------------------------ common UI ------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        root.addWidget(self._build_header())
        body = QtWidgets.QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        body.addWidget(self._build_nav())
        self.pageStack = QtWidgets.QStackedWidget()
        self.pageStack.addWidget(self._build_monitor_page())
        self.pageStack.addWidget(self._build_realtime_page())
        self.pageStack.addWidget(self._build_params_page())
        self.pageStack.addWidget(self._build_dispatch_page())
        self.pageStack.addWidget(self._build_history_page())
        self.pageStack.addWidget(self._build_alarm_page())
        body.addWidget(self.pageStack, 1); root.addLayout(body, 1)
        self.navGroup = QtWidgets.QButtonGroup(self); self.navGroup.setExclusive(True)
        for i, b in enumerate(self.navButtons): self.navGroup.addButton(b, i); b.clicked.connect(lambda _, i=i: self.pageStack.setCurrentIndex(i))
        self.navButtons[0].setChecked(True)
        self.statusBar().showMessage("B EMS 就绪 | 可在顶部配置 A 服务器 IP 与 TCP 端口")

    def _build_header(self):
        bar = QtWidgets.QFrame(); bar.setObjectName("headerBar"); bar.setFixedHeight(72)
        lay = QtWidgets.QHBoxLayout(bar); lay.setContentsMargins(20,0,20,0); lay.setSpacing(12)
        badge = QtWidgets.QFrame(); badge.setObjectName("logoBadge"); badge.setFixedSize(38,38); badge.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QtWidgets.QVBoxLayout(badge); bl.setContentsMargins(0,0,0,0); glyph = QtWidgets.QLabel("B"); glyph.setObjectName("logoGlyph"); glyph.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); bl.addWidget(glyph); lay.addWidget(badge)
        tb = QtWidgets.QVBoxLayout(); tb.setSpacing(0)
        self.appTitle = QtWidgets.QLabel("南极孤立微电网 EMS 主站"); self.appTitle.setObjectName("appTitle")
        self.appSubtitle = QtWidgets.QLabel("PC-B · 能量管理与调度系统 · 闭环 EMS"); self.appSubtitle.setObjectName("appSubtitle")
        tb.addWidget(self.appTitle); tb.addWidget(self.appSubtitle); lay.addLayout(tb); lay.addStretch(1)
        self.clockLabel = QtWidgets.QLabel(); self.clockLabel.setObjectName("clockLabel"); lay.addWidget(self.clockLabel); lay.addSpacing(6)
        self.aDot, self.aStatus = self._status_pill(lay, "A 服务器未连接", COLORS["warn"])
        self.emsDot, self.emsStatus = self._status_pill(lay, "EMS 正常", COLORS["good"])
        self.cStatus = self._status_pill(lay, "C 优先", COLORS["primary"])[1]
        return bar

    def _status_pill(self, parent, text, color):
        pill = QtWidgets.QFrame(); pill.setProperty("role","pill"); pill.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        pl = QtWidgets.QHBoxLayout(pill); pl.setContentsMargins(10,5,12,5); pl.setSpacing(7)
        dot = QtWidgets.QFrame(); dot.setFixedSize(10,10); dot.setStyleSheet(f"background:{color};border-radius:5px;")
        label = QtWidgets.QLabel(text); label.setProperty("role","pillText"); pl.addWidget(dot); pl.addWidget(label); parent.addWidget(pill); return dot, label

    def _build_nav(self):
        panel = QtWidgets.QFrame(); panel.setObjectName("navPanel"); panel.setFixedWidth(200)
        lay = QtWidgets.QVBoxLayout(panel); lay.setContentsMargins(0,16,0,16); lay.setSpacing(2)
        cap = QtWidgets.QLabel("功能导航"); cap.setProperty("role","navCaption"); lay.addWidget(cap)
        self.navButtons = []
        for text in ["运行监控", "实时曲线", "参数设置", "EMS 调度", "历史数据", "报警与通信"]:
            b = QtWidgets.QPushButton(text); b.setProperty("role","nav"); b.setCheckable(True); self.navButtons.append(b); lay.addWidget(b)
        lay.addStretch()
        site = QtWidgets.QLabel("B / EMS\nTCP Client → A Server\nDiesel reserve: 10 kW\nC protection priority")
        site.setProperty("role","siteLabel"); lay.addWidget(site); return panel

    def _section(self, title: str):
        x = QtWidgets.QLabel(title); x.setProperty("role","sectionTitle"); return x

    def _card(self):
        x = QtWidgets.QFrame(); x.setProperty("role","card"); return x

    # ------------------------------ pages ------------------------------
    def _build_monitor_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18)
        root.addWidget(self._section("运行监控"))
        conn=self._card(); gl=QtWidgets.QGridLayout(conn); gl.setContentsMargins(16,12,16,12)
        title=QtWidgets.QLabel("A 服务器连接"); title.setProperty("role","cardTitle"); gl.addWidget(title,0,0,1,2)
        gl.addWidget(QtWidgets.QLabel("A 公网 / 局域网可达 IP"),1,0)
        self.host_edit=QtWidgets.QLineEdit("127.0.0.1"); self.host_edit.setPlaceholderText("例如 192.168.1.100"); self.host_edit.setToolTip("填写 A 微电网模拟器的可达 IPv4 地址"); gl.addWidget(self.host_edit,1,1)
        gl.addWidget(QtWidgets.QLabel("TCP 端口"),1,2); self.port_spin=QtWidgets.QSpinBox(); self.port_spin.setRange(1,65535); self.port_spin.setValue(5000); gl.addWidget(self.port_spin,1,3)
        self.connect_btn=QtWidgets.QPushButton("连接 A 服务器"); self.connect_btn.setProperty("role","success"); self.connect_btn.clicked.connect(self._toggle_connection); gl.addWidget(self.connect_btn,1,4)
        self.connection_label=QtWidgets.QLabel("A 服务器未连接"); self.connection_label.setProperty("role","pillText"); gl.addWidget(self.connection_label,1,5)
        self.local_hint=QtWidgets.QLabel(f"本机 IP：{self._local_ip()}"); self.local_hint.setProperty("role","source"); gl.addWidget(self.local_hint,0,5,1,1,QtCore.Qt.AlignmentFlag.AlignRight)
        root.addWidget(conn)
        grid=QtWidgets.QGridLayout(); self.cards={}
        specs=[("load","负荷","kW"),("wind_avail","风电 Available","kW"),("wind_limit","风电 Operating Limit","kW"),("wind_actual","风电 Actual","kW"),("diesel_actual","柴油 Actual","kW"),("wind_target","风电 Target","kW"),("diesel_target","柴油 Target","kW"),("wind_speed","风速","m/s")]
        for i,(key,name,unit) in enumerate(specs): self.cards[key]=ValueCard(name,unit); grid.addWidget(self.cards[key],i//4,i%4)
        root.addLayout(grid)
        info=self._card(); fl=QtWidgets.QFormLayout(info); self.session_label=QtWidgets.QLabel("--"); self.time_label=QtWidgets.QLabel("--"); self.state_label=QtWidgets.QLabel("等待 A state"); self.reason_label=QtWidgets.QLabel("--")
        fl.addRow("Session / Step",self.session_label); fl.addRow("Simulation Time / Age",self.time_label); fl.addRow("风机状态 / Fault",self.state_label); fl.addRow("最近调度",self.reason_label); root.addWidget(info); root.addStretch(); return page

    def _build_realtime_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18); root.addWidget(self._section("实时曲线"))
        card=self._card(); lay=QtWidgets.QVBoxLayout(card); self.chart=TrendChart([("Load","kW"),("Available","kW"),("Operating Limit","kW"),("Wind Target","kW"),("Wind Actual","kW"),("Diesel Actual","kW")]); lay.addWidget(self.chart); root.addWidget(card,1)
        tools=QtWidgets.QHBoxLayout(); clear=QtWidgets.QPushButton("清空曲线"); clear.setProperty("role","secondary"); clear.clicked.connect(self.chart.clear); req=QtWidgets.QPushButton("立即请求 A 状态"); req.setProperty("role","primary"); req.clicked.connect(self._request_state); tools.addWidget(clear); tools.addWidget(req); tools.addStretch(); root.addLayout(tools); return page

    def _build_params_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18); root.addWidget(self._section("参数设置"))
        card=self._card(); form=QtWidgets.QFormLayout(card); form.setContentsMargins(18,18,18,18)
        self.wind_min=QtWidgets.QDoubleSpinBox(); self.wind_min.setRange(0,100000); self.wind_min.setValue(0)
        self.wind_max=QtWidgets.QDoubleSpinBox(); self.wind_max.setRange(0,100000); self.wind_max.setValue(100)
        self.diesel_max=QtWidgets.QDoubleSpinBox(); self.diesel_max.setRange(10,100000); self.diesel_max.setValue(120)
        self.reserve=QtWidgets.QDoubleSpinBox(); self.reserve.setRange(10,100000); self.reserve.setValue(10); self.reserve.setEnabled(False)
        self.max_age=QtWidgets.QDoubleSpinBox(); self.max_age.setRange(0.01,3600); self.max_age.setValue(2)
        form.addRow("风电最小目标 (kW)",self.wind_min); form.addRow("风电额定上限 (kW)",self.wind_max); form.addRow("柴油额定上限 (kW)",self.diesel_max); form.addRow("柴油 Reserve (kW)",self.reserve); form.addRow("状态最大年龄 (s)",self.max_age)
        hint=QtWidgets.QLabel("采集/调度周期沿用 B runtime：1 s / 5 s。Reserve 固定 10 kW；C 保护优先。参数仅影响 B 本地计算，不修改 A 的 actual。"); hint.setProperty("role","hint"); form.addRow(hint)
        save=QtWidgets.QPushButton("应用本地 EMS 参数"); save.setProperty("role","primary"); save.clicked.connect(self._apply_params); form.addRow(save); root.addWidget(card); root.addStretch(); return page

    def _build_dispatch_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18); root.addWidget(self._section("EMS 调度"))
        buttons=QtWidgets.QHBoxLayout(); self.request_btn=QtWidgets.QPushButton("立即请求 A 状态"); self.request_btn.setProperty("role","secondary"); self.request_btn.clicked.connect(self._request_state); self.dispatch_btn=QtWidgets.QPushButton("计算并发送 B Dispatch"); self.dispatch_btn.setProperty("role","primary"); self.dispatch_btn.clicked.connect(self._dispatch); buttons.addWidget(self.request_btn); buttons.addWidget(self.dispatch_btn); buttons.addStretch(); root.addLayout(buttons)
        result=self._card(); form=QtWidgets.QFormLayout(result); self.result_wind=QtWidgets.QLabel("--"); self.result_diesel=QtWidgets.QLabel("--"); self.result_unserved=QtWidgets.QLabel("--"); self.result_surplus=QtWidgets.QLabel("--"); self.result_reason=QtWidgets.QLabel("--")
        form.addRow("Wind Target",self.result_wind); form.addRow("Diesel Target",self.result_diesel); form.addRow("Target Unserved",self.result_unserved); form.addRow("Target Surplus",self.result_surplus); form.addRow("Reason",self.result_reason); root.addWidget(result)
        policy=self._card(); f=QtWidgets.QFormLayout(policy); f.addRow("柴油 Reserve","10 kW（固定）"); f.addRow("柴油正常调度上限","diesel_max - 10 kW"); f.addRow("C 优先","C fault / operating limit 优先于 B 正常请求"); f.addRow("Pitch","B 不发送 pitch_target_deg"); root.addWidget(policy); root.addStretch(); return page

    def _build_history_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18); root.addWidget(self._section("历史数据"))
        self.history=QtWidgets.QTableWidget(0,8); self.history.setHorizontalHeaderLabels(["Time","Session/Step","Load","Wind Avail","Wind Limit","Wind Actual","Diesel Actual","Diesel Target"]); self.history.horizontalHeader().setStretchLastSection(True); self.history.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers); root.addWidget(self.history)
        row=QtWidgets.QHBoxLayout(); clear=QtWidgets.QPushButton("清空 GUI 历史"); clear.setProperty("role","secondary"); clear.clicked.connect(self.history.setRowCount); row.addWidget(clear); row.addStretch(); root.addLayout(row); return page

    def _build_alarm_page(self):
        page=QtWidgets.QWidget(); root=QtWidgets.QVBoxLayout(page); root.setContentsMargins(22,18,22,18); root.addWidget(self._section("报警与通信"))
        card=self._card(); f=QtWidgets.QFormLayout(card); self.tcp_state=QtWidgets.QLabel("未连接"); self.last_ack=QtWidgets.QLabel("--"); self.local_ip=QtWidgets.QLabel(self._local_ip()); self.remote_ip=QtWidgets.QLabel("--"); f.addRow("TCP 状态",self.tcp_state); f.addRow("本机 IP",self.local_ip); f.addRow("A 地址",self.remote_ip); f.addRow("最近 ACK",self.last_ack); root.addWidget(card)
        self.events=QtWidgets.QTableWidget(0,2); self.events.setHorizontalHeaderLabels(["时间","事件"]); self.events.horizontalHeader().setStretchLastSection(True); self.events.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers); root.addWidget(self.events,1); return page

    # ------------------------------ TCP ------------------------------
    @staticmethod
    def _local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8",80)); return s.getsockname()[0]
        except OSError:
            try: return socket.gethostbyname(socket.gethostname())
            except OSError: return "未知"

    def _build_timers(self) -> None:
        self.timer=QtCore.QTimer(self); self.timer.setInterval(250); self.timer.timeout.connect(self._poll_socket); self.timer.start()
        self.clockTimer=QtCore.QTimer(self); self.clockTimer.setInterval(1000); self.clockTimer.timeout.connect(self._update_clock); self.clockTimer.start(); self._update_clock()

    def _update_clock(self): self.clockLabel.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _set_connection_state(self,text,level):
        self.connection_label.setText(text); self.tcp_state.setText(text)
        color={"good":COLORS["good"],"warn":COLORS["warn"],"bad":COLORS["bad"]}.get(level,COLORS["warn"])
        self.aDot.setStyleSheet(f"background:{color};border-radius:5px;")
        self.aStatus.setText("A 在线" if level=="good" else "A 未连接")

    def _log(self,text):
        now=datetime.now().strftime("%H:%M:%S")
        row=self.events.rowCount(); self.events.insertRow(row); self.events.setItem(row,0,QtWidgets.QTableWidgetItem(now)); self.events.setItem(row,1,QtWidgets.QTableWidgetItem(text)); self.events.scrollToBottom(); self.statusBar().showMessage(text)

    def _toggle_connection(self):
        if self.client is not None and self.client.connected: self._disconnect_server()
        else: self._connect_server()

    def _connect_server(self):
        host=self.host_edit.text().strip(); port=int(self.port_spin.value())
        if not host: self._set_connection_state("请输入 A 服务器 IP","bad"); return
        if host=="0.0.0.0": self._set_connection_state("客户端不能使用 0.0.0.0","bad"); return
        self.connect_btn.setEnabled(False); self.connect_btn.setText("连接中…"); QtWidgets.QApplication.processEvents()
        try:
            self.client=EMSTcpClient(host,port,timeout_s=2.0); self.client.connect(); self.remote_ip.setText(f"{host}:{port}"); self._set_connection_state(f"已连接 {host}:{port}","good"); self.connect_btn.setText("断开 A 服务器"); self._log(f"B → A TCP 已连接：{host}:{port}；等待/请求 state")
            self._request_state()
        except Exception as exc:
            self.client=None; self.connect_btn.setText("连接 A 服务器"); self._set_connection_state(f"连接失败：{exc}","bad"); self._log(f"连接 A 失败：{exc}")
        finally: self.connect_btn.setEnabled(True)

    def _disconnect_server(self):
        if self.client:
            try: self.client.close()
            except Exception: pass
        self.client=None; self.state=None; self.connect_btn.setText("连接 A 服务器"); self._set_connection_state("A 服务器未连接","warn"); self._log("已断开 A 服务器")

    def _request_state(self):
        if self.client is None or not self.client.connected: self._log("请先连接 A 服务器"); return
        try: self.client.request_state(full=self.client.needs_full_sync); self._log("已发送 state_request")
        except Exception as exc: self._log(f"state_request 失败：{exc}"); self._set_connection_state(f"通信错误：{exc}","bad")

    def _poll_socket(self):
        c=self.client
        if c is None or not c.connected: return
        try: results=c.receive_once()
        except socket.timeout:
            if c._pending_state_request_seq is None:
                try: c.request_state(full=c.needs_full_sync)
                except Exception: pass
            return
        except (ConnectionError,OSError,ProtocolError) as exc:
            self._set_connection_state(f"通信中断：{exc}","bad"); self._log(f"A 通信中断：{exc}")
            try: c.close()
            except Exception: pass
            self.connect_btn.setText("连接 A 服务器"); return
        for item in results:
            if isinstance(item,GridState): self._apply_state(item)
            elif isinstance(item,Ack): self._apply_ack(item)
        if c._pending_state_request_seq is None and c._pending_ack_seq is None:
            try: c.request_state(full=False)
            except Exception: pass

    # ------------------------------ state / dispatch ------------------------------
    def _apply_state(self,state:GridState):
        self.state=state
        vals={"load":state.load_power_kw,"wind_avail":state.wind_available_kw,"wind_limit":state.wind_operating_limit_kw,"wind_actual":state.wind_actual_kw,"diesel_actual":state.diesel_actual_kw,"wind_target":state.wind_target_kw,"wind_speed":state.wind_speed_mps}
        for k,v in vals.items(): self.cards[k].set_value(f"{v:.2f}")
        self.session_label.setText(f"{state.session_id} / {state.step}"); self.time_label.setText(f"{state.sim_time_s:.3f} s / age {state.received_age_s:.3f} s")
        self.state_label.setText(("FAULT · C 优先" if state.fault else ("RUNNING" if state.wind_running else "STOPPED"))); self.state_label.setProperty("state","bad" if state.fault else ("good" if state.wind_running else "warn")); self.state_label.style().unpolish(self.state_label); self.state_label.style().polish(self.state_label)
        self._set_connection_state(f"已连接 {self.client.host}:{self.client.port}","good")
        self.chart.push({"Load":state.load_power_kw,"Available":state.wind_available_kw,"Operating Limit":state.wind_operating_limit_kw,"Wind Target":state.wind_target_kw,"Wind Actual":state.wind_actual_kw,"Diesel Actual":state.diesel_actual_kw})
        row=self.history.rowCount(); self.history.insertRow(row); data=[datetime.now().strftime("%H:%M:%S"),f"{state.session_id}/{state.step}",f"{state.load_power_kw:.2f}",f"{state.wind_available_kw:.2f}",f"{state.wind_operating_limit_kw:.2f}",f"{state.wind_actual_kw:.2f}",f"{state.diesel_actual_kw:.2f}",f"{state.wind_target_kw:.2f}"]
        for i,v in enumerate(data): self.history.setItem(row,i,QtWidgets.QTableWidgetItem(v))

    def _apply_params(self):
        self._params.update(wind_min_kw=self.wind_min.value(),wind_max_kw=self.wind_max.value(),diesel_max_kw=self.diesel_max.value(),reserve_kw=10.0,max_age_s=self.max_age.value()); self._log(f"已应用 EMS 参数：wind≤{self.wind_max.value():.1f} kW，diesel≤{self.diesel_max.value():.1f} kW，reserve=10 kW，age≤{self.max_age.value():.2f}s")

    def _make_core(self):
        return EMSCore(DispatchConfig(wind_min_kw=float(self._params["wind_min_kw"]),wind_max_kw=float(self._params["wind_max_kw"]),diesel_max_kw=float(self._params["diesel_max_kw"]),reserve_kw=10.0,max_state_age_s=float(self._params["max_age_s"]),c_has_control_priority=True))

    def _dispatch(self):
        if self.state is None or self.client is None or not self.client.connected: self._log("不能调度：尚未收到 A 的有效 state"); return
        try:
            self.core=self._make_core(); decision=self.core.decide(self.state); r=decision.result
            self.result_wind.setText(f"{r.wind_target_kw:.2f} kW"); self.result_diesel.setText(f"{r.diesel_target_kw:.2f} kW"); self.result_unserved.setText(f"{r.target_unserved_kw:.2f} kW"); self.result_surplus.setText(f"{r.target_surplus_kw:.2f} kW"); self.result_reason.setText(r.reason); self.reason_label.setText(r.reason)
            self.dispatch_btn.setEnabled(False); QtWidgets.QApplication.processEvents(); seq=self.client.send_dispatch(decision); ack=self.client.get_ack(seq)
            if ack is None: self._log(f"Dispatch seq={seq} 已发送，但 ACK 尚未建立")
            elif ack.accepted: self.cards["diesel_target"].set_value(f"{r.diesel_target_kw:.2f}"); self._log(f"Dispatch seq={seq} ACK accepted；actual 将在后续 A state 中体现")
            else: self._log(f"Dispatch seq={seq} 被 A 拒绝：{ack.reason}")
        except DispatchDeliveryUnknown as exc: self._log(f"Dispatch 投递结果未知，禁止盲目重发：{exc}")
        except Exception as exc: self._log(f"Dispatch 失败：{exc}")
        finally: self.dispatch_btn.setEnabled(True)

    def _apply_ack(self,ack:Ack):
        self.last_ack.setText(f"seq={ack.ack_seq} · {'accepted' if ack.accepted else 'rejected'} · {ack.reason}"); self._log(f"收到 ACK seq={ack.ack_seq} accepted={ack.accepted} reason={ack.reason}")

    def closeEvent(self,event:QtGui.QCloseEvent):
        if self.client:
            try: self.client.close()
            except Exception: pass
        event.accept()


def main() -> int:
    app=QtWidgets.QApplication(sys.argv); window=MainWindow(); window.show(); return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
