"""CSV-driven PyQt6 interface for A, styled to match the C host UI."""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime
from pathlib import Path
import sys

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import SimulationConfig, load_config
from ..repository import Repository
from ..scenario import (
    ScenarioCurve,
    ScenarioPoint,
    load_scenario_csv,
    save_scenario_csv,
)
from .curve_widget import CurveEditor, format_sim_time


ROOT_DIR = Path(__file__).resolve().parents[2]

# Kept deliberately identical to C_controller/host/gui.py on the remote main
# branch so A and C read as parts of one SCADA suite.
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
    "target": "#e19a1a",
    "actual": "#6b4fd8",
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
QPushButton[role="nav"] {{
    background: transparent; border: none; text-align: left; border-radius: 7px;
    padding: 12px 18px; color: #33516d; font-size: 15px; font-weight: 600;
}}
QPushButton[role="nav"]:hover {{ background: #e8f0f8; }}
QPushButton[role="nav"]:checked {{ background: {COLORS['primary']}; color: #ffffff; }}
QLabel[role="siteLabel"] {{ color: #9aabba; font-size: 11px; padding: 8px 18px; }}

QLabel[role="sectionTitle"] {{ color: {COLORS['primary_dark']}; font-size: 19px; font-weight: 700; }}
QFrame[role="card"] {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}
QLabel[role="cardTitle"] {{ color: {COLORS['text_soft']}; font-size: 13px; font-weight: 600; }}
QLabel[role="bigValue"] {{ color: #123a5f; font-size: 30px; font-weight: 700; }}
QLabel[role="unit"] {{ color: #5c758c; font-size: 13px; font-weight: 600; }}
QLabel[role="source"] {{ color: {COLORS['text_muted']}; font-size: 11px; }}
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
QPushButton:disabled {{ background: #dfe6ed; color: #9aabba; }}

QSlider::groove:horizontal {{ height: 6px; background: #dce6f0; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {COLORS['primary']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; background: #ffffff; border: 2px solid {COLORS['primary']}; border-radius: 8px; }}

QTableWidget {{ background: #ffffff; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; color: #2c4359; alternate-background-color: #f7fafd; }}
QTableWidget::item {{ padding: 5px; }}
QHeaderView::section {{ background: #edf3f9; color: #33516d; font-weight: 700; border: none; border-bottom: 1px solid #d5e2ed; padding: 8px; }}
QTableCornerButton::section {{ background: #edf3f9; border: none; }}
QStatusBar {{ background: #e6edf5; color: #5e7891; border-top: 1px solid #d4e1ed; }}
"""


PROTOCOL_ROWS = (
    ("sampled_at_utc", "A 状态断面采样时间", "UTC"),
    ("wind_speed_mps", "CSV 风速输入", "m/s"),
    ("wind_available_kw", "风资源能力，不含 B/C 控制", "kW"),
    ("wind_operating_limit_kw", "考虑 C 许可与桨距后的稳态上限", "kW"),
    ("load_power_kw", "CSV 负荷输入", "kW"),
    ("wind_actual_kw", "A 模型计算的风电实际出力", "kW"),
    ("diesel_actual_kw", "A 模型计算的柴发实际出力", "kW"),
    ("wind_target_kw", "B 调度目标，不是实际出力", "kW"),
    ("pitch_actual_deg", "C 风机动作", "deg"),
    ("wind_running", "风机运行遥信", "bool"),
    ("fault", "故障遥信", "bool"),
)


class SimulatorWindow(QMainWindow):
    def __init__(self, *, db_path: Path, config_path: Path, scenario_path: Path) -> None:
        super().__init__()
        self.db_path = db_path.resolve()
        self.config_path = config_path.resolve()
        self.config: SimulationConfig = load_config(self.config_path)
        self.repository = Repository(self.db_path)
        self.scenario_path = scenario_path.resolve()
        self._scenario_from_database = False
        self.scenario = self._initial_scenario(self.scenario_path)
        self._runner = QProcess(self)
        self._tcp_server = QProcess(self)

        self.setWindowTitle("南极孤立微电网电网模拟器　监控与场景系统")
        self.resize(1500, 900)
        self.setMinimumSize(1200, 760)
        self._build_ui()
        self.setStyleSheet(_QSS)
        self._connect_signals()
        self._set_scenario(
            self.scenario,
            None if self._scenario_from_database else self.scenario_path,
        )
        self._start_timers()
        self.refresh_state()

    def _initial_scenario(self, requested_path: Path) -> ScenarioCurve:
        if self.db_path.is_file():
            try:
                scenario = self.repository.get_scenario()
                self._scenario_from_database = True
                return scenario
            except (OSError, RuntimeError, ValueError):
                pass
        return load_scenario_csv(requested_path)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())
        self.pageStack = QStackedWidget()
        self.pageStack.addWidget(self._build_scenario_page())
        self.pageStack.addWidget(self._build_monitor_page())
        self.pageStack.addWidget(self._build_protocol_page())
        body.addWidget(self.pageStack, 1)
        root.addLayout(body, 1)

        self.navGroup = QButtonGroup(self)
        self.navGroup.setExclusive(True)
        for index, button in enumerate(self.navButtons):
            self.navGroup.addButton(button, index)
            button.clicked.connect(
                lambda _checked, page=index: self.pageStack.setCurrentIndex(page)
            )
        self.navButtons[0].setChecked(True)
        self.statusBar().showMessage("系统就绪｜示例物理参数尚未确认，当前结果仅为软件 mock")

    def _build_header(self) -> QFrame:
        bar = self._frame("headerBar")
        bar.setFixedHeight(72)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        badge = self._frame("logoBadge")
        badge.setFixedSize(38, 38)
        badge_layout = QVBoxLayout(badge)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        glyph = QLabel("A")
        glyph.setObjectName("logoGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_layout.addWidget(glyph)
        layout.addWidget(badge)

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        self.appTitle = QLabel("南极孤立微电网电网模拟器")
        self.appTitle.setObjectName("appTitle")
        self.appSubtitle = QLabel("PC-A · 场景、仿真与通信监控系统")
        self.appSubtitle.setObjectName("appSubtitle")
        title_block.addWidget(self.appTitle)
        title_block.addWidget(self.appSubtitle)
        layout.addLayout(title_block)
        layout.addStretch(1)

        self.clockLabel = QLabel()
        self.clockLabel.setObjectName("clockLabel")
        layout.addWidget(self.clockLabel)
        layout.addSpacing(6)
        self.dbDot, self.dbPill = self._status_pill(layout, "grid.db 未连接", COLORS["warn"])
        self.simDot, self.simPill = self._status_pill(layout, "仿真未启动", COLORS["warn"])
        self.tcpDot, self.tcpPill = self._status_pill(layout, "TCP 服务停止", COLORS["bad"])
        return bar

    def _build_nav(self) -> QFrame:
        panel = self._frame("navPanel")
        panel.setFixedWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(2)
        caption = QLabel("功能导航")
        caption.setProperty("role", "navCaption")
        layout.addWidget(caption)
        self.btnScenario = self._nav_button("场景曲线")
        self.btnMonitor = self._nav_button("运行监控")
        self.btnProtocol = self._nav_button("TCP 协议状态")
        self.navButtons = [self.btnScenario, self.btnMonitor, self.btnProtocol]
        for button in self.navButtons:
            layout.addWidget(button)
        layout.addStretch(1)
        site = QLabel("PC-A 电网模拟器\n孤立微电网 / 工业监控")
        site.setProperty("role", "siteLabel")
        layout.addWidget(site)
        return panel

    def _build_scenario_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(self._section_title("风速与负荷场景曲线"))

        file_card = self._frame()
        file_card.setProperty("role", "card")
        file_layout = QHBoxLayout(file_card)
        file_layout.setContentsMargins(16, 12, 16, 12)
        file_layout.setSpacing(8)
        self.open_button = self._button("加载 CSV", "primary")
        self.save_button = self._button("另存 CSV", "secondary")
        self.initialize_button = self._button("初始化 grid.db", "success")
        file_layout.addWidget(self.open_button)
        file_layout.addWidget(self.save_button)
        file_layout.addWidget(self.initialize_button)
        self.source_label = QLabel()
        self.source_label.setProperty("role", "source")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_layout.addWidget(self.source_label, 1)
        layout.addWidget(file_card)

        times = [point.sim_time_s for point in self.scenario.points]
        wind_values = [point.wind_speed_mps for point in self.scenario.points]
        load_values = [point.load_power_kw for point in self.scenario.points]
        self.wind_editor = CurveEditor(
            key="wind_speed_mps",
            name="风速场景输入",
            unit="m/s",
            color=COLORS["wind"],
            times_s=times,
            values=wind_values,
            maximum=max(self.config.wind.cut_out_speed_mps, max(wind_values) * 1.05, 1.0),
        )
        self.load_editor = CurveEditor(
            key="load_power_kw",
            name="负荷场景输入",
            unit="kW",
            color=COLORS["warn"],
            times_s=times,
            values=load_values,
            maximum=max(max(load_values) * 1.1, 1.0),
        )
        layout.addWidget(self._chart_card(self.wind_editor), 1)
        layout.addWidget(self._chart_card(self.load_editor), 1)

        preview_card = self._frame()
        preview_card.setProperty("role", "card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 10, 16, 10)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        preview_layout.addWidget(self.time_slider)
        self.preview = QLabel()
        self.preview.setProperty("role", "cardTitle")
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_card)
        return page

    def _build_monitor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(self._section_title("电网模拟器运行监控"))

        control_card = self._frame()
        control_card.setProperty("role", "card")
        controls = QHBoxLayout(control_card)
        controls.setContentsMargins(16, 12, 16, 12)
        controls.addWidget(QLabel("仿真控制"))
        self.start_button = self._button("▶  启动仿真", "success")
        self.pause_button = self._button("Ⅱ  暂停", "secondary")
        self.resume_button = self._button("▶  继续", "primary")
        self.stop_button = self._button("■  停止", "danger")
        for button in (
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.stop_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        self.runtime_detail = QLabel("数据库未初始化")
        self.runtime_detail.setProperty("role", "source")
        self.runtime_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        controls.addWidget(self.runtime_detail)
        layout.addWidget(control_card)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)
        kpi_defs = (
            ("风速", "m/s", "来源：CSV 场景输入", COLORS["wind"]),
            ("负荷", "kW", "来源：CSV 场景输入", COLORS["warn"]),
            ("风电实际功率", "kW", "来源：A 物理模型", COLORS["actual"]),
            ("柴发实际功率", "kW", "来源：A 物理模型", COLORS["good"]),
        )
        self.kpi_labels: dict[str, QLabel] = {}
        for key, definition in zip(
            ("wind_speed_mps", "load_power_kw", "wind_actual_kw", "diesel_actual_kw"),
            kpi_defs,
        ):
            card, value = self._kpi_card(*definition)
            self.kpi_labels[key] = value
            kpi_row.addWidget(card, 1)
        layout.addLayout(kpi_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(14)
        self.monitor_status: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title, detail in (
            ("simulation", "仿真状态", "A 计算进程"),
            ("wind", "风机状态", "实际运行遥信"),
            ("B", "B EMS 通信", "A TCP 客户端状态"),
            ("C", "C STM32 通信", "A TCP 客户端状态"),
        ):
            card, status, detail_label = self._status_card(title, "●  未知", "warn", detail)
            self.monitor_status[key] = (status, detail_label)
            status_row.addWidget(card, 1)
        layout.addLayout(status_row)

        capability_card = self._frame()
        capability_card.setProperty("role", "card")
        capability_layout = QGridLayout(capability_card)
        capability_layout.setContentsMargins(20, 16, 20, 16)
        self.capability_labels: dict[str, QLabel] = {}
        for index, (key, title, color) in enumerate(
            (
                ("wind_available_kw", "风资源可用功率", COLORS["avail"]),
                ("wind_operating_limit_kw", "风机运行上限", COLORS["primary"]),
                ("wind_target_kw", "B 风电目标", COLORS["target"]),
                ("power_imbalance_kw", "功率不平衡", COLORS["bad"]),
            )
        ):
            title_label = QLabel(title)
            title_label.setProperty("role", "cardTitle")
            value_label = QLabel("-")
            value_label.setStyleSheet(f"color:{color}; font-size:22px; font-weight:700;")
            capability_layout.addWidget(title_label, 0, index)
            capability_layout.addWidget(value_label, 1, index)
            self.capability_labels[key] = value_label
        layout.addWidget(capability_card)
        layout.addStretch(1)
        return page

    def _build_protocol_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(self._section_title("A → B/C TCP 状态报文"))

        server_card = self._frame()
        server_card.setProperty("role", "card")
        server_layout = QHBoxLayout(server_card)
        server_layout.setContentsMargins(16, 12, 16, 12)
        self.tcp_button = self._button("启动 TCP 服务", "primary")
        server_layout.addWidget(self.tcp_button)
        server_layout.addWidget(
            QLabel(f"监听配置：{self.config.server_bind}:{self.config.server_port}")
        )
        server_layout.addStretch(1)
        self.envelope_label = QLabel("session_id=-  step=-  sim_time_s=-")
        self.envelope_label.setProperty("role", "source")
        self.envelope_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        server_layout.addWidget(self.envelope_label)
        layout.addWidget(server_card)

        self.protocol_table = QTableWidget(len(PROTOCOL_ROWS), 4)
        self.protocol_table.setHorizontalHeaderLabels(("字段", "当前值", "单位", "语义"))
        self.protocol_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.protocol_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.protocol_table.setAlternatingRowColors(True)
        self.protocol_table.verticalHeader().setVisible(False)
        header = self.protocol_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.protocol_value_items: dict[str, QTableWidgetItem] = {}
        for row, (key, meaning, unit) in enumerate(PROTOCOL_ROWS):
            key_item = QTableWidgetItem(key)
            value_item = QTableWidgetItem("-")
            unit_item = QTableWidgetItem(unit)
            meaning_item = QTableWidgetItem(meaning)
            for item in (key_item, value_item, unit_item, meaning_item):
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.protocol_table.setItem(row, 0, key_item)
            self.protocol_table.setItem(row, 1, value_item)
            self.protocol_table.setItem(row, 2, unit_item)
            self.protocol_table.setItem(row, 3, meaning_item)
            self.protocol_value_items[key] = value_item
        layout.addWidget(self.protocol_table, 1)

        note = self._frame()
        note.setProperty("role", "card")
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("ⓘ 控制权边界")
        title.setProperty("role", "cardTitle")
        description = QLabel(
            "B 只发送 target/enable；C 只发送 wind_enable/pitch_target_deg；"
            "A 根据场景、目标、动作和设备约束计算 actual。ACK 接收成功不代表出力已经到达目标。"
        )
        description.setProperty("role", "source")
        note_layout.addWidget(title)
        note_layout.addWidget(description)
        layout.addWidget(note)
        return page

    @staticmethod
    def _frame(object_name: str | None = None) -> QFrame:
        frame = QFrame()
        if object_name:
            frame.setObjectName(object_name)
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return frame

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel("▍ " + text)
        label.setProperty("role", "sectionTitle")
        return label

    @staticmethod
    def _button(text: str, role: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _nav_button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", "nav")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(44)
        return button

    def _status_pill(
        self, parent_layout: QHBoxLayout, text: str, color: str
    ) -> tuple[QFrame, QLabel]:
        pill = self._frame()
        pill.setProperty("role", "pill")
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(7)
        dot = QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color}; border-radius:5px;")
        label = QLabel(text)
        label.setProperty("role", "pillText")
        layout.addWidget(dot)
        layout.addWidget(label)
        parent_layout.addWidget(pill)
        return dot, label

    def _chart_card(self, chart: CurveEditor) -> QFrame:
        card = self._frame()
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(chart)
        return card

    def _kpi_card(
        self, title: str, unit: str, source: str, accent: str
    ) -> tuple[QFrame, QLabel]:
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(116)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        header = QHBoxLayout()
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{accent}; border-radius:4px;")
        title_label = QLabel(title)
        title_label.setProperty("role", "cardTitle")
        header.addWidget(dot)
        header.addWidget(title_label)
        header.addStretch(1)
        layout.addLayout(header)
        value_row = QHBoxLayout()
        value = QLabel("-")
        value.setProperty("role", "bigValue")
        unit_label = QLabel(unit)
        unit_label.setProperty("role", "unit")
        value_row.addWidget(value)
        value_row.addWidget(unit_label)
        value_row.addStretch(1)
        layout.addLayout(value_row)
        source_label = QLabel(source)
        source_label.setProperty("role", "source")
        layout.addWidget(source_label)
        return card, value

    def _status_card(
        self, title: str, status_text: str, state: str, detail: str
    ) -> tuple[QFrame, QLabel, QLabel]:
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(100)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setProperty("role", "cardTitle")
        status = QLabel(status_text)
        status.setProperty("state", state)
        detail_label = QLabel(detail)
        detail_label.setProperty("role", "source")
        layout.addWidget(title_label)
        layout.addWidget(status)
        layout.addWidget(detail_label)
        return card, status, detail_label

    def _connect_signals(self) -> None:
        self.open_button.clicked.connect(self.open_csv)
        self.save_button.clicked.connect(self.save_csv)
        self.initialize_button.clicked.connect(self.initialize_database)
        self.time_slider.valueChanged.connect(self.update_preview)
        self.wind_editor.curveChanged.connect(self.update_preview)
        self.load_editor.curveChanged.connect(self.update_preview)
        self.start_button.clicked.connect(lambda: self.control_simulation("start"))
        self.pause_button.clicked.connect(lambda: self.control_simulation("pause"))
        self.resume_button.clicked.connect(lambda: self.control_simulation("resume"))
        self.stop_button.clicked.connect(lambda: self.control_simulation("stop"))
        self.tcp_button.clicked.connect(self.toggle_tcp_server)
        self._tcp_server.stateChanged.connect(self._tcp_state_changed)
        self._tcp_server.readyReadStandardError.connect(self._show_tcp_error)
        self._runner.readyReadStandardOutput.connect(self._discard_runner_output)
        self._runner.readyReadStandardError.connect(self._show_runner_error)

    def _start_timers(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self.refresh_state)
        self._refresh_timer.start()
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    def _update_clock(self) -> None:
        now = datetime.now()
        weekday = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[
            now.weekday()
        ]
        self.clockLabel.setText(now.strftime("%Y-%m-%d %H:%M:%S") + "  " + weekday)

    def _set_scenario(self, scenario: ScenarioCurve, source_path: Path | None) -> None:
        self.scenario = scenario
        if source_path is not None:
            self.scenario_path = source_path.resolve()
        times = [point.sim_time_s for point in scenario.points]
        wind = [point.wind_speed_mps for point in scenario.points]
        load = [point.load_power_kw for point in scenario.points]
        self.wind_editor.set_data(
            times,
            wind,
            maximum=max(self.config.wind.cut_out_speed_mps, max(wind) * 1.05, 1.0),
        )
        self.load_editor.set_data(times, load, maximum=max(max(load) * 1.1, 1.0))
        self.time_slider.setRange(0, len(times) - 1)
        self.time_slider.setValue(0)
        self.source_label.setText(
            f"场景：{self.scenario_path}" if source_path else "场景：当前 grid.db 快照"
        )
        self.update_preview()

    def _scenario_from_editors(self) -> ScenarioCurve:
        return ScenarioCurve(
            tuple(
                ScenarioPoint(seconds, wind, load)
                for seconds, wind, load in zip(
                    self.wind_editor.times_s(),
                    self.wind_editor.values(),
                    self.load_editor.values(),
                )
            )
        )

    def open_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "加载 A 场景 CSV", str(self.scenario_path.parent), "CSV (*.csv)"
        )
        if not path:
            return
        try:
            scenario = load_scenario_csv(path)
            if (
                scenario.points[0].sim_time_s > self.config.start_s
                or scenario.points[-1].sim_time_s < self.config.end_s
            ):
                raise ValueError("CSV 时间轴不能覆盖配置中的仿真起止时刻")
            self._set_scenario(scenario, Path(path))
            self.statusBar().showMessage("CSV 已加载｜需初始化新数据库后才用于仿真", 6000)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "CSV 加载失败", str(error))

    def save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "另存 A 场景 CSV", str(self.scenario_path), "CSV (*.csv)"
        )
        if not path:
            return
        try:
            save_scenario_csv(path, self._scenario_from_editors())
            self.scenario_path = Path(path).resolve()
            self.source_label.setText(f"场景：{self.scenario_path}")
            self.statusBar().showMessage("CSV 已保存", 4000)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "CSV 保存失败", str(error))

    def initialize_database(self) -> None:
        try:
            self.repository.initialize(self.config, self._scenario_from_editors())
            self.statusBar().showMessage("grid.db 初始化完成", 4000)
            self.refresh_state()
        except (FileExistsError, OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                "初始化失败",
                f"{error}\n\n为保护已有运行数据，本界面不会覆盖现有数据库。",
            )

    def update_preview(self, *_unused) -> None:
        index = min(self.time_slider.value(), len(self.wind_editor.times_s()) - 1)
        seconds = self.wind_editor.times_s()[index]
        self.wind_editor.set_playhead(seconds)
        self.load_editor.set_playhead(seconds)
        self.preview.setText(
            f"仿真时刻 {format_sim_time(seconds)}    "
            f"风速 {self.wind_editor.value_at(seconds):.3f} m/s    "
            f"负荷 {self.load_editor.value_at(seconds):.3f} kW"
        )

    def _ensure_runner(self) -> None:
        if self._runner.state() != QProcess.ProcessState.NotRunning:
            return
        self._runner.setWorkingDirectory(str(ROOT_DIR))
        self._runner.start(
            sys.executable,
            ["-m", "A_simulator", "run", "--db", str(self.db_path)],
        )

    def control_simulation(self, action: str) -> None:
        try:
            status = self.repository.set_status(action)
            if action in {"start", "resume"}:
                self._ensure_runner()
            self.statusBar().showMessage(f"仿真状态：{status}", 3000)
            self.refresh_state()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "仿真控制失败", str(error))

    def toggle_tcp_server(self) -> None:
        if self._tcp_server.state() != QProcess.ProcessState.NotRunning:
            self._tcp_server.terminate()
            return
        if not self.db_path.is_file():
            QMessageBox.warning(self, "TCP 服务", "请先初始化 grid.db")
            return
        self._tcp_server.setWorkingDirectory(str(ROOT_DIR))
        self._tcp_server.start(
            sys.executable,
            [
                "-m",
                "A_simulator",
                "serve",
                "--db",
                str(self.db_path),
                "--config",
                str(self.config_path),
            ],
        )

    def _tcp_state_changed(self, state: QProcess.ProcessState) -> None:
        running = state != QProcess.ProcessState.NotRunning
        self.tcp_button.setText("停止 TCP 服务" if running else "启动 TCP 服务")
        self._set_button_role(self.tcp_button, "danger" if running else "primary")
        self._set_pill(
            self.tcpDot,
            self.tcpPill,
            "TCP 服务运行" if running else "TCP 服务停止",
            COLORS["good"] if running else COLORS["bad"],
        )

    @staticmethod
    def _set_button_role(button: QPushButton, role: str) -> None:
        button.setProperty("role", role)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    @staticmethod
    def _set_pill(dot: QFrame, label: QLabel, text: str, color: str) -> None:
        dot.setStyleSheet(f"background:{color}; border-radius:5px;")
        label.setText(text)

    @staticmethod
    def _set_state_label(label: QLabel, state: str, text: str) -> None:
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _discard_runner_output(self) -> None:
        self._runner.readAllStandardOutput()

    def _show_runner_error(self) -> None:
        text = bytes(self._runner.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            self.statusBar().showMessage(f"仿真进程：{text[-180:]}", 8000)

    def _show_tcp_error(self) -> None:
        text = bytes(self._tcp_server.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            self.statusBar().showMessage(f"TCP：{text[-180:]}", 8000)

    def refresh_state(self) -> None:
        if not self.db_path.is_file():
            self.runtime_detail.setText("数据库未初始化")
            self._set_pill(self.dbDot, self.dbPill, "grid.db 未连接", COLORS["warn"])
            self._set_control_buttons(None)
            return
        try:
            runtime = self.repository.runtime()
            state = self.repository.get_state()
            connections = self.repository.connection_statuses()
        except (OSError, RuntimeError, ValueError) as error:
            self.runtime_detail.setText(f"数据库读取失败：{error}")
            self._set_pill(self.dbDot, self.dbPill, "grid.db 异常", COLORS["bad"])
            self._set_control_buttons(None)
            return

        status = str(runtime["status"])
        self._set_pill(self.dbDot, self.dbPill, "grid.db 正常", COLORS["good"])
        sim_color = COLORS["good"] if status == "running" else COLORS["warn"]
        if status in {"stopped", "completed"}:
            sim_color = COLORS["bad"] if status == "stopped" else COLORS["good"]
        self._set_pill(self.simDot, self.simPill, f"仿真 {status}", sim_color)
        self.runtime_detail.setText(
            f"grid.db：{self.db_path}　step={state.step}　参数：{runtime['parameter_status']}"
        )
        self.envelope_label.setText(
            f"session_id={state.session_id}　step={state.step}　sim_time_s={state.sim_time_s:.3f}"
        )

        for key, label in self.kpi_labels.items():
            label.setText(f"{getattr(state, key):.1f}")
        for key, label in self.capability_labels.items():
            label.setText(f"{getattr(state, key):.3f} kW")

        state_names = {
            "ready": "就绪",
            "running": "运行",
            "paused": "暂停",
            "stopped": "停止",
            "completed": "完成",
        }
        sim_state = "good" if status == "running" else "warn"
        if status == "stopped":
            sim_state = "bad"
        self._update_status_card(
            "simulation", sim_state, f"●  {state_names.get(status, status)}", f"step={state.step}　t={format_sim_time(state.sim_time_s)}"
        )
        self._update_status_card(
            "wind",
            "good" if state.wind_running else "bad",
            "●  运行" if state.wind_running else "●  停止",
            f"桨距 {state.pitch_actual_deg:.1f} deg　故障={'是' if state.fault else '否'}",
        )
        for peer in ("B", "C"):
            item = connections.get(peer, {"connected": False, "detail": "not connected"})
            connected = bool(item["connected"])
            self._update_status_card(
                peer,
                "good" if connected else "bad",
                "●  在线" if connected else "●  离线",
                str(item["detail"]),
            )

        payload = state.protocol_payload()
        for key, item in self.protocol_value_items.items():
            value = payload[key]
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, float):
                text = f"{value:.3f}"
            else:
                text = str(value)
            item.setText(text)

        if not self.time_slider.isSliderDown():
            times = self.wind_editor.times_s()
            right = bisect_left(times, state.sim_time_s)
            if right >= len(times):
                index = len(times) - 1
            elif right == 0:
                index = 0
            else:
                index = min(
                    (right - 1, right),
                    key=lambda candidate: abs(times[candidate] - state.sim_time_s),
                )
            self.time_slider.setValue(index)
        self._set_control_buttons(status)

    def _update_status_card(self, key: str, state: str, text: str, detail: str) -> None:
        label, detail_label = self.monitor_status[key]
        self._set_state_label(label, state, text)
        detail_label.setText(detail)

    def _set_control_buttons(self, status: str | None) -> None:
        self.start_button.setEnabled(status == "ready")
        self.pause_button.setEnabled(status == "running")
        self.resume_button.setEnabled(status == "paused")
        self.stop_button.setEnabled(status in {"running", "paused"})

    def closeEvent(self, event) -> None:
        for process in (self._runner, self._tcp_server):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(1000):
                    process.kill()
        super().closeEvent(event)


def run_gui(*, db_path: Path, config_path: Path, scenario_path: Path) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = SimulatorWindow(
        db_path=db_path,
        config_path=config_path,
        scenario_path=scenario_path,
    )
    window.show()
    return app.exec()
