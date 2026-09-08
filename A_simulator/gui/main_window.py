"""Integrated PyQt6 SCADA interface for the A microgrid simulator."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import socket
import sqlite3
import sys
import time
from typing import Any, Iterable, Mapping

from PyQt6.QtCore import QObject, QProcess, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import SimulationConfig, load_config
from ..repository import Repository
from ..scenario import ScenarioCurve, ScenarioPoint, load_scenario_csv, save_scenario_csv
from .curve_widget import CurveEditor, TimeSeriesChart, format_beijing_time, format_sim_time


ROOT_DIR = Path(__file__).resolve().parents[2]
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
HISTORY_TABLE_ROW_LIMIT = 1000

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
    "load": "#d78f19",
    "avail": "#1fa15a",
    "target": "#d5a31a",
    "actual": "#6b4fd8",
    "diesel": "#00a1a7",
    "imbalance": "#d64545",
}


_QSS = f"""
QMainWindow {{ background: {COLORS['bg']}; }}
QWidget {{ font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI"; color: {COLORS['text']}; }}
QToolTip {{ background: #ffffff; color: #274056; border: 1px solid #cad8e5; }}

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
QLabel[role="bigValue"] {{ color: #123a5f; font-size: 28px; font-weight: 700; }}
QLabel[role="unit"] {{ color: #5c758c; font-size: 13px; font-weight: 600; }}
QLabel[role="source"] {{ color: {COLORS['text_muted']}; font-size: 11px; }}
QLabel[state="good"] {{ color: {COLORS['good']}; font-size: 15px; font-weight: 700; }}
QLabel[state="warn"] {{ color: {COLORS['warn']}; font-size: 15px; font-weight: 700; }}
QLabel[state="bad"] {{ color: {COLORS['bad']}; font-size: 15px; font-weight: 700; }}

QPushButton[role="primary"] {{ background: {COLORS['primary']}; color: #ffffff; border: none; border-radius: 6px; padding: 8px 18px; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #285fb8; }}
QPushButton[role="success"] {{ background: {COLORS['good']}; color: #ffffff; border: none; border-radius: 6px; padding: 8px 18px; font-weight: 600; }}
QPushButton[role="success"]:hover {{ background: #1a8a4d; }}
QPushButton[role="danger"] {{ background: {COLORS['bad']}; color: #ffffff; border: none; border-radius: 6px; padding: 8px 18px; font-weight: 600; }}
QPushButton[role="danger"]:hover {{ background: #bd3a3a; }}
QPushButton[role="secondary"] {{ background: #eef3f8; color: #33516d; border: 1px solid #d2dfea; border-radius: 6px; padding: 8px 18px; font-weight: 600; }}
QPushButton[role="secondary"]:hover {{ background: #e2ebf3; }}
QPushButton[role="segment"] {{ background: #eef3f8; color: #45607a; border: 1px solid #d2dfea; padding: 6px 14px; font-weight: 600; }}
QPushButton[role="segment"]:checked {{ background: {COLORS['primary']}; color: #ffffff; }}
QPushButton:disabled {{ background: #dfe6ed; color: #9aabba; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: #ffffff; border: 1px solid #cbd9e6; border-radius: 5px; padding: 6px 8px; selection-background-color: {COLORS['primary']}; }}
QLineEdit:read-only {{ background: #f4f7fa; color: #5c758c; }}
QComboBox:disabled, QSpinBox:disabled {{ background: #edf1f5; color: #8b9caf; }}

QSlider::groove:horizontal {{ height: 6px; background: #dce6f0; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {COLORS['primary']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; background: #ffffff; border: 2px solid {COLORS['primary']}; border-radius: 8px; }}

QTableWidget {{ background: #ffffff; border: 1px solid #d5e2ed; gridline-color: #e4ecf3; color: #2c4359; alternate-background-color: #f7fafd; }}
QTableWidget::item {{ padding: 5px; }}
QHeaderView::section {{ background: #edf3f9; color: #33516d; font-weight: 700; border: none; border-bottom: 1px solid #d5e2ed; padding: 7px; }}
QTableCornerButton::section {{ background: #edf3f9; border: none; }}
QScrollArea {{ border: none; background: {COLORS['bg']}; }}
QStatusBar {{ background: #e6edf5; color: #5e7891; border-top: 1px solid #d4e1ed; }}
"""


PROTOCOL_ROWS = (
    ("sampled_at_utc", "A 状态断面 UTC 授时", "UTC"),
    ("wind_speed_mps", "A CSV 风速输入", "m/s"),
    ("load_power_kw", "A CSV 负荷输入", "kW"),
    ("wind_available_kw", "C 计算、A 校验并转发", "kW"),
    ("wind_operating_limit_kw", "C 计算、A 校验并转发", "kW"),
    ("wind_target_kw", "B 调度目标", "kW"),
    ("wind_actual_kw", "A 物理模型实际出力", "kW"),
    ("diesel_target_kw", "B 调度目标", "kW"),
    ("diesel_actual_kw", "A 物理模型实际出力", "kW"),
    ("pitch_actual_deg", "C 动作经 A 仿真后的桨距", "deg"),
    ("wind_running", "A 仿真后的风机运行状态", "bool"),
    ("diesel_running", "A 仿真后的柴发运行状态", "bool"),
    ("fault", "A 状态断面故障标志", "bool"),
    ("power_imbalance_kw", "A: load - wind actual - diesel actual", "kW"),
)


HISTORY_FIELDS = (
    ("wind_speed_mps", "风速", "m/s", COLORS["wind"]),
    ("load_power_kw", "负荷", "kW", COLORS["load"]),
    ("wind_available_kw", "风电可用", "kW", COLORS["avail"]),
    ("wind_target_kw", "风电目标", "kW", COLORS["target"]),
    ("wind_actual_kw", "风电实际", "kW", COLORS["actual"]),
    ("diesel_target_kw", "柴发目标", "kW", COLORS["warn"]),
    ("diesel_actual_kw", "柴发实际", "kW", COLORS["diesel"]),
    ("power_imbalance_kw", "功率不平衡", "kW", COLORS["imbalance"]),
)


PARAMETER_NAMES = {
    "a_step_s": "A 仿真步长",
    "a_poll_s": "A 执行周期",
    "wind_ramp_up_kw_per_s": "风机升功率爬坡",
    "wind_ramp_down_kw_per_s": "风机降功率爬坡",
    "diesel_min_power_kw": "柴发最小功率",
    "diesel_max_power_kw": "柴发最大功率",
    "diesel_ramp_up_kw_per_s": "柴发升功率爬坡",
    "diesel_ramp_down_kw_per_s": "柴发降功率爬坡",
    "reserve_kw": "柴发备用容量",
    "b_poll_s": "B 状态采集周期",
    "b_dispatch_s": "B 调度周期",
    "wind_rated_power_kw": "风机额定功率",
    "cut_in_speed_mps": "切入风速",
    "rated_speed_mps": "额定风速",
    "cut_out_speed_mps": "切出风速",
    "pitch_full_output_deg": "满功率桨距角",
    "pitch_feather_deg": "完全顺桨角",
    "c_control_s": "C 控制周期",
    "c_timeout_s": "C 通信超时",
    "simulation.step_s": "仿真步长",
    "simulation.poll_interval_s": "执行周期",
    "WT01.rated_power_kw": "风机额定功率",
    "WT01.cut_in_speed_mps": "切入风速",
    "WT01.rated_speed_mps": "额定风速",
    "WT01.cut_out_speed_mps": "切出风速",
    "WT01.pitch_full_output_deg": "满功率桨距角",
    "WT01.pitch_feather_deg": "完全顺桨角",
    "WT01.ramp_up_kw_per_s": "风机升功率爬坡",
    "WT01.ramp_down_kw_per_s": "风机降功率爬坡",
    "DG01.min_power_kw": "柴发最小功率",
    "DG01.max_power_kw": "柴发最大功率",
    "DG01.ramp_up_kw_per_s": "柴发升功率爬坡",
    "DG01.ramp_down_kw_per_s": "柴发降功率爬坡",
    "B.wind_target_kw": "风电调度目标",
    "B.diesel_target_kw": "柴发调度目标",
    "B.wind_enable": "B 风机启用请求",
    "B.diesel_enable": "B 柴发启用请求",
    "C.wind_available_kw": "C 风电可用功率",
    "C.wind_operating_limit_kw": "C 风机运行上限",
    "C.pitch_target_deg": "C 目标桨距角",
    "C.wind_enable": "C 风机运行许可",
}


def _row_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    keys = getattr(value, "keys", None)
    if callable(keys):
        return {str(key): value[key] for key in keys()}
    result: dict[str, Any] = {}
    for name in (
        "key", "name", "parameter", "parameter_key", "device_id", "value", "unit",
        "owner", "source", "updated_at_utc", "editable", "old_value", "new_value",
        "changed_at_utc", "created_at_utc", "level", "event", "message", "step",
        "session_id", "sim_time_s", "sampled_at_utc", "wind_speed_mps",
        "load_power_kw", "wind_available_kw", "wind_operating_limit_kw",
        "wind_target_kw", "wind_actual_kw", "diesel_target_kw", "diesel_actual_kw",
        "pitch_actual_deg", "wind_running", "diesel_running", "fault",
        "power_imbalance_kw",
    ):
        if hasattr(value, name):
            result[name] = getattr(value, name)
    return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _beijing_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TIMEZONE)


def _display_timestamp(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return _beijing_datetime(parsed).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC+8"


def _local_ipv4_addresses() -> list[str]:
    values = {"127.0.0.1"}
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if address:
                values.add(address)
    except OSError:
        pass
    return sorted(values, key=lambda item: (item.startswith("127."), item))


class _HistoryLoadSignals(QObject):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class _HistoryLoadTask(QRunnable):
    """Read history through a dedicated SQLite connection outside the GUI thread."""

    def __init__(
        self,
        *,
        request_id: int,
        db_path: Path,
        limit: int,
        selected_session: str | None,
        log_level: str,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.db_path = db_path
        self.limit = limit
        self.selected_session = selected_session
        self.log_level = log_level
        self.signals = _HistoryLoadSignals()

    def run(self) -> None:
        try:
            repository = Repository(self.db_path)
            sessions = [
                str(_row_mapping(row).get("session_id", ""))
                for row in repository.history_sessions()
            ]
            sessions = list(dict.fromkeys(item for item in sessions if item))
            selected_session = self.selected_session
            if selected_session not in sessions:
                selected_session = sessions[-1] if sessions else None
            states = (
                repository.state_history(limit=self.limit, session_id=selected_session)
                if selected_session is not None
                else []
            )
            logs = repository.logs(limit=self.limit)
            if self.log_level != "全部":
                logs = [
                    row for row in logs
                    if str(_row_mapping(row).get("level", "")).upper() == self.log_level
                ]
            self.signals.loaded.emit(
                self.request_id,
                {
                    "sessions": sessions,
                    "selected_session": selected_session,
                    "states": [_row_mapping(row) for row in states],
                    "logs": [_row_mapping(row) for row in logs],
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            self.signals.failed.emit(self.request_id, str(error))


class SimulatorWindow(QMainWindow):
    def __init__(
        self,
        *,
        db_path: Path,
        config_path: Path,
        scenario_path: Path,
        bind_address: str | None = None,
        port: int | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path.resolve()
        self.config_path = config_path.resolve()
        self.config: SimulationConfig = load_config(self.config_path)
        self.repository = Repository(self.db_path)
        self.scenario_path = scenario_path.resolve()
        self._scenario_from_database = False
        self.scenario = self._initial_scenario(self.scenario_path)
        self._scenario_origin_utc = _utc_now()
        self._runner = QProcess(self)
        self._tcp_server = QProcess(self)
        self._history_pool = QThreadPool(self)
        self._history_pool.setMaxThreadCount(1)
        self._history_request_id = 0
        self._history_loading = False
        self._history_refresh_pending = False
        self._history_task: _HistoryLoadTask | None = None
        self._last_live_refresh = 0.0
        self._last_parameter_refresh = 0.0
        self._ui_events: list[dict[str, object]] = []
        self._parameter_editors: dict[str, QDoubleSpinBox] = {}
        self._parameter_originals: dict[str, float] = {}
        self._parameter_rows: list[dict[str, object]] = []
        self._database_health = "待检测" if self.db_path.is_file() else "未初始化"
        self._bind_address = bind_address or self.config.server_bind
        self._port = self.config.server_port if port is None else int(port)
        if not 1 <= self._port <= 65535:
            raise ValueError("port must be from 1 to 65535")

        self.setWindowTitle("南极孤立微电网模拟器　监控与控制系统")
        self.resize(1580, 940)
        self.setMinimumSize(1220, 760)
        self._build_ui()
        self.setStyleSheet(_QSS)
        self._connect_signals()
        self._set_scenario(self.scenario, None if self._scenario_from_database else self.scenario_path)
        self._start_timers()
        self.refresh_state()

    def _initial_scenario(self, requested_path: Path) -> ScenarioCurve:
        if self.db_path.is_file():
            try:
                scenario = self.repository.get_scenario()
                self._scenario_from_database = True
                return scenario
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
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
        self.pageStack.addWidget(self._build_monitor_page())
        self.pageStack.addWidget(self._build_parameters_page())
        self.pageStack.addWidget(self._build_history_page())
        self.pageStack.addWidget(self._build_alarm_page())
        body.addWidget(self.pageStack, 1)
        root.addLayout(body, 1)

        self.navGroup = QButtonGroup(self)
        self.navGroup.setExclusive(True)
        for index, button in enumerate(self.navButtons):
            self.navGroup.addButton(button, index)
            button.clicked.connect(lambda _checked, page=index: self._switch_page(page))
        self.navButtons[0].setChecked(True)
        self.statusBar().showMessage(
            f"系统就绪｜参数状态：{self.config.parameter_status}｜授时源：系统 UTC，界面显示 UTC+8"
        )

    def _build_header(self) -> QFrame:
        bar = self._frame("headerBar")
        bar.setFixedHeight(74)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        badge = self._frame("logoBadge")
        badge.setFixedSize(40, 40)
        badge_layout = QVBoxLayout(badge)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        glyph = QLabel("A")
        glyph.setObjectName("logoGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_layout.addWidget(glyph)
        layout.addWidget(badge)

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel("南极孤立微电网模拟器")
        title.setObjectName("appTitle")
        subtitle = QLabel("PC-A · 场景、实际出力仿真与通信服务")
        subtitle.setObjectName("appSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        layout.addLayout(title_block)
        layout.addStretch(1)

        self.clockLabel = QLabel()
        self.clockLabel.setObjectName("clockLabel")
        layout.addWidget(self.clockLabel)
        layout.addSpacing(4)
        self.dbDot, self.dbPill = self._status_pill(layout, "grid.db 未连接", COLORS["warn"])
        self.bDot, self.bPill = self._status_pill(layout, "B 离线", COLORS["bad"])
        self.cDot, self.cPill = self._status_pill(layout, "C 离线", COLORS["bad"])
        self.tcpDot, self.tcpPill = self._status_pill(layout, "TCP 停止", COLORS["bad"])
        return bar

    def _build_nav(self) -> QFrame:
        panel = self._frame("navPanel")
        panel.setFixedWidth(190)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(2)
        caption = QLabel("功能导航")
        caption.setProperty("role", "navCaption")
        layout.addWidget(caption)
        self.btnMonitor = self._nav_button("运行监控")
        self.btnParameters = self._nav_button("参数设置")
        self.btnHistory = self._nav_button("历史数据")
        self.btnProtocol = self._nav_button("报警与通信")
        # Compatibility alias: scenario editing now lives inside the monitor page.
        self.btnScenario = self.btnMonitor
        self.navButtons = [self.btnMonitor, self.btnParameters, self.btnHistory, self.btnProtocol]
        for button in self.navButtons:
            layout.addWidget(button)
        layout.addStretch(1)
        site = QLabel("PC-A 电网模拟器\n孤立微电网 / 工业监控")
        site.setProperty("role", "siteLabel")
        layout.addWidget(site)
        return panel

    def _scroll_page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("scrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(13)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page, layout

    def _build_monitor_page(self) -> QWidget:
        page, layout = self._scroll_page()
        layout.addWidget(self._section_title("电网模拟器运行监控"))
        layout.addWidget(self._build_communication_card())
        layout.addWidget(self._build_simulation_controls())

        kpi_grid = QGridLayout()
        kpi_grid.setHorizontalSpacing(12)
        kpi_grid.setVerticalSpacing(10)
        definitions = (
            ("wind_speed_mps", "风速", "m/s", "来源：A CSV 场景", COLORS["wind"]),
            ("load_power_kw", "负荷", "kW", "来源：A CSV 场景", COLORS["load"]),
            ("wind_available_kw", "风电可用", "kW", "来源：C 计算，经 A 转发", COLORS["avail"]),
            ("wind_target_kw", "风电目标", "kW", "来源：B EMS", COLORS["target"]),
            ("wind_actual_kw", "风电实际", "kW", "来源：A 物理模型", COLORS["actual"]),
            ("diesel_target_kw", "柴发目标", "kW", "来源：B EMS", COLORS["warn"]),
            ("diesel_actual_kw", "柴发实际", "kW", "来源：A 物理模型", COLORS["diesel"]),
            ("power_imbalance_kw", "功率不平衡", "kW", "A: 负荷 - 风实际 - 柴实际", COLORS["imbalance"]),
        )
        self.kpi_labels: dict[str, QLabel] = {}
        for index, (key, title, unit, source, accent) in enumerate(definitions):
            card, value = self._kpi_card(title, unit, source, accent)
            self.kpi_labels[key] = value
            kpi_grid.addWidget(card, index // 4, index % 4)
        self.capability_labels = {
            key: self.kpi_labels[key]
            for key in (
                "wind_available_kw", "wind_operating_limit_kw",
                "wind_target_kw", "power_imbalance_kw"
            )
            if key in self.kpi_labels
        }
        layout.addLayout(kpi_grid)

        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.monitor_status: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title, detail in (
            ("simulation", "仿真状态", "A 计算进程"),
            ("wind", "风机状态", "实际运行遥信"),
            ("B", "B EMS 通信", "A TCP 会话"),
            ("C", "C STM32 通信", "A TCP 会话"),
        ):
            card, status, detail_label = self._status_card(title, "●  未知", "warn", detail)
            self.monitor_status[key] = (status, detail_label)
            status_row.addWidget(card, 1)
        layout.addLayout(status_row)

        chart_header = QHBoxLayout()
        chart_title = QLabel("曲线视图")
        chart_title.setProperty("role", "cardTitle")
        chart_header.addWidget(chart_title)
        chart_header.addStretch(1)
        self.btnRealtimeCharts = self._button("实时曲线", "segment")
        self.btnScenarioCharts = self._button("场景编辑", "segment")
        for button in (self.btnRealtimeCharts, self.btnScenarioCharts):
            button.setCheckable(True)
            chart_header.addWidget(button)
        self.btnRealtimeCharts.setChecked(True)
        self.chartModeGroup = QButtonGroup(self)
        self.chartModeGroup.setExclusive(True)
        self.chartModeGroup.addButton(self.btnRealtimeCharts, 0)
        self.chartModeGroup.addButton(self.btnScenarioCharts, 1)
        layout.addLayout(chart_header)

        self.chartStack = QStackedWidget()
        self.chartStack.setMinimumHeight(300)
        realtime = QWidget()
        realtime_layout = QHBoxLayout(realtime)
        realtime_layout.setContentsMargins(0, 0, 0, 0)
        realtime_layout.setSpacing(12)
        self.windTrend = TimeSeriesChart(
            title="实时风速曲线", unit="m/s", colors={"风速": COLORS["wind"]}
        )
        self.powerTrend = TimeSeriesChart(
            title="实时功率曲线",
            unit="kW",
            colors={
                "负荷": COLORS["load"], "可用": COLORS["avail"],
                "风目标": COLORS["target"], "风实际": COLORS["actual"],
                "柴目标": COLORS["warn"], "柴实际": COLORS["diesel"],
            },
        )
        realtime_layout.addWidget(self._chart_card(self.windTrend), 1)
        realtime_layout.addWidget(self._chart_card(self.powerTrend), 1)
        self.chartStack.addWidget(realtime)

        scenario_page = QWidget()
        scenario_layout = QVBoxLayout(scenario_page)
        scenario_layout.setContentsMargins(0, 0, 0, 0)
        scenario_layout.setSpacing(8)
        times = [point.sim_time_s for point in self.scenario.points]
        wind_values = [point.wind_speed_mps for point in self.scenario.points]
        load_values = [point.load_power_kw for point in self.scenario.points]
        editor_row = QHBoxLayout()
        editor_row.setSpacing(12)
        self.wind_editor = CurveEditor(
            key="wind_speed_mps", name="风速场景输入", unit="m/s", color=COLORS["wind"],
            times_s=times, values=wind_values,
            maximum=max(self.config.wind.cut_out_speed_mps, max(wind_values) * 1.05, 1.0),
        )
        self.load_editor = CurveEditor(
            key="load_power_kw", name="负荷场景输入", unit="kW", color=COLORS["load"],
            times_s=times, values=load_values, maximum=max(max(load_values) * 1.1, 1.0),
        )
        editor_row.addWidget(self._chart_card(self.wind_editor), 1)
        editor_row.addWidget(self._chart_card(self.load_editor), 1)
        scenario_layout.addLayout(editor_row)
        preview_card = self._frame()
        preview_card.setProperty("role", "card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(14, 8, 14, 8)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview = QLabel()
        self.preview.setProperty("role", "cardTitle")
        preview_layout.addWidget(self.time_slider)
        preview_layout.addWidget(self.preview)
        scenario_layout.addWidget(preview_card)
        self.chartStack.addWidget(scenario_page)
        layout.addWidget(self.chartStack)
        layout.addStretch(1)
        return page

    def _build_communication_card(self) -> QFrame:
        card = self._frame()
        card.setProperty("role", "card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(9)

        local_addresses = _local_ipv4_addresses()
        grid.addWidget(QLabel("本机 IPv4"), 0, 0)
        self.localIpEdit = QLineEdit(" / ".join(local_addresses))
        self.localIpEdit.setReadOnly(True)
        self.localIpEdit.setToolTip("供队友选择同一局域网可达地址；127.0.0.1 仅限本机")
        grid.addWidget(self.localIpEdit, 0, 1, 1, 2)
        grid.addWidget(QLabel("监听地址"), 0, 3)
        self.bindCombo = QComboBox()
        self.bindCombo.setEditable(True)
        bind_values = [self._bind_address, "0.0.0.0", "127.0.0.1", *local_addresses]
        for value in dict.fromkeys(item for item in bind_values if item):
            self.bindCombo.addItem(value)
        self.bindCombo.setCurrentText(self._bind_address)
        grid.addWidget(self.bindCombo, 0, 4)
        grid.addWidget(QLabel("监听端口"), 0, 5)
        self.portSpin = QSpinBox()
        self.portSpin.setRange(1, 65535)
        self.portSpin.setValue(self._port)
        grid.addWidget(self.portSpin, 0, 6)
        self.tcp_button = self._button("启动 TCP 服务", "primary")
        grid.addWidget(self.tcp_button, 0, 7)

        self.open_button = self._button("加载 CSV", "secondary")
        self.save_button = self._button("另存 CSV", "secondary")
        self.initialize_button = self._button("初始化 grid.db", "success")
        grid.addWidget(self.open_button, 1, 0)
        grid.addWidget(self.save_button, 1, 1)
        grid.addWidget(self.initialize_button, 1, 2)
        self.source_label = QLabel()
        self.source_label.setProperty("role", "source")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.source_label, 1, 3, 1, 3)
        self.dbPathLabel = QLabel(f"数据库：{self.db_path}")
        self.dbPathLabel.setProperty("role", "source")
        self.dbPathLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.dbPathLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.dbPathLabel, 1, 6, 1, 2)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(4, 1)
        return card

    def _build_simulation_controls(self) -> QFrame:
        card = self._frame()
        card.setProperty("role", "card")
        controls = QHBoxLayout(card)
        controls.setContentsMargins(16, 10, 16, 10)
        controls.setSpacing(8)
        controls.addWidget(QLabel("仿真控制"))
        self.start_button = self._button("▶  启动", "success")
        self.pause_button = self._button("Ⅱ  暂停", "secondary")
        self.resume_button = self._button("▶  继续", "primary")
        self.stop_button = self._button("■  停止", "danger")
        self.new_session_button = self._button("↻  新建会话", "secondary")
        for button in (
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.new_session_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        self.runtime_detail = QLabel("数据库未初始化")
        self.runtime_detail.setProperty("role", "source")
        self.runtime_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.runtime_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        controls.addWidget(self.runtime_detail)
        return card

    def _build_parameters_page(self) -> QWidget:
        page, layout = self._scroll_page()
        title_row = QHBoxLayout()
        title_row.addWidget(self._section_title("参数设置与 A/B/C 同步"))
        title_row.addStretch(1)
        self.parameterSyncLabel = QLabel("等待 grid.db 参数快照")
        self.parameterSyncLabel.setProperty("role", "source")
        title_row.addWidget(self.parameterSyncLabel)
        self.refreshParametersButton = self._button("刷新", "secondary")
        self.saveParametersButton = self._button("保存 A 参数", "primary")
        title_row.addWidget(self.refreshParametersButton)
        title_row.addWidget(self.saveParametersButton)
        layout.addLayout(title_row)

        note = self._frame()
        note.setProperty("role", "card")
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(15, 10, 15, 10)
        note_title = QLabel("参数写权限")
        note_title.setProperty("role", "cardTitle")
        note_text = QLabel(
            "仅 owner=A 的行可在此修改；B 的调度参数和 C 的风机参数由 TCP 同步后只读展示。"
            "来源与更新时间用于辨认最近一次有效更新，A 不会覆盖 B/C 的控制权。"
        )
        note_text.setWordWrap(True)
        note_text.setProperty("role", "source")
        note_layout.addWidget(note_title)
        note_layout.addWidget(note_text)
        layout.addWidget(note)

        self.parameterTable = QTableWidget(0, 7)
        self.parameterTable.setHorizontalHeaderLabels(
            ("参数", "键", "Owner", "值", "单位", "来源", "更新时间 UTC+8")
        )
        self.parameterTable.setAlternatingRowColors(True)
        self.parameterTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.parameterTable.verticalHeader().setVisible(False)
        self.parameterTable.verticalHeader().setMinimumSectionSize(40)
        self.parameterTable.verticalHeader().setDefaultSectionSize(40)
        parameter_header = self.parameterTable.horizontalHeader()
        parameter_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        parameter_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        parameter_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        parameter_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        parameter_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        parameter_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        parameter_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.parameterTable.setMinimumHeight(390)
        layout.addWidget(self.parameterTable)

        history_title = QLabel("最近参数变更")
        history_title.setProperty("role", "cardTitle")
        layout.addWidget(history_title)
        self.parameterHistoryTable = QTableWidget(0, 7)
        self.parameterHistoryTable.setHorizontalHeaderLabels(
            ("时间 UTC+8", "参数", "Owner", "原值", "新值", "来源", "说明")
        )
        self.parameterHistoryTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.parameterHistoryTable.setAlternatingRowColors(True)
        self.parameterHistoryTable.verticalHeader().setVisible(False)
        self.parameterHistoryTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.parameterHistoryTable.setMinimumHeight(180)
        layout.addWidget(self.parameterHistoryTable)
        layout.addStretch(1)
        return page

    def _build_history_page(self) -> QWidget:
        page, layout = self._scroll_page()
        control_row = QHBoxLayout()
        control_row.addWidget(self._section_title("历史数据可视化"))
        control_row.addStretch(1)
        control_row.addWidget(QLabel("会话"))
        self.historySessionCombo = QComboBox()
        self.historySessionCombo.setMinimumWidth(190)
        control_row.addWidget(self.historySessionCombo)
        control_row.addWidget(QLabel("绘制字段"))
        self.historyMetricCombo = QComboBox()
        for key, label, unit, color in HISTORY_FIELDS:
            self.historyMetricCombo.addItem(label, (key, unit, color))
        control_row.addWidget(self.historyMetricCombo)
        control_row.addWidget(QLabel("记录数"))
        self.historyLimitSpin = QSpinBox()
        self.historyLimitSpin.setRange(10, 5000)
        self.historyLimitSpin.setValue(300)
        self.historyLimitSpin.setSingleStep(50)
        control_row.addWidget(self.historyLimitSpin)
        self.refreshHistoryButton = self._button("刷新历史", "primary")
        control_row.addWidget(self.refreshHistoryButton)
        layout.addLayout(control_row)

        self.historyTrend = TimeSeriesChart(
            title="历史状态曲线", unit="kW", colors={"历史": COLORS["primary"]}
        )
        history_chart_card = self._chart_card(self.historyTrend)
        history_chart_card.setMinimumHeight(270)
        layout.addWidget(history_chart_card)

        self.historyTable = QTableWidget(0, 11)
        self.historyTable.setHorizontalHeaderLabels(
            ("采样时间 UTC+8", "step", "风速", "负荷", "可用", "风目标", "风实际", "柴目标", "柴实际", "不平衡", "session")
        )
        self.historyTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.historyTable.setAlternatingRowColors(True)
        self.historyTable.verticalHeader().setVisible(False)
        self.historyTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.historyTable.horizontalHeader().setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        self.historyTable.setColumnWidth(0, 205)
        self.historyTable.setMinimumHeight(270)
        layout.addWidget(self.historyTable)

        log_row = QHBoxLayout()
        log_title = QLabel("运行日志文本")
        log_title.setProperty("role", "cardTitle")
        log_row.addWidget(log_title)
        log_row.addStretch(1)
        log_row.addWidget(QLabel("级别"))
        self.logLevelCombo = QComboBox()
        self.logLevelCombo.addItems(("全部", "INFO", "WARNING", "ERROR"))
        log_row.addWidget(self.logLevelCombo)
        layout.addLayout(log_row)
        self.logTable = QTableWidget(0, 5)
        self.logTable.setHorizontalHeaderLabels(("时间 UTC+8", "级别", "事件", "step", "消息"))
        self.logTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.logTable.setAlternatingRowColors(True)
        self.logTable.verticalHeader().setVisible(False)
        self.logTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.logTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.logTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.logTable.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.logTable.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.logTable.setColumnWidth(0, 205)
        self.logTable.setMinimumHeight(220)
        layout.addWidget(self.logTable)
        layout.addStretch(1)
        return page

    def _build_alarm_page(self) -> QWidget:
        page, layout = self._scroll_page()
        layout.addWidget(self._section_title("报警与通信"))

        summary = self._frame()
        summary.setProperty("role", "card")
        summary_layout = QGridLayout(summary)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        self.communicationLabels: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            (("local", "本机 IPv4"), ("endpoint", "监听端点"), ("database", "数据库"), ("utc", "授时"))
        ):
            summary_layout.addWidget(QLabel(title), 0, column)
            value = QLabel("-")
            value.setProperty("role", "cardTitle")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            summary_layout.addWidget(value, 1, column)
            self.communicationLabels[key] = value
        layout.addWidget(summary)

        self.envelope_label = QLabel("session_id=-  step=-  sim_time_s=-  sampled_at_utc=-")
        self.envelope_label.setProperty("role", "source")
        self.envelope_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.envelope_label)

        self.protocol_table = QTableWidget(len(PROTOCOL_ROWS), 4)
        self.protocol_table.setHorizontalHeaderLabels(("字段", "当前值", "单位", "Owner / 语义"))
        self.protocol_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.protocol_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.protocol_table.setAlternatingRowColors(True)
        self.protocol_table.verticalHeader().setVisible(False)
        header = self.protocol_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.protocol_value_items: dict[str, QTableWidgetItem] = {}
        for row, (key, meaning, unit) in enumerate(PROTOCOL_ROWS):
            for column, text in enumerate((key, "-", unit, meaning)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                self.protocol_table.setItem(row, column, item)
                if column == 1:
                    self.protocol_value_items[key] = item
        self.protocol_table.setMinimumHeight(390)
        layout.addWidget(self.protocol_table)

        alert_row = QHBoxLayout()
        alert_title = QLabel("近期告警与通信事件")
        alert_title.setProperty("role", "cardTitle")
        alert_row.addWidget(alert_title)
        alert_row.addStretch(1)
        self.clearUiEventsButton = self._button("清除界面事件", "secondary")
        alert_row.addWidget(self.clearUiEventsButton)
        layout.addLayout(alert_row)
        self.alertTable = QTableWidget(0, 4)
        self.alertTable.setHorizontalHeaderLabels(("时间 UTC+8", "级别", "来源", "内容"))
        self.alertTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alertTable.setAlternatingRowColors(True)
        self.alertTable.verticalHeader().setVisible(False)
        self.alertTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.alertTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.alertTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.alertTable.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.alertTable.setMinimumHeight(210)
        layout.addWidget(self.alertTable)
        layout.addStretch(1)
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

    def _status_pill(self, parent_layout: QHBoxLayout, text: str, color: str) -> tuple[QFrame, QLabel]:
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

    def _chart_card(self, chart: QWidget) -> QFrame:
        card = self._frame()
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.addWidget(chart)
        return card

    def _kpi_card(self, title: str, unit: str, source: str, accent: str) -> tuple[QFrame, QLabel]:
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(104)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(2)
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

    def _status_card(self, title: str, status_text: str, state: str, detail: str) -> tuple[QFrame, QLabel, QLabel]:
        card = self._frame()
        card.setProperty("role", "card")
        card.setMinimumHeight(92)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setProperty("role", "cardTitle")
        status = QLabel(status_text)
        status.setProperty("state", state)
        detail_label = QLabel(detail)
        detail_label.setProperty("role", "source")
        detail_label.setWordWrap(True)
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
        self.new_session_button.clicked.connect(self.create_new_session)
        self.tcp_button.clicked.connect(self.toggle_tcp_server)
        self.chartModeGroup.idClicked.connect(self.chartStack.setCurrentIndex)
        self.refreshParametersButton.clicked.connect(lambda: self.refresh_parameters(force=True))
        self.saveParametersButton.clicked.connect(self.save_a_parameters)
        self.refreshHistoryButton.clicked.connect(self.refresh_history)
        self.historySessionCombo.currentIndexChanged.connect(self._schedule_history_refresh)
        self.historyMetricCombo.currentIndexChanged.connect(self._schedule_history_refresh)
        self.historyLimitSpin.valueChanged.connect(self._schedule_history_refresh)
        self.logLevelCombo.currentIndexChanged.connect(self._schedule_history_refresh)
        self.clearUiEventsButton.clicked.connect(self._clear_ui_events)

        self._tcp_server.stateChanged.connect(self._tcp_state_changed)
        self._tcp_server.readyReadStandardOutput.connect(lambda: self._read_process_output(self._tcp_server, "TCP", False))
        self._tcp_server.readyReadStandardError.connect(lambda: self._read_process_output(self._tcp_server, "TCP", True))
        self._tcp_server.errorOccurred.connect(lambda error: self._process_error("TCP", error))
        self._tcp_server.finished.connect(lambda code, _status: self._process_finished("TCP", code))
        self._runner.readyReadStandardOutput.connect(lambda: self._read_process_output(self._runner, "仿真", False, discard=True))
        self._runner.readyReadStandardError.connect(lambda: self._read_process_output(self._runner, "仿真", True))
        self._runner.errorOccurred.connect(lambda error: self._process_error("仿真", error))
        self._runner.finished.connect(lambda code, _status: self._runner_finished(code))

    def _start_timers(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self.refresh_state)
        self._refresh_timer.start()
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._history_debounce_timer = QTimer(self)
        self._history_debounce_timer.setSingleShot(True)
        self._history_debounce_timer.setInterval(250)
        self._history_debounce_timer.timeout.connect(self.refresh_history)
        self._update_clock()

    def _switch_page(self, index: int) -> None:
        self.pageStack.setCurrentIndex(index)
        if index == 1:
            self.refresh_parameters(force=True)
        elif index == 2:
            self.refresh_history()
        elif index == 3:
            self._refresh_alerts()

    def _update_clock(self) -> None:
        now = _beijing_datetime(_utc_now())
        weekday = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[now.weekday()]
        self.clockLabel.setText(now.strftime("%Y-%m-%d %H:%M:%S") + f" UTC+8 北京时间  {weekday}")
        self.communicationLabels["utc"].setText(now.strftime("%H:%M:%S UTC+8"))

    def _scenario_labels(self, times: Iterable[float]) -> list[str]:
        return [
            _beijing_datetime(
                self._scenario_origin_utc + timedelta(seconds=float(value))
            ).strftime("%H:%M:%S")
            for value in times
        ]

    def _set_scenario(self, scenario: ScenarioCurve, source_path: Path | None) -> None:
        self.scenario = scenario
        self._scenario_origin_utc = _utc_now()
        if source_path is not None:
            self.scenario_path = source_path.resolve()
        times = [point.sim_time_s for point in scenario.points]
        wind = [point.wind_speed_mps for point in scenario.points]
        load = [point.load_power_kw for point in scenario.points]
        self.wind_editor.set_data(times, wind, maximum=max(self.config.wind.cut_out_speed_mps, max(wind) * 1.05, 1.0))
        self.load_editor.set_data(times, load, maximum=max(max(load) * 1.1, 1.0))
        utc_labels = self._scenario_labels(times)
        self.wind_editor.set_time_labels(utc_labels)
        self.load_editor.set_time_labels(utc_labels)
        self.time_slider.setRange(0, len(times) - 1)
        self.time_slider.setValue(0)
        if source_path:
            self.source_label.setText(f"场景：{self.scenario_path.name}")
            self.source_label.setToolTip(str(self.scenario_path))
        else:
            self.source_label.setText("场景：当前 grid.db 快照")
            self.source_label.setToolTip("场景点来自当前 grid.db")
        self.update_preview()

    def _scenario_from_editors(self) -> ScenarioCurve:
        return ScenarioCurve(tuple(
            ScenarioPoint(seconds, wind, load)
            for seconds, wind, load in zip(
                self.wind_editor.times_s(), self.wind_editor.values(), self.load_editor.values()
            )
        ))

    def open_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载 A 场景 CSV", str(self.scenario_path.parent), "CSV (*.csv)")
        if not path:
            return
        try:
            scenario = load_scenario_csv(path)
            if scenario.points[0].sim_time_s > self.config.start_s or scenario.points[-1].sim_time_s < self.config.end_s:
                raise ValueError("CSV 时间轴不能覆盖配置中的仿真起止时刻")
            self._set_scenario(scenario, Path(path))
            self.chartStack.setCurrentIndex(1)
            self.btnScenarioCharts.setChecked(True)
            self.statusBar().showMessage("CSV 已加载｜需初始化新数据库后才用于仿真", 6000)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "CSV 加载失败", str(error))

    def save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "另存 A 场景 CSV", str(self.scenario_path), "CSV (*.csv)")
        if not path:
            return
        try:
            save_scenario_csv(path, self._scenario_from_editors())
            self.scenario_path = Path(path).resolve()
            self.source_label.setText(f"场景：{self.scenario_path.name}")
            self.source_label.setToolTip(str(self.scenario_path))
            self.statusBar().showMessage("CSV 已保存", 4000)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "CSV 保存失败", str(error))

    def initialize_database(self) -> None:
        try:
            self.repository.initialize(self.config, self._scenario_from_editors())
            self._append_ui_event("INFO", "数据库", f"初始化完成：{self.db_path}")
            self.statusBar().showMessage("grid.db 初始化完成", 4000)
            self.refresh_state()
        except (FileExistsError, OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.critical(self, "初始化失败", f"{error}\n\n为保护已有运行数据，本界面不会覆盖现有数据库。")

    def update_preview(self, *_unused) -> None:
        times = self.wind_editor.times_s()
        index = min(self.time_slider.value(), len(times) - 1)
        seconds = times[index]
        self.wind_editor.set_playhead(seconds)
        self.load_editor.set_playhead(seconds)
        utc_value = self._scenario_origin_utc + timedelta(seconds=seconds)
        self.preview.setText(
            f"北京时间 {_beijing_datetime(utc_value).isoformat(timespec='milliseconds')}　内部 sim_time_s={seconds:.3f}　"
            f"风速 {self.wind_editor.value_at(seconds):.3f} m/s　负荷 {self.load_editor.value_at(seconds):.3f} kW"
        )

    def _ensure_runner(self) -> None:
        if self._runner.state() != QProcess.ProcessState.NotRunning:
            return
        self._runner.setWorkingDirectory(str(ROOT_DIR))
        self._runner.start(sys.executable, ["-m", "A_simulator", "run", "--db", str(self.db_path)])

    def control_simulation(self, action: str) -> None:
        try:
            status = self.repository.set_status(action)
            if action in {"start", "resume"}:
                self._ensure_runner()
            self._append_ui_event("INFO", "仿真", f"控制动作 {action} -> {status}")
            self.statusBar().showMessage(f"仿真状态：{status}", 3000)
            self.refresh_state()
        except (FileNotFoundError, RuntimeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "仿真控制失败", str(error))

    def create_new_session(self) -> None:
        try:
            session_id = self.repository.create_session()
            self._set_scenario(self.repository.get_scenario(), None)
            self._append_ui_event(
                "INFO", "仿真", f"已创建安全的新会话：{session_id}"
            )
            self.statusBar().showMessage(
                "新会话已就绪；历史记录保留，控制目标已安全复位", 6000
            )
            self.refresh_state()
        except (FileNotFoundError, RuntimeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "新建会话失败", str(error))

    def toggle_tcp_server(self) -> None:
        if self._tcp_server.state() != QProcess.ProcessState.NotRunning:
            self._append_ui_event("INFO", "TCP", "正在停止服务")
            self._tcp_server.terminate()
            # Windows console processes do not always react to terminate().  Keep
            # the GUI responsive and escalate to kill only if it is still alive.
            QTimer.singleShot(1200, self._force_stop_tcp)
            return
        if not self.db_path.is_file():
            QMessageBox.warning(self, "TCP 服务", "请先初始化 grid.db")
            return
        try:
            self.repository.runtime()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self._database_health = "异常"
            self._update_communication_summary()
            QMessageBox.warning(self, "TCP 服务", f"grid.db 无法使用：{error}")
            return
        self._database_health = "正常"
        bind = self.bindCombo.currentText().strip()
        if not bind:
            QMessageBox.warning(self, "TCP 服务", "监听地址不能为空")
            return
        port = self.portSpin.value()
        self._tcp_server.setWorkingDirectory(str(ROOT_DIR))
        self._tcp_server.start(
            sys.executable,
            [
                "-m", "A_simulator", "serve", "--db", str(self.db_path),
                "--config", str(self.config_path), "--bind", bind, "--port", str(port),
            ],
        )
        self._append_ui_event("INFO", "TCP", f"启动监听 {bind}:{port}")

    def _force_stop_tcp(self) -> None:
        if self._tcp_server.state() != QProcess.ProcessState.NotRunning:
            self._append_ui_event("WARNING", "TCP", "服务未响应停止请求，已强制结束子进程")
            self._tcp_server.kill()

    def _tcp_state_changed(self, state: QProcess.ProcessState) -> None:
        running = state != QProcess.ProcessState.NotRunning
        self.tcp_button.setText("停止 TCP 服务" if running else "启动 TCP 服务")
        self._set_button_role(self.tcp_button, "danger" if running else "primary")
        self.bindCombo.setEnabled(not running)
        self.portSpin.setEnabled(not running)
        self._set_pill(
            self.tcpDot, self.tcpPill,
            "TCP 运行" if running else "TCP 停止",
            COLORS["good"] if running else COLORS["bad"],
        )
        self._update_communication_summary()

    def _read_process_output(self, process: QProcess, source: str, stderr: bool, discard: bool = False) -> None:
        raw = process.readAllStandardError() if stderr else process.readAllStandardOutput()
        text = bytes(raw).decode("utf-8", "replace").strip()
        if not text or discard:
            return
        level = "ERROR" if stderr else "INFO"
        message = text[-500:].replace("\x00", "")
        self._append_ui_event(level, source, message)
        self.statusBar().showMessage(f"{source}：{message[-180:]}", 8000)

    def _process_error(self, source: str, error: QProcess.ProcessError) -> None:
        self._append_ui_event("ERROR", source, f"进程错误：{error.name}")

    def _process_finished(self, source: str, code: int) -> None:
        if source == "TCP" and self.db_path.is_file():
            try:
                self.repository.reset_connections("TCP child process stopped")
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                pass
        level = "INFO" if code == 0 else "ERROR"
        self._append_ui_event(level, source, f"进程结束，exit_code={code}")

    def _runner_finished(self, code: int) -> None:
        recovered = False
        if self.db_path.is_file():
            try:
                if self.repository.runtime()["status"] == "running":
                    self.repository.set_status("pause")
                    recovered = True
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                pass
        if recovered:
            self._append_ui_event(
                "WARNING",
                "仿真",
                f"计算子进程已退出（exit_code={code}），数据库状态已自动转为 paused",
            )
        else:
            self._process_finished("仿真", code)
        self.refresh_state()

    def _append_ui_event(self, level: str, source: str, message: str) -> None:
        self._ui_events.append({
            "created_at_utc": _rfc3339(_utc_now()), "level": level,
            "source": source, "message": message,
        })
        self._ui_events = self._ui_events[-200:]
        self._refresh_alerts()

    def _clear_ui_events(self) -> None:
        self._ui_events.clear()
        self._refresh_alerts()

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

    def refresh_state(self) -> None:
        self._update_communication_summary()
        if not self.db_path.is_file():
            self._database_health = "未初始化"
            self._update_communication_summary()
            self.runtime_detail.setText("数据库未初始化")
            self._set_pill(self.dbDot, self.dbPill, "grid.db 未连接", COLORS["warn"])
            self._set_control_buttons(None)
            self.initialize_button.setEnabled(True)
            self.refresh_parameters()
            return
        self.initialize_button.setEnabled(False)
        try:
            runtime = self.repository.runtime()
            state = self.repository.get_state()
            connections = self.repository.connection_statuses()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self._database_health = "异常"
            self._update_communication_summary()
            self.runtime_detail.setText(f"数据库读取失败：{error}")
            self._set_pill(self.dbDot, self.dbPill, "grid.db 异常", COLORS["bad"])
            self._set_control_buttons(None)
            return

        status = str(runtime["status"])
        self._database_health = "正常"
        self._update_communication_summary()
        self._set_pill(self.dbDot, self.dbPill, "grid.db 正常", COLORS["good"])
        self.runtime_detail.setText(
            f"step={state.step}　sim={format_sim_time(state.sim_time_s)}　"
            f"北京时间={_display_timestamp(state.sampled_at_utc)}　参数={runtime['parameter_status']}"
        )
        self.envelope_label.setText(
            f"session_id={state.session_id}　step={state.step}　sim_time_s={state.sim_time_s:.3f}　sampled_at_utc={state.sampled_at_utc}"
        )

        for key, label in self.kpi_labels.items():
            label.setText(f"{float(getattr(state, key)):.1f}")

        state_names = {"ready": "就绪", "running": "运行", "paused": "暂停", "stopped": "停止", "completed": "完成"}
        sim_state = "good" if status == "running" else "warn"
        if status == "stopped":
            sim_state = "bad"
        self._update_status_card(
            "simulation", sim_state, f"●  {state_names.get(status, status)}",
            f"step={state.step}　采样 {format_beijing_time(state.sampled_at_utc)} UTC+8",
        )
        self._update_status_card(
            "wind", "good" if state.wind_running else "bad",
            "●  运行" if state.wind_running else "●  停止",
            f"桨距 {state.pitch_actual_deg:.1f} deg　故障={'是' if state.fault else '否'}",
        )
        for peer in ("B", "C"):
            item = connections.get(peer, {"connected": False, "detail": "not connected"})
            connected = bool(item.get("connected"))
            detail = str(item.get("detail", ""))
            seen = item.get("last_seen_at_utc")
            if seen:
                detail = f"{detail}　最近 {format_beijing_time(str(seen))} UTC+8"
            self._update_status_card(peer, "good" if connected else "bad", "●  在线" if connected else "●  离线", detail)
            dot, pill = (self.bDot, self.bPill) if peer == "B" else (self.cDot, self.cPill)
            self._set_pill(dot, pill, f"{peer} {'在线' if connected else '离线'}", COLORS["good"] if connected else COLORS["bad"])

        payload = state.protocol_payload()
        for key, item in self.protocol_value_items.items():
            value = payload.get(key, "-")
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (int, float)):
                text = f"{float(value):.3f}"
            else:
                text = str(value)
            item.setText(text)

        if not self.time_slider.isSliderDown():
            times = self.wind_editor.times_s()
            index = min(range(len(times)), key=lambda candidate: abs(times[candidate] - state.sim_time_s))
            self.time_slider.setValue(index)
        self._set_control_buttons(status)

        now = time.monotonic()
        if now - self._last_live_refresh >= 0.9:
            self._last_live_refresh = now
            self._refresh_live_charts(state)
        if now - self._last_parameter_refresh >= 1.5:
            self.refresh_parameters()

    def _state_history(
        self,
        limit: int,
        fallback: object | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        method = getattr(self.repository, "state_history", None)
        rows: Iterable[object]
        if callable(method):
            try:
                rows = method(limit=limit, session_id=session_id)
            except TypeError:
                try:
                    rows = method(limit=limit)
                except TypeError:
                    rows = method(limit)
        else:
            rows = [] if fallback is None else [fallback]
        result = [_row_mapping(row) for row in rows]
        if session_id is not None:
            result = [
                row for row in result
                if str(row.get("session_id", "")) == session_id
            ]
        if not result and fallback is not None:
            fallback_row = _row_mapping(fallback)
            if session_id is None or str(fallback_row.get("session_id", "")) == session_id:
                result = [fallback_row]
        return result[-limit:]

    def _history_sessions(self) -> list[str]:
        method = getattr(self.repository, "history_sessions", None)
        if callable(method):
            raw = method()
            sessions = [
                str(_row_mapping(row).get("session_id", "")) for row in raw
            ]
            return list(dict.fromkeys(session for session in sessions if session))
        return list(dict.fromkeys(
            str(row.get("session_id", ""))
            for row in self._state_history(self.historyLimitSpin.maximum())
            if row.get("session_id")
        ))

    def _refresh_live_charts(self, current_state: object) -> None:
        try:
            current = _row_mapping(current_state)
            current_session = str(current.get("session_id", ""))
            rows = self._state_history(
                120, current_state, session_id=current_session
            )
            timestamps = [str(row.get("sampled_at_utc", "")) for row in rows]
            self.windTrend.set_series(timestamps, {"风速": [float(row.get("wind_speed_mps", 0.0)) for row in rows]})
            self.powerTrend.set_series(
                timestamps,
                {
                    "负荷": [float(row.get("load_power_kw", 0.0)) for row in rows],
                    "可用": [float(row.get("wind_available_kw", 0.0)) for row in rows],
                    "风目标": [float(row.get("wind_target_kw", 0.0)) for row in rows],
                    "风实际": [float(row.get("wind_actual_kw", 0.0)) for row in rows],
                    "柴目标": [float(row.get("diesel_target_kw", 0.0)) for row in rows],
                    "柴实际": [float(row.get("diesel_actual_kw", 0.0)) for row in rows],
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, sqlite3.Error) as error:
            self.statusBar().showMessage(f"实时历史读取失败：{error}", 5000)

    def _fallback_parameter_rows(self) -> list[dict[str, object]]:
        now = _rfc3339(_utc_now())
        rows: list[dict[str, object]] = [
            {"key": "a_step_s", "value": self.config.step_s, "unit": "s", "owner": "A", "source": "config", "updated_at_utc": now, "editable": True},
            {"key": "a_poll_s", "value": self.config.poll_interval_s, "unit": "s", "owner": "A", "source": "config", "updated_at_utc": now, "editable": True},
        ]
        wind_owner = {
            "rated_power_kw": "C", "cut_in_speed_mps": "C", "rated_speed_mps": "C",
            "cut_out_speed_mps": "C", "pitch_full_output_deg": "C", "pitch_feather_deg": "C",
            "ramp_up_kw_per_s": "A", "ramp_down_kw_per_s": "A",
        }
        wind_units = {
            "rated_power_kw": "kW", "cut_in_speed_mps": "m/s", "rated_speed_mps": "m/s",
            "cut_out_speed_mps": "m/s", "pitch_full_output_deg": "deg", "pitch_feather_deg": "deg",
            "ramp_up_kw_per_s": "kW/s", "ramp_down_kw_per_s": "kW/s",
        }
        for name, value in asdict(self.config.wind).items():
            owner = wind_owner[name]
            canonical = {
                "rated_power_kw": "wind_rated_power_kw",
                "ramp_up_kw_per_s": "wind_ramp_up_kw_per_s",
                "ramp_down_kw_per_s": "wind_ramp_down_kw_per_s",
            }.get(name, name)
            rows.append({"key": canonical, "value": value, "unit": wind_units[name], "owner": owner, "source": "config", "updated_at_utc": now, "editable": owner == "A"})
        diesel_units = {"min_power_kw": "kW", "max_power_kw": "kW", "ramp_up_kw_per_s": "kW/s", "ramp_down_kw_per_s": "kW/s"}
        for name, value in asdict(self.config.diesel).items():
            rows.append({"key": f"diesel_{name}", "value": value, "unit": diesel_units[name], "owner": "A", "source": "config", "updated_at_utc": now, "editable": True})
        rows.extend(
            (
                {"key": "reserve_kw", "value": 10.0, "unit": "kW", "owner": "B", "source": "统一基线", "updated_at_utc": now, "editable": False},
                {"key": "b_poll_s", "value": 1.0, "unit": "s", "owner": "B", "source": "统一基线", "updated_at_utc": now, "editable": False},
                {"key": "b_dispatch_s", "value": 5.0, "unit": "s", "owner": "B", "source": "统一基线", "updated_at_utc": now, "editable": False},
                {"key": "c_control_s", "value": 1.0, "unit": "s", "owner": "C", "source": "统一基线", "updated_at_utc": now, "editable": False},
                {"key": "c_timeout_s", "value": 3.0, "unit": "s", "owner": "C", "source": "统一基线", "updated_at_utc": now, "editable": False},
            )
        )
        if self.db_path.is_file():
            try:
                state = self.repository.get_state()
                runtime_rows = (
                    ("B.wind_target_kw", state.wind_target_kw, "kW", "B"),
                    ("B.diesel_target_kw", state.diesel_target_kw, "kW", "B"),
                    ("C.wind_available_kw", state.wind_available_kw, "kW", "C"),
                    ("C.wind_operating_limit_kw", state.wind_operating_limit_kw, "kW", "C"),
                    ("C.pitch_target_deg", state.pitch_actual_deg, "deg", "C"),
                )
                for key, value, unit, owner in runtime_rows:
                    rows.append({"key": key, "value": value, "unit": unit, "owner": owner, "source": "TCP 同步/状态", "updated_at_utc": state.sampled_at_utc, "editable": False})
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                pass
        return rows

    def _normalize_parameter_rows(self, raw: object) -> list[dict[str, object]]:
        if isinstance(raw, Mapping):
            if isinstance(raw.get("parameters"), (list, tuple)):
                values: Iterable[object] = raw["parameters"]
            else:
                values = [dict(value, key=key) if isinstance(value, Mapping) else {"key": key, "value": value} for key, value in raw.items()]
        else:
            values = raw if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)) else []
        rows: list[dict[str, object]] = []
        for value in values:
            row = _row_mapping(value)
            key = row.get("key") or row.get("parameter_key") or row.get("parameter") or row.get("name")
            device = row.get("device_id")
            if device and key and not str(key).startswith(f"{device}."):
                key = f"{device}.{key}"
            if not key:
                continue
            owner = str(row.get("owner", "A")).upper()
            rows.append({
                "key": str(key), "value": row.get("value", row.get("new_value", "-")),
                "unit": str(row.get("unit", "")), "owner": owner,
                "source": str(row.get("source", owner)),
                "updated_at_utc": str(row.get("updated_at_utc", row.get("changed_at_utc", "-"))),
                "editable": bool(row.get("editable", owner == "A")) and owner == "A",
            })
        return rows

    def refresh_parameters(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_parameter_refresh < 1.5:
            return
        if not force and any(editor.hasFocus() for editor in self._parameter_editors.values()):
            return
        self._last_parameter_refresh = now
        rows: list[dict[str, object]]
        method = getattr(self.repository, "parameter_snapshot", None)
        try:
            rows = self._normalize_parameter_rows(method()) if callable(method) and self.db_path.is_file() else self._fallback_parameter_rows()
            if not rows:
                rows = self._fallback_parameter_rows()
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            rows = self._fallback_parameter_rows()
            self.parameterSyncLabel.setText(f"参数接口降级：{error}")
        else:
            self.parameterSyncLabel.setText(f"已同步 {len(rows)} 项｜{_display_timestamp(_rfc3339(_utc_now()))}")
        self._parameter_rows = rows
        self._populate_parameter_table(rows)
        self._refresh_parameter_history()

    def _populate_parameter_table(self, rows: list[dict[str, object]]) -> None:
        pending_values = {
            key: editor.value()
            for key, editor in self._parameter_editors.items()
            if key in self._parameter_originals
            and not math.isclose(
                editor.value(), self._parameter_originals[key], rel_tol=0.0, abs_tol=1e-9
            )
        }
        self.parameterTable.setRowCount(len(rows))
        self._parameter_editors.clear()
        self._parameter_originals.clear()
        readonly_brush = QColor("#f2f5f8")
        for index, row in enumerate(rows):
            key = str(row["key"])
            owner = str(row["owner"])
            editable = bool(row["editable"])
            values = (
                PARAMETER_NAMES.get(key, key.split(".")[-1]), key, owner,
                "", str(row["unit"]), str(row["source"]), _display_timestamp(row["updated_at_utc"]),
            )
            for column, text in enumerate(values):
                if column == 3:
                    continue
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if not editable:
                    item.setBackground(readonly_brush)
                self.parameterTable.setItem(index, column, item)
            value = row.get("value")
            if editable and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                editor = QDoubleSpinBox()
                editor.setRange(0.0, 1_000_000.0)
                editor.setDecimals(3)
                editor.setValue(pending_values.get(key, float(value)))
                editor.setSingleStep(0.1)
                editor.setMinimumHeight(36)
                editor.setStyleSheet(
                    "QDoubleSpinBox { padding: 0 6px; } "
                    "QDoubleSpinBox QLineEdit { padding: 0; border: none; }"
                )
                self.parameterTable.setCellWidget(index, 3, editor)
                self._parameter_editors[key] = editor
                self._parameter_originals[key] = float(value)
            else:
                text = "true" if value is True else "false" if value is False else str(value)
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setBackground(readonly_brush)
                self.parameterTable.setItem(index, 3, item)

    def save_a_parameters(self) -> None:
        changes = {
            key: editor.value()
            for key, editor in self._parameter_editors.items()
            if not math.isclose(editor.value(), self._parameter_originals[key], rel_tol=0.0, abs_tol=1e-9)
        }
        if not changes:
            self.statusBar().showMessage("A 参数没有变化", 3000)
            return
        method = getattr(self.repository, "update_a_parameters", None)
        if not callable(method):
            QMessageBox.warning(self, "参数保存", "当前 Repository 尚未提供 update_a_parameters()，未写入任何参数。")
            return
        try:
            method(changes)
            self._append_ui_event("INFO", "参数", "A 参数已更新：" + ", ".join(changes))
            self.statusBar().showMessage(f"已保存 {len(changes)} 项 A 参数", 4000)
            self.refresh_parameters(force=True)
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "参数保存失败", str(error))

    def _refresh_parameter_history(self) -> None:
        method = getattr(self.repository, "parameter_history", None)
        try:
            if not callable(method) or not self.db_path.is_file():
                rows: list[dict[str, Any]] = []
            else:
                try:
                    raw = method(limit=50)
                except TypeError:
                    raw = method(50)
                rows = [_row_mapping(row) for row in raw]
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            rows = []
        self.parameterHistoryTable.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            key = str(row.get("key", row.get("parameter_key", row.get("name", "-"))))
            values = (
                _display_timestamp(row.get("changed_at_utc", row.get("updated_at_utc", row.get("created_at_utc", "-")))),
                PARAMETER_NAMES.get(key, key), row.get("owner", "-"), row.get("old_value", "-"),
                row.get("new_value", row.get("value", "-")), row.get("source", "-"),
                row.get("message", row.get("reason", "")),
            )
            for column, value in enumerate(values):
                self.parameterHistoryTable.setItem(row_index, column, QTableWidgetItem(str(value)))

    def _schedule_history_refresh(self, *_unused) -> None:
        if hasattr(self, "_history_debounce_timer"):
            self._history_debounce_timer.start()

    def refresh_history(self, *_unused) -> None:
        if not self.db_path.is_file():
            self.historyTrend.clear()
            self.historyTable.setRowCount(0)
            self.logTable.setRowCount(0)
            self.refreshHistoryButton.setEnabled(True)
            self.refreshHistoryButton.setText("刷新历史")
            return
        self._history_request_id += 1
        if self._history_loading:
            self._history_refresh_pending = True
            return
        self._start_history_load(self._history_request_id)

    def _start_history_load(self, request_id: int) -> None:
        selected = self.historySessionCombo.currentData()
        selected_session = str(selected) if selected else None
        self._history_loading = True
        self._history_refresh_pending = False
        self.refreshHistoryButton.setEnabled(False)
        self.refreshHistoryButton.setText("读取中…")
        task = _HistoryLoadTask(
            request_id=request_id,
            db_path=self.db_path,
            limit=self.historyLimitSpin.value(),
            selected_session=selected_session,
            log_level=self.logLevelCombo.currentText(),
        )
        task.signals.loaded.connect(self._history_loaded)
        task.signals.failed.connect(self._history_failed)
        self._history_task = task
        self._history_pool.start(task)

    def _history_loaded(self, request_id: int, snapshot: object) -> None:
        self._history_loading = False
        self._history_task = None
        if request_id == self._history_request_id and isinstance(snapshot, Mapping):
            self._apply_history_snapshot(dict(snapshot))
        self._finish_history_request(request_id)

    def _history_failed(self, request_id: int, message: str) -> None:
        self._history_loading = False
        self._history_task = None
        if request_id == self._history_request_id:
            self.statusBar().showMessage(f"历史状态读取失败：{message}", 5000)
        self._finish_history_request(request_id)

    def _finish_history_request(self, request_id: int) -> None:
        if self._history_refresh_pending or request_id != self._history_request_id:
            self._start_history_load(self._history_request_id)
            return
        self.refreshHistoryButton.setEnabled(True)
        self.refreshHistoryButton.setText("刷新历史")

    def _apply_history_snapshot(self, snapshot: dict[str, object]) -> None:
        sessions = [str(value) for value in snapshot.get("sessions", [])]
        selected_session = snapshot.get("selected_session")
        selected_rows = [
            _row_mapping(row) for row in snapshot.get("states", [])
        ]
        log_rows = [_row_mapping(row) for row in snapshot.get("logs", [])]

        self.historySessionCombo.blockSignals(True)
        self.historySessionCombo.clear()
        for index, session_id in enumerate(sessions):
            prefix = "当前 · " if index == len(sessions) - 1 else "历史 · "
            self.historySessionCombo.addItem(prefix + session_id[:12], session_id)
            self.historySessionCombo.setItemData(index, session_id, Qt.ItemDataRole.ToolTipRole)
        if selected_session in sessions:
            self.historySessionCombo.setCurrentIndex(sessions.index(selected_session))
        self.historySessionCombo.blockSignals(False)

        key, unit, color = self.historyMetricCombo.currentData()
        label = self.historyMetricCombo.currentText()
        session_label = str(selected_session)[:8] if selected_session else "无会话"
        self.historyTrend.title = f"历史 · {label} · {session_label}"
        self.historyTrend.unit = unit
        self.historyTrend.colors = {label: color}
        self.historyTrend.set_series(
            [str(row.get("sampled_at_utc", "")) for row in selected_rows],
            {label: [float(row.get(key, 0.0)) for row in selected_rows]},
        ) if selected_rows else self.historyTrend.clear()
        newest = list(reversed(selected_rows[-HISTORY_TABLE_ROW_LIMIT:]))
        keys = (
            "sampled_at_utc", "step", "wind_speed_mps", "load_power_kw", "wind_available_kw",
            "wind_target_kw", "wind_actual_kw", "diesel_target_kw", "diesel_actual_kw",
            "power_imbalance_kw", "session_id",
        )
        self.historyTable.setUpdatesEnabled(False)
        try:
            self.historyTable.setRowCount(len(newest))
            for row_index, row in enumerate(newest):
                for column, field in enumerate(keys):
                    value = row.get(field, "-")
                    if field == "sampled_at_utc":
                        text = _display_timestamp(value)
                    else:
                        text = f"{float(value):.3f}" if isinstance(value, float) else str(value)
                    self.historyTable.setItem(row_index, column, QTableWidgetItem(text))
        finally:
            self.historyTable.setUpdatesEnabled(True)
        self._populate_log_table(log_rows)
        if len(selected_rows) > len(newest):
            self.statusBar().showMessage(
                f"曲线已加载 {len(selected_rows)} 条；表格显示最近 {len(newest)} 条",
                5000,
            )

    def _repository_logs(self, limit: int) -> list[dict[str, Any]]:
        method = getattr(self.repository, "logs", None)
        if not callable(method):
            return []
        try:
            raw = method(limit=limit)
        except TypeError:
            raw = method(limit)
        return [_row_mapping(row) for row in raw]

    def _populate_log_table(self, rows: list[dict[str, Any]]) -> None:
        visible_rows = rows[:HISTORY_TABLE_ROW_LIMIT]
        self.logTable.setUpdatesEnabled(False)
        try:
            self.logTable.setRowCount(len(visible_rows))
            for row_index, row in enumerate(visible_rows):
                values = (
                    _display_timestamp(row.get("created_at_utc", "-")),
                    row.get("level", "-"), row.get("event", "-"),
                    row.get("step", "-"), row.get("message", ""),
                )
                for column, value in enumerate(values):
                    self.logTable.setItem(row_index, column, QTableWidgetItem(str(value)))
        finally:
            self.logTable.setUpdatesEnabled(True)

    def _refresh_alerts(self) -> None:
        database_events: list[dict[str, Any]] = []
        if self.db_path.is_file():
            try:
                database_events = [
                    {**row, "source": str(row.get("event", "grid.db"))}
                    for row in self._repository_logs(100)
                    if str(row.get("level", "")).upper() in {"WARNING", "ERROR", "CRITICAL"}
                ]
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                pass
        rows = [*database_events, *self._ui_events]
        rows.sort(key=lambda row: str(row.get("created_at_utc", "")), reverse=True)
        rows = rows[:100]
        self.alertTable.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                _display_timestamp(row.get("created_at_utc", "-")), row.get("level", "-"),
                row.get("source", row.get("event", "-")), row.get("message", ""),
            )
            for column, value in enumerate(values):
                self.alertTable.setItem(row_index, column, QTableWidgetItem(str(value)))

    def _update_communication_summary(self) -> None:
        bind = self.bindCombo.currentText().strip()
        port = self.portSpin.value()
        running = self._tcp_server.state() != QProcess.ProcessState.NotRunning
        self.communicationLabels["local"].setText(self.localIpEdit.text())
        self.communicationLabels["endpoint"].setText(f"{bind}:{port}　{'运行' if running else '停止'}")
        self.communicationLabels["database"].setText(
            f"{self.db_path.name}　{self._database_health}"
        )

    def _update_status_card(self, key: str, state: str, text: str, detail: str) -> None:
        label, detail_label = self.monitor_status[key]
        self._set_state_label(label, state, text)
        detail_label.setText(detail)

    def _set_control_buttons(self, status: str | None) -> None:
        self.start_button.setEnabled(status == "ready")
        self.pause_button.setEnabled(status == "running")
        self.resume_button.setEnabled(status == "paused")
        self.stop_button.setEnabled(status in {"running", "paused"})
        self.new_session_button.setEnabled(status in {"stopped", "completed"})

    def closeEvent(self, event) -> None:
        self._history_debounce_timer.stop()
        self._history_pool.clear()
        if (
            self._runner.state() != QProcess.ProcessState.NotRunning
            and self.db_path.is_file()
        ):
            try:
                if self.repository.runtime()["status"] == "running":
                    self.repository.set_status("pause")
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                pass
        for process in (self._runner, self._tcp_server):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(1000):
                    process.kill()
                    process.waitForFinished(1000)
        super().closeEvent(event)


def run_gui(
    *,
    db_path: Path,
    config_path: Path,
    scenario_path: Path,
    bind_address: str | None = None,
    port: int | None = None,
) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = SimulatorWindow(
        db_path=db_path,
        config_path=config_path,
        scenario_path=scenario_path,
        bind_address=bind_address,
        port=port,
    )
    window.show()
    return app.exec()
