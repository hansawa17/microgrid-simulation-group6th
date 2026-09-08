# -*- coding: utf-8 -*-
"""
南极孤立微电网 EMS 主站 B —— PyQt6 图形界面

本文件仿照 PC-C 的 gui.py 视觉风格，但页面内容针对 B / EMS 调度职责设计。
当前阶段主动跳过 A/B/C 通信联调，因此本 GUI 不建立 TCP 连接；界面以本地
EMS 状态、参数和调度演示为主，并保留后续接入 serviceB.py / ems.db 的控件接口。

主要原则：
1. B 是闭环 EMS：展示负荷、风电 available / operating limit / target / actual、柴油 target / actual。
2. B 只负责 target / enable，不发送 pitch，不修改 actual；C 的风机保护优先级在状态中明确展示。
3. 柴油 reserve 固定 10 kW，正常调度上限为 diesel_max_kw - 10 kW。
4. GUI 不假定尚未冻结的设备额定功率；参数均可配置，默认值仅为演示占位。
5. 所有数据更新接口集中在 MainWindow.set_state_snapshot() / apply_dispatch_result()，
   后续接入 runtime / serviceB / repository 时不需要重做页面。
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from B_dispatch.models import DispatchConfig, GridState, DispatchResult
    from B_dispatch.dispatch import DispatchError, calculate_dispatch
except ImportError:  # 允许在不安装 B 包的情况下先预览界面
    DispatchConfig = None
    GridState = None
    DispatchResult = None
    DispatchError = Exception
    calculate_dispatch = None


COLORS = {
    "bg": "#e9eff6",
    "surface": "#ffffff",
    "border": "#d7e2ed",
    "header_bg": "#ffffff",
    "nav_bg": "#f6f9fc",
    "primary": "#2f6fd6",
    "primary_dark": "#16324f",
    "text": "#274056",
    "text_soft": "#45607a",
    "text_muted": "#8b9caf",
    "good": "#1fa15a",
    "warn": "#e19a1a",
    "bad": "#d64545",
    "wind": "#2f6fd6",
    "avail": "#1fa15a",
    "limit": "#2b93b5",
    "target": "#e19a1a",
    "actual": "#6b4fd8",
    "diesel": "#8a5a2b",
    "load": "#355a78",
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
QPushButton[role="danger"]:hover {{ background: #bd3a3a; }}
QPushButton[role="secondary"] {{ background: #eef3f8; color: #33516d; border: 1px solid #d2dfea; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="secondary"]:hover {{ background: #e2ebf3; }}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{ background: #ffffff; border: 1px solid #c9d8e6; border-radius: 5px; padding: 6px 8px; color: {COLORS['text']}; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border: 1px solid {COLORS['primary']}; }}
QTableWidget {{ background: #ffffff; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; color: #2c4359; alternate-background-color: #f7fafd; }}
QTableWidget::item {{ padding: 4px; }}
QHeaderView::section {{ background: #edf3f9; color: #33516d; font-weight: 700; border: none; border-bottom: 1px solid #d5e2ed; padding: 8px; }}
QTabWidget::pane {{ border: 1px solid #d5e2ed; background: #ffffff; border-radius: 6px; }}
QTabBar::tab {{ background: #edf3f9; color: {COLORS['text_soft']}; padding: 9px 22px; border: 1px solid #d5e2ed; border-bottom: none; font-weight: 600; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {COLORS['primary']}; color: #ffffff; }}
QStatusBar {{ background: #e6edf5; color: #5e7891; border-top: 1px solid #d4e1ed; }}
"""


@dataclass
class DemoSnapshot:
    session_id: str = "demo-b"
    step: int = 101
    sim_time_s: float = 100.0
    wind_speed_mps: float = 9.5
    wind_available_kw: float = 72.0
    wind_operating_limit_kw: float = 68.0
    load_power_kw: float = 88.0
    wind_actual_kw: float = 61.0
    diesel_actual_kw: float = 27.0
    wind_running: bool = True
    fault: bool = False
    wind_target_kw: float = 68.0
    diesel_target_kw: float = 20.0
    wind_enable: bool = True
    diesel_enable: bool = True
    sampled_at_utc: str = "2026-09-08T03:00:00.000Z"
    received_at_utc: str = "2026-09-08T03:00:00.050Z"
    received_age_s: float = 0.05
    pitch_actual_deg: float = 3.2


class TrendChart(QtWidgets.QWidget):
    """轻量级趋势图，避免 B GUI 对 pyqtgraph 形成硬依赖。"""

    def __init__(self, series: Iterable[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._series_names = [name for name, _ in series]
        self._series_values = {name: [] for name in self._series_names}
        self.setMinimumHeight(230)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

    def push(self, values: dict[str, float], max_points: int = 60) -> None:
        for name in self._series_names:
            value = float(values.get(name, math.nan))
            data = self._series_values[name]
            data.append(value)
            if len(data) > max_points:
                del data[:-max_points]
        self.update()

    def seed(self, rows: list[dict[str, float]]) -> None:
        for row in rows:
            self.push(row)

    def clear(self) -> None:
        for name in self._series_names:
            self._series_values[name].clear()
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(48, 18, -18, -34)
        painter.fillRect(self.rect(), QtGui.QColor("#f7fafd"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#dde6ef"), 1))
        for i in range(6):
            y = rect.top() + rect.height() * i / 5
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        for i in range(7):
            x = rect.left() + rect.width() * i / 6
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())

        all_values = [v for data in self._series_values.values() for v in data if math.isfinite(v)]
        if not all_values:
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["text_muted"])))
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "等待 EMS 状态数据")
            self._draw_legend(painter)
            return

        lo = min(all_values)
        hi = max(all_values)
        if abs(hi - lo) < 1e-9:
            hi += 1.0
            lo -= 1.0

        for index, name in enumerate(self._series_names):
            data = self._series_values[name]
            if len(data) < 2:
                continue
            color = [COLORS["load"], COLORS["avail"], COLORS["limit"], COLORS["target"], COLORS["actual"], COLORS["diesel"]][index % 6]
            pen = QtGui.QPen(QtGui.QColor(color), 2)
            painter.setPen(pen)
            points = []
            for i, value in enumerate(data):
                if not math.isfinite(value):
                    continue
                x = rect.left() + rect.width() * (i / max(1, len(data) - 1))
                y = rect.bottom() - rect.height() * ((value - lo) / (hi - lo))
                points.append(QtCore.QPointF(x, y))
            if len(points) >= 2:
                painter.drawPolyline(QtGui.QPolygonF(points))

        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["text_muted"])))
        painter.drawText(4, rect.top(), 38, 18, QtCore.Qt.AlignmentFlag.AlignRight, f"{hi:.0f}")
        painter.drawText(4, rect.bottom() - 18, 38, 18, QtCore.Qt.AlignmentFlag.AlignRight, f"{lo:.0f}")
        self._draw_legend(painter)

    def _draw_legend(self, painter: QtGui.QPainter) -> None:
        x = 52
        y = self.height() - 18
        for index, name in enumerate(self._series_names):
            color = [COLORS["load"], COLORS["avail"], COLORS["limit"], COLORS["target"], COLORS["actual"], COLORS["diesel"]][index % 6]
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 3))
            painter.drawLine(x, y, x + 14, y)
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["text_soft"])))
            painter.drawText(x + 18, y + 4, name)
            x += max(72, len(name) * 10 + 34)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("南极孤立微电网 EMS 主站 B")
        self.resize(1920, 1080)
        self.setMinimumSize(1400, 800)

        self.snapshot = DemoSnapshot()
        self._history_rows: list[list[str]] = []
        self._event_rows: list[list[str]] = []
        self._param_defaults = {
            "wind_min_kw": 0.0,
            "wind_max_kw": 100.0,
            "diesel_max_kw": 120.0,
            "reserve_kw": 10.0,
        }

        self._build_ui()
        self.setStyleSheet(_QSS)
        self._seed_demo_history()
        self._start_clock()
        self._refresh_all()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())

        self.pageStack = QtWidgets.QStackedWidget()
        self.pageStack.addWidget(self._build_monitor_page())
        self.pageStack.addWidget(self._build_realtime_page())
        self.pageStack.addWidget(self._build_params_page())
        self.pageStack.addWidget(self._build_dispatch_page())
        self.pageStack.addWidget(self._build_history_page())
        self.pageStack.addWidget(self._build_alarm_page())
        body.addWidget(self.pageStack, 1)
        root.addLayout(body, 1)

        self.navGroup = QtWidgets.QButtonGroup(self)
        self.navGroup.setExclusive(True)
        for i, button in enumerate(self.navButtons):
            self.navGroup.addButton(button, i)
            button.clicked.connect(lambda _checked, i=i: self.pageStack.setCurrentIndex(i))
        self.navButtons[0].setChecked(True)
        self.statusBar().showMessage("B EMS 就绪 | 当前为本地 GUI 演示模式，通信联调已按计划暂缓")

    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(72)
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(12)

        badge = QtWidgets.QFrame()
        badge.setObjectName("logoBadge")
        badge.setFixedSize(38, 38)
        badge.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QtWidgets.QVBoxLayout(badge)
        bl.setContentsMargins(0, 0, 0, 0)
        glyph = QtWidgets.QLabel("B")
        glyph.setObjectName("logoGlyph")
        glyph.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(glyph)
        lay.addWidget(badge)

        tb = QtWidgets.QVBoxLayout()
        tb.setSpacing(0)
        self.appTitle = QtWidgets.QLabel("南极孤立微电网 EMS 主站")
        self.appTitle.setObjectName("appTitle")
        self.appSubtitle = QtWidgets.QLabel("PC-B · 能量管理与调度系统 · 闭环 EMS")
        self.appSubtitle.setObjectName("appSubtitle")
        tb.addWidget(self.appTitle)
        tb.addWidget(self.appSubtitle)
        lay.addLayout(tb)
        lay.addStretch(1)

        self.clockLabel = QtWidgets.QLabel()
        self.clockLabel.setObjectName("clockLabel")
        lay.addWidget(self.clockLabel)
        lay.addSpacing(8)

        self.localDot, self.localStatus = self._status_pill(lay, "EMS 正常", COLORS["good"])
        self.loopDot, self.loopStatus = self._status_pill(lay, "闭环模式", COLORS["good"])
        self.commDot, self.commStatus = self._status_pill(lay, "通信联调暂缓", COLORS["warn"])
        return bar

    def _status_pill(self, parent_layout, text: str, color: str):
        pill = QtWidgets.QFrame()
        pill.setProperty("role", "pill")
        pill.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        pl = QtWidgets.QHBoxLayout(pill)
        pl.setContentsMargins(10, 5, 12, 5)
        pl.setSpacing(7)
        dot = QtWidgets.QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "pillText")
        pl.addWidget(dot)
        pl.addWidget(label)
        parent_layout.addWidget(pill)
        return dot, label

    def _build_nav(self):
        panel = QtWidgets.QFrame()
        panel.setObjectName("navPanel")
        panel.setFixedWidth(200)
        lay = QtWidgets.QVBoxLayout(panel)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(2)
        caption = QtWidgets.QLabel("功能导航")
        caption.setProperty("role", "navCaption")
        lay.addWidget(caption)

        names = ["运行监控", "实时曲线", "调度参数", "手动调度", "历史数据", "报警与事件"]
        self.navButtons = [self._nav_button(name) for name in names]
        for button in self.navButtons:
            lay.addWidget(button)
        lay.addStretch(1)
        site = QtWidgets.QLabel("PC-B EMS 主站\n孤立微电网 / 能量管理")
        site.setProperty("role", "siteLabel")
        lay.addWidget(site)
        return panel

    def _nav_button(self, text: str):
        button = QtWidgets.QPushButton(text)
        button.setProperty("role", "nav")
        button.setCheckable(True)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(44)
        return button

    @staticmethod
    def _frame():
        frame = QtWidgets.QFrame()
        frame.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        return frame

    def _section_title(self, text: str):
        label = QtWidgets.QLabel("▍ " + text)
        label.setProperty("role", "sectionTitle")
        return label

    def _button(self, text: str, role: str):
        button = QtWidgets.QPushButton(text)
        button.setProperty("role", role)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        return button

    def _kpi_card(self, title: str, value: str, unit: str, source: str, accent: str):
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(118)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)
        head = QtWidgets.QHBoxLayout()
        dot = QtWidgets.QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {accent}; border-radius: 4px;")
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("role", "cardTitle")
        head.addWidget(dot)
        head.addWidget(title_label)
        head.addStretch(1)
        lay.addLayout(head)
        val_row = QtWidgets.QHBoxLayout()
        value_label = QtWidgets.QLabel(value)
        value_label.setProperty("role", "bigValue")
        unit_label = QtWidgets.QLabel(unit)
        unit_label.setProperty("role", "unit")
        val_row.addWidget(value_label)
        val_row.addWidget(unit_label)
        val_row.addStretch(1)
        lay.addLayout(val_row)
        source_label = QtWidgets.QLabel(source)
        source_label.setProperty("role", "source")
        lay.addWidget(source_label)
        return card, value_label

    def _status_card(self, title: str, status: str, state: str, detail: str):
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(96)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(4)
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("role", "cardTitle")
        value_label = QtWidgets.QLabel(status)
        value_label.setProperty("state", state)
        detail_label = QtWidgets.QLabel(detail)
        detail_label.setProperty("role", "source")
        lay.addWidget(title_label)
        lay.addWidget(value_label)
        lay.addWidget(detail_label)
        return card, value_label, detail_label

    def _build_monitor_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("EMS 运行监控"))

        info = self._frame()
        info.setProperty("role", "card")
        il = QtWidgets.QHBoxLayout(info)
        il.setContentsMargins(16, 10, 16, 10)
        self.sessionLabel = QtWidgets.QLabel()
        self.sessionLabel.setProperty("role", "cardTitle")
        self.stepLabel = QtWidgets.QLabel()
        self.stepLabel.setProperty("role", "source")
        self.timeLabel = QtWidgets.QLabel()
        self.timeLabel.setProperty("role", "source")
        il.addWidget(self.sessionLabel)
        il.addStretch(1)
        il.addWidget(self.stepLabel)
        il.addSpacing(18)
        il.addWidget(self.timeLabel)
        v.addWidget(info)

        defs = [
            ("负荷", "88.0", "kW", "A → B", COLORS["load"]),
            ("风电可用", "72.0", "kW", "A 资源可用功率", COLORS["avail"]),
            ("风电运行上限", "68.0", "kW", "C / 设备约束后上限", COLORS["limit"]),
            ("风电目标", "68.0", "kW", "B 调度目标", COLORS["target"]),
            ("风电实际", "61.0", "kW", "A 实际结果", COLORS["actual"]),
            ("柴油实际", "27.0", "kW", "A 实际结果", COLORS["diesel"]),
        ]
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        self.kpiLabels = {}
        for key, val, unit, source, accent in defs:
            card, value_label = self._kpi_card(key, val, unit, source, accent)
            row.addWidget(card, 1)
            self.kpiLabels[key] = value_label
        v.addLayout(row)

        states = QtWidgets.QHBoxLayout()
        states.setSpacing(12)
        self.stateLabels = {}
        state_defs = [
            ("EMS 控制模式", "● 闭环", "good", "B closed-loop"),
            ("风机状态", "● 运行", "good", "A actual / enable"),
            ("C 优先权", "● 生效", "good", "保护 / operating limit 优先"),
            ("柴油状态", "● 运行", "good", "reserve = 10 kW"),
            ("状态新鲜度", "● 0.05 s", "good", "B 本机接收时间"),
        ]
        for title, status, state, detail in state_defs:
            card, value_label, _detail = self._status_card(title, status, state, detail)
            states.addWidget(card, 1)
            self.stateLabels[title] = value_label
        v.addLayout(states)

        self.monitorChart = TrendChart([
            ("load", COLORS["load"]),
            ("wind available", COLORS["avail"]),
            ("wind limit", COLORS["limit"]),
            ("wind target", COLORS["target"]),
            ("wind actual", COLORS["actual"]),
            ("diesel actual", COLORS["diesel"]),
        ])
        v.addWidget(self._wrap_chart_card("实时功率总览", "最近 60 点 · kW", self.monitorChart), 1)
        return page

    def _wrap_chart_card(self, title: str, subtitle: str, chart: QWidget):
        card = self._frame()
        card.setProperty("role", "card")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        head = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("role", "cardTitle")
        sub = QtWidgets.QLabel(subtitle)
        sub.setProperty("role", "source")
        head.addWidget(title_label)
        head.addStretch(1)
        head.addWidget(sub)
        lay.addLayout(head)
        lay.addWidget(chart, 1)
        return card

    def _build_realtime_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("EMS 实时曲线"))
        note = self._frame()
        note.setProperty("role", "card")
        nl = QtWidgets.QHBoxLayout(note)
        nl.setContentsMargins(16, 10, 16, 10)
        left = QtWidgets.QLabel("采样窗口：最近 60 点")
        left.setProperty("role", "cardTitle")
        right = QtWidgets.QLabel("当前阶段：本地状态演示，不建立 TCP")
        right.setProperty("role", "source")
        nl.addWidget(left)
        nl.addStretch(1)
        nl.addWidget(right)
        v.addWidget(note)

        self.realtimePowerChart = TrendChart([
            ("load", COLORS["load"]),
            ("wind available", COLORS["avail"]),
            ("wind limit", COLORS["limit"]),
            ("wind target", COLORS["target"]),
            ("wind actual", COLORS["actual"]),
            ("diesel actual", COLORS["diesel"]),
        ])
        self.realtimeWindChart = TrendChart([
            ("wind speed", COLORS["wind"]),
        ])
        v.addWidget(self._wrap_chart_card("功率趋势", "load / available / limit / target / actual / diesel (kW)", self.realtimePowerChart), 1)
        v.addWidget(self._wrap_chart_card("风速趋势", "wind_speed_mps (m/s)", self.realtimeWindChart), 1)
        return page

    def _build_params_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("EMS 调度参数设置"))

        card = self._frame()
        card.setProperty("role", "card")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(24, 20, 24, 20)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(14)

        fields = [
            ("风电最小目标", "wind_min_kw", 0.0),
            ("风电最大额定", "wind_max_kw", 100.0),
            ("柴油最大功率", "diesel_max_kw", 120.0),
            ("柴油备用容量", "reserve_kw", 10.0),
            ("状态最大年龄", "max_state_age_s", 2.0),
            ("状态采集周期", "poll_period_s", 1.0),
            ("调度周期", "dispatch_period_s", 5.0),
        ]
        self.paramSpins = {}
        for row_index, (label, key, default) in enumerate(fields):
            grid.addWidget(QtWidgets.QLabel(label), row_index, 0)
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 100000.0)
            spin.setDecimals(2)
            spin.setValue(default)
            spin.setSingleStep(0.5)
            unit = "kW" if "kw" in key else "s"
            grid.addWidget(spin, row_index, 1)
            grid.addWidget(QtWidgets.QLabel(unit), row_index, 2)
            self.paramSpins[key] = spin

        mode_label = QtWidgets.QLabel("控制模式")
        grid.addWidget(mode_label, 7, 0)
        self.modeCombo = QtWidgets.QComboBox()
        self.modeCombo.addItems(["闭环", "开环预留"])
        self.modeCombo.setCurrentIndex(0)
        grid.addWidget(self.modeCombo, 7, 1)
        grid.addWidget(QtWidgets.QLabel("B 默认闭环"), 7, 2)

        self.applyParamButton = self._button("应用参数", "primary")
        self.resetParamButton = self._button("恢复默认", "secondary")
        grid.addWidget(self.applyParamButton, 8, 1)
        grid.addWidget(self.resetParamButton, 8, 2)
        self.applyParamButton.clicked.connect(self._apply_params)
        self.resetParamButton.clicked.connect(self._reset_params)
        grid.setColumnStretch(1, 1)
        v.addWidget(card)

        ro = self._frame()
        ro.setProperty("role", "card")
        rl = QtWidgets.QVBoxLayout(ro)
        rl.setContentsMargins(16, 14, 16, 14)
        t = QtWidgets.QLabel("ⓘ B 的职责边界")
        t.setProperty("role", "cardTitle")
        d = QtWidgets.QLabel("B 输出 wind_target_kw / diesel_target_kw / wind_enable / diesel_enable；不发送 pitch_target_deg，不修改 wind_actual_kw / diesel_actual_kw。柴油 reserve 固定为 10 kW，C 对风机保护/控制具有优先权。")
        d.setWordWrap(True)
        d.setProperty("role", "source")
        rl.addWidget(t)
        rl.addWidget(d)
        v.addWidget(ro)
        v.addStretch(1)
        return page

    def _build_dispatch_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("本地调度演示"))

        card = self._frame()
        card.setProperty("role", "card")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(24, 20, 24, 20)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(14)

        self.dispatchLoad = self._spin(88.0)
        self.dispatchAvail = self._spin(72.0)
        self.dispatchLimit = self._spin(68.0)
        self.dispatchActualWind = self._spin(61.0)
        self.dispatchActualDiesel = self._spin(27.0)
        self.dispatchFault = QtWidgets.QComboBox()
        self.dispatchFault.addItems(["正常", "C / 风机故障"])

        rows = [
            ("负荷 power", self.dispatchLoad, "kW"),
            ("风电 available", self.dispatchAvail, "kW"),
            ("风电 operating limit", self.dispatchLimit, "kW"),
            ("风电 actual（只读语义）", self.dispatchActualWind, "kW"),
            ("柴油 actual（只读语义）", self.dispatchActualDiesel, "kW"),
            ("风机故障", self.dispatchFault, ""),
        ]
        for r, (label, widget, unit) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            grid.addWidget(widget, r, 1)
            if unit:
                grid.addWidget(QtWidgets.QLabel(unit), r, 2)

        self.calcButton = self._button("计算 B 调度目标", "primary")
        self.safeButton = self._button("模拟安全兜底", "danger")
        grid.addWidget(self.calcButton, 6, 1)
        grid.addWidget(self.safeButton, 6, 2)
        self.calcButton.clicked.connect(self._calculate_demo_dispatch)
        self.safeButton.clicked.connect(self._show_safe_fallback)
        grid.setColumnStretch(1, 1)
        v.addWidget(card)

        result = self._frame()
        result.setProperty("role", "card")
        rl = QtWidgets.QVBoxLayout(result)
        rl.setContentsMargins(18, 16, 18, 16)
        self.dispatchResultTitle = QtWidgets.QLabel("最近调度结果：尚未计算")
        self.dispatchResultTitle.setProperty("role", "cardTitle")
        self.dispatchResultLabel = QtWidgets.QLabel("等待调度")
        self.dispatchResultLabel.setProperty("state", "good")
        self.dispatchReasonLabel = QtWidgets.QLabel("")
        self.dispatchReasonLabel.setProperty("role", "source")
        self.dispatchReasonLabel.setWordWrap(True)
        rl.addWidget(self.dispatchResultTitle)
        rl.addWidget(self.dispatchResultLabel)
        rl.addWidget(self.dispatchReasonLabel)
        v.addWidget(result)

        v.addStretch(1)
        return page

    @staticmethod
    def _spin(value: float):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.0, 100000.0)
        spin.setDecimals(2)
        spin.setValue(value)
        spin.setSingleStep(1.0)
        return spin

    def _build_history_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("EMS 历史数据"))
        filter_card = self._frame()
        filter_card.setProperty("role", "card")
        fl = QtWidgets.QHBoxLayout(filter_card)
        fl.setContentsMargins(16, 10, 16, 10)
        fl.addWidget(QtWidgets.QLabel("本地历史：ems.db 对接位预留"))
        fl.addStretch(1)
        self.historyRefreshButton = self._button("刷新演示数据", "secondary")
        self.historyRefreshButton.clicked.connect(self._refresh_history)
        fl.addWidget(self.historyRefreshButton)
        v.addWidget(filter_card)

        self.historyTable = QtWidgets.QTableWidget(0, 10)
        self.historyTable.setHorizontalHeaderLabels([
            "step", "sim_time_s", "load", "wind_avail", "wind_limit",
            "wind_target", "wind_actual", "diesel_target", "diesel_actual", "reason"
        ])
        self.historyTable.setAlternatingRowColors(True)
        self.historyTable.horizontalHeader().setStretchLastSection(True)
        self.historyTable.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.historyTable, 1)
        return page

    def _build_alarm_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        v.addWidget(self._section_title("报警与 EMS 事件"))

        summary = self._frame()
        summary.setProperty("role", "card")
        sl = QtWidgets.QHBoxLayout(summary)
        sl.setContentsMargins(16, 12, 16, 12)
        self.alarmSummary = QtWidgets.QLabel("当前无活动故障")
        self.alarmSummary.setProperty("state", "good")
        self.boundarySummary = QtWidgets.QLabel("通信联调暂缓；B 本地调度界面可用")
        self.boundarySummary.setProperty("role", "source")
        sl.addWidget(self.alarmSummary)
        sl.addStretch(1)
        sl.addWidget(self.boundarySummary)
        v.addWidget(summary)

        self.eventTable = QtWidgets.QTableWidget(0, 4)
        self.eventTable.setHorizontalHeaderLabels(["time", "level", "event", "message"])
        self.eventTable.setAlternatingRowColors(True)
        self.eventTable.horizontalHeader().setStretchLastSection(True)
        self.eventTable.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.eventTable, 1)
        return page

    # ------------------------------ 数据 / 演示 ------------------------------
    def _seed_demo_history(self) -> None:
        base = DemoSnapshot()
        rows = []
        for i in range(30):
            load = 82.0 + (i % 7) * 1.8
            speed = 8.4 + (i % 9) * 0.18
            available = 55.0 + (i % 10) * 2.2
            limit = max(0.0, available - (i % 4) * 1.5)
            wind_target = min(limit, max(0.0, load - 20.0))
            diesel_target = max(0.0, min(110.0, load - wind_target))
            wind_actual = max(0.0, wind_target - (i % 4) * 1.2)
            diesel_actual = max(0.0, diesel_target - (i % 3) * 0.7)
            rows.append([
                str(base.step - 29 + i), f"{base.sim_time_s - 29 + i:.1f}",
                f"{load:.1f}", f"{available:.1f}", f"{limit:.1f}",
                f"{wind_target:.1f}", f"{wind_actual:.1f}", f"{diesel_target:.1f}", f"{diesel_actual:.1f}",
                "风优先 / 柴油补缺"
            ])
        self._history_rows = rows
        self._event_rows = [
            ["03:00:00", "INFO", "state", "收到演示状态，B 闭环调度就绪"],
            ["03:00:01", "INFO", "dispatch", "风电目标受 operating limit 约束"],
            ["03:00:02", "INFO", "reserve", "柴油 reserve = 10 kW"],
            ["03:00:03", "WARN", "integration", "A/B/C 通信联调按计划暂缓"],
        ]
        self._refresh_history()
        self._refresh_events()

        seed = []
        for row in rows:
            seed.append({
                "load": float(row[2]),
                "wind available": float(row[3]),
                "wind limit": float(row[4]),
                "wind target": float(row[5]),
                "wind actual": float(row[6]),
                "diesel actual": float(row[8]),
            })
        self.monitorChart.seed(seed)
        self.realtimePowerChart.seed(seed)
        self.realtimeWindChart.seed([{"wind speed": 8.4 + (i % 9) * 0.18} for i in range(30)])

    def _refresh_history(self) -> None:
        self.historyTable.setRowCount(0)
        for row_data in self._history_rows:
            r = self.historyTable.rowCount()
            self.historyTable.insertRow(r)
            for c, value in enumerate(row_data):
                self.historyTable.setItem(r, c, QtWidgets.QTableWidgetItem(value))

    def _refresh_events(self) -> None:
        self.eventTable.setRowCount(0)
        for row_data in self._event_rows:
            r = self.eventTable.rowCount()
            self.eventTable.insertRow(r)
            for c, value in enumerate(row_data):
                self.eventTable.setItem(r, c, QtWidgets.QTableWidgetItem(value))

    def _refresh_all(self) -> None:
        s = self.snapshot
        self.sessionLabel.setText(f"session：{s.session_id}")
        self.stepLabel.setText(f"step：{s.step}")
        self.timeLabel.setText(f"sim time：{s.sim_time_s:.1f} s")
        values = {
            "负荷": s.load_power_kw,
            "风电可用": s.wind_available_kw,
            "风电运行上限": s.wind_operating_limit_kw,
            "风电目标": s.wind_target_kw,
            "风电实际": s.wind_actual_kw,
            "柴油实际": s.diesel_actual_kw,
        }
        for key, value in values.items():
            self.kpiLabels[key].setText(f"{value:.1f}")
        self.stateLabels["EMS 控制模式"].setText("● 闭环")
        self.stateLabels["风机状态"].setText("● 运行" if s.wind_running else "● 停止")
        self.stateLabels["C 优先权"].setText("● 生效" if not s.fault else "● 保护优先")
        self.stateLabels["柴油状态"].setText("● 运行" if s.diesel_enable else "● OFF")
        self.stateLabels["状态新鲜度"].setText(f"● {s.received_age_s:.2f} s")
        self.alarmSummary.setText("当前存在 C / 风机故障" if s.fault else "当前无活动故障")
        self.alarmSummary.setProperty("state", "bad" if s.fault else "good")
        self.alarmSummary.style().unpolish(self.alarmSummary)
        self.alarmSummary.style().polish(self.alarmSummary)

        self.monitorChart.push({
            "load": s.load_power_kw,
            "wind available": s.wind_available_kw,
            "wind limit": s.wind_operating_limit_kw,
            "wind target": s.wind_target_kw,
            "wind actual": s.wind_actual_kw,
            "diesel actual": s.diesel_actual_kw,
        })
        self.realtimePowerChart.push({
            "load": s.load_power_kw,
            "wind available": s.wind_available_kw,
            "wind limit": s.wind_operating_limit_kw,
            "wind target": s.wind_target_kw,
            "wind actual": s.wind_actual_kw,
            "diesel actual": s.diesel_actual_kw,
        })
        self.realtimeWindChart.push({"wind speed": s.wind_speed_mps})

        self.dispatchLoad.setValue(s.load_power_kw)
        self.dispatchAvail.setValue(s.wind_available_kw)
        self.dispatchLimit.setValue(s.wind_operating_limit_kw)
        self.dispatchActualWind.setValue(s.wind_actual_kw)
        self.dispatchActualDiesel.setValue(s.diesel_actual_kw)
        self._append_event("INFO", "refresh", f"状态刷新 step={s.step}")
        self._update_event_table_tail()

    def _start_clock(self) -> None:
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick_clock)
        self.timer.start(1000)
        self._tick_clock()

    def _tick_clock(self) -> None:
        self.clockLabel.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _append_event(self, level: str, event: str, message: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self._event_rows.append([now, level, event, message])
        self._event_rows = self._event_rows[-50:]

    def _update_event_table_tail(self) -> None:
        if hasattr(self, "eventTable"):
            self._refresh_events()

    def _apply_params(self) -> None:
        reserve = self.paramSpins["reserve_kw"].value()
        diesel_max = self.paramSpins["diesel_max_kw"].value()
        if diesel_max < reserve:
            QtWidgets.QMessageBox.warning(self, "参数错误", "柴油最大功率不能小于 reserve 10 kW。")
            return
        self._append_event("INFO", "params", "调度参数已在 GUI 本地生效；未写入远程设备")
        self.statusBar().showMessage("参数已在本地 EMS 演示中生效；通信联调仍暂缓")
        self._refresh_events()

    def _reset_params(self) -> None:
        defaults = {
            "wind_min_kw": 0.0,
            "wind_max_kw": 100.0,
            "diesel_max_kw": 120.0,
            "reserve_kw": 10.0,
            "max_state_age_s": 2.0,
            "poll_period_s": 1.0,
            "dispatch_period_s": 5.0,
        }
        for key, value in defaults.items():
            self.paramSpins[key].setValue(value)
        self._append_event("INFO", "params", "调度参数恢复为 GUI 演示默认值")
        self._refresh_events()

    def _calculate_demo_dispatch(self) -> None:
        load = self.dispatchLoad.value()
        available = self.dispatchAvail.value()
        operating = self.dispatchLimit.value()
        actual_wind = self.dispatchActualWind.value()
        actual_diesel = self.dispatchActualDiesel.value()
        fault = self.dispatchFault.currentIndex() == 1
        wind_max = self.paramSpins["wind_max_kw"].value()
        diesel_max = self.paramSpins["diesel_max_kw"].value()
        reserve = self.paramSpins["reserve_kw"].value()
        if operating > available:
            QtWidgets.QMessageBox.warning(self, "状态无效", "wind_operating_limit_kw 不能大于 wind_available_kw。")
            return

        if GridState is not None and DispatchConfig is not None and calculate_dispatch is not None:
            state = GridState(
                session_id="gui-demo", step=self.snapshot.step, sim_time_s=self.snapshot.sim_time_s,
                wind_speed_mps=self.snapshot.wind_speed_mps, wind_available_kw=available,
                wind_operating_limit_kw=operating, load_power_kw=load, wind_actual_kw=actual_wind,
                diesel_actual_kw=actual_diesel, wind_running=not fault, fault=fault,
                sampled_at_utc=self.snapshot.sampled_at_utc, received_at_utc=self.snapshot.received_at_utc,
                received_age_s=self.snapshot.received_age_s, wind_target_kw=self.snapshot.wind_target_kw,
                pitch_actual_deg=self.snapshot.pitch_actual_deg,
            )
            config = DispatchConfig(
                wind_max_kw=wind_max, diesel_max_kw=diesel_max,
                wind_min_kw=self.paramSpins["wind_min_kw"].value(), reserve_kw=reserve,
                max_state_age_s=self.paramSpins["max_state_age_s"].value(), c_has_control_priority=True,
            )
            try:
                result = calculate_dispatch(state, config)
            except DispatchError as exc:
                self._show_dispatch_result(None, f"调度拒绝：{exc}", "bad")
                return
            self._show_dispatch_result(result, result.reason, "good" if result.target_unserved_kw <= 0 else "warn")
            return

        # 无 B 包时的 UI 预览兜底：仍遵守同一约束语义
        if fault:
            wind_target = 0.0
            diesel_target = 0.0
            reason = "C/风机故障：B 不请求正常风机出力"
        else:
            wind_target = min(operating, wind_max, load)
            diesel_target = min(max(0.0, load - wind_target), max(0.0, diesel_max - reserve))
            reason = "风优先 / operating limit 约束 / 柴油保留 10 kW reserve"
        result = {
            "wind_target_kw": wind_target,
            "diesel_target_kw": diesel_target,
            "wind_enable": wind_target > 0,
            "diesel_enable": diesel_target > 0,
            "target_unserved_kw": max(0.0, load - wind_target - diesel_target),
        }
        self._show_dispatch_result(result, reason, "good" if result["target_unserved_kw"] <= 0 else "warn")

    def _show_dispatch_result(self, result, reason: str, state: str) -> None:
        self.dispatchReasonLabel.setText(reason)
        self.dispatchResultLabel.setProperty("state", state)
        self.dispatchResultLabel.style().unpolish(self.dispatchResultLabel)
        self.dispatchResultLabel.style().polish(self.dispatchResultLabel)
        if result is None:
            self.dispatchResultTitle.setText("最近调度结果：拒绝")
            self.dispatchResultLabel.setText("未产生有效 dispatch")
            self._append_event("WARN", "dispatch", reason)
            self._refresh_events()
            return
        if hasattr(result, "wind_target_kw"):
            self.dispatchResultTitle.setText("最近调度结果")
            self.dispatchResultLabel.setText(
                f"wind_target={result.wind_target_kw:.1f} kW · diesel_target={result.diesel_target_kw:.1f} kW · "
                f"wind_enable={result.wind_enable} · diesel_enable={result.diesel_enable} · "
                f"unserved={result.target_unserved_kw:.1f} kW"
            )
        else:
            self.dispatchResultTitle.setText("最近调度结果")
            self.dispatchResultLabel.setText(
                f"wind_target={result['wind_target_kw']:.1f} kW · diesel_target={result['diesel_target_kw']:.1f} kW · "
                f"wind_enable={result['wind_enable']} · diesel_enable={result['diesel_enable']} · "
                f"unserved={result['target_unserved_kw']:.1f} kW"
            )
        self._append_event("INFO" if state == "good" else "WARN", "dispatch", reason)
        self._refresh_events()

    def _show_safe_fallback(self) -> None:
        self.dispatchResultTitle.setText("安全兜底")
        self.dispatchResultLabel.setText("wind_target=0.0 kW · diesel_target=0.0 kW · wind_enable=False · diesel_enable=False")
        self.dispatchReasonLabel.setText("GUI 演示：按 runtime.py 的安全兜底语义生成零输出目标；未发送到 A。")
        self.dispatchResultLabel.setProperty("state", "warn")
        self.dispatchResultLabel.style().unpolish(self.dispatchResultLabel)
        self.dispatchResultLabel.style().polish(self.dispatchResultLabel)
        self._append_event("WARN", "safe_fallback", "GUI 触发本地零输出安全兜底")
        self._refresh_events()

    # ------------------------------ 对外更新接口 ------------------------------
    def set_state_snapshot(self, snapshot: DemoSnapshot | dict) -> None:
        """后续由 serviceB/runtime 调用的统一状态入口；当前也可用于本地演示。"""
        if isinstance(snapshot, DemoSnapshot):
            self.snapshot = snapshot
        else:
            data = self.snapshot.__dict__.copy()
            data.update(snapshot)
            self.snapshot = DemoSnapshot(**data)
        self._refresh_all()

    def apply_dispatch_result(self, result) -> None:
        """后续由 EMSCore / serviceB 调用的统一 dispatch 展示入口。"""
        if result is None:
            return
        self.snapshot.wind_target_kw = float(result.wind_target_kw)
        self.snapshot.diesel_target_kw = float(result.diesel_target_kw)
        self.snapshot.wind_enable = bool(result.wind_enable)
        self.snapshot.diesel_enable = bool(result.diesel_enable)
        self._show_dispatch_result(result, getattr(result, "reason", ""), "good" if result.target_unserved_kw <= 0 else "warn")
        self._refresh_all()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
