# -*- coding: utf-8 -*-
"""
南极孤立微电网风力发电子站 PC-C 上位机 —— 图形界面层（仅显示，不含业务逻辑）

说明：
1. 本文件为手写界面，不再依赖 gui.ui / pyuic6 生成。
   （旧的 gui.ui 里用了 Qt Designer 的 "class" 动态属性，pyuic6 会生成不存在的
   setClass() 调用导致崩溃；此外有个无 sizeHint 的 spacer 也会生成非法 QSpacerItem。）
2. 所有控件都以 self.<objectName> 的形式挂在 MainWindow 上，
   后续接入通信 / 控制 / 数据库逻辑时，直接操作这些属性即可，例如：
       self.windSpeedValue.setText("10.2")
       self.pcADot.setStyleSheet("background:#d64545;border-radius:5px;")
3. 主题配色集中在 _QSS 与 COLORS 两个常量里，按需修改。
4. 当前所有数据都是静态占位值，用于界面预览，尚未接入任何真实数据源。
"""

import time
from datetime import datetime

from PyQt6 import QtCore, QtGui, QtWidgets

import config
from plot_widget import build_wind_curve, build_power_curve


# --------------------------------------------------------------------------- #
#  主题配色（供代码内引用）
# --------------------------------------------------------------------------- #
COLORS = {
    "bg":           "#e9eff6",   # 窗口底色
    "surface":      "#ffffff",   # 卡片底色
    "border":       "#d7e2ed",   # 卡片描边
    "header_bg":    "#ffffff",
    "nav_bg":       "#f6f9fc",
    "primary":      "#2f6fd6",   # 主色（选中态 / 主按钮）
    "primary_dark": "#16324f",   # 标题深蓝
    "text":         "#274056",   # 正文
    "text_soft":    "#45607a",   # 次级标题
    "text_muted":   "#8b9caf",   # 弱提示
    "good":         "#1fa15a",   # 运行 / 在线 / 正常
    "warn":         "#e19a1a",   # 告警
    "bad":          "#d64545",   # 停止 / 故障
    "wind":         "#2f6fd6",   # 风速卡强调色
    "avail":        "#1fa15a",   # 可用功率
    "target":       "#e19a1a",   # 目标功率
    "actual":       "#6b4fd8",   # 实际功率
}


# --------------------------------------------------------------------------- #
#  主题样式表
# --------------------------------------------------------------------------- #
_QSS = f"""
QMainWindow {{ background: {COLORS['bg']}; }}
QWidget {{ font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI"; color: {COLORS['text']}; }}

/* ---------- 顶栏 ---------- */
#headerBar {{ background: {COLORS['header_bg']}; border-bottom: 1px solid {COLORS['border']}; }}
#logoBadge {{ background: {COLORS['primary']}; border-radius: 6px; }}
#logoGlyph {{ color: #ffffff; font-size: 19px; font-weight: 800; }}
#appTitle  {{ color: {COLORS['primary_dark']}; font-size: 20px; font-weight: 700; }}
#appSubtitle {{ color: {COLORS['text_muted']}; font-size: 12px; }}
#clockLabel {{ color: #2b4a68; font-size: 14px; font-weight: 600; }}

/* ---------- 状态胶囊 ---------- */
QFrame[role="pill"]     {{ background: #eef4fa; border: 1px solid #dfe8f1; border-radius: 14px; }}
QLabel[role="pillText"] {{ color: #33516d; font-size: 12px; font-weight: 600; }}

/* ---------- 左侧导航 ---------- */
#navPanel {{ background: {COLORS['nav_bg']}; border-right: 1px solid #dbe5ef; }}
QLabel[role="navCaption"] {{ color: {COLORS['text_muted']}; font-size: 12px; font-weight: 700; padding: 4px 18px; }}
QPushButton[role="nav"] {{
    background: transparent; border: none; text-align: left; border-radius: 7px;
    padding: 12px 18px; color: #33516d; font-size: 15px; font-weight: 600;
}}
QPushButton[role="nav"]:hover   {{ background: #e8f0f8; }}
QPushButton[role="nav"]:checked {{ background: {COLORS['primary']}; color: #ffffff; }}
QLabel[role="siteLabel"] {{ color: #9aabba; font-size: 11px; padding: 8px 18px; }}

/* ---------- 通用 ---------- */
QLabel[role="sectionTitle"] {{ color: {COLORS['primary_dark']}; font-size: 19px; font-weight: 700; }}
QFrame[role="card"] {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}
QLabel[role="cardTitle"] {{ color: {COLORS['text_soft']}; font-size: 13px; font-weight: 600; }}
QLabel[role="bigValue"]  {{ color: #123a5f; font-size: 30px; font-weight: 700; }}
QLabel[role="unit"]      {{ color: #5c758c; font-size: 13px; font-weight: 600; }}
QLabel[role="source"]    {{ color: {COLORS['text_muted']}; font-size: 11px; }}
QLabel[role="hint"]      {{ color: #a7b6c6; font-size: 12px; }}

/* 状态指示灯文字 */
QLabel[state="good"] {{ color: {COLORS['good']}; font-size: 16px; font-weight: 700; }}
QLabel[state="warn"] {{ color: {COLORS['warn']}; font-size: 16px; font-weight: 700; }}
QLabel[state="bad"]  {{ color: {COLORS['bad']};  font-size: 16px; font-weight: 700; }}

/* 曲线占位区 */
QFrame[role="chartCanvas"] {{ background: #f7fafd; border: 1px solid #e1e9f2; border-radius: 6px; }}

/* ---------- 按钮 ---------- */
QPushButton[role="primary"]   {{ background: {COLORS['primary']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="primary"]:hover   {{ background: #285fb8; }}
QPushButton[role="success"]   {{ background: {COLORS['good']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="success"]:hover   {{ background: #1a8a4d; }}
QPushButton[role="danger"]    {{ background: {COLORS['bad']}; color: #ffffff; border: none; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="danger"]:hover    {{ background: #bd3a3a; }}
QPushButton[role="secondary"] {{ background: #eef3f8; color: #33516d; border: 1px solid #d2dfea; border-radius: 6px; padding: 9px 20px; font-weight: 600; }}
QPushButton[role="secondary"]:hover {{ background: #e2ebf3; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QDateTimeEdit {{ background: #ffffff; border: 1px solid #c9d8e6; border-radius: 5px; padding: 6px 8px; color: {COLORS['text']}; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus {{ border: 1px solid {COLORS['primary']}; }}

/* ---------- 表格 ---------- */
QTableWidget {{ background: #ffffff; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; color: #2c4359; alternate-background-color: #f7fafd; }}
QTableWidget::item {{ padding: 4px; }}
QHeaderView::section {{ background: #edf3f9; color: #33516d; font-weight: 700; border: none; border-bottom: 1px solid #d5e2ed; padding: 8px; }}
QTableCornerButton::section {{ background: #edf3f9; border: none; }}

/* ---------- 选项卡 ---------- */
QTabWidget::pane {{ border: 1px solid #d5e2ed; background: #ffffff; border-radius: 6px; }}
QTabBar::tab {{ background: #edf3f9; color: {COLORS['text_soft']}; padding: 9px 22px; border: 1px solid #d5e2ed; border-bottom: none; font-weight: 600; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {COLORS['primary']}; color: #ffffff; }}

/* ---------- 状态栏 ---------- */
QStatusBar {{ background: #e6edf5; color: #5e7891; border-top: 1px solid #d4e1ed; }}
"""


# --------------------------------------------------------------------------- #
#  主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("南极孤立微电网风力发电子站　监控与控制系统")
        self.resize(1920, 1080)
        self.setMinimumSize(1400, 800)

        self._build_ui()
        self.setStyleSheet(_QSS)
        self._start_clock()

        self._history_headers = []
        self._history_rows = []

    # ------------------------------------------------------------------ #
    #  基础构建
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())

        # 页面栈：六个页面
        self.pageStack = QtWidgets.QStackedWidget()
        self.pageStack.setObjectName("pageStack")
        self.pageStack.addWidget(self._build_monitor_page())   # 0 运行监控
        self.pageStack.addWidget(self._build_realtime_page())  # 1 实时曲线
        self.pageStack.addWidget(self._build_params_page())    # 2 参数设置
        self.pageStack.addWidget(self._build_control_page())   # 3 远程控制
        self.pageStack.addWidget(self._build_history_page())   # 4 历史数据
        self.pageStack.addWidget(self._build_alarm_page())     # 5 报警与通信
        body.addWidget(self.pageStack, 1)
        root.addLayout(body, 1)

        # 导航按钮 <-> 页面 联动（纯界面导航，非设备控制）
        self.navGroup = QtWidgets.QButtonGroup(self)
        self.navGroup.setExclusive(True)
        for i, btn in enumerate(self.navButtons):
            self.navGroup.addButton(btn, i)
            btn.clicked.connect(lambda _checked, i=i: self.pageStack.setCurrentIndex(i))
        self.navButtons[0].setChecked(True)

        self.statusBar().showMessage("系统就绪　|　界面预览模式：通信、控制与数据尚未接入")

    # ------------------------------------------------------------------ #
    #  顶栏
    # ------------------------------------------------------------------ #
    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(72)

        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(12)

        # Logo 徽标
        badge = QtWidgets.QFrame()
        badge.setObjectName("logoBadge")
        badge.setFixedSize(38, 38)
        badge.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QtWidgets.QVBoxLayout(badge)
        bl.setContentsMargins(0, 0, 0, 0)
        glyph = QtWidgets.QLabel("C")
        glyph.setObjectName("logoGlyph")
        glyph.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(glyph)
        lay.addWidget(badge)

        # 标题块
        tb = QtWidgets.QVBoxLayout()
        tb.setSpacing(0)
        self.appTitle = QtWidgets.QLabel("南极孤立微电网风力发电子站")
        self.appTitle.setObjectName("appTitle")
        self.appSubtitle = QtWidgets.QLabel("PC-C 上位机 · 监控与控制系统")
        self.appSubtitle.setObjectName("appSubtitle")
        tb.addWidget(self.appTitle)
        tb.addWidget(self.appSubtitle)
        lay.addLayout(tb)

        lay.addStretch(1)

        # 当前时间
        self.clockLabel = QtWidgets.QLabel()
        self.clockLabel.setObjectName("clockLabel")
        lay.addWidget(self.clockLabel)
        lay.addSpacing(6)

        # 三个状态胶囊
        self.pcADot, self.pcAStatus = self._status_pill(lay, "PC-A 在线", COLORS["good"])
        self.stmDot, self.stmStatus = self._status_pill(lay, "STM32 正常", COLORS["good"])
        self.pcCDot, self.pcCStatus = self._status_pill(lay, "PC-C 正常", COLORS["good"])

        return bar

    def _status_pill(self, parent_layout, text, color):
        """在顶栏放一个「圆点 + 文字」状态胶囊，返回 (圆点, 文字) 供后续更新。"""
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

    # ------------------------------------------------------------------ #
    #  左侧导航
    # ------------------------------------------------------------------ #
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

        self.btnMonitor  = self._nav_button("运行监控")
        self.btnRealtime = self._nav_button("实时曲线")
        self.btnParams   = self._nav_button("参数设置")
        self.btnControl  = self._nav_button("远程控制")
        self.btnHistory  = self._nav_button("历史数据")
        self.btnAlarm    = self._nav_button("报警与通信")
        self.navButtons = [
            self.btnMonitor, self.btnRealtime, self.btnParams,
            self.btnControl, self.btnHistory, self.btnAlarm,
        ]
        for btn in self.navButtons:
            lay.addWidget(btn)

        lay.addStretch(1)

        site = QtWidgets.QLabel("PC-C 风力发电子站\n孤立微电网 / 工业监控")
        site.setProperty("role", "siteLabel")
        lay.addWidget(site)

        return panel

    def _nav_button(self, text):
        btn = QtWidgets.QPushButton(text)
        btn.setProperty("role", "nav")
        btn.setCheckable(True)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(44)
        return btn

    # ------------------------------------------------------------------ #
    #  通用小组件工厂
    # ------------------------------------------------------------------ #
    @staticmethod
    def _frame():
        f = QtWidgets.QFrame()
        f.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        return f

    def _section_title(self, text):
        label = QtWidgets.QLabel("▍ " + text)
        label.setProperty("role", "sectionTitle")
        return label

    # ------------------------------------------------------------------ #
    #  连接管理（串口 / TCP / 本地仿真 / 数据库）
    # ------------------------------------------------------------------ #
    def _build_connection_card(self):
        card = self._frame()
        card.setProperty("role", "card")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        # 串口（STM32）
        grid.addWidget(QtWidgets.QLabel("串口 (STM32)"), 0, 0)
        self.serialPortCombo = QtWidgets.QComboBox()
        self.serialPortCombo.setMinimumWidth(220)
        grid.addWidget(self.serialPortCombo, 0, 1)
        self.refreshPortsButton = self._button("刷新端口", "secondary")
        grid.addWidget(self.refreshPortsButton, 0, 2)
        self.connectSerialButton = self._button("连接 STM32", "primary")
        grid.addWidget(self.connectSerialButton, 0, 3)
        self.disconnectSerialButton = self._button("断开串口", "secondary")
        self.disconnectSerialButton.setEnabled(False)
        grid.addWidget(self.disconnectSerialButton, 0, 4)

        # 本地仿真
        grid.addWidget(QtWidgets.QLabel("无硬件演示"), 0, 5)
        self.simulateButton = self._button("启动本地仿真", "secondary")
        grid.addWidget(self.simulateButton, 0, 6)

        # TCP（PC-A，预留）
        grid.addWidget(QtWidgets.QLabel("TCP (PC-A)"), 1, 0)
        self.tcpHostEdit = QtWidgets.QLineEdit(config.TCP_HOST_DEFAULT)
        self.tcpHostEdit.setMinimumWidth(140)
        grid.addWidget(self.tcpHostEdit, 1, 1)
        self.tcpPortEdit = QtWidgets.QLineEdit(str(config.TCP_PORT_DEFAULT))
        self.tcpPortEdit.setMaximumWidth(80)
        grid.addWidget(self.tcpPortEdit, 1, 2)
        self.connectTcpButton = self._button("连接 TCP", "primary")
        grid.addWidget(self.connectTcpButton, 1, 3)
        self.disconnectTcpButton = self._button("断开 TCP", "secondary")
        self.disconnectTcpButton.setEnabled(False)
        grid.addWidget(self.disconnectTcpButton, 1, 4)

        # 数据库状态
        grid.addWidget(QtWidgets.QLabel("数据库"), 1, 5)
        self.dbStatusLabel = QtWidgets.QLabel("已连接 wind.db")
        self.dbStatusLabel.setProperty("state", "good")
        grid.addWidget(self.dbStatusLabel, 1, 6)

        grid.setColumnStretch(1, 1)
        return card

    # ------------------------------------------------------------------ #
    #  ① 运行监控
    # ------------------------------------------------------------------ #
    def _build_monitor_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("风力发电机运行监控"))

        # 连接管理
        v.addWidget(self._build_connection_card())

        # 核心运行数据：四个大卡片
        kpi_defs = [
            ("风速",     "9.5",   "m/s", "来源：A → STM32 → PC-C", COLORS["wind"]),
            ("可用功率", "722.2", "kW",  "来源：STM32 计算",       COLORS["avail"]),
            ("目标功率", "500.0", "kW",  "来源：EMS B（经 A 转发）", COLORS["target"]),
            ("实际功率", "500.0", "kW",  "来源：A 计算",            COLORS["actual"]),
        ]
        kpi_row = QtWidgets.QHBoxLayout()
        kpi_row.setSpacing(14)
        self.kpiValues = []
        for title, value, unit, source, accent in kpi_defs:
            card, val_label = self._kpi_card(title, value, unit, source, accent)
            kpi_row.addWidget(card, 1)
            self.kpiValues.append(val_label)
        v.addLayout(kpi_row)
        self.windSpeedValue, self.powerAvailableValue, self.powerSetValue, self.powerActualValue = self.kpiValues

        # 风机运行状态 + 通信/设备状态
        status_defs = [
            ("风机状态",      "●  运行", "good", "status = 1 → 运行　　0 → 停止"),
            ("控制模式",      "●  闭环", "good", "0 → 开环　　1 → 闭环"),
            ("PC-A 通信",     "●  在线", "good", "TCP 连接：正常　最后通信：14:25:03"),
            ("STM32 状态",    "●  正常", "good", "UART 通信：正常　最后数据：14:25:03"),
            ("PC-C 自身状态", "●  正常", "good", "PC-A 在线　TCP 正常　UART 正常"),
        ]
        state_row = QtWidgets.QHBoxLayout()
        state_row.setSpacing(14)
        self.statusValues = []
        self.statusDetails = []
        for title, status, state, detail in status_defs:
            card, val_label, detail_label = self._status_card(title, status, state, detail)
            state_row.addWidget(card, 1)
            self.statusValues.append(val_label)
            self.statusDetails.append(detail_label)
        v.addLayout(state_row)
        (self.turbineStatusValue, self.controlModeValue, self.pcACommValue,
         self.stmStatusValue, self.pcCSelfValue) = self.statusValues

        # 当前控制周期
        self.cycleLabel = QtWidgets.QLabel("当前控制周期：101")
        self.cycleLabel.setProperty("role", "cardTitle")
        v.addWidget(self.cycleLabel)

        # 两张实时曲线（真实 pyqtgraph 曲线）
        chart_row = QtWidgets.QHBoxLayout()
        chart_row.setSpacing(14)
        self.windChart = build_wind_curve()
        self.powerChart = build_power_curve()
        wind_card = self._wrap_chart_card("实时风速曲线", "timestamp → wind_speed_mps (m/s)", self.windChart)
        power_card = self._wrap_chart_card("实时功率曲线", "可用 / 目标 / 实际功率 (kW)", self.powerChart)
        chart_row.addWidget(wind_card, 1)
        chart_row.addWidget(power_card, 1)
        v.addLayout(chart_row, 1)

        return page

    def _kpi_card(self, title, value, unit, source, accent):
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(116)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        # 标题 + 强调色圆点
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(7)
        dot = QtWidgets.QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {accent}; border-radius: 4px;")
        t = QtWidgets.QLabel(title)
        t.setProperty("role", "cardTitle")
        head.addWidget(dot)
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)

        # 数值 + 单位
        val_row = QtWidgets.QHBoxLayout()
        val_row.setSpacing(6)
        val = QtWidgets.QLabel(value)
        val.setProperty("role", "bigValue")
        u = QtWidgets.QLabel(unit)
        u.setProperty("role", "unit")
        val_row.addWidget(val)
        val_row.addWidget(u)
        val_row.addStretch(1)
        lay.addLayout(val_row)

        src = QtWidgets.QLabel(source)
        src.setProperty("role", "source")
        lay.addWidget(src)

        return card, val

    def _status_card(self, title, status_text, state, detail):
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(100)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        t = QtWidgets.QLabel(title)
        t.setProperty("role", "cardTitle")
        lay.addWidget(t)

        st = QtWidgets.QLabel(status_text)
        st.setProperty("state", state)
        lay.addWidget(st)

        d = QtWidgets.QLabel(detail)
        d.setProperty("role", "source")
        lay.addWidget(d)

        return card, st, d

    def _wrap_chart_card(self, title, subtitle, chart):
        card = self._frame()
        card.setProperty("role", "card")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        t = QtWidgets.QLabel(title)
        t.setProperty("role", "cardTitle")
        sub = QtWidgets.QLabel(subtitle)
        sub.setProperty("role", "source")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(sub)
        lay.addLayout(head)

        lay.addWidget(chart, 1)
        return card

    # ------------------------------------------------------------------ #
    #  ② 实时曲线
    # ------------------------------------------------------------------ #
    def _build_realtime_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("实时曲线"))

        info = self._frame()
        info.setProperty("role", "card")
        il = QtWidgets.QHBoxLayout(info)
        il.setContentsMargins(16, 12, 16, 12)
        l = QtWidgets.QLabel("数据窗口：最近 60 秒")
        l.setProperty("role", "cardTitle")
        r = QtWidgets.QLabel("横轴：timestamp　　采样数据实时刷新")
        r.setProperty("role", "source")
        il.addWidget(l)
        il.addStretch(1)
        il.addWidget(r)
        v.addWidget(info)

        self.rtWindChart = build_wind_curve()
        self.rtPowerChart = build_power_curve()
        self.rtWindChart.setMinimumHeight(300)
        self.rtPowerChart.setMinimumHeight(300)
        wind_card = self._wrap_chart_card("实时风速曲线", "wind_speed_mps / m/s", self.rtWindChart)
        power_card = self._wrap_chart_card("实时功率曲线", "wind_available_kw / wind_target_kw / wind_actual_kw (kW)", self.rtPowerChart)
        v.addWidget(wind_card, 1)
        v.addWidget(power_card, 1)

        return page

    # ------------------------------------------------------------------ #
    #  ③ 参数设置
    # ------------------------------------------------------------------ #
    def _build_params_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("风力发电机参数设置"))

        card = self._frame()
        card.setProperty("role", "card")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(24, 20, 24, 20)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(14)

        fields = [
            ("切入风速",  "cut_in_speed",           "3.0",    "m/s"),
            ("额定风速",  "rated_speed",            "12.0",   "m/s"),
            ("切出风速",  "cut_out_speed",          "25.0",   "m/s"),
            ("额定功率",  "rated_power",            "1000.0", "kW"),
            ("最大桨距角", "deg_max",                "20.0",   "°"),
            ("控制周期",  "control_period",         "1.0",    "s"),
            ("通信超时",  "communication_timeout",  "3.0",    "s"),
        ]
        self.paramEdits = {}
        for row, (label, key, default, unit) in enumerate(fields):
            lab = QtWidgets.QLabel(label)
            edit = QtWidgets.QLineEdit(default)
            unit_lab = QtWidgets.QLabel(unit)
            grid.addWidget(lab, row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(unit_lab, row, 2)
            self.paramEdits[key] = edit

        # 控制模式（下拉）
        grid.addWidget(QtWidgets.QLabel("控制模式"), 7, 0)
        self.controlModeCombo = QtWidgets.QComboBox()
        self.controlModeCombo.addItems(["闭环", "开环"])
        grid.addWidget(self.controlModeCombo, 7, 1)
        grid.addWidget(QtWidgets.QLabel("可修改"), 7, 2)

        self.applyParamsButton = self._button("应用参数", "primary")
        self.resetParamsButton = self._button("恢复默认", "secondary")
        grid.addWidget(self.applyParamsButton, 8, 1)
        grid.addWidget(self.resetParamsButton, 8, 2)

        # 让第 1 列（输入框）可拉伸，第 0/2 列保持紧凑
        grid.setColumnStretch(1, 1)

        v.addWidget(card)

        # 只读数据说明
        ro = self._frame()
        ro.setProperty("role", "card")
        rl = QtWidgets.QVBoxLayout(ro)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(4)
        t = QtWidgets.QLabel("ⓘ 不可修改的实时数据（仅显示）")
        t.setProperty("role", "cardTitle")
        d = QtWidgets.QLabel("wind_speed_mps · wind_available_kw · wind_operating_limit_kw · wind_target_kw · wind_actual_kw · wind_running · pitch_target_deg · cycle · timestamp · communication_status")
        d.setProperty("role", "source")
        rl.addWidget(t)
        rl.addWidget(d)
        v.addWidget(ro)

        v.addStretch(1)
        return page

    def _button(self, text, role):
        btn = QtWidgets.QPushButton(text)
        btn.setProperty("role", role)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        return btn

    # ------------------------------------------------------------------ #
    #  ④ 远程控制
    # ------------------------------------------------------------------ #
    def _build_control_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("风力发电机远程控制"))

        card = self._frame()
        card.setProperty("role", "card")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(28, 24, 28, 24)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)

        self.controlStatusLabel = QtWidgets.QLabel("当前状态：● 运行　　控制模式：● 闭环")
        self.controlStatusLabel.setProperty("state", "good")
        grid.addWidget(self.controlStatusLabel, 0, 0, 1, 2)

        self.startButton  = self._control_button("▶　启动", "success")
        self.stopButton   = self._control_button("■　停止", "danger")
        self.resetButton  = self._control_button("↻　复位", "secondary")
        self.autoButton   = self._control_button("⟳　AUTO / 闭环", "secondary")
        self.manualButton = self._control_button("⟳　MANUAL / 开环", "secondary")

        grid.addWidget(self.startButton, 1, 0)
        grid.addWidget(self.stopButton, 1, 1)
        grid.addWidget(self.resetButton, 2, 0)
        grid.addWidget(self.autoButton, 2, 1)
        grid.addWidget(self.manualButton, 3, 0, 1, 2)

        v.addWidget(card)
        v.addStretch(1)
        return page

    def _control_button(self, text, role):
        btn = self._button(text, role)
        btn.setMinimumSize(180, 64)
        return btn

    # ------------------------------------------------------------------ #
    #  ⑤ 历史数据
    # ------------------------------------------------------------------ #
    def _build_history_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("历史数据查询"))

        # 查询条件
        query = self._frame()
        query.setProperty("role", "card")
        ql = QtWidgets.QHBoxLayout(query)
        ql.setContentsMargins(16, 12, 16, 12)
        ql.setSpacing(8)

        ql.addWidget(QtWidgets.QLabel("开始时间"))
        self.startTimeEdit = self._datetime_edit()
        ql.addWidget(self.startTimeEdit)
        ql.addWidget(QtWidgets.QLabel("结束时间"))
        self.endTimeEdit = self._datetime_edit()
        ql.addWidget(self.endTimeEdit)
        ql.addWidget(QtWidgets.QLabel("数据类型"))
        self.dataTypeCombo = QtWidgets.QComboBox()
        self.dataTypeCombo.addItems(["运行数据", "控制历史"])
        ql.addWidget(self.dataTypeCombo)
        ql.addSpacing(8)
        self.queryButton = self._button("查询", "primary")
        self.refreshButton = self._button("刷新", "secondary")
        self.exportButton = self._button("导出", "secondary")
        ql.addWidget(self.queryButton)
        ql.addWidget(self.refreshButton)
        ql.addWidget(self.exportButton)
        ql.addStretch(1)
        v.addWidget(query)

        # 数据表
        headers = ["时间戳", "周期", "风速(m/s)", "可用功率(kW)", "目标功率(kW)",
                   "实际功率(kW)", "状态", "桨距角(°)", "控制模式", "通信状态"]
        rows = [
            ["2026-09-07 14:25:01", "101", "9.5", "722.2", "500.0", "500.0", "运行", "8.5", "闭环", "正常"],
            ["2026-09-07 14:24:51", "100", "9.3", "698.7", "500.0", "498.3", "运行", "8.2", "闭环", "正常"],
            ["2026-09-07 14:24:41", "99",  "9.1", "670.4", "500.0", "500.6", "运行", "8.0", "闭环", "正常"],
            ["2026-09-07 14:24:31", "98",  "8.9", "635.8", "500.0", "499.2", "运行", "7.8", "闭环", "正常"],
            ["2026-09-07 14:24:21", "97",  "8.5", "612.3", "500.0", "501.1", "运行", "7.5", "闭环", "正常"],
            ["2026-09-07 14:24:11", "96",  "8.3", "590.1", "500.0", "497.8", "运行", "7.2", "闭环", "正常"],
        ]
        self.historyTable = self._make_table(headers, rows)
        v.addWidget(self.historyTable, 1)

        return page

    def _datetime_edit(self):
        edit = QtWidgets.QDateTimeEdit()
        edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        edit.setCalendarPopup(True)
        edit.setDateTime(QtCore.QDateTime.currentDateTime())
        return edit

    def _make_table(self, headers, rows):
        table = QtWidgets.QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultSectionSize(110)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QtWidgets.QTableWidgetItem(val)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                table.setItem(r, c, item)
        return table

    # ------------------------------------------------------------------ #
    #  ⑥ 报警与通信
    # ------------------------------------------------------------------ #
    def _build_alarm_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)

        v.addWidget(self._section_title("报警与通信"))

        self.alarmTabs = QtWidgets.QTabWidget()

        # Tab 1：系统报警
        alarm_headers = ["时间", "等级", "类型", "来源", "描述"]
        alarm_rows = [
            ["14:25:01", "INFO",    "START", "GUI",   "风机启动成功"],
            ["14:24:21", "WARNING", "WIND",  "STM32", "风速接近切出值"],
            ["14:20:32", "ERROR",   "UART",  "UART",  "STM32 通信超时"],
        ]
        self.alarmTable = self._make_table(alarm_headers, alarm_rows)
        alarm_tab = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(alarm_tab)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.alarmTable)
        self.alarmTabs.addTab(alarm_tab, "系统报警")

        # Tab 2：通信日志
        comm_headers = ["时间", "接口", "方向", "消息类型", "周期", "数据长度", "结果", "延迟(ms)", "错误码"]
        comm_rows = [
            ["14:25:01", "TCP",  "RX", "wind_control", "101", "128", "成功", "3", "0"],
            ["14:25:01", "UART", "TX", "wind_control", "101", "64",  "成功", "2", "0"],
            ["14:25:02", "UART", "RX", "wind_status",  "101", "96",  "成功", "4", "0"],
            ["14:25:02", "TCP",  "TX", "wind_status",  "101", "96",  "成功", "3", "0"],
        ]
        self.communicationTable = self._make_table(comm_headers, comm_rows)
        comm_tab = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(comm_tab)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.communicationTable)
        self.alarmTabs.addTab(comm_tab, "通信日志")

        v.addWidget(self.alarmTabs, 1)
        return page

    # ------------------------------------------------------------------ #
    #  时钟（纯显示，非业务逻辑）
    # ------------------------------------------------------------------ #
    def _start_clock(self):
        self._clock_timer = QtCore.QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        now = datetime.now()
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        self.clockLabel.setText(now.strftime("%Y-%m-%d %H:%M:%S") + "  " + weekday)

    # ------------------------------------------------------------------ #
    #  供 Controller 调用的公开方法
    # ------------------------------------------------------------------ #
    def status_message(self, msg):
        self.statusBar().showMessage(msg)

    def load_params_form(self, params):
        for key in ("cut_in_speed", "rated_speed", "cut_out_speed", "rated_power",
                    "deg_max", "control_period", "communication_timeout"):
            self.paramEdits[key].setText(f"{float(params.get(key, 0)):.2f}")
        mode = int(params.get("control_mode", 1))
        self.controlModeCombo.setCurrentIndex(0 if mode == 1 else 1)

    def read_params_form(self):
        params = {}
        for key in ("cut_in_speed", "rated_speed", "cut_out_speed", "rated_power",
                    "deg_max", "control_period", "communication_timeout"):
            text = self.paramEdits[key].text().strip()
            try:
                params[key] = float(text)
            except ValueError:
                raise ValueError(f"参数“{config.PARAM_LABELS[key][0]}”不是有效数字")
        params["control_mode"] = 1 if self.controlModeCombo.currentIndex() == 0 else 0
        return params

    def update_kpi(self, wind_speed, power_available, power_set, power_actual):
        self.windSpeedValue.setText(f"{wind_speed:.1f}")
        self.powerAvailableValue.setText(f"{power_available:.1f}")
        self.powerSetValue.setText(f"{power_set:.1f}")
        self.powerActualValue.setText(f"{power_actual:.1f}")

    def _set_state_label(self, label, state, text):
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def update_status(self, status=None, control_mode=None, cycle=None):
        if status is not None:
            run = status == 1
            self._set_state_label(self.turbineStatusValue, "good" if run else "bad",
                                  "●  运行" if run else "●  停止")
        if control_mode is not None:
            closed = control_mode == 1
            self._set_state_label(self.controlModeValue, "good" if closed else "warn",
                                  "●  闭环" if closed else "●  开环")
            self.controlStatusLabel.setText(
                f"当前状态：{self.turbineStatusValue.text()}　控制模式：{self.controlModeValue.text()}")
        if cycle is not None:
            self.cycleLabel.setText(f"当前控制周期：{cycle}")

    def set_comm_pills(self, pca_online=None, stm32_online=None):
        now = datetime.now().strftime("%H:%M:%S")
        if pca_online is not None:
            color = COLORS["good"] if pca_online else COLORS["bad"]
            self.pcADot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            self.pcAStatus.setText("PC-A 在线" if pca_online else "PC-A 离线")
            self._set_state_label(self.pcACommValue, "good" if pca_online else "bad",
                                  "●  在线" if pca_online else "●  离线")
            self.statusDetails[2].setText(
                f"TCP 连接：{'正常' if pca_online else '断开'}　最后通信：{now}")
        if stm32_online is not None:
            color = COLORS["good"] if stm32_online else COLORS["bad"]
            self.stmDot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            self.stmStatus.setText("STM32 正常" if stm32_online else "STM32 超时/离线")
            self._set_state_label(self.stmStatusValue, "good" if stm32_online else "bad",
                                  "●  正常" if stm32_online else "●  异常")
            self.statusDetails[3].setText(
                f"UART 通信：{'正常' if stm32_online else '超时'}　最后数据：{now}")

    def append_curves(self, data, t=None):
        t = t if t is not None else time.time()
        self.windChart.append_multi(t, {"wind_speed_mps": data.get("wind_speed_mps")})
        self.rtWindChart.append_multi(t, {"wind_speed_mps": data.get("wind_speed_mps")})
        pw = {
            "wind_available_kw": data.get("wind_available_kw"),
            "wind_target_kw": data.get("wind_target_kw"),
            "wind_actual_kw": data.get("wind_actual_kw"),
        }
        self.powerChart.append_multi(t, pw)
        self.rtPowerChart.append_multi(t, pw)
        self.windChart.redraw()
        self.rtWindChart.redraw()
        self.powerChart.redraw()
        self.rtPowerChart.redraw()

    def clear_curves(self):
        for c in (self.windChart, self.rtWindChart, self.powerChart, self.rtPowerChart):
            c.clear()
            c.redraw()

    def set_history_rows(self, rows, kind="telemetry"):
        if kind == "telemetry":
            headers = ["时间戳", "周期", "风速(m/s)", "可用功率(kW)", "目标功率(kW)",
                       "实际功率(kW)", "状态", "桨距角(°)", "控制模式", "通信状态"]
            formatted = []
            for ts, cycle, ws, pa, ps, pact, status, deg, cm, cs in rows:
                formatted.append([
                    ts, str(cycle), f"{ws:.1f}", f"{pa:.1f}", f"{ps:.1f}", f"{pact:.1f}",
                    "运行" if status == 1 else "停止",
                    f"{deg:.1f}" if deg is not None else "-",
                    "闭环" if cm == 1 else "开环",
                    "正常" if cs == 1 else "超时",
                ])
        else:
            headers = ["时间戳", "周期", "风速(m/s)", "可用功率(kW)", "目标功率(kW)",
                       "实际功率(kW)", "桨距角(°)", "状态", "柴发设定(kW)", "负荷(kW)", "决策状态"]
            formatted = []
            for ts, cycle, ws, pa, ps, pact, deg, status, pd, load, ds in rows:
                formatted.append([
                    ts, str(cycle), f"{ws:.1f}", f"{pa:.1f}", f"{ps:.1f}", f"{pact:.1f}",
                    f"{deg:.1f}" if deg is not None else "-",
                    "运行" if status == 1 else "停止",
                    f"{pd:.1f}" if pd is not None else "-",
                    f"{load:.1f}" if load is not None else "-",
                    ds or "-",
                ])
        self._fill_table(self.historyTable, headers, formatted)
        self._history_headers = headers
        self._history_rows = formatted

    def get_history_table(self):
        return self._history_headers, self._history_rows

    def set_system_log_rows(self, rows):
        headers = ["时间", "等级", "类型", "来源", "描述"]
        self._fill_table(self.alarmTable, headers, [list(r) for r in rows])

    def set_comm_log_rows(self, rows):
        headers = ["时间", "接口", "方向", "消息类型", "周期", "数据长度", "结果", "延迟(ms)", "错误码"]
        formatted = []
        for ts, itf, direction, mtype, cycle, dlen, result, latency, errcode in rows:
            formatted.append([
                ts, itf, direction, mtype or "-", str(cycle) if cycle is not None else "-",
                str(dlen), "成功" if result == 1 else "失败",
                f"{latency:.0f}" if latency is not None else "-", errcode or "0",
            ])
        self._fill_table(self.communicationTable, headers, formatted)

    def _fill_table(self, table, headers, rows):
        table.setRowCount(0)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        for r in rows:
            row_idx = table.rowCount()
            table.insertRow(row_idx)
            for c, val in enumerate(r):
                item = QtWidgets.QTableWidgetItem(str(val))
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                table.setItem(row_idx, c, item)
