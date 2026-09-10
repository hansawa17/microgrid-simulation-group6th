# -*- coding: utf-8 -*-
"""B / EMS PyQt6 SCADA GUI.

本界面按课程 3.2 要求组织：SCADA 副本、周期采集/调度、设备参数、连接状态、
调度与四遥历史、日志、调度评价，并保留 B→A TCP 客户端的 IP/端口配置。

设计边界：B 只发送 target/enable；不写 A 的 actual，不发送 pitch。
C 对风机保护/控制具有优先权；柴油 reserve 固定为 10 kW；柴油允许 OFF。
"""
from __future__ import annotations

import csv
import math
import queue
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from B_dispatch.models import DispatchConfig, GridState
    from B_dispatch.operator_core import EMSCore
    from B_dispatch.repository import EMSRepository
    from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError, parse_endpoint
except ImportError:
    from .models import DispatchConfig, GridState
    from .operator_core import EMSCore
    from .repository import EMSRepository
    from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError, parse_endpoint

BLUE = '#2f6fd6'
BG = '#e9eff6'
BORDER = '#d7e2ed'
DARK = '#16324f'
TEXT = '#274056'
MUTED = '#7f93a7'
GOOD = '#1fa15a'
BAD = '#d64545'
QSS = f"""
QMainWindow {{ background:{BG}; }}
QWidget {{ font-family:'Microsoft YaHei','Segoe UI'; color:{TEXT}; }}
#header {{ background:#fff; border-bottom:1px solid {BORDER}; }}
#logo {{ background:{BLUE}; color:#fff; border-radius:6px; font-size:19px; font-weight:800; }}
#title {{ color:{DARK}; font-size:20px; font-weight:700; }}
#subtitle {{ color:{MUTED}; font-size:12px; }}
#nav {{ background:#f6f9fc; border-right:1px solid #dbe5ef; }}
QPushButton[nav='true'] {{ background:transparent; border:none; border-radius:7px; text-align:left; padding:11px 16px; color:#33516d; font-size:14px; font-weight:600; }}
QPushButton[nav='true']:hover {{ background:#e8f0f8; }}
QPushButton[nav='true']:checked {{ background:{BLUE}; color:#fff; }}
QFrame[card='true'] {{ background:#fff; border:1px solid {BORDER}; border-radius:8px; }}
QLabel[section='true'] {{ color:{DARK}; font-size:19px; font-weight:700; }}
QLabel[cardtitle='true'] {{ color:#45607a; font-size:13px; font-weight:600; }}
QLabel[value='true'] {{ color:#123a5f; font-size:24px; font-weight:700; }}
QLabel[hint='true'] {{ color:{MUTED}; font-size:11px; }}
QPushButton[kind='primary'] {{ background:{BLUE}; color:#fff; border:none; border-radius:6px; padding:8px 16px; font-weight:600; }}
QPushButton[kind='success'] {{ background:{GOOD}; color:#fff; border:none; border-radius:6px; padding:8px 16px; font-weight:600; }}
QPushButton[kind='danger'] {{ background:{BAD}; color:#fff; border:none; border-radius:6px; padding:8px 16px; font-weight:600; }}
QPushButton[kind='secondary'] {{ background:#eef3f8; color:#33516d; border:1px solid #d2dfea; border-radius:6px; padding:8px 16px; font-weight:600; }}
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox,QDateTimeEdit {{ background:#fff; border:1px solid #c9d8e6; border-radius:5px; padding:6px 8px; min-height:20px; }}
QTableWidget {{ background:#fff; border:1px solid #d5e2ed; gridline-color:#e4ecf3; alternate-background-color:#f7fafd; }}
QHeaderView::section {{ background:#edf3f9; color:#33516d; font-weight:700; border:none; border-bottom:1px solid #d5e2ed; padding:8px; }}
QStatusBar {{ background:#e6edf5; color:#5e7891; }}
"""


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return '未知'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class Card(QtWidgets.QFrame):
    def __init__(self, title: str, unit: str = '') -> None:
        super().__init__()
        self.setProperty('card', True)
        l = QtWidgets.QVBoxLayout(self)
        l.setContentsMargins(14, 10, 14, 10)
        t = QtWidgets.QLabel(title); t.setProperty('cardtitle', True)
        self.value = QtWidgets.QLabel('--'); self.value.setProperty('value', True)
        u = QtWidgets.QLabel(unit); u.setProperty('hint', True)
        l.addWidget(t); l.addWidget(self.value); l.addWidget(u)

    def set_value(self, value: object) -> None:
        self.value.setText(str(value))


class TrendChart(QtWidgets.QWidget):
    def __init__(self, names: Iterable[str]) -> None:
        super().__init__()
        self.names = list(names)
        self.data = {n: [] for n in self.names}
        self.setMinimumHeight(300)
        self.palette_colors = ['#355a78','#1fa15a','#2b93b5','#e19a1a','#6b4fd8','#8a5a2b']

    def push(self, row: dict[str, float], max_points: int = 180) -> None:
        for name in self.names:
            v = row.get(name, math.nan)
            self.data[name].append(float(v) if v is not None else math.nan)
            if len(self.data[name]) > max_points:
                del self.data[name][:-max_points]
        self.update()

    def clear(self) -> None:
        for values in self.data.values():
            values.clear()
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor('#f7fafd'))
        r = self.rect().adjusted(58, 18, -18, -46)
        p.setPen(QtGui.QPen(QtGui.QColor('#dde6ef'), 1))
        for i in range(6):
            y = int(r.top() + r.height() * i / 5)
            p.drawLine(r.left(), y, r.right(), y)
        finite = [v for values in self.data.values() for v in values if math.isfinite(v)]
        if not finite:
            p.setPen(QtGui.QColor(MUTED)); p.drawText(r, QtCore.Qt.AlignmentFlag.AlignCenter, '等待 A 状态或本地 mock 数据')
            return
        lo, hi = min(finite), max(finite)
        if hi - lo < 1e-9: lo -= 1; hi += 1
        for idx, name in enumerate(self.names):
            values = self.data[name]; points = []
            for i, value in enumerate(values):
                if not math.isfinite(value): continue
                x = r.left() + r.width() * i / max(1, len(values)-1)
                y = r.bottom() - r.height() * (value-lo)/(hi-lo)
                points.append(QtCore.QPointF(x, y))
            if len(points) >= 2:
                p.setPen(QtGui.QPen(QtGui.QColor(self.palette_colors[idx % len(self.palette_colors)]), 2))
                p.drawPolyline(QtGui.QPolygonF(points))
        p.setPen(QtGui.QColor(MUTED))
        p.drawText(4, r.top(), 48, 18, QtCore.Qt.AlignmentFlag.AlignRight, f'{hi:.0f}')
        p.drawText(4, r.bottom()-18, 48, 18, QtCore.Qt.AlignmentFlag.AlignRight, f'{lo:.0f}')
        x = 64
        y = self.height() - 18
        for idx, name in enumerate(self.names):
            p.setPen(QtGui.QPen(QtGui.QColor(self.palette_colors[idx % len(self.palette_colors)]), 3))
            p.drawLine(x, y, x+14, y)
            p.setPen(QtGui.QColor('#45607a')); p.drawText(x+18, y+4, name)
            x += max(82, len(name)*9+42)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('南极孤立微电网 EMS 主站 B')
        self.resize(1920, 1080)
        self.setMinimumSize(1400, 800)
        self.setStyleSheet(QSS)
        self.client: EMSTcpClient | None = None
        self.state: GridState | None = None
        self.last_decision = None
        self.last_ack: Ack | None = None
        self.demo_mode = True
        self.db_path = Path(__file__).resolve().parents[1] / 'data' / 'runtime' / 'ems.db'
        self.repo = EMSRepository(self.db_path)
        self.params = {'wind_min_kw':0.0,'wind_max_kw':100.0,'diesel_max_kw':120.0,'reserve_kw':10.0,'max_age_s':2.0,
                       'poll_period_s':1.0,'dispatch_period_s':5.0,'closed_loop':True}
        self.core = EMSCore(self.config())
        self.auto_dispatch = False
        self.trace_rows: list[tuple] = []
        self.log_lines: list[str] = []
        # Manual dispatch is queued instead of rejected when a state_request is outstanding.
        self._manual_dispatch_pending = False
        self._connection_desired = False
        self._connection_endpoint: tuple[str, int] | None = None
        self._connection_generation = 0
        self._connect_in_progress = False
        self._connect_results: queue.Queue[tuple[int, EMSTcpClient, Exception | None]] = queue.Queue()
        self._reconnect_attempt = 0
        self._reconnect_due = 0.0
        self._building = True
        self.build_ui()
        self.load_db_config()
        self.build_timers()
        self._building = False
        self.set_connection_state(False)
        self.log('GUI 启动：本地 mock 模式；A 为 TCP server，B 为 TCP client')

    def config(self) -> DispatchConfig:
        return DispatchConfig(wind_min_kw=self.params['wind_min_kw'], wind_max_kw=self.params['wind_max_kw'],
                              diesel_max_kw=self.params['diesel_max_kw'], reserve_kw=10.0,
                              max_state_age_s=self.params['max_age_s'], c_has_control_priority=True)

    def make_card(self) -> QtWidgets.QFrame:
        c = QtWidgets.QFrame(); c.setProperty('card', True); return c

    def section(self, text: str) -> QtWidgets.QLabel:
        x = QtWidgets.QLabel(text); x.setProperty('section', True); return x

    def build_ui(self) -> None:
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        outer.addWidget(self.header())
        body = QtWidgets.QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        body.addWidget(self.nav())
        self.stack = QtWidgets.QStackedWidget()
        factories = [self.monitor_page, self.curve_page, self.demo_page, self.params_page,
                     self.dispatch_page, self.history_page, self.comm_page, self.alarm_page]
        for factory in factories: self.stack.addWidget(factory())
        body.addWidget(self.stack, 1); outer.addLayout(body, 1)
        self.group = QtWidgets.QButtonGroup(self); self.group.setExclusive(True)
        for i, button in enumerate(self.nav_buttons):
            self.group.addButton(button, i)
            button.clicked.connect(lambda _, j=i: self.stack.setCurrentIndex(j))
        self.nav_buttons[0].setChecked(True)
        self.statusBar().showMessage('B EMS 就绪')

    def header(self) -> QtWidgets.QFrame:
        bar = QtWidgets.QFrame(); bar.setObjectName('header'); bar.setFixedHeight(72)
        l = QtWidgets.QHBoxLayout(bar); l.setContentsMargins(20,0,20,0)
        logo = QtWidgets.QLabel('B'); logo.setObjectName('logo'); logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); logo.setFixedSize(38,38); l.addWidget(logo)
        t = QtWidgets.QVBoxLayout()
        a = QtWidgets.QLabel('南极孤立微电网 EMS 主站'); a.setObjectName('title')
        b = QtWidgets.QLabel('PC-B · 能量管理与调度 · Closed Loop'); b.setObjectName('subtitle')
        t.addWidget(a); t.addWidget(b); l.addLayout(t); l.addStretch()
        self.clock = QtWidgets.QLabel(); l.addWidget(self.clock)
        self.a_status = QtWidgets.QLabel('A 未连接'); l.addWidget(self.a_status)
        self.mode_status = QtWidgets.QLabel('本地演示'); l.addWidget(self.mode_status)
        l.addWidget(QtWidgets.QLabel('C 优先'))
        return bar

    def nav(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(); panel.setObjectName('nav'); panel.setFixedWidth(215)
        l = QtWidgets.QVBoxLayout(panel); l.setContentsMargins(0,16,0,16)
        cap = QtWidgets.QLabel('功能导航'); cap.setProperty('hint', True); l.addWidget(cap)
        self.nav_buttons = []
        labels = ['运行监控','实时曲线','本地场景 / 手动调度','参数设置','EMS 调度','历史数据','通信诊断','报警与评价']
        for text in labels:
            b = QtWidgets.QPushButton(text); b.setProperty('nav', True); b.setCheckable(True); self.nav_buttons.append(b); l.addWidget(b)
        l.addStretch()
        hint = QtWidgets.QLabel('B / EMS\nTCP Client → A Server\nReserve = 10 kW\nC protection priority\nPython 3.11 · PyQt6')
        hint.setProperty('hint', True); l.addWidget(hint)
        return panel

    def connection_card(self) -> QtWidgets.QFrame:
        c = self.make_card(); g = QtWidgets.QGridLayout(c); g.setContentsMargins(14,10,14,10)
        g.addWidget(QtWidgets.QLabel('A 服务器连接'),0,0,1,2)
        g.addWidget(QtWidgets.QLabel('A 可达地址'),1,0); self.host = QtWidgets.QLineEdit('127.0.0.1'); self.host.setPlaceholderText('192.168.1.100 或 public.example:38243'); g.addWidget(self.host,1,1)
        g.addWidget(QtWidgets.QLabel('TCP 端口'),1,2); self.port = QtWidgets.QSpinBox(); self.port.setRange(1,65535); self.port.setValue(5000); g.addWidget(self.port,1,3)
        self.connect_btn = QtWidgets.QPushButton('连接 A 服务器'); self.connect_btn.setProperty('kind','success'); self.connect_btn.clicked.connect(self.toggle_connection); g.addWidget(self.connect_btn,1,4)
        self.request_btn = QtWidgets.QPushButton('请求状态'); self.request_btn.setProperty('kind','primary'); self.request_btn.clicked.connect(self.request_state); g.addWidget(self.request_btn,1,5)
        ip = QtWidgets.QLabel(f'本机 IP：{local_ip()}'); ip.setProperty('hint', True); g.addWidget(ip,0,4,1,2,QtCore.Qt.AlignmentFlag.AlignRight)
        return c

    def monitor_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('运行监控')); l.addWidget(self.connection_card())
        grid = QtWidgets.QGridLayout(); self.cards = {}
        for i, (key,title,unit) in enumerate([
            ('load','负荷','kW'),('avail','风电 Available','kW'),('limit','风电 Operating Limit','kW'),('wind_actual','风电 Actual','kW'),
            ('diesel_actual','柴油 Actual','kW'),('wind_target','风电 Target','kW'),('diesel_target','柴油 Target','kW'),('imbalance','功率不平衡','kW'),
            ('wind_speed','风速','m/s'),('headroom','柴油可用余量','kW'),('age','状态年龄','s'),('pitch','桨距 Actual','deg')]):
            self.cards[key] = Card(title, unit); grid.addWidget(self.cards[key], i//4, i%4)
        l.addLayout(grid)
        detail = self.make_card(); f = QtWidgets.QFormLayout(detail)
        self.session = QtWidgets.QLabel('--'); self.simtime = QtWidgets.QLabel('--'); self.sampled = QtWidgets.QLabel('--'); self.wind_state = QtWidgets.QLabel('--'); self.reason = QtWidgets.QLabel('--')
        for label, widget in [('Session / Step',self.session),('Simulation Time',self.simtime),('A sampled_at_utc',self.sampled),('风机状态 / Fault',self.wind_state),('最近调度',self.reason)]: f.addRow(label,widget)
        l.addWidget(detail); l.addStretch(); return w

    def curve_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('实时曲线'))
        c = self.make_card(); q = QtWidgets.QVBoxLayout(c); self.chart = TrendChart(['Load','Available','Operating Limit','Wind Target','Wind Actual','Diesel Actual']); q.addWidget(self.chart); l.addWidget(c,1)
        row = QtWidgets.QHBoxLayout(); clear = QtWidgets.QPushButton('清空曲线'); clear.setProperty('kind','secondary'); clear.clicked.connect(self.chart.clear); req = QtWidgets.QPushButton('立即请求 A 状态'); req.setProperty('kind','primary'); req.clicked.connect(self.request_state); row.addWidget(clear); row.addWidget(req); row.addStretch(); l.addLayout(row); return w

    def demo_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('本地场景 / 手动调度'))
        note = QtWidgets.QLabel('MOCK：只验证 B 调度算法，不替代 A 的独立 CSV 场景。联网后 A state 是唯一状态源。'); note.setProperty('hint',True); l.addWidget(note)
        c = self.make_card(); f = QtWidgets.QFormLayout(c)
        self.d_load = QtWidgets.QDoubleSpinBox(); self.d_load.setRange(0,1000); self.d_load.setValue(120); self.d_load.setSuffix(' kW')
        self.d_avail = QtWidgets.QDoubleSpinBox(); self.d_avail.setRange(0,1000); self.d_avail.setValue(90); self.d_avail.setSuffix(' kW')
        self.d_limit = QtWidgets.QDoubleSpinBox(); self.d_limit.setRange(0,1000); self.d_limit.setValue(80); self.d_limit.setSuffix(' kW')
        self.d_wind_actual = QtWidgets.QDoubleSpinBox(); self.d_wind_actual.setRange(0,1000); self.d_wind_actual.setValue(70); self.d_wind_actual.setSuffix(' kW')
        self.d_diesel_actual = QtWidgets.QDoubleSpinBox(); self.d_diesel_actual.setRange(0,1000); self.d_diesel_actual.setValue(40); self.d_diesel_actual.setSuffix(' kW')
        self.d_speed = QtWidgets.QDoubleSpinBox(); self.d_speed.setRange(0,50); self.d_speed.setValue(10); self.d_speed.setSuffix(' m/s')
        self.d_fault = QtWidgets.QCheckBox('模拟 C fault / protection active')
        f.addRow('负荷',self.d_load); f.addRow('风电 Available',self.d_avail); f.addRow('风电 Operating Limit',self.d_limit); f.addRow('风电 Actual',self.d_wind_actual); f.addRow('柴油 Actual',self.d_diesel_actual); f.addRow('风速',self.d_speed); f.addRow('故障',self.d_fault)
        l.addWidget(c)
        row = QtWidgets.QHBoxLayout(); b = QtWidgets.QPushButton('生成 Mock State'); b.setProperty('kind','secondary'); b.clicked.connect(self.apply_demo_state); calc = QtWidgets.QPushButton('计算本地调度'); calc.setProperty('kind','primary'); calc.clicked.connect(self.calculate_current); row.addWidget(b); row.addWidget(calc); row.addStretch(); l.addLayout(row)
        self.demo_result = QtWidgets.QTextEdit(); self.demo_result.setReadOnly(True); self.demo_result.setMinimumHeight(190); l.addWidget(self.demo_result,1); return w

    def params_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('参数设置'))
        c = self.make_card(); f = QtWidgets.QFormLayout(c)
        self.p_wind_min = QtWidgets.QDoubleSpinBox(); self.p_wind_min.setRange(0,1000)
        self.p_wind_max = QtWidgets.QDoubleSpinBox(); self.p_wind_max.setRange(0,1000)
        self.p_diesel_max = QtWidgets.QDoubleSpinBox(); self.p_diesel_max.setRange(0,2000)
        self.p_reserve = QtWidgets.QDoubleSpinBox(); self.p_reserve.setRange(10,1000); self.p_reserve.setValue(10); self.p_reserve.setEnabled(False)
        self.p_age = QtWidgets.QDoubleSpinBox(); self.p_age.setRange(0.1,60); self.p_age.setDecimals(2)
        self.p_poll = QtWidgets.QDoubleSpinBox(); self.p_poll.setRange(0.1,60); self.p_poll.setDecimals(2)
        self.p_dispatch = QtWidgets.QDoubleSpinBox(); self.p_dispatch.setRange(0.1,300); self.p_dispatch.setDecimals(2)
        self.p_closed = QtWidgets.QCheckBox('B closed loop（默认开启）'); self.p_closed.setChecked(True)
        for label, widget in [('风电最小目标',self.p_wind_min),('风电容量上限',self.p_wind_max),('柴油容量上限',self.p_diesel_max),('柴油 reserve',self.p_reserve),('最大状态年龄',self.p_age),('采集周期',self.p_poll),('调度周期',self.p_dispatch),('运行模式',self.p_closed)]: f.addRow(label,widget)
        l.addWidget(c)
        note = QtWidgets.QLabel('柴油 reserve 固定 10 kW；正常 B 目标上限 = diesel_max - 10 kW。数值是可配置输入，不代表课程原文给定的物理额定值。'); note.setProperty('hint',True); l.addWidget(note)
        row = QtWidgets.QHBoxLayout(); load = QtWidgets.QPushButton('从 ems.db 读取'); load.setProperty('kind','secondary'); load.clicked.connect(self.load_db_config); save = QtWidgets.QPushButton('保存参数'); save.setProperty('kind','primary'); save.clicked.connect(self.save_params); row.addWidget(load); row.addWidget(save); row.addStretch(); l.addLayout(row); l.addStretch(); return w

    def dispatch_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('EMS 调度'))
        c = self.make_card(); g = QtWidgets.QGridLayout(c); g.addWidget(QtWidgets.QLabel('执行方式'),0,0); self.auto_box = QtWidgets.QCheckBox('自动闭环：按调度周期计算并下发'); self.auto_box.setChecked(False); self.auto_box.toggled.connect(self.set_auto); g.addWidget(self.auto_box,0,1,1,3)
        self.calc_btn = QtWidgets.QPushButton('计算当前状态'); self.calc_btn.setProperty('kind','primary'); self.calc_btn.clicked.connect(self.calculate_current); g.addWidget(self.calc_btn,1,0)
        self.send_btn = QtWidgets.QPushButton('下发当前调度'); self.send_btn.setProperty('kind','success'); self.send_btn.clicked.connect(self.send_current); g.addWidget(self.send_btn,1,1)
        self.safe_label = QtWidgets.QLabel('安全策略：点击下发后先获取最新 A state；若已有 state_request，则自动等待；状态过期不下发；ACK 未知不盲目重发。'); self.safe_label.setProperty('hint',True); g.addWidget(self.safe_label,2,0,1,4)
        l.addWidget(c)
        out = self.make_card(); f = QtWidgets.QFormLayout(out); self.d_w = QtWidgets.QLabel('--'); self.d_d = QtWidgets.QLabel('--'); self.d_unserved = QtWidgets.QLabel('--'); self.d_surplus = QtWidgets.QLabel('--'); self.d_enable = QtWidgets.QLabel('--'); self.d_reason = QtWidgets.QLabel('--'); self.ack_label = QtWidgets.QLabel('--')
        for label, widget in [('风电目标',self.d_w),('柴油目标',self.d_d),('目标缺供',self.d_unserved),('目标过剩',self.d_surplus),('Enable',self.d_enable),('Reason',self.d_reason),('最近 ACK',self.ack_label)]: f.addRow(label,widget)
        l.addWidget(out); l.addStretch(); return w

    def history_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('历史数据'))
        bar = self.make_card(); g = QtWidgets.QGridLayout(bar); g.addWidget(QtWidgets.QLabel('Session'),0,0); self.h_session = QtWidgets.QLineEdit(); g.addWidget(self.h_session,0,1); g.addWidget(QtWidgets.QLabel('最多行数'),0,2); self.h_limit = QtWidgets.QSpinBox(); self.h_limit.setRange(10,5000); self.h_limit.setValue(200); g.addWidget(self.h_limit,0,3)
        q = QtWidgets.QPushButton('查询'); q.setProperty('kind','primary'); q.clicked.connect(self.refresh_history); ex = QtWidgets.QPushButton('导出 CSV'); ex.setProperty('kind','secondary'); ex.clicked.connect(self.export_history); g.addWidget(q,0,4); g.addWidget(ex,0,5); l.addWidget(bar)
        self.history = QtWidgets.QTableWidget(0,8); self.history.setHorizontalHeaderLabels(['时间','Session','Step','Load','Wind Avail','Wind Limit','Wind Actual','Diesel Actual']); self.history.setAlternatingRowColors(True); self.history.horizontalHeader().setStretchLastSection(True); l.addWidget(self.history,1); return w

    def comm_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('通信诊断'))
        c = self.make_card(); f = QtWidgets.QFormLayout(c); self.comm_state=QtWidgets.QLabel('--'); self.comm_pending=QtWidgets.QLabel('--'); self.comm_full=QtWidgets.QLabel('--'); self.comm_seq=QtWidgets.QLabel('--'); self.comm_ack=QtWidgets.QLabel('--')
        for label,widget in [('A 连接状态',self.comm_state),('未完成 state_request',self.comm_pending),('需要 full sync',self.comm_full),('最近入站 seq',self.comm_seq),('最近 ACK',self.comm_ack)]: f.addRow(label,widget)
        l.addWidget(c); self.log_view=QtWidgets.QPlainTextEdit(); self.log_view.setReadOnly(True); l.addWidget(self.log_view,1); return w

    def alarm_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('报警与评价'))
        top = QtWidgets.QHBoxLayout(); self.kpi_total=Card('评价样本','项'); self.kpi_unserved=Card('目标缺供','kWh-equivalent'); self.kpi_surplus=Card('目标过剩','kWh-equivalent'); self.kpi_ack=Card('ACK 接受率','%'); top.addWidget(self.kpi_total); top.addWidget(self.kpi_unserved); top.addWidget(self.kpi_surplus); top.addWidget(self.kpi_ack); l.addLayout(top)
        self.alarm_table=QtWidgets.QTableWidget(0,5); self.alarm_table.setHorizontalHeaderLabels(['等级','事件','说明','Session','Step']); self.alarm_table.horizontalHeader().setStretchLastSection(True); l.addWidget(self.alarm_table,1)
        return w

    def build_timers(self) -> None:
        self.clock_timer = QtCore.QTimer(self); self.clock_timer.timeout.connect(self.tick_clock); self.clock_timer.start(500)
        # Keep socket work non-blocking while collecting state/ACK promptly on LAN.
        self.poll_timer = QtCore.QTimer(self); self.poll_timer.timeout.connect(self.poll_socket); self.poll_timer.start(50)
        self.runtime_timer = QtCore.QTimer(self); self.runtime_timer.timeout.connect(self.runtime_tick)
        self.set_runtime_timer()
        self.history_timer = QtCore.QTimer(self); self.history_timer.timeout.connect(self.refresh_views); self.history_timer.start(3000)

    def set_runtime_timer(self) -> None:
        self.runtime_timer.stop(); self.runtime_timer.start(max(100,int(self.params['dispatch_period_s']*1000)))

    def tick_clock(self) -> None:
        self.clock.setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.update_comm_diag()

    def log(self, message: str) -> None:
        line = f'{datetime.now().strftime("%H:%M:%S")}  {message}'
        self.log_lines.append(line); self.log_lines = self.log_lines[-500:]
        if hasattr(self,'log_view'): self.log_view.setPlainText('\n'.join(self.log_lines)); self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def set_connection_state(self, connected: bool) -> None:
        self.demo_mode = not connected
        self.a_status.setText('A 已连接' if connected else 'A 未连接')
        self.mode_status.setText('TCP 闭环' if connected else '本地演示')
        self.connect_btn.setText('断开 A 服务器' if connected else '连接 A 服务器')
        self.connect_btn.setProperty('kind','danger' if connected else 'success')
        self.connect_btn.style().unpolish(self.connect_btn); self.connect_btn.style().polish(self.connect_btn)
        if not connected:
            self._manual_dispatch_pending = False
            self.send_btn.setEnabled(True)
            self.send_btn.setText('下发当前调度')
        self.update_comm_diag()

    def _set_connecting_state(self, detail: str) -> None:
        self.a_status.setText(detail)
        self.mode_status.setText('TCP 重连中')
        self.connect_btn.setText('取消连接')
        self.connect_btn.setProperty('kind','danger')
        self.connect_btn.style().unpolish(self.connect_btn); self.connect_btn.style().polish(self.connect_btn)

    def _begin_connection(self) -> None:
        if not self._connection_desired or self._connect_in_progress or self._connection_endpoint is None:
            return
        host, port = self._connection_endpoint
        self._connect_in_progress = True
        self._connection_generation += 1
        generation = self._connection_generation
        candidate = EMSTcpClient(
            host,
            port,
            timeout_s=0.5,
            connect_timeout_s=5.0,
            state_response_timeout_s=6.0,
            state_poll_period_s=self.params['poll_period_s'],
        )
        self._set_connecting_state(f'正在连接 A（第 {self._reconnect_attempt + 1} 次）')
        self.log(f'尝试连接 A：{host}:{port}（建连超时 5 s）')
        if host in {'127.0.0.1', 'localhost', '::1'}:
            self.log('提示：回环地址只适用于 A 与 B 在同一台电脑；跨电脑请填写 A 的局域网 IP 或公网隧道地址')

        def connect_in_background() -> None:
            error: Exception | None = None
            try:
                candidate.connect()
            except Exception as exc:
                error = exc
                candidate.close()
            self._connect_results.put((generation, candidate, error))

        threading.Thread(target=connect_in_background, name='B-A-connect', daemon=True).start()

    def _schedule_reconnect(self, reason: str) -> None:
        if not self._connection_desired:
            return
        self._reconnect_attempt += 1
        delay = min(15.0, float(2 ** min(self._reconnect_attempt - 1, 4)))
        self._reconnect_due = time.monotonic() + delay
        self.set_connection_state(False)
        self._set_connecting_state(f'A 已断开，{delay:g} s 后重连')
        self.log(f'{reason}；{delay:g} s 后自动重连')

    def _progress_connection(self) -> None:
        while True:
            try:
                generation, candidate, error = self._connect_results.get_nowait()
            except queue.Empty:
                break
            if generation != self._connection_generation or not self._connection_desired:
                candidate.close()
                continue
            self._connect_in_progress = False
            if error is not None:
                self._schedule_reconnect(f'连接失败：{error}')
                continue
            self.client = candidate
            self._reconnect_attempt = 0
            self._reconnect_due = 0.0
            self.set_connection_state(True)
            self.log(f'已连接 A：{candidate.host}:{candidate.port}，等待全量 state')

        if (
            self._connection_desired
            and not self._connect_in_progress
            and (not self.client or not self.client.connected)
            and time.monotonic() >= self._reconnect_due
        ):
            self._begin_connection()

    def _handle_connection_loss(self, reason: str) -> None:
        if self.client:
            self.client.close()
        self.client = None
        self._schedule_reconnect(reason)

    def toggle_connection(self) -> None:
        if self._connection_desired or (self.client and self.client.connected):
            self._connection_desired = False
            self._connection_generation += 1
            self._connect_in_progress = False
            if self.client: self.client.close()
            self.client=None; self.set_connection_state(False); self.log('已手动断开 A server，并停止自动重连'); return
        try:
            host, port = parse_endpoint(self.host.text(), int(self.port.value()))
        except Exception as exc:
            self.set_connection_state(False); self.log(f'地址无效：{exc}'); QtWidgets.QMessageBox.warning(self,'TCP 地址错误',str(exc)); return
        self.host.setText(host); self.port.setValue(port)
        self._connection_endpoint = (host, port)
        self._connection_desired = True
        self._reconnect_attempt = 0
        self._reconnect_due = 0.0
        self._begin_connection()

    def request_state(self) -> None:
        if not self.client or not self.client.connected:
            self.log('未连接 A：本地演示模式不会发送 TCP state_request'); return
        try:
            seq = self.client.request_state(full=self.client.needs_full_sync); self.log(f'发送 state_request seq={seq}')
        except Exception as exc:
            self._handle_connection_loss(f'state_request 失败：{exc}')

    def poll_socket(self) -> None:
        self._progress_connection()
        c = self.client
        if not c or not c.connected: return
        try:
            results = c.receive_available()
            got_state = False
            got_ack = False
            for result in results:
                if isinstance(result, GridState):
                    got_state = True
                    self.set_state_snapshot(result)
                    self.log(f'收到 A state session={result.session_id} step={result.step}')
                elif isinstance(result, Ack):
                    got_ack = True
                    self.last_ack=result
                    self.handle_ack(result)
            # The button request is intentionally completed only after A's fresh state
            # has cleared the client's pending state_request flag.
            if got_state and self._manual_dispatch_pending:
                self._try_send_queued_dispatch()
            elif got_ack and self._manual_dispatch_pending and c._pending_state_request_seq is None and c._pending_ack_seq is None:
                self._try_send_queued_dispatch()
        except Exception as exc:
            self._handle_connection_loss(f'TCP 接收异常：{exc}')

    def set_state_snapshot(self, state: GridState) -> None:
        self.state = state; self.demo_mode=False if self.client and self.client.connected else True; self.refresh_state_views(); self.save_state_best_effort()

    def refresh_state_views(self) -> None:
        s=self.state
        if s is None: return
        imbalance=s.load_power_kw-s.wind_actual_kw-s.diesel_actual_kw
        headroom=max(self.params['diesel_max_kw']-s.diesel_actual_kw,0.0)
        wind_target=self.last_decision.result.wind_target_kw if self.last_decision else s.wind_target_kw
        diesel_target=self.last_decision.result.diesel_target_kw if self.last_decision else 0.0
        for key,value in {'load':f'{s.load_power_kw:.1f}','avail':f'{s.wind_available_kw:.1f}','limit':f'{s.wind_operating_limit_kw:.1f}','wind_actual':f'{s.wind_actual_kw:.1f}',
                          'diesel_actual':f'{s.diesel_actual_kw:.1f}','wind_target':f'{wind_target:.1f}','diesel_target':f'{diesel_target:.1f}','imbalance':f'{imbalance:+.1f}',
                          'wind_speed':f'{s.wind_speed_mps:.2f}','headroom':f'{headroom:.1f}','age':f'{s.received_age_s:.2f}','pitch':'--' if s.pitch_actual_deg is None else f'{s.pitch_actual_deg:.1f}'}.items(): self.cards[key].set_value(value)
        self.session.setText(f'{s.session_id} / {s.step}'); self.simtime.setText(f'{s.sim_time_s:.2f} s'); self.sampled.setText(s.sampled_at_utc or '--'); self.wind_state.setText(f'Running={s.wind_running} / Fault={s.fault}'); self.reason.setText(self.last_decision.result.reason if self.last_decision else '--')
        self.chart.push({'Load':s.load_power_kw,'Available':s.wind_available_kw,'Operating Limit':s.wind_operating_limit_kw,'Wind Target':wind_target,'Wind Actual':s.wind_actual_kw,'Diesel Actual':s.diesel_actual_kw})
        self.update_comm_diag(); self.refresh_alarms()

    def demo_state(self) -> GridState:
        return GridState(session_id='GUI-MOCK', step=0, sim_time_s=0.0, wind_speed_mps=self.d_speed.value(), wind_available_kw=self.d_avail.value(),
                         wind_operating_limit_kw=min(self.d_limit.value(),self.d_avail.value()), load_power_kw=self.d_load.value(),
                         wind_actual_kw=self.d_wind_actual.value(), diesel_actual_kw=self.d_diesel_actual.value(), wind_running=True,
                         fault=self.d_fault.isChecked(), received_age_s=0.0, sampled_at_utc=utc_now(), received_at_utc=utc_now(),
                         wind_target_kw=0.0, pitch_actual_deg=0.0)

    def apply_demo_state(self) -> None:
        self.state=self.demo_state(); self.demo_mode=True; self.last_decision=None; self.refresh_state_views(); self.demo_result.setPlainText('Mock State 已加载。')
        self.log('生成本地 mock state')

    def calculate_current(self) -> None:
        if self.state is None: self.state=self.demo_state()
        self.core=EMSCore(self.config())
        try:
            self.last_decision=self.core.decide(self.state)
        except Exception as exc:
            self.last_decision=None; self.log(f'调度计算拒绝：{exc}'); QtWidgets.QMessageBox.warning(self,'调度未执行',str(exc)); return
        r=self.last_decision.result
        self.d_w.setText(f'{r.wind_target_kw:.2f} kW'); self.d_d.setText(f'{r.diesel_target_kw:.2f} kW'); self.d_unserved.setText(f'{r.target_unserved_kw:.2f} kW'); self.d_surplus.setText(f'{r.target_surplus_kw:.2f} kW')
        self.d_enable.setText(f'Wind={r.wind_enable} / Diesel={r.diesel_enable}'); self.d_reason.setText(r.reason)
        self.demo_result.setPlainText(f'wind_target = {r.wind_target_kw:.2f} kW\ndiesel_target = {r.diesel_target_kw:.2f} kW\nwind_enable = {r.wind_enable}\ndiesel_enable = {r.diesel_enable}\ntarget_unserved = {r.target_unserved_kw:.2f} kW\ntarget_surplus = {r.target_surplus_kw:.2f} kW\nreason = {r.reason}')
        self.refresh_state_views(); self.log('EMS decision 已计算')

    def send_current(self) -> None:
        """Queue a manual dispatch behind a fresh A state instead of rejecting it."""
        if not self.client or not self.client.connected:
            self.log('未连接 A：拒绝发送 TCP dispatch'); QtWidgets.QMessageBox.information(self,'当前为本地模式','先连接 A server 才会真正下发 dispatch。'); return
        if self.client._uncertain_dispatch_seq is not None:
            self.log(f'ACK 未知：seq={self.client._uncertain_dispatch_seq}，禁止盲目重发')
            QtWidgets.QMessageBox.warning(self,'ACK 未知','指令可能已到达 A，但 ACK 无法确认。请先恢复连接并核对历史，不要直接重发。')
            return
        if self._manual_dispatch_pending:
            return

        self._manual_dispatch_pending = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText('等待 A 当前状态...')
        self.statusBar().showMessage('等待 A 返回最新 state，随后自动下发 EMS 调度')
        self.log('手动下发已排队：等待 A 当前 state')

        c = self.client
        try:
            # Always obtain a fresh state for a manual send. If one is already
            # outstanding, request_state simply returns its sequence.
            c.request_state(full=c.needs_full_sync)
        except ProtocolError as exc:
            # A dispatch ACK may still be pending. Keep the request queued;
            # poll_socket will retry once the ACK/state transaction is clear.
            self.log(f'当前无法立即请求 state，保持排队：{exc}')
        except Exception as exc:
            self._cancel_queued_dispatch(f'state_request 失败：{exc}', show_message=True)
            return

        if c._pending_state_request_seq is None and c._pending_ack_seq is None:
            self._try_send_queued_dispatch()

    def _try_send_queued_dispatch(self) -> None:
        if not self._manual_dispatch_pending:
            return
        c = self.client
        if not c or not c.connected:
            self._cancel_queued_dispatch('A 已断开，取消排队的 dispatch', show_message=False)
            return
        if c._uncertain_dispatch_seq is not None:
            self._cancel_queued_dispatch(f'ACK 未知 seq={c._uncertain_dispatch_seq}，不自动重发', show_message=False)
            return
        if c._pending_state_request_seq is not None or c._pending_ack_seq is not None:
            return
        if self.state is None:
            self.log('最新 state 尚未进入 GUI，继续等待')
            return

        try:
            # Recompute from the state that just arrived, never from the older
            # decision that was on screen when the user clicked the button.
            self.calculate_current()
            if self.last_decision is None:
                self._cancel_queued_dispatch('最新 state 无法生成有效 EMS decision', show_message=False)
                return
            seq = c.send_dispatch(self.last_decision)
            ack = c.get_ack(seq)
            if ack is not None:
                self.last_ack = ack
                self.handle_ack(ack)
            self.log(f'发送 dispatch seq={seq}，等待 ACK')
            self._manual_dispatch_pending = False
            self.send_btn.setEnabled(True)
            self.send_btn.setText('下发当前调度')
            self.statusBar().showMessage(f'调度已下发 seq={seq}' + (f' / ACK={ack.accepted}' if ack else ''))
        except DispatchDeliveryUnknown as exc:
            self.last_ack=None
            self._manual_dispatch_pending = False
            self.send_btn.setEnabled(True)
            self.send_btn.setText('下发当前调度')
            self.log(f'ACK 未知：seq={exc.seq}，禁止盲目重发')
            QtWidgets.QMessageBox.warning(self,'ACK 未知','指令可能已到达 A，但 ACK 无法确认。请先恢复连接并核对历史，不要直接重发。')
        except ProtocolError as exc:
            # A state may have become pending again between the GUI checks and
            # the transport call. Keep the request queued rather than showing
            # the old "cannot dispatch while a state request is pending" error.
            if c._pending_state_request_seq is not None or c._pending_ack_seq is not None:
                self.log(f'dispatch 等待中：{exc}')
                return
            self._cancel_queued_dispatch(f'dispatch 失败：{exc}', show_message=True)
        except Exception as exc:
            self._cancel_queued_dispatch(f'dispatch 失败：{exc}', show_message=True)

    def _cancel_queued_dispatch(self, message: str, *, show_message: bool) -> None:
        self._manual_dispatch_pending = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText('下发当前调度')
        self.log(message)
        self.statusBar().showMessage(message)
        if show_message:
            QtWidgets.QMessageBox.warning(self,'dispatch 失败',message)

    def handle_ack(self, ack: Ack) -> None:
        self.ack_label.setText(f'seq={ack.ack_seq} / accepted={ack.accepted} / {ack.reason}')
        self.comm_ack.setText(f'{ack.ack_seq} / {ack.accepted}')
        self.statusBar().showMessage(f'ACK seq={ack.ack_seq} accepted={ack.accepted}')
        self.log(f'ACK seq={ack.ack_seq} accepted={ack.accepted} reason={ack.reason}')
        self.refresh_views()

    def set_auto(self, checked: bool) -> None:
        self.auto_dispatch=checked
        self.log(f'自动闭环调度：{"开启" if checked else "关闭"}')

    def runtime_tick(self) -> None:
        if not self.auto_dispatch or not self.params['closed_loop']: return
        if self._manual_dispatch_pending: return
        if not self.client or not self.client.connected: return
        if self.state is None: return
        # Do not fight the transport's state-request / ACK transaction. The
        # next timer tick will run once the connection is idle again.
        if self.client._pending_state_request_seq is not None or self.client._pending_ack_seq is not None: return
        try:
            self.calculate_current(); self.send_current()
        except Exception as exc: self.log(f'自动调度异常：{exc}')

    def save_params(self) -> None:
        reserve=10.0
        values={'wind_min_kw':self.p_wind_min.value(),'wind_max_kw':self.p_wind_max.value(),'diesel_max_kw':self.p_diesel_max.value(),
                'reserve_kw':reserve,'max_age_s':self.p_age.value(),'poll_period_s':self.p_poll.value(),'dispatch_period_s':self.p_dispatch.value(),'closed_loop':self.p_closed.isChecked()}
        if values['wind_max_kw'] < values['wind_min_kw'] or values['diesel_max_kw'] < reserve:
            QtWidgets.QMessageBox.warning(self,'参数错误','请检查风电上下限，并确保柴油容量不小于 10 kW reserve。'); return
        self.params.update(values); self.core=EMSCore(self.config()); self.set_runtime_timer()
        try:
            self.repo.initialize(); self.repo.set_parameters(wind_min_kw=values['wind_min_kw'],wind_max_kw=values['wind_max_kw'],diesel_max_kw=values['diesel_max_kw'],reserve_kw=reserve)
            self.repo.set_runtime_config(poll_period_s=values['poll_period_s'],dispatch_period_s=values['dispatch_period_s'],closed_loop=values['closed_loop'])
            self.log('参数已写入 ems.db')
        except Exception as exc: self.log(f'参数写库失败：{exc}')
        self.statusBar().showMessage('EMS 参数已更新')

    def load_db_config(self) -> None:
        try:
            self.repo.initialize(); p=self.repo.get_parameters(); r=self.repo.get_runtime_config()
            self.params.update(wind_min_kw=p['wind_min_kw'],wind_max_kw=p['wind_max_kw'],diesel_max_kw=p['diesel_max_kw'],reserve_kw=10.0,max_age_s=2.0,
                               poll_period_s=r['poll_period_s'],dispatch_period_s=r['dispatch_period_s'],closed_loop=bool(r['closed_loop']))
        except Exception as exc:
            self.log(f'ems.db 尚未有完整参数，使用 GUI 默认值：{exc}')
        if hasattr(self,'p_wind_min'):
            self.p_wind_min.setValue(self.params['wind_min_kw']); self.p_wind_max.setValue(self.params['wind_max_kw']); self.p_diesel_max.setValue(self.params['diesel_max_kw']); self.p_reserve.setValue(10.0); self.p_age.setValue(self.params['max_age_s']); self.p_poll.setValue(self.params['poll_period_s']); self.p_dispatch.setValue(self.params['dispatch_period_s']); self.p_closed.setChecked(self.params['closed_loop'])
        self.core=EMSCore(self.config()); self.set_runtime_timer() if hasattr(self,'runtime_timer') else None

    def save_state_best_effort(self) -> None:
        if self.state is None: return
        try: self.repo.initialize(); self.repo.save_state(self.state)
        except Exception as exc: self.log(f'状态本地落库失败：{exc}')

    def update_comm_diag(self) -> None:
        c=self.client
        self.comm_state.setText('CONNECTED' if c and c.connected else 'DISCONNECTED')
        if not c:
            self.comm_pending.setText('--'); self.comm_full.setText('--'); self.comm_seq.setText('--'); return
        self.comm_pending.setText(str(c._pending_state_request_seq)); self.comm_full.setText(str(c.needs_full_sync)); self.comm_seq.setText(str(c._last_incoming_seq)); self.comm_ack.setText('--' if self.last_ack is None else f'{self.last_ack.ack_seq}/{self.last_ack.accepted}')

    def refresh_history(self) -> None:
        try:
            self.repo.initialize()
            query='SELECT received_at_utc,session_id,step,load_power_kw,wind_available_kw,wind_operating_limit_kw,wind_actual_kw,diesel_actual_kw FROM state_history'
            args=[]
            if self.h_session.text().strip(): query += ' WHERE session_id=?'; args.append(self.h_session.text().strip())
            query += ' ORDER BY id DESC LIMIT ?'; args.append(int(self.h_limit.value()))
            with self.repo.connection() as conn: rows=conn.execute(query,args).fetchall()
            self.history.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,key in enumerate(row.keys()): self.history.setItem(i,j,QtWidgets.QTableWidgetItem(str(row[key])))
        except Exception as exc: self.log(f'历史查询失败：{exc}')

    def export_history(self) -> None:
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'导出状态历史 CSV','ems_state_history.csv','CSV (*.csv)')
        if not path: return
        try:
            self.repo.initialize()
            with self.repo.connection() as conn: rows=conn.execute('SELECT * FROM state_history ORDER BY id ASC').fetchall()
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f); writer.writerow(rows[0].keys() if rows else ['no_data']); writer.writerows([list(row) for row in rows])
            self.log(f'已导出：{path}')
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'导出失败',str(exc))

    def refresh_views(self) -> None:
        if self.stack.currentIndex()==5: self.refresh_history()
        self.refresh_alarms()

    def refresh_alarms(self) -> None:
        if not hasattr(self,'alarm_table'): return
        rows=[]
        if self.state and self.state.fault: rows.append(('HIGH','C_FAULT','C protection/control priority，B 抑制风机目标',self.state.session_id,self.state.step))
        if self.state and self.state.received_age_s > self.params['max_age_s']: rows.append(('HIGH','STALE_STATE',f'state age={self.state.received_age_s:.2f}s',self.state.session_id,self.state.step))
        if self.client and self.client._uncertain_dispatch_seq is not None: rows.append(('HIGH','DELIVERY_UNKNOWN',f'dispatch seq={self.client._uncertain_dispatch_seq} ACK unknown',self.state.session_id if self.state else '',self.state.step if self.state else ''))
        try:
            self.repo.initialize()
            with self.repo.connection() as conn: db_rows=conn.execute('SELECT level,event_type,message,session_id,step FROM event_log ORDER BY id DESC LIMIT 100').fetchall()
            rows.extend([tuple(x) for x in db_rows])
        except Exception: pass
        self.alarm_table.setRowCount(min(len(rows),300))
        for i,row in enumerate(rows[:300]):
            for j,v in enumerate(row): self.alarm_table.setItem(i,j,QtWidgets.QTableWidgetItem(str(v)))
        self.calculate_kpi()

    def calculate_kpi(self) -> None:
        try:
            self.repo.initialize()
            with self.repo.connection() as conn:
                total=conn.execute('SELECT COUNT(*) FROM dispatch_evaluation').fetchone()[0]
                unserved=conn.execute('SELECT COALESCE(SUM(target_unserved_kw),0) FROM dispatch_evaluation').fetchone()[0]
                surplus=conn.execute('SELECT COALESCE(SUM(target_surplus_kw),0) FROM dispatch_evaluation').fetchone()[0]
                all_cmd=conn.execute('SELECT COUNT(*) FROM dispatch_commands WHERE ack_accepted IS NOT NULL').fetchone()[0]
                accepted=conn.execute('SELECT COUNT(*) FROM dispatch_commands WHERE ack_accepted=1').fetchone()[0]
            self.kpi_total.set_value(total); self.kpi_unserved.set_value(f'{unserved:.1f}'); self.kpi_surplus.set_value(f'{surplus:.1f}'); self.kpi_ack.set_value('--' if not all_cmd else f'{accepted*100/all_cmd:.1f}')
        except Exception: pass

    def apply_dispatch_result(self, result) -> None:
        self.last_decision = type('DecisionProxy', (), {'result': result, 'state': self.state})() if self.state else None
        self.refresh_state_views()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.auto_dispatch=False
        self._manual_dispatch_pending=False
        self._connection_desired=False
        self._connection_generation += 1
        if self.client: self.client.close()
        event.accept()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(); window.show(); return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
