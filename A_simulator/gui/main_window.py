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

from PyQt6.QtCore import QProcess, QTimer, Qt
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
from .curve_widget import CurveEditor, TimeSeriesChart, format_sim_time, format_utc_time


ROOT_DIR = Path(__file__).resolve().parents[2]

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
    ("sampled_at_utc", "A çŠ¶æ€æ–­é¢ UTC æŽˆæ—¶", "UTC"),
    ("wind_speed_mps", "A CSV é£Žé€Ÿè¾“å…¥", "m/s"),
    ("load_power_kw", "A CSV è´Ÿè·è¾“å…¥", "kW"),
    ("wind_available_kw", "C è®¡ç®—ã€A æ ¡éªŒå¹¶è½¬å‘", "kW"),
    ("wind_operating_limit_kw", "C è®¡ç®—ã€A æ ¡éªŒå¹¶è½¬å‘", "kW"),
    ("wind_target_kw", "B è°ƒåº¦ç›®æ ‡", "kW"),
    ("wind_actual_kw", "A ç‰©ç†æ¨¡åž‹å®žé™…å‡ºåŠ›", "kW"),
    ("diesel_target_kw", "B è°ƒåº¦ç›®æ ‡", "kW"),
    ("diesel_actual_kw", "A ç‰©ç†æ¨¡åž‹å®žé™…å‡ºåŠ›", "kW"),
    ("pitch_actual_deg", "C åŠ¨ä½œç» A ä»¿çœŸåŽçš„æ¡¨è·", "deg"),
    ("wind_running", "A ä»¿çœŸåŽçš„é£Žæœºè¿è¡ŒçŠ¶æ€", "bool"),
    ("diesel_running", "A ä»¿çœŸåŽçš„æŸ´å‘è¿è¡ŒçŠ¶æ€", "bool"),
    ("fault", "A çŠ¶æ€æ–­é¢æ•…éšœæ ‡å¿—", "bool"),
    ("power_imbalance_kw", "A: load - wind actual - diesel actual", "kW"),
)


HISTORY_FIELDS = (
    ("wind_speed_mps", "é£Žé€Ÿ", "m/s", COLORS["wind"]),
    ("load_power_kw", "è´Ÿè·", "kW", COLORS["load"]),
    ("wind_available_kw", "é£Žç”µå¯ç”¨", "kW", COLORS["avail"]),
    ("wind_target_kw", "é£Žç”µç›®æ ‡", "kW", COLORS["target"]),
    ("wind_actual_kw", "é£Žç”µå®žé™…", "kW", COLORS["actual"]),
    ("diesel_target_kw", "æŸ´å‘ç›®æ ‡", "kW", COLORS["warn"]),
    ("diesel_actual_kw", "æŸ´å‘å®žé™…", "kW", COLORS["diesel"]),
    ("power_imbalance_kw", "åŠŸçŽ‡ä¸å¹³è¡¡", "kW", COLORS["imbalance"]),
)


PARAMETER_NAMES = {
    "a_step_s": "A ä»¿çœŸæ­¥é•¿",
    "a_poll_s": "A æ‰§è¡Œå‘¨æœŸ",
    "wind_ramp_up_kw_per_s": "é£Žæœºå‡åŠŸçŽ‡çˆ¬å¡",
    "wind_ramp_down_kw_per_s": "é£Žæœºé™åŠŸçŽ‡çˆ¬å¡",
    "diesel_min_power_kw": "æŸ´å‘æœ€å°åŠŸçŽ‡",
    "diesel_max_power_kw": "æŸ´å‘æœ€å¤§åŠŸçŽ‡",
    "diesel_ramp_up_kw_per_s": "æŸ´å‘å‡åŠŸçŽ‡çˆ¬å¡",
    "diesel_ramp_down_kw_per_s": "æŸ´å‘é™åŠŸçŽ‡çˆ¬å¡",
    "reserve_kw": "æŸ´å‘å¤‡ç”¨å®¹é‡",
    "b_poll_s": "B çŠ¶æ€é‡‡é›†å‘¨æœŸ",
    "b_dispatch_s": "B è°ƒåº¦å‘¨æœŸ",
    "wind_rated_power_kw": "é£Žæœºé¢å®šåŠŸçŽ‡",
    "cut_in_speed_mps": "åˆ‡å…¥é£Žé€Ÿ",
    "rated_speed_mps": "é¢å®šé£Žé€Ÿ",
    "cut_out_speed_mps": "åˆ‡å‡ºé£Žé€Ÿ",
    "pitch_full_output_deg": "æ»¡åŠŸçŽ‡æ¡¨è·è§’",
    "pitch_feather_deg": "å®Œå…¨é¡ºæ¡¨è§’",
    "c_control_s": "C æŽ§åˆ¶å‘¨æœŸ",
    "c_timeout_s": "C é€šä¿¡è¶…æ—¶",
    "simulation.step_s": "ä»¿çœŸæ­¥é•¿",
    "simulation.poll_interval_s": "æ‰§è¡Œå‘¨æœŸ",
    "WT01.rated_power_kw": "é£Žæœºé¢å®šåŠŸçŽ‡",
    "WT01.cut_in_speed_mps": "åˆ‡å…¥é£Žé€Ÿ",
    "WT01.rated_speed_mps": "é¢å®šé£Žé€Ÿ",
    "WT01.cut_out_speed_mps": "åˆ‡å‡ºé£Žé€Ÿ",
    "WT01.pitch_full_output_deg": "æ»¡åŠŸçŽ‡æ¡¨è·è§’",
    "WT01.pitch_feather_deg": "å®Œå…¨é¡ºæ¡¨è§’",
    "WT01.ramp_up_kw_per_s": "é£Žæœºå‡åŠŸçŽ‡çˆ¬å¡",
    "WT01.ramp_down_kw_per_s": "é£Žæœºé™åŠŸçŽ‡çˆ¬å¡",
    "DG01.min_power_kw": "æŸ´å‘æœ€å°åŠŸçŽ‡",
    "DG01.max_power_kw": "æŸ´å‘æœ€å¤§åŠŸçŽ‡",
    "DG01.ramp_up_kw_per_s": "æŸ´å‘å‡åŠŸçŽ‡çˆ¬å¡",
    "DG01.ramp_down_kw_per_s": "æŸ´å‘é™åŠŸçŽ‡çˆ¬å¡",
    "B.wind_target_kw": "é£Žç”µè°ƒåº¦ç›®æ ‡",
    "B.diesel_target_kw": "æŸ´å‘è°ƒåº¦ç›®æ ‡",
    "B.wind_enable": "B é£Žæœºå¯ç”¨è¯·æ±‚",
    "B.diesel_enable": "B æŸ´å‘å¯ç”¨è¯·æ±‚",
    "C.wind_available_kw": "C é£Žç”µå¯ç”¨åŠŸçŽ‡",
    "C.wind_operating_limit_kw": "C é£Žæœºè¿è¡Œä¸Šé™",
    "C.pitch_target_deg": "C ç›®æ ‡æ¡¨è·è§’",
    "C.wind_enable": "C é£Žæœºè¿è¡Œè®¸å¯",
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
        self._last_live_refresh = 0.0
        self._last_parameter_refresh = 0.0
        self._ui_events: list[dict[str, object]] = []
        self._parameter_editors: dict[str, QDoubleSpinBox] = {}
        self._parameter_originals: dict[str, float] = {}
        self._parameter_rows: list[dict[str, object]] = []
        self._database_health = "å¾…æ£€æµ‹" if self.db_path.is_file() else "æœªåˆå§‹åŒ–"
        self._bind_address = bind_address or self.config.server_bind
        self._port = self.config.server_port if port is None else int(port)
        if not 1 <= self._port <= 65535:
            raise ValueError("port must be from 1 to 65535")

        self.setWindowTitle("å—æžå­¤ç«‹å¾®ç”µç½‘æ¨¡æ‹Ÿå™¨ã€€ç›‘æŽ§ä¸ŽæŽ§åˆ¶ç³»ç»Ÿ")
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
            f"ç³»ç»Ÿå°±ç»ªï½œå‚æ•°çŠ¶æ€ï¼š{self.config.parameter_status}ï½œæŽˆæ—¶æºï¼šæ“ä½œç³»ç»Ÿ UTC"
        )

    def _build_header(self) -> QFrame:
        bar = self._frame("headerBar")
        bar.setFixedHeight(74)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        badge = self._fr×¿}îÚ$z{-®éÜj×&rÂÖ–ær“ ¢–b—6–ç7Fæ6R‡&rævWB‚'&ÖWFW'2"’Â†Æ—7BÂGWÆR’“ ¢fÇVW3¢—FW&&ÆU¶ö&¦V7EÒÒ&u²'&ÖWFW'2%Ð¢VÇ6S ¢fÇVW2Ò¶F–7B‡fÇVRÂ¶W“Ö¶W’’–b—6–ç7Fæ6R‡fÇVRÂÖ–ær’VÇ6R²&¶W’#¢¶W’Â'fÇVR#¢fÇVWÒf÷"¶W’ÂfÇVR–â&ræ—FV×2‚•Ð¢VÇ6S ¢fÇVW2Ò&r–b—6–ç7Fæ6R‡&rÂ—FW&&ÆR’æBæ÷B—6–ç7Fæ6R‡&rÂ‡7G"Â'—FW2’’VÇ6RµÐ¢&÷w3¢Æ—7E¶F–7E·7G"Âö&¦V7EÕÒÒµÐ¢f÷"fÇVR–âfÇVW3 ¢&÷rÒ÷&÷uöÖ–ær‡fÇVR¢¶W’Ò&÷rævWB‚&¶W’"’÷"&÷rævWB‚'&ÖWFW%ö¶W’"’÷"&÷rævWB‚'&ÖWFW""’÷"&÷rævWB‚&æÖR"¢FWf–6RÒ&÷rævWB‚&FWf–6Uö–B"¢–bFWf–6RæB¶W’æBæ÷B7G"†¶W’’ç7F'G7v—F‚†b'¶FWf–6WÒâ"“ ¢¶W’Òb'¶FWf–6WÒç¶¶W—Ò ¢–bæ÷B¶W“ ¢6öçF–çVP¢÷væW"Ò7G"‡&÷rævWB‚&÷væW""Â$"’’çWW"‚¢&÷w2æVæB‡°¢&¶W’#¢7G"†¶W’’Â'fÇVR#¢&÷rævWB‚'fÇVR"Â&÷rævWB‚&æWu÷fÇVR"Â"Ò"’’À¢'Væ—B#¢7G"‡&÷rævWB‚'Væ—B"Â""’’Â&÷væW"#¢÷væW"À¢'6÷W&6R#¢7G"‡&÷rævWB‚'6÷W&6R"Â÷væW"’’À¢'WFFVEöE÷WF2#¢7G"‡&÷rævWB‚'WFFVEöE÷WF2"Â&÷rævWB‚&6†ævVEöE÷WF2"Â"Ò"’’’À¢&VF—F&ÆR#¢&ööÂ‡&÷rævWB‚&VF—F&ÆR"Â÷væW"ÓÒ$"’’æB÷væW"ÓÒ$"À¢Ò¢&WGW&â&÷w0 ¢FVb&Vg&W6…÷&ÖWFW'2‡6VÆbÂf÷&6S¢&ööÂÒfÇ6R’ÓâæöæS ¢æ÷rÒF–ÖRæÖöæ÷Föæ–2‚¢–bæ÷Bf÷&6RæBæ÷rÒ6VÆbåöÆ7E÷&ÖWFW%÷&Vg&W6‚ÂãS ¢&WGW&à¢–bæ÷Bf÷&6RæBç’†VF—F÷"æ†4fö7W2‚’f÷"VF—F÷"–â6VÆbå÷&ÖWFW%öVF—F÷'2çfÇVW2‚’“ ¢&WGW&à¢6VÆbåöÆ7E÷&ÖWFW%÷&Vg&W6‚Òæ÷p¢&÷w3¢Æ—7E¶F–7E·7G"Âö&¦V7EÕÐ¢ÖWF†öBÒvWFGG"‡6VÆbç&W÷6—F÷'’Â'&ÖWFW%÷6æ6†÷B"ÂæöæR¢G'“ ¢&÷w2Ò6VÆbåöæ÷&ÖÆ—¦U÷&ÖWFW%÷&÷w2†ÖWF†öB‚’’–b6ÆÆ&ÆR†ÖWF†öB’æB6VÆbæF%÷F‚æ—5öf–ÆR‚’VÇ6R6VÆbåöfÆÆ&6µ÷&ÖWFW%÷&÷w2‚¢–bæ÷B&÷w3 ¢&÷w2Ò6VÆbåöfÆÆ&6µ÷&ÖWFW%÷&÷w2‚¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"’2W'&÷# ¢&÷w2Ò6VÆbåöfÆÆ&6µ÷&ÖWFW%÷&÷w2‚¢6VÆbç&ÖWFW%7–æ4Æ&VÂç6WEFW‡B†b.Xø.i[hê^Xú>™˜Þ{ª~ûÉ§¶W'&÷'Ò"¢VÇ6S ¢6VÆbç&ÖWFW%7–æ4Æ&VÂç6WEFW‡B†b.[{.YÎjÚR¶ÆVâ‡&÷w2—ÒšžûÙÇµ÷&f3333’…÷WF5öæ÷r‚’—Ò"¢6VÆbå÷&ÖWFW%÷&÷w2Ò&÷w0¢6VÆbå÷÷VÆFU÷&ÖWFW%÷F&ÆR‡&÷w2¢6VÆbå÷&Vg&W6…÷&ÖWFW%ö†—7F÷'’‚ ¢FVb÷÷VÆFU÷&ÖWFW%÷F&ÆR‡6VÆbÂ&÷w3¢Æ—7E¶F–7E·7G"Âö&¦V7EÕÒ’ÓâæöæS ¢VæF–æu÷fÇVW2Ò°¢¶W“¢VF—F÷"çfÇVR‚¢f÷"¶W’ÂVF—F÷"–â6VÆbå÷&ÖWFW%öVF—F÷'2æ—FV×2‚¢–b¶W’–â6VÆbå÷&ÖWFW%ö÷&–v–æÇ0¢æBæ÷BÖF‚æ—66Æ÷6R€¢VF—F÷"çfÇVR‚’Â6VÆbå÷&ÖWFW%ö÷&–v–æÇ5¶¶W•ÒÂ&VÅ÷FöÃÓãÂ'5÷FöÃÓRÓ¢¢Ð¢6VÆbç&ÖWFW%F&ÆRç6WE&÷t6÷VçB†ÆVâ‡&÷w2’¢6VÆbå÷&ÖWFW%öVF—F÷'2æ6ÆV"‚¢6VÆbå÷&ÖWFW%ö÷&–v–æÇ2æ6ÆV"‚¢&VFöæÇ•ö''W6‚Ò6öÆ÷"‚"6c&cVc‚"¢f÷"–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢¶W’Ò7G"‡&÷u²&¶W’%Ò¢÷væW"Ò7G"‡&÷u²&÷væW"%Ò¢VF—F&ÆRÒ&ööÂ‡&÷u²&VF—F&ÆR%Ò¢fÇVW2Ò€¢$ÔUDU%ôäÔU2ævWB†¶W’Â¶W’ç7Æ—B‚"â"•²ÓÒ’Â¶W’Â÷væW"À¢""Â7G"‡&÷u²'Væ—B%Ò’Â7G"‡&÷u²'6÷W&6R%Ò’Â7G"‡&÷u²'WFFVEöE÷WF2%Ò’À¢¢f÷"6öÇVÖâÂFW‡B–âVçVÖW&FR‡fÇVW2“ ¢–b6öÇVÖâÓÒ3 ¢6öçF–çVP¢—FVÒÒF&ÆUv–FvWD—FVÒ‡FW‡B¢—FVÒç6WDfÆw2†—FVÒæfÆw2‚’båBä—FVÔfÆrä—FVÔ—4VF—F&ÆR¢–bæ÷BVF—F&ÆS ¢—FVÒç6WD&6¶w&÷VæB‡&VFöæÇ•ö''W6‚¢6VÆbç&ÖWFW%F&ÆRç6WD—FVÒ†–æFW‚Â6öÇVÖâÂ—FVÒ¢fÇVRÒ&÷rævWB‚'fÇVR"¢–bVF—F&ÆRæB—6–ç7Fæ6R‡fÇVRÂ†–çBÂfÆöB’’æBæ÷B—6–ç7Fæ6R‡fÇVRÂ&ööÂ’æBÖF‚æ—6f–æ—FR†fÆöB‡fÇVR’“ ¢VF—F÷"ÒF÷V&ÆU7–ä&÷‚‚¢VF—F÷"ç6WE&ævRƒãÂóóã¢VF—F÷"ç6WDFV6–ÖÇ2ƒ2¢VF—F÷"ç6WEfÇVR‡VæF–æu÷fÇVW2ævWB†¶W’ÂfÆöB‡fÇVR’’¢VF—F÷"ç6WE6–ævÆU7FWƒã¢VF—F÷"ç6WDÖ–æ–×VÔ†V–v‡Bƒ3b¢VF—F÷"ç6WE7G–ÆU6†VWB€¢%F÷V&ÆU7–ä&÷‚²FF–æs¢gƒ²Ò ¢%F÷V&ÆU7–ä&÷‚Æ–æTVF—B²FF–æs¢²&÷&FW#¢æöæS²Ò ¢¢6VÆbç&ÖWFW%F&ÆRç6WD6VÆÅv–FvWB†–æFW‚Â2ÂVF—F÷"¢6VÆbå÷&ÖWFW%öVF—F÷'5¶¶W•ÒÒVF—F÷ ¢6VÆbå÷&ÖWFW%ö÷&–v–æÇ5¶¶W•ÒÒfÆöB‡fÇVR¢VÇ6S ¢FW‡BÒ'G'VR"–bfÇVR—2G'VRVÇ6R&fÇ6R"–bfÇVR—2fÇ6RVÇ6R7G"‡fÇVR¢—FVÒÒF&ÆUv–FvWD—FVÒ‡FW‡B¢—FVÒç6WDfÆw2†—FVÒæfÆw2‚’båBä—FVÔfÆrä—FVÔ—4VF—F&ÆR¢—FVÒç6WD&6¶w&÷VæB‡&VFöæÇ•ö''W6‚¢6VÆbç&ÖWFW%F&ÆRç6WD—FVÒ†–æFW‚Â2Â—FVÒ ¢FVb6fUö÷&ÖWFW'2‡6VÆb’ÓâæöæS ¢6†ævW2Ò°¢¶W“¢VF—F÷"çfÇVR‚¢f÷"¶W’ÂVF—F÷"–â6VÆbå÷&ÖWFW%öVF—F÷'2æ—FV×2‚¢–bæ÷BÖF‚æ—66Æ÷6R†VF—F÷"çfÇVR‚’Â6VÆbå÷&ÖWFW%ö÷&–v–æÇ5¶¶W•ÒÂ&VÅ÷FöÃÓãÂ'5÷FöÃÓRÓ’¢Ð¢–bæ÷B6†ævW3 ¢6VÆbç7FGW4&"‚’ç6†÷tÖW76vR‚$Xø.i[k*iÈžXùŽXÉb"Â3¢&WGW&à¢ÖWF†öBÒvWFGG"‡6VÆbç&W÷6—F÷'’Â'WFFUö÷&ÖWFW'2"ÂæöæR¢–bæ÷B6ÆÆ&ÆR†ÖWF†öB“ ¢ÖW76vT&÷‚çv&æ–ær‡6VÆbÂ.Xø.i[KùÞZÙ‚"Â.[Ù>X˜Ò&W÷6—F÷'’[	®iÊ®hùKé²WFFUö÷&ÖWFW'2‚žûÈÎiÊ®XižXZ^K»¾KÙ^Xø.i[8""¢&WGW&à¢G'“ ¢ÖWF†öB†6†ævW2¢6VÆbåöVæE÷V•öWfVçB‚$”ädò"Â.Xø.i["Â$Xø.i[[{.i»NikûÉ¢"²"Â"æ¦ö–â†6†ævW2’¢6VÆbç7FGW4&"‚’ç6†÷tÖW76vR†b.[{.KùÞZÙ‚¶ÆVâ†6†ævW2—Òš’Xø.i["ÂC¢6VÆbç&Vg&W6…÷&ÖWFW'2†f÷&6SÕG'VR¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"’2W'&÷# ¢ÖW76vT&÷‚çv&æ–ær‡6VÆbÂ.Xø.i[KùÞZÙŽZK‹JR"Â7G"†W'&÷"’ ¢FVb÷&Vg&W6…÷&ÖWFW%ö†—7F÷'’‡6VÆb’ÓâæöæS ¢ÖWF†öBÒvWFGG"‡6VÆbç&W÷6—F÷'’Â'&ÖWFW%ö†—7F÷'’"ÂæöæR¢G'“ ¢–bæ÷B6ÆÆ&ÆR†ÖWF†öB’÷"æ÷B6VÆbæF%÷F‚æ—5öf–ÆR‚“ ¢&÷w3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢VÇ6S ¢G'“ ¢&rÒÖWF†öB†Æ–Ö—CÓS¢W†6WBG—TW'&÷# ¢&rÒÖWF†öBƒS¢&÷w2Òµ÷&÷uöÖ–ær‡&÷r’f÷"&÷r–â&uÐ¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"“ ¢&÷w2ÒµÐ¢6VÆbç&ÖWFW$†—7F÷'•F&ÆRç6WE&÷t6÷VçB†ÆVâ‡&÷w2’¢f÷"&÷uö–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢¶W’Ò7G"‡&÷rævWB‚&¶W’"Â&÷rævWB‚'&ÖWFW%ö¶W’"Â&÷rævWB‚&æÖR"Â"Ò"’’’¢fÇVW2Ò€¢&÷rævWB‚&6†ævVEöE÷WF2"Â&÷rævWB‚'WFFVEöE÷WF2"Â&÷rævWB‚&7&VFVEöE÷WF2"Â"Ò"’’’À¢$ÔUDU%ôäÔU2ævWB†¶W’Â¶W’’Â&÷rævWB‚&÷væW""Â"Ò"’Â&÷rævWB‚&öÆE÷fÇVR"Â"Ò"’À¢&÷rævWB‚&æWu÷fÇVR"Â&÷rævWB‚'fÇVR"Â"Ò"’’Â&÷rævWB‚'6÷W&6R"Â"Ò"’À¢&÷rævWB‚&ÖW76vR"Â&÷rævWB‚'&V6öâ"Â""’’À¢¢f÷"6öÇVÖâÂfÇVR–âVçVÖW&FR‡fÇVW2“ ¢6VÆbç&ÖWFW$†—7F÷'•F&ÆRç6WD—FVÒ‡&÷uö–æFW‚Â6öÇVÖâÂF&ÆUv–FvWD—FVÒ‡7G"‡fÇVR’’ ¢FVb&Vg&W6…ö†—7F÷'’‡6VÆbÂ¥÷VçW6VB’ÓâæöæS ¢–bæ÷B6VÆbæF%÷F‚æ—5öf–ÆR‚“ ¢6VÆbæ†—7F÷'•G&VæBæ6ÆV"‚¢6VÆbæ†—7F÷'•F&ÆRç6WE&÷t6÷VçBƒ¢6VÆbæÆöuF&ÆRç6WE&÷t6÷VçBƒ¢&WGW&à¢G'“ ¢6W76–öç2Ò6VÆbåö†—7F÷'•÷6W76–öç2‚¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"’2W'&÷# ¢6VÆbç7FGW4&"‚’ç6†÷tÖW76vR†b.XènXû.x«nhŠû¾XùnZK‹J^ûÉ§¶W'&÷'Ò"ÂS¢&WGW&à¢6VÆV7FVE÷6W76–öâÒ6VÆbæ†—7F÷'•6W76–öä6öÖ&òæ7W'&VçDFF‚¢–b6VÆV7FVE÷6W76–öâæ÷B–â6W76–öç3 ¢6VÆV7FVE÷6W76–öâÒ6W76–öç5²ÓÒ–b6W76–öç2VÇ6RæöæP¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òæ&Æö6µ6–væÇ2…G'VR¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òæ6ÆV"‚¢f÷"–æFW‚Â6W76–öåö–B–âVçVÖW&FR‡6W76–öç2“ ¢&Vf—‚Ò.[Ù>X˜Ò+r"–b–æFW‚ÓÒÆVâ‡6W76–öç2’ÒVÇ6R.XènXû"+r ¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òæFD—FVÒ‡&Vf—‚²6W76–öåö–E³£%ÒÂ6W76–öåö–B¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òç6WD—FVÔFF†–æFW‚Â6W76–öåö–BÂBä—FVÔFF&öÆRåFööÅF—&öÆR¢–b6VÆV7FVE÷6W76–öâ—2æ÷BæöæS ¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òç6WD7W'&VçD–æFW‚‡6W76–öç2æ–æFW‚‡6VÆV7FVE÷6W76–öâ’¢6VÆbæ†—7F÷'•6W76–öä6öÖ&òæ&Æö6µ6–væÇ2„fÇ6R¢G'“ ¢6VÆV7FVE÷&÷w2Ò6VÆbå÷7FFUö†—7F÷'’€¢6VÆbæ†—7F÷'”Æ–Ö—E7–âçfÇVR‚’Â6W76–öåö–C×6VÆV7FVE÷6W76–öà¢’–b6VÆV7FVE÷6W76–öâ—2æ÷BæöæRVÇ6RµÐ¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"’2W'&÷# ¢6VÆbç7FGW4&"‚’ç6†÷tÖW76vR†b.XènXû.x«nhŠû¾XùnZK‹J^ûÉ§¶W'&÷'Ò"ÂS¢6VÆV7FVE÷&÷w2ÒµÐ¢¶W’ÂVæ—BÂ6öÆ÷"Ò6VÆbæ†—7F÷'”ÖWG&–46öÖ&òæ7W'&VçDFF‚¢Æ&VÂÒ6VÆbæ†—7F÷'”ÖWG&–46öÖ&òæ7W'&VçEFW‡B‚¢6W76–öåöÆ&VÂÒ7G"‡6VÆV7FVE÷6W76–öâ•³£…Ò–b6VÆV7FVE÷6W76–öâVÇ6R.izKÉ®ŠùÒ ¢6VÆbæ†—7F÷'•G&VæBçF—FÆRÒb.XènXû"+r¶Æ&VÇÒ+r·6W76–öåöÆ&VÇÒ ¢6VÆbæ†—7F÷'•G&VæBçVæ—BÒVæ—@¢6VÆbæ†—7F÷'•G&VæBæ6öÆ÷'2Ò¶Æ&VÃ¢6öÆ÷'Ð¢6VÆbæ†—7F÷'•G&VæBç6WE÷6W&–W2€¢·7G"‡&÷rævWB‚'6×ÆVEöE÷WF2"Â""’’f÷"&÷r–â6VÆV7FVE÷&÷w5ÒÀ¢¶Æ&VÃ¢¶fÆöB‡&÷rævWB†¶W’Âã’’f÷"&÷r–â6VÆV7FVE÷&÷w5×ÒÀ¢’–b6VÆV7FVE÷&÷w2VÇ6R6VÆbæ†—7F÷'•G&VæBæ6ÆV"‚¢æWvW7BÒÆ—7B‡&WfW'6VB‡6VÆV7FVE÷&÷w2’¢6VÆbæ†—7F÷'•F&ÆRç6WE&÷t6÷VçB†ÆVâ†æWvW7B’¢¶W—2Ò€¢'6×ÆVEöE÷WF2"Â'7FW"Â'v–æE÷7VVEö×2"Â&ÆöE÷÷vW%ö·r"Â'v–æEöf–Æ&ÆUö·r"À¢'v–æE÷F&vWEö·r"Â'v–æEö7GVÅö·r"Â&F–W6VÅ÷F&vWEö·r"Â&F–W6VÅö7GVÅö·r"À¢'÷vW%ö–Ö&Ææ6Uö·r"Â'6W76–öåö–B"À¢¢f÷"&÷uö–æFW‚Â&÷r–âVçVÖW&FR†æWvW7B“ ¢f÷"6öÇVÖâÂf–VÆB–âVçVÖW&FR†¶W—2“ ¢fÇVRÒ&÷rævWB†f–VÆBÂ"Ò"¢FW‡BÒb'¶fÆöB‡fÇVR“¢ã6gÒ"–b—6–ç7Fæ6R‡fÇVRÂfÆöB’VÇ6R7G"‡fÇVR¢6VÆbæ†—7F÷'•F&ÆRç6WD—FVÒ‡&÷uö–æFW‚Â6öÇVÖâÂF&ÆUv–FvWD—FVÒ‡FW‡B’¢6VÆbå÷&Vg&W6…öÆöw2‚ ¢FVb÷&W÷6—F÷'•öÆöw2‡6VÆbÂÆ–Ö—C¢–çB’ÓâÆ—7E¶F–7E·7G"Âç•ÕÓ ¢ÖWF†öBÒvWFGG"‡6VÆbç&W÷6—F÷'’Â&Æöw2"ÂæöæR¢–bæ÷B6ÆÆ&ÆR†ÖWF†öB“ ¢&WGW&âµÐ¢G'“ ¢&rÒÖWF†öB†Æ–Ö—CÖÆ–Ö—B¢W†6WBG—TW'&÷# ¢&rÒÖWF†öB†Æ–Ö—B¢&WGW&âµ÷&÷uöÖ–ær‡&÷r’f÷"&÷r–â&uÐ ¢FVb÷&Vg&W6…öÆöw2‡6VÆb’ÓâæöæS ¢G'“ ¢&÷w2Ò6VÆbå÷&W÷6—F÷'•öÆöw2‡6VÆbæ†—7F÷'”Æ–Ö—E7–âçfÇVR‚’¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"’2W'&÷# ¢6VÆbç7FGW4&"‚’ç6†÷tÖW76vR†b.iz^[ù~Šû¾XùnZK‹J^ûÉ§¶W'&÷'Ò"ÂS¢&÷w2ÒµÐ¢ÆWfVÅöf–ÇFW"Ò6VÆbæÆötÆWfVÄ6öÖ&òæ7W'&VçEFW‡B‚¢–bÆWfVÅöf–ÇFW"Ò.XZŽ˜:‚# ¢&÷w2Ò·&÷rf÷"&÷r–â&÷w2–b7G"‡&÷rævWB‚&ÆWfVÂ"Â""’’çWW"‚’ÓÒÆWfVÅöf–ÇFW%Ð¢6VÆbæÆöuF&ÆRç6WE&÷t6÷VçB†ÆVâ‡&÷w2’¢f÷"&÷uö–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢fÇVW2Ò€¢&÷rævWB‚&7&VFVEöE÷WF2"Â"Ò"’Â&÷rævWB‚&ÆWfVÂ"Â"Ò"’Â&÷rævWB‚&WfVçB"Â"Ò"’À¢&÷rævWB‚'7FW"Â"Ò"’Â&÷rævWB‚&ÖW76vR"Â""’À¢¢f÷"6öÇVÖâÂfÇVR–âVçVÖW&FR‡fÇVW2“ ¢6VÆbæÆöuF&ÆRç6WD—FVÒ‡&÷uö–æFW‚Â6öÇVÖâÂF&ÆUv–FvWD—FVÒ‡7G"‡fÇVR’’ ¢FVb÷&Vg&W6…öÆW'G2‡6VÆb’ÓâæöæS ¢FF&6UöWfVçG3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢–b6VÆbæF%÷F‚æ—5öf–ÆR‚“ ¢G'“ ¢FF&6UöWfVçG2Ò°¢²¢§&÷rÂ'6÷W&6R#¢7G"‡&÷rævWB‚&WfVçB"Â&w&–BæF""’—Ð¢f÷"&÷r–â6VÆbå÷&W÷6—F÷'•öÆöw2ƒ¢–b7G"‡&÷rævWB‚&ÆWfVÂ"Â""’’çWW"‚’–â²%t$ä”är"Â$U%$õ""Â$5$•D”4Â'Ð¢Ð¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"“ ¢70¢&÷w2Ò²¦FF&6UöWfVçG2Â§6VÆbå÷V•öWfVçG5Ð¢&÷w2ç6÷'B†¶W“ÖÆÖ&F&÷s¢7G"‡&÷rævWB‚&7&VFVEöE÷WF2"Â""’’Â&WfW'6SÕG'VR¢&÷w2Ò&÷w5³£Ð¢6VÆbæÆW'EF&ÆRç6WE&÷t6÷VçB†ÆVâ‡&÷w2’¢f÷"&÷uö–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢fÇVW2Ò€¢&÷rævWB‚&7&VFVEöE÷WF2"Â"Ò"’Â&÷rævWB‚&ÆWfVÂ"Â"Ò"’À¢&÷rævWB‚'6÷W&6R"Â&÷rævWB‚&WfVçB"Â"Ò"’’Â&÷rævWB‚&ÖW76vR"Â""’À¢¢f÷"6öÇVÖâÂfÇVR–âVçVÖW&FR‡fÇVW2“ ¢6VÆbæÆW'EF&ÆRç6WD—FVÒ‡&÷uö–æFW‚Â6öÇVÖâÂF&ÆUv–FvWD—FVÒ‡7G"‡fÇVR’’ ¢FVb÷WFFUö6öÖ×Væ–6F–öå÷7VÖÖ'’‡6VÆb’ÓâæöæS ¢&–æBÒ6VÆbæ&–æD6öÖ&òæ7W'&VçEFW‡B‚’ç7G&—‚¢÷'BÒ6VÆbç÷'E7–âçfÇVR‚¢'Vææ–ærÒ6VÆbå÷F7÷6W'fW"ç7FFR‚’Ò&ö6W72å&ö6W757FFRäæ÷E'Vææ–æp¢6VÆbæ6öÖ×Væ–6F–öäÆ&VÇ5²&Æö6Â%Òç6WEFW‡B‡6VÆbæÆö6Ä—VF—BçFW‡B‚’¢6VÆbæ6öÖ×Væ–6F–öäÆ&VÇ5²&VæGö–çB%Òç6WEFW‡B†b'¶&–æGÓ§·÷'GÞ8²~‹ùŠÂr–b'Vææ–ærVÇ6R~XÎjÚ"wÒ"¢6VÆbæ6öÖ×Væ–6F–öäÆ&VÇ5²&FF&6R%Òç6WEFW‡B€¢b'·6VÆbæF%÷F‚ææÖWÞ8·6VÆbåöFF&6Uö†VÇF‡Ò ¢ ¢FVb÷WFFU÷7FGW5ö6&B‡6VÆbÂ¶W“¢7G"Â7FFS¢7G"ÂFW‡C¢7G"ÂFWF–Ã¢7G"’ÓâæöæS ¢Æ&VÂÂFWF–ÅöÆ&VÂÒ6VÆbæÖöæ—F÷%÷7FGW5¶¶W•Ð¢6VÆbå÷6WE÷7FFUöÆ&VÂ†Æ&VÂÂ7FFRÂFW‡B¢FWF–ÅöÆ&VÂç6WEFW‡B†FWF–Â ¢FVb÷6WEö6öçG&öÅö'WGFöç2‡6VÆbÂ7FGW3¢7G"ÂæöæR’ÓâæöæS ¢6VÆbç7F'Eö'WGFöâç6WDVæ&ÆVB‡7FGW2ÓÒ'&VG’"¢6VÆbçW6Uö'WGFöâç6WDVæ&ÆVB‡7FGW2ÓÒ''Vææ–ær"¢6VÆbç&W7VÖUö'WGFöâç6WDVæ&ÆVB‡7FGW2ÓÒ'W6VB"¢6VÆbç7F÷ö'WGFöâç6WDVæ&ÆVB‡7FGW2–â²''Vææ–ær"Â'W6VB'Ò¢6VÆbææWu÷6W76–öåö'WGFöâç6WDVæ&ÆVB‡7FGW2–â²'7F÷VB"Â&6ö×ÆWFVB'Ò ¢FVb6Æ÷6TWfVçB‡6VÆbÂWfVçB’ÓâæöæS ¢–b€¢6VÆbå÷'VææW"ç7FFR‚’Ò&ö6W72å&ö6W757FFRäæ÷E'Vææ–æp¢æB6VÆbæF%÷F‚æ—5öf–ÆR‚¢“ ¢G'“ ¢–b6VÆbç&W÷6—F÷'’ç'VçF–ÖR‚•²'7FGW2%ÒÓÒ''Vææ–ær# ¢6VÆbç&W÷6—F÷'’ç6WE÷7FGW2‚'W6R"¢W†6WB„õ4W'&÷"Â'VçF–ÖTW'&÷"ÂfÇVTW'&÷"Â7Æ—FS2äW'&÷"“ ¢70¢f÷"&ö6W72–â‡6VÆbå÷'VææW"Â6VÆbå÷F7÷6W'fW"“ ¢–b&ö6W72ç7FFR‚’Ò&ö6W72å&ö6W757FFRäæ÷E'Vææ–æs ¢&ö6W72çFW&Ö–æFR‚¢–bæ÷B&ö6W72çv—Df÷$f–æ—6†VBƒ“ ¢&ö6W72æ¶–ÆÂ‚¢&ö6W72çv—Df÷$f–æ—6†VBƒ¢7WW"‚’æ6Æ÷6TWfVçB†WfVçB  ¦FVb'VåöwV’€¢¢À¢F%÷Fƒ¢F‚À¢6öæf–u÷Fƒ¢F‚À¢66Væ&–õ÷Fƒ¢F‚À¢&–æEöFG&W73¢7G"ÂæöæRÒæöæRÀ¢÷'C¢–çBÂæöæRÒæöæRÀ¢’Óâ–çC ¢ÒÆ–6F–öâæ–ç7Fæ6R‚’÷"Æ–6F–öâ‡7—2æ&wb¢v–æF÷rÒ6–×VÆF÷%v–æF÷r€¢F%÷FƒÖF%÷F‚À¢6öæf–u÷FƒÖ6öæf–u÷F‚À¢66Væ&–õ÷Fƒ×66Væ&–õ÷F‚À¢&–æEöFG&W73Ö&–æEöFG&W72À¢÷'C×÷'BÀ¢¢v–æF÷rç6†÷r‚¢&WGW&âæW†V2‚