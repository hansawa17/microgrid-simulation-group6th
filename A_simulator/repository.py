"""SQLite persistence shared by the simulator, TCP server and future GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
from typing import Mapping
import uuid

from .config import SimulationConfig
from .models import (
    ControlInputs,
    DieselGeneratorParameters,
    SimulationState,
    WindTurbineParameters,
    simulate_step,
)
from .scenario import ScenarioCurve, ScenarioPoint


SCHEMA_VERSION = 5


SCHEMA = """
PRAGMA user_version = 5;

CREATE TABLE IF NOT EXISTS simulation_control (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ready', 'running', 'paused', 'stopped', 'completed')),
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    step_s REAL NOT NULL,
    poll_interval_s REAL NOT NULL,
    sim_time_s REAL NOT NULL,
    step INTEGER NOT NULL,
    server_seq INTEGER NOT NULL DEFAULT 0,
    parameter_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_points (
    step INTEGER NOT NULL UNIQUE CHECK (step >= 0),
    sim_time_s REAL PRIMARY KEY,
    wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0)
);

CREATE TABLE IF NOT EXISTS device_parameters (
    device_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY (device_id, name)
);

CREATE TABLE IF NOT EXISTS parameter_state (
    name TEXT PRIMARY KEY,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('A', 'B', 'C')),
    source TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    editable INTEGER NOT NULL CHECK (editable IN (0, 1))
);

CREATE TABLE IF NOT EXISTS parameter_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    old_value REAL,
    new_value REAL NOT NULL,
    unit TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('A', 'B', 'C')),
    source TEXT NOT NULL,
    changed_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    wind_target_kw REAL NOT NULL,
    diesel_target_kw REAL NOT NULL,
    dispatch_wind_enable INTEGER NOT NULL CHECK (dispatch_wind_enable IN (0, 1)),
    controller_wind_enable INTEGER NOT NULL CHECK (controller_wind_enable IN (0, 1)),
    diesel_enable INTEGER NOT NULL CHECK (diesel_enable IN (0, 1)),
    pitch_target_deg REAL NOT NULL,
    controller_wind_available_kw REAL NOT NULL,
    controller_wind_operating_limit_kw REAL NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS current_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    sim_time_s REAL NOT NULL,
    sampled_at_utc TEXT NOT NULL,
    wind_speed_mps REAL NOT NULL,
    load_power_kw REAL NOT NULL,
    wind_available_kw REAL NOT NULL,
    wind_operating_limit_kw REAL NOT NULL,
    wind_target_kw REAL NOT NULL,
    wind_actual_kw REAL NOT NULL,
    diesel_target_kw REAL NOT NULL,
    diesel_actual_kw REAL NOT NULL,
    pitch_actual_deg REAL NOT NULL,
    wind_running INTEGER NOT NULL CHECK (wind_running IN (0, 1)),
    diesel_running INTEGER NOT NULL CHECK (diesel_running IN (0, 1)),
    fault INTEGER NOT NULL CHECK (fault IN (0, 1)),
    power_imbalance_kw REAL NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    sim_time_s REAL NOT NULL,
    sampled_at_utc TEXT NOT NULL,
    wind_speed_mps REAL NOT NULL,
    load_power_kw REAL NOT NULL,
    wind_available_kw REAL NOT NULL,
    wind_operating_limit_kw REAL NOT NULL,
    wind_target_kw REAL NOT NULL,
    wind_actual_kw REAL NOT NULL,
    diesel_target_kw REAL NOT NULL,
    diesel_actual_kw REAL NOT NULL,
    pitch_actual_deg REAL NOT NULL,
    wind_running INTEGER NOT NULL,
    diesel_running INTEGER NOT NULL,
    fault INTEGER NOT NULL,
    power_imbalance_kw REAL NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (session_id, step)
);

CREATE TABLE IF NOT EXISTS scada_points (
    point_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('telemetry', 'signal', 'setpoint', 'control')),
    value_json TEXT NOT NULL,
    unit TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_step INTEGER NOT NULL,
    sampled_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scada_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    point_id TEXT NOT NULL,
    value_json TEXT NOT NULL,
    sampled_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    source TEXT NOT NULL,
    seq INTEGER NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
    reason TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    UNIQUE (session_id, source, seq)
);

CREATE TABLE IF NOT EXISTS connection_status (
    peer TEXT PRIMARY KEY CHECK (peer IN ('B', 'C')),
    connected INTEGER NOT NULL CHECK (connected IN (0, 1)),
    last_seen_at_utc TEXT,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    session_id TEXT,
    step INTEGER,
    created_at_utc TEXT NOT NULL
);
"""


PARAMETER_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS parameter_state (
    name TEXT PRIMARY KEY,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('A', 'B', 'C')),
    source TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    editable INTEGER NOT NULL CHECK (editable IN (0, 1))
    )""",
    """CREATE TABLE IF NOT EXISTS parameter_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    old_value REAL,
    new_value REAL NOT NULL,
    unit TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('A', 'B', 'C')),
    source TEXT NOT NULL,
    changed_at_utc TEXT NOT NULL
    )""",
)


def _utc_now() -> str:
    value = datetime.now(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@contextmanager
def _connect(path: Path):
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _json_value(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _state_from_row(row: sqlite3.Row) -> SimulationState:
    return SimulationState(
        session_id=row["session_id"],
        step=row["step"],
        sim_time_s=row["sim_time_s"],
        sampled_at_utc=row["sampled_at_utc"],
        wind_speed_mps=row["wind_speed_mps"],
        load_power_kw=row["load_power_kw"],
        wind_available_kw=row["wind_available_kw"],
        wind_operating_limit_kw=row["wind_operating_limit_kw"],
        wind_target_kw=row["wind_target_kw"],
        wind_actual_kw=row["wind_actual_kw"],
        diesel_target_kw=row["diesel_target_kw"],
        diesel_actual_kw=row["diesel_actual_kw"],
        pitch_actual_deg=row["pitch_actual_deg"],
        wind_running=bool(row["wind_running"]),
        diesel_running=bool(row["diesel_running"]),
        fault=bool(row["fault"]),
        power_imbalance_kw=row["power_imbalance_kw"],
    )


def _state_values(state: SimulationState, recorded_at_utc: str) -> tuple[object, ...]:
    return (
        state.session_id,
        state.step,
        state.sim_time_s,
        state.sampled_at_utc,
        state.wind_speed_mps,
        state.load_power_kw,
        state.wind_available_kw,
        state.wind_operating_limit_kw,
        state.wind_target_kw,
        state.wind_actual_kw,
        state.diesel_target_kw,
        state.diesel_actual_kw,
        state.pitch_actual_deg,
        int(state.wind_running),
        int(state.diesel_running),
        int(state.fault),
        state.power_imbalance_kw,
        recorded_at_utc,
    )


STATE_COLUMNS = """session_id, step, sim_time_s, sampled_at_utc, wind_speed_mps, load_power_kw,
wind_available_kw, wind_operating_limit_kw, wind_target_kw, wind_actual_kw, diesel_target_kw,
diesel_actual_kw, pitch_actual_deg, wind_running, diesel_running, fault,
power_imbalance_kw"""


PARAMETER_UNITS = {
    "rated_power_kw": "kW",
    "cut_in_speed_mps": "m/s",
    "rated_speed_mps": "m/s",
    "cut_out_speed_mps": "m/s",
    "pitch_full_output_deg": "deg",
    "pitch_feather_deg": "deg",
    "ramp_up_kw_per_s": "kW/s",
    "ramp_down_kw_per_s": "kW/s",
    "min_power_kw": "kW",
    "max_power_kw": "kW",
}


# Canonical cross-module parameter names.  ``editable`` means editable from
# A's local UI; B/C-owned values are read-only in A and can only arrive through
# the protocol source/type allowlist.  Protocol v1 does not authenticate peers.
PARAMETER_SPECS: dict[str, tuple[str, str, bool]] = {
    "a_step_s": ("s", "A", True),
    "a_poll_s": ("s", "A", True),
    "wind_ramp_up_kw_per_s": ("kW/s", "A", True),
    "wind_ramp_down_kw_per_s": ("kW/s", "A", True),
    "diesel_min_power_kw": ("kW", "A", True),
    "diesel_max_power_kw": ("kW", "A", True),
    "diesel_ramp_up_kw_per_s": ("kW/s", "A", True),
    "diesel_ramp_down_kw_per_s": ("kW/s", "A", True),
    "reserve_kw": ("kW", "B", False),
    "b_poll_s": ("s", "B", False),
    "b_dispatch_s": ("s", "B", False),
    "wind_rated_power_kw": ("kW", "C", False),
    "cut_in_speed_mps": ("m/s", "C", False),
    "rated_speed_mps": ("m/s", "C", False),
    "cut_out_speed_mps": ("m/s", "C", False),
    "pitch_full_output_deg": ("deg", "C", False),
    "pitch_feather_deg": ("deg", "C", False),
    "c_control_s": ("s", "C", False),
    "c_timeout_s": ("s", "C", False),
}

A_PARAMETER_NAMES = frozenset(
    name for name, (_, owner, _) in PARAMETER_SPECS.items() if owner == "A"
)
B_PARAMETER_NAMES = frozenset(
    name for name, (_, owner, _) in PARAMETER_SPECS.items() if owner == "B"
)
C_PARAMETER_NAMES = frozenset(
    name for name, (_, owner, _) in PARAMETER_SPECS.items() if owner == "C"
)

# Canonical names which mirror values consumed by ``step_once``.
DEVICE_PARAMETER_STORAGE = {
    "wind_rated_power_kw": ("WT01", "rated_power_kw"),
    "cut_in_speed_mps": ("WT01", "cut_in_speed_mps"),
    "rated_speed_mps": ("WT01", "rated_speed_mps"),
    "cut_out_speed_mps": ("WT01", "cut_out_speed_mps"),
    "pitch_full_output_deg": ("WT01", "pitch_full_output_deg"),
    "pitch_feather_deg": ("WT01", "pitch_feather_deg"),
    "wind_ramp_up_kw_per_s": ("WT01", "ramp_up_kw_per_s"),
    "wind_ramp_down_kw_per_s": ("WT01", "ramp_down_kw_per_s"),
    "diesel_min_power_kw": ("DG01", "min_power_kw"),
    "diesel_max_power_kw": ("DG01", "max_power_kw"),
    "diesel_ramp_up_kw_per_s": ("DG01", "ramp_up_kw_per_s"),
    "diesel_ramp_down_kw_per_s": ("DG01", "ramp_down_kw_per_s"),
}

SIMULATION_PARAMETER_STORAGE = {
    "a_step_s": "step_s",
    "a_poll_s": "poll_interval_s",
}

BASELINE_REMOTE_PARAMETERS = {
    "reserve_kw": 10.0,
    "b_poll_s": 1.0,
    "b_dispatch_s": 5.0,
    "c_control_s": 1.0,
    "c_timeout_s": 3.0,
}


def _configured_parameter_values(config: SimulationConfig) -> dict[str, float]:
    """Return the normalized parameter snapshot used to seed a new database."""

    return {
        "a_step_s": config.step_s,
        "a_poll_s": config.poll_interval_s,
        "wind_ramp_up_kw_per_s": config.wind.ramp_up_kw_per_s,
        "wind_ramp_down_kw_per_s": config.wind.ramp_down_kw_per_s,
        "diesel_min_power_kw": config.diesel.min_power_kw,
        "diesel_max_power_kw": config.diesel.max_power_kw,
        "diesel_ramp_up_kw_per_s": config.diesel.ramp_up_kw_per_s,
        "diesel_ramp_down_kw_per_s": config.diesel.ramp_down_kw_per_s,
        "wind_rated_power_kw": config.wind.rated_power_kw,
        "cut_in_speed_mps": config.wind.cut_in_speed_mps,
        "rated_speed_mps": config.wind.rated_speed_mps,
        "cut_out_speed_mps": config.wind.cut_out_speed_mps,
        "pitch_full_output_deg": config.wind.pitch_full_output_deg,
        "pitch_feather_deg": config.wind.pitch_feather_deg,
        **BASELINE_REMOTE_PARAMETERS,
    }


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    reason: str
    duplicate: bool = False


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self, config: SimulationConfig, scenario: ScenarioCurve) -> str:
        if self.path.exists() and self.path.stat().st_size > 0:
            raise FileExistsError(f"database already exists: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if scenario.points[0].sim_time_s > config.start_s or scenario.points[-1].sim_time_s < config.end_s:
            raise ValueError("scenario curve must cover the configured simulation start and end")
        session_id = str(uuid.uuid4())
        first = scenario.at(config.start_s)
        initial = config.initial_control
        timestamp = _utc_now()
        state = SimulationState(
            session_id=session_id,
            step=0,
            sim_time_s=config.start_s,
            sampled_at_utc=timestamp,
            wind_speed_mps=first.wind_speed_mps,
            load_power_kw=first.load_power_kw,
            wind_available_kw=float(initial["controller_wind_available_kw"]),
            wind_operating_limit_kw=float(initial["controller_wind_operating_limit_kw"]),
            wind_target_kw=float(initial["wind_target_kw"]),
            wind_actual_kw=0.0,
            diesel_target_kw=float(initial["diesel_target_kw"]),
            diesel_actual_kw=0.0,
            pitch_actual_deg=float(initial["pitch_target_deg"]),
            wind_running=False,
            diesel_running=False,
            fault=False,
            power_imbalance_kw=first.load_power_kw,
        )
        with _connect(self.path) as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """INSERT INTO simulation_control
                   (singleton_id, session_id, status, start_s, end_s, step_s,
                    poll_interval_s, sim_time_s, step, parameter_status)
                   VALUES (1, ?, 'ready', ?, ?, ?, ?, ×OzæÚ$z{-®éÜj×6öçG&öÅ÷2%Ó ¢&—6RfÇVTW'&÷"‚&5÷F–ÖV÷WE÷5ö×W7Eöæ÷Eö&Uö&VÆ÷uö5ö6öçG&öÅ÷2" ¢FVb÷WFFU÷&ÖWFW'2€¢6VÆbÀ¢6öææV7F–öã¢7Æ—FS2ä6öææV7F–öâÀ¢&ÖWFW'3¢Ö–æu·7G"Âö&¦V7EÒÀ¢¢À¢ÆÆ÷vVC¢g&÷¦Vç6WE·7G%ÒÀ¢÷væW#¢7G"À¢6÷W&6S¢7G"À¢’ÓâæöæS ¢WFFW2Ò6VÆbåöæ÷&ÖÆ—¦VE÷WFFW2‡&ÖWFW'2ÂÆÆ÷vVB¢&÷w2Ò6öææV7F–öâæW†V7WFR€¢%4TÄT5BæÖRÂfÇVRe$ôÒ&ÖWFW%÷7FFR ¢’æfWF6†ÆÂ‚¢7W'&VçBÒ·7G"‡&÷u²&æÖR%Ò“¢fÆöB‡&÷u²'fÇVR%Ò’f÷"&÷r–â&÷w7Ð¢–b6WB†7W'&VçB’Ò6WB…$ÔUDU%õ5T52“ ¢&—6R'VçF–ÖTW'&÷"‚'&ÖWFW%÷7FFRFöW2æ÷BÖF6‚F†RcR&ÖWFW"&Vv—7G'’"¢&÷7V7F—fRÒ²¢¦7W'&VçBÂ¢§WFFW7Ð¢6VÆbå÷fÆ–FFU÷&ÖWFW%÷&VÆF–öç6†—2‡&÷7V7F—fR¢6†ævVEöæÖW2Ò¶æÖRf÷"æÖRÂfÇVR–âWFFW2æ—FV×2‚’–bfÇVRÒ7W'&VçE¶æÖU×Ð ¢2&R×6VæF–ærâÇ&VG’7–æ6‡&öæ—¦VB6æ6†÷B—2fÆ–BÂ'WB—B—2æ÷@¢2&ÖWFW"6†ævRæB×W7Bæ÷B–æfÆFRF†R†—7F÷'’F&ÆRà¢–bæ÷B6†ævVEöæÖW3 ¢&WGW&à ¢F–ÖW7F×Ò÷WF5öæ÷r‚¢f÷"æÖR–â6÷'FVB†6†ævVEöæÖW2“ ¢Væ—BÂW‡V7FVEö÷væW"ÂòÒ$ÔUDU%õ5T55¶æÖUÐ¢–bW‡V7FVEö÷væW"Ò÷væW# ¢&—6RfÇVTW'&÷"‚'VæWF†÷&—¦VE÷&ÖWFW""¢öÆE÷fÇVRÒ7W'&VçE¶æÖUÐ¢æWu÷fÇVRÒWFFW5¶æÖUÐ¢6öææV7F–öâæW†V7WFR€¢""%UDDR&ÖWFW%÷7FFP¢4UBfÇVSÓòÂ6÷W&6SÓòÂWFFVEöE÷WF3Óòt„U$RæÖSÓò"""À¢†æWu÷fÇVRÂ6÷W&6RÂF–ÖW7F×ÂæÖR’À¢¢6öææV7F–öâæW†V7WFR€¢""$”å4U%B”åDò&ÖWFW%ö†—7F÷'¢†æÖRÂöÆE÷fÇVRÂæWu÷fÇVRÂVæ—BÂ÷væW"Â6÷W&6RÂ6†ævVEöE÷WF2¢dÅTU2ƒòÂòÂòÂòÂòÂòÂò’"""À¢†æÖRÂöÆE÷fÇVRÂæWu÷fÇVRÂVæ—BÂ÷væW"Â6÷W&6RÂF–ÖW7F×’À¢¢–bæÖR–âDUd”4Uõ$ÔUDU%õ5Dõ$tS ¢FWf–6Uö–BÂ7F÷&vUöæÖRÒDUd”4Uõ$ÔUDU%õ5Dõ$tU¶æÖUÐ¢6öææV7F–öâæW†V7WFR€¢""%UDDRFWf–6U÷&ÖWFW'24UBfÇVSÓð¢t„U$RFWf–6Uö–CÓòäBæÖSÓò"""À¢†æWu÷fÇVRÂFWf–6Uö–BÂ7F÷&vUöæÖR’À¢¢VÆ–bæÖR–â4”ÕTÄD”ôåõ$ÔUDU%õ5Dõ$tS ¢6öÇVÖâÒ4”ÕTÄD”ôåõ$ÔUDU%õ5Dõ$tU¶æÖUÐ¢6öææV7F–öâæW†V7WFR€¢b%UDDR6–×VÆF–öåö6öçG&öÂ4UB¶6öÇVÖçÓÓòt„U$R6–ævÆWFöåö–CÓ"À¢†æWu÷fÇVRÂ’À¢ ¢5÷‡—6–6ÅöæÖW2Ò5õ$ÔUDU%ôäÔU2Ò²&5ö6öçG&öÅ÷2"Â&5÷F–ÖV÷WE÷2'Ð¢5ö7F–öåö–çfÆ–FFVBÒ÷væW"ÓÒ$2"æB&ööÂ€¢6†ævVEöæÖW2æ–çFW'6V7F–öâ†5÷‡—6–6ÅöæÖW2¢¢–b5ö7F–öåö–çfÆ–FFVC ¢26ææ÷B&V6ö×WFRæWr27F–öââ–çfÆ–FFRF†R66†VB7F–öà¢2VçF–Â2V&Æ—6†W2v–æEö7F–öâ&6VBöâF†RWFFVB&ÖWFW'2à¢6öææV7F–öâæW†V7WFR€¢""%UDDR6öçG&öÅ÷7FFP¢4UB6öçG&öÆÆW%÷v–æEöVæ&ÆSÓÂ—F6…÷F&vWEöFVsÓòÀ¢6öçG&öÆÆW%÷v–æEöf–Æ&ÆUö·sÓÀ¢6öçG&öÆÆW%÷v–æEö÷W&F–æuöÆ–Ö—Eö·sÓÀ¢WFFVEöE÷WF3Óòt„U$R6–ævÆWFöåö–CÓ"""À¢‡&÷7V7F—fU²'—F6…öfVF†W%öFVr%ÒÂF–ÖW7F×’À¢ ¢'VçF–ÖRÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5B6W76–öåö–BÂ7FWe$ôÒ6–×VÆF–öåö6öçG&öÂt„U$R6–ævÆWFöåö–CÓ ¢’æfWF6†öæR‚¢–b'VçF–ÖR—2æöæS ¢&—6R'VçF–ÖTW'&÷"‚'6–×VÆF–öåö6öçG&öÂ&÷r—2Ö—76–ær"¢æÖW2Ò"Â"æ¦ö–â‡6÷'FVB†6†ævVEöæÖW2’¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂt”ädòrÂw&ÖWFW'5÷WFFVBrÂòÂòÂòÂò’"À¢†b'6÷W&6S×·6÷W&6WÓ²æÖW3×¶æÖW7Ò"Â'VçF–ÖU²'6W76–öåö–B%ÒÂ'VçF–ÖU²'7FW%ÒÂF–ÖW7F×’À¢¢–b5ö7F–öåö–çfÆ–FFVC ¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂt”ädòrÂv5ö7F–öåö–çfÆ–FFVBrÂòÂòÂòÂò’"À¢€¢'v—F–ærf÷"v–æEö7F–öâ6Æ7VÆFVBv—F‚WFFVB2&ÖWFW'2"À¢'VçF–ÖU²'6W76–öåö–B%ÒÀ¢'VçF–ÖU²'7FW%ÒÀ¢F–ÖW7F×À¢’À¢ ¢FVbæW‡E÷6W'fW%÷6W‡6VÆb’Óâ–çC ¢6VÆbåöVç7W&UöW†—7G2‚¢v—F‚ö6öææV7B‡6VÆbçF‚’26öææV7F–öã ¢6öææV7F–öâæW†V7WFR‚$$Tt”â”ÔÔTD”DR"¢6öææV7F–öâæW†V7WFR€¢%UDDR6–×VÆF–öåö6öçG&öÂ4UB6W'fW%÷6W×6W'fW%÷6W³t„U$R6–ævÆWFöåö–CÓ ¢¢&WGW&â6öææV7F–öâæW†V7WFR€¢%4TÄT5B6W'fW%÷6We$ôÒ6–×VÆF–öåö6öçG&öÂt„U$R6–ævÆWFöåö–CÓ ¢’æfWF6†öæR‚•³Ð ¢FVbÇ•ö6öÖÖæB‡6VÆbÂÖW76vS¢F–7E·7G"Âö&¦V7EÒ’Óâ6öÖÖæE&W7VÇC ¢6VÆbåöVç7W&UöW†—7G2‚¢–ÆöBÒÖW76vU²'–ÆöB%Ð¢76W'B—6–ç7Fæ6R‡–ÆöBÂF–7B¢6W76–öåö–BÒÖW76vRævWB‚'6W76–öåö–B"¢6÷W&6RÒ7G"†ÖW76vU²'6÷W&6R%Ò¢6WÒ–çB†ÖW76vU²'6W%Ò¢ÖW76vU÷G—RÒ7G"†ÖW76vU²'G—R%Ò¢F–ÖW7F×Ò÷WF5öæ÷r‚¢v—F‚ö6öææV7B‡6VÆbçF‚’26öææV7F–öã ¢6öææV7F–öâæW†V7WFR‚$$Tt”â”ÔÔTD”DR"¢'VçF–ÖRÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5B6W76–öåö–BÂ7FWe$ôÒ6–×VÆF–öåö6öçG&öÂt„U$R6–ævÆWFöåö–CÓ ¢’æfWF6†öæR‚¢–b'VçF–ÖR—2æöæS ¢&—6R'VçF–ÖTW'&÷"‚'6–×VÆF–öåö6öçG&öÂ&÷r—2Ö—76–ær"¢–ÆöEö§6öâÒö§6öå÷fÇVR‡–ÆöB¢W†—7F–ærÒ6öææV7F–öâæW†V7WFR€¢""%4TÄT5BÖW76vU÷G—RÂ–ÆöEö§6öâÂ66WFVBÂ&V6öâe$ôÒ6öÖÖæG0¢t„U$R6W76–öåö–B•2òäB6÷W&6SÓòäB6WÓò"""À¢‡6W76–öåö–BÂ6÷W&6RÂ6W’À¢’æfWF6†öæR‚¢–bW†—7F–ær—2æ÷BæöæS ¢G'“ ¢W†—7F–æu÷–ÆöBÒö§6öå÷fÇVR†§6öâæÆöG2†W†—7F–æu²'–ÆöEö§6öâ%Ò’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"“ ¢W†—7F–æu÷–ÆöBÒ7G"†W†—7F–æu²'–ÆöEö§6öâ%Ò¢–bW†—7F–æu²&ÖW76vU÷G—R%ÒÓÒÖW76vU÷G—RæBW†—7F–æu÷–ÆöBÓÒ–ÆöEö§6öã ¢&WGW&â6öÖÖæE&W7VÇB†&ööÂ†W†—7F–æu²&66WFVB%Ò’ÂW†—7F–æu²'&V6öâ%ÒÂG'VR¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂut$ä”ärrÂv6öÖÖæE÷&V¦V7FVBrÂòÂòÂòÂò’"À¢‚'6Wö6öæfÆ–7B"Â'VçF–ÖU²'6W76–öåö–B%ÒÂ'VçF–ÖU²'7FW%ÒÂF–ÖW7F×’À¢¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ'6Wö6öæfÆ–7B"¢&W7VÇBÒ6VÆbå÷fÆ–FFUöæEöÇ’†6öææV7F–öâÂ'VçF–ÖRÂÖW76vU÷G—RÂ6÷W&6RÂ6W76–öåö–BÂ6WÂ–ÆöB¢6öææV7F–öâæW†V7WFR€¢""$”å4U%B”åDò6öÖÖæG0¢‡6W76–öåö–BÂ6÷W&6RÂ6WÂÖW76vU÷G—RÂ–ÆöEö§6öâÂ66WFVBÂ&V6öâÂ&V6V—fVEöE÷WF2¢dÅTU2ƒòÂòÂòÂòÂòÂòÂòÂò’"""À¢‡6W76–öåö–BÂ6÷W&6RÂ6WÂÖW76vU÷G—RÂ–ÆöEö§6öâÀ¢–çB‡&W7VÇBæ66WFVB’Â&W7VÇBç&V6öâÂF–ÖW7F×’À¢¢–bæ÷B&W7VÇBæ66WFVC ¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂut$ä”ärrÂv6öÖÖæE÷&V¦V7FVBrÂòÂòÂòÂò’"À¢‡&W7VÇBç&V6öâÂ'VçF–ÖU²'6W76–öåö–B%ÒÂ'VçF–ÖU²'7FW%ÒÂF–ÖW7F×’À¢¢&WGW&â&W7VÇ@ ¢FVb÷fÆ–FFUöæEöÇ’€¢6VÆbÀ¢6öææV7F–öã¢7Æ—FS2ä6öææV7F–öâÀ¢'VçF–ÖS¢7Æ—FS2å&÷rÀ¢ÖW76vU÷G—S¢7G"À¢6÷W&6S¢7G"À¢6W76–öåö–C¢ö&¦V7BÀ¢6W¢–çBÀ¢–ÆöC¢F–7E·7G"Âö&¦V7EÒÀ¢’Óâ6öÖÖæE&W7VÇC ¢–b6W76–öåö–BÒ'VçF–ÖU²'6W76–öåö–B%Ó ¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ'7FÆU÷6W76–öâ"¢W‡V7FVE÷6÷W&6RÒ²&F—7F6‚#¢$""Â'v–æEö7F–öâ#¢$2'ÒævWB†ÖW76vU÷G—R¢–bÖW76vU÷G—RÓÒ'&ÖWFW%÷WFFR# ¢–b6÷W&6Ræ÷B–â²$""Â$2'Ó ¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ'VæWF†÷&—¦VEöÖW76vU÷G—R"¢VÆ–bW‡V7FVE÷6÷W&6R—2æöæR÷"6÷W&6RÒW‡V7FVE÷6÷W&6S ¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ'VæWF†÷&—¦VEöÖW76vU÷G—R"¢Ö…÷6WÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5BÔ‚‡6W’e$ôÒ6öÖÖæG2t„U$R6W76–öåö–CÓòäB6÷W&6SÓò"À¢‡6W76–öåö–BÂ6÷W&6R’À¢’æfWF6†öæR‚•³Ð¢–bÖ…÷6W—2æ÷BæöæRæB6WÃÒÖ…÷6W ¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ&÷WEööeö÷&FW""¢G'“ ¢–bÖW76vU÷G—RÓÒ&F—7F6‚# ¢6VÆbåöÇ•öF—7F6‚†6öææV7F–öâÂ–ÆöB¢VÆ–bÖW76vU÷G—RÓÒ'v–æEö7F–öâ# ¢6VÆbåöÇ•÷v–æEö7F–öâ†6öææV7F–öâÂ–ÆöB¢VÇ6S ¢6VÆbåöÇ•÷&ÖWFW%÷WFFR†6öææV7F–öâÂ–ÆöBÂ6÷W&6R¢W†6WBfÇVTW'&÷"2W†3 ¢&WGW&â6öÖÖæE&W7VÇB„fÇ6RÂ7G"†W†2’¢&WGW&â6öÖÖæE&W7VÇB…G'VRÂ&66WFVB" ¢7FF–6ÖWF†ö@¢FVb÷7G&–7Eö&ööÂ‡–ÆöC¢F–7E·7G"Âö&¦V7EÒÂæÖS¢7G"’Óâ&ööÃ ¢fÇVRÒ–ÆöBævWB†æÖR¢–bG—R‡fÇVR’—2æ÷B&ööÃ ¢&—6RfÇVTW'&÷"†b&–çfÆ–E÷¶æÖWÒ"¢&WGW&âfÇVP ¢7FF–6ÖWF†ö@¢FVb÷7G&–7EöçVÖ&W"‡–ÆöC¢F–7E·7G"Âö&¦V7EÒÂæÖS¢7G"’ÓâfÆöC ¢fÇVRÒ–ÆöBævWB†æÖR¢–b—6–ç7Fæ6R‡fÇVRÂ&ööÂ’÷"æ÷B—6–ç7Fæ6R‡fÇVRÂ†–çBÂfÆöB’“ ¢&—6RfÇVTW'&÷"†b&–çfÆ–E÷¶æÖWÒ"¢&W7VÇBÒfÆöB‡fÇVR¢–bæ÷BÖF‚æ—6f–æ—FR‡&W7VÇB’÷"&W7VÇBÂ ¢&—6RfÇVTW'&÷"†b&–çfÆ–E÷¶æÖWÒ"¢&WGW&â&W7VÇ@ ¢FVböÇ•öF—7F6‚‡6VÆbÂ6öææV7F–öã¢7Æ—FS2ä6öææV7F–öâÂ–ÆöC¢F–7E·7G"Âö&¦V7EÒ’ÓâæöæS ¢W‡V7FVBÒ²'v–æE÷F&vWEö·r"Â&F–W6VÅ÷F&vWEö·r"Â'v–æEöVæ&ÆR"Â&F–W6VÅöVæ&ÆR'Ð¢–b6WB‡–ÆöB’ÒW‡V7FVC ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöF—7F6…öf–VÆG2"¢v–æE÷F&vWBÒ6VÆbå÷7G&–7EöçVÖ&W"‡–ÆöBÂ'v–æE÷F&vWEö·r"¢F–W6VÅ÷F&vWBÒ6VÆbå÷7G&–7EöçVÖ&W"‡–ÆöBÂ&F–W6VÅ÷F&vWEö·r"¢v–æEöVæ&ÆRÒ6VÆbå÷7G&–7Eö&ööÂ‡–ÆöBÂ'v–æEöVæ&ÆR"¢F–W6VÅöVæ&ÆRÒ6VÆbå÷7G&–7Eö&ööÂ‡–ÆöBÂ&F–W6VÅöVæ&ÆR"¢Æ–Ö—G2Ò°¢&÷u²&FWf–6Uö–B%Ó¢&÷u²'fÇVR%Ð¢f÷"&÷r–â6öææV7F–öâæW†V7WFR€¢%4TÄT5BFWf–6Uö–BÂfÇVRe$ôÒFWf–6U÷&ÖWFW'2t„U$RæÖR”â‚w&FVE÷÷vW%ö·rrÂvÖ…÷÷vW%ö·rr’ ¢¢Ð¢–bv–æE÷F&vWBâÆ–Ö—G5²%uC%Ó ¢&—6RfÇVTW'&÷"‚'v–æE÷F&vWEö÷WEööe÷&ævR"¢–bF–W6VÅ÷F&vWBâÆ–Ö—G5²$Ds%Ó ¢&—6RfÇVTW'&÷"‚&F–W6VÅ÷F&vWEö÷WEööe÷&ævR"¢6öææV7F–öâæW†V7WFR€¢""%UDDR6öçG&öÅ÷7FFR4UBv–æE÷F&vWEö·sÓòÂF–W6VÅ÷F&vWEö·sÓòÀ¢F—7F6…÷v–æEöVæ&ÆSÓòÂF–W6VÅöVæ&ÆSÓòÂWFFVEöE÷WF3Óòt„U$R6–ævÆWFöåö–CÓ"""À¢‡v–æE÷F&vWBÂF–W6VÅ÷F&vWBÂ–çB‡v–æEöVæ&ÆR’Â–çB†F–W6VÅöVæ&ÆR’Â÷WF5öæ÷r‚’’À¢ ¢FVböÇ•÷v–æEö7F–öâ‡6VÆbÂ6öææV7F–öã¢7Æ—FS2ä6öææV7F–öâÂ–ÆöC¢F–7E·7G"Âö&¦V7EÒ’ÓâæöæS ¢W‡V7FVBÒ°¢'v–æEöVæ&ÆR"À¢'—F6…÷F&vWEöFVr"À¢'v–æEöf–Æ&ÆUö·r"À¢'v–æEö÷W&F–æuöÆ–Ö—Eö·r"À¢Ð¢–b6WB‡–ÆöB’ÒW‡V7FVC ¢&—6RfÇVTW'&÷"‚&–çfÆ–E÷v–æEö7F–öåöf–VÆG2"¢v–æEöVæ&ÆRÒ6VÆbå÷7G&–7Eö&ööÂ‡–ÆöBÂ'v–æEöVæ&ÆR"¢—F6‚Ò6VÆbå÷7G&–7EöçVÖ&W"‡–ÆöBÂ'—F6…÷F&vWEöFVr"¢f–Æ&ÆRÒ6VÆbå÷7G&–7EöçVÖ&W"‡–ÆöBÂ'v–æEöf–Æ&ÆUö·r"¢÷W&F–æuöÆ–Ö—BÒ6VÆbå÷7G&–7EöçVÖ&W"‡–ÆöBÂ'v–æEö÷W&F–æuöÆ–Ö—Eö·r"¢&÷rÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5BfÇVRe$ôÒFWf–6U÷&ÖWFW'2t„U$RFWf–6Uö–CÒuuCräBæÖSÒw—F6…öfVF†W%öFVrr ¢’æfWF6†öæR‚¢–b&÷r—2æöæR÷"—F6‚â&÷u²'fÇVR%Ó ¢&—6RfÇVTW'&÷"‚'—F6…÷F&vWEö÷WEööe÷&ævR"¢&FVE÷&÷rÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5BfÇVRe$ôÒFWf–6U÷&ÖWFW'2t„U$RFWf–6Uö–CÒuuCräBæÖSÒw&FVE÷÷vW%ö·rr ¢’æfWF6†öæR‚¢–b&FVE÷&÷r—2æöæR÷"f–Æ&ÆRâ&FVE÷&÷u²'fÇVR%Ó ¢&—6RfÇVTW'&÷"‚'v–æEöf–Æ&ÆUö÷WEööe÷&ævR"¢–b÷W&F–æuöÆ–Ö—Bâf–Æ&ÆS ¢&—6RfÇVTW'&÷"‚'v–æEö÷W&F–æuöÆ–Ö—Eö÷WEööe÷&ævR"¢6öææV7F–öâæW†V7WFR€¢""%UDDR6öçG&öÅ÷7FFR4UB6öçG&öÆÆW%÷v–æEöVæ&ÆSÓòÂ—F6…÷F&vWEöFVsÓòÀ¢6öçG&öÆÆW%÷v–æEöf–Æ&ÆUö·sÓòÂ6öçG&öÆÆW%÷v–æEö÷W&F–æuöÆ–Ö—Eö·sÓòÀ¢WFFVEöE÷WF3Óòt„U$R6–ævÆWFöåö–CÓ"""À¢†–çB‡v–æEöVæ&ÆR’Â—F6‚Âf–Æ&ÆRÂ÷W&F–æuöÆ–Ö—BÂ÷WF5öæ÷r‚’’À¢ ¢FVböÇ•÷&ÖWFW%÷WFFR€¢6VÆbÀ¢6öææV7F–öã¢7Æ—FS2ä6öææV7F–öâÀ¢–ÆöC¢F–7E·7G"Âö&¦V7EÒÀ¢6÷W&6S¢7G"À¢’ÓâæöæS ¢–b6WB‡–ÆöB’Ò²'&ÖWFW'2'Ò÷"æ÷B—6–ç7Fæ6R‡–ÆöE²'&ÖWFW'2%ÒÂF–7B“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–E÷&ÖWFW%÷WFFUöf–VÆG2"¢–b6÷W&6RÓÒ$"# ¢ÆÆ÷vVBÒ%õ$ÔUDU%ôäÔU0¢6÷W&6UöÆ&VÂÒ$%÷F7 ¢VÆ–b6÷W&6RÓÒ$2# ¢ÆÆ÷vVBÒ5õ$ÔUDU%ôäÔU0¢6÷W&6UöÆ&VÂÒ$5÷F7 ¢VÇ6S¢2FVfVç6R–âFWFƒ²F†RVçfVÆ÷RfÆ–FF÷"Ç&VG’&V¦V7G2F†—2à¢&—6RfÇVTW'&÷"‚'VæWF†÷&—¦VEöÖW76vU÷G—R"¢6VÆbå÷WFFU÷&ÖWFW'2€¢6öææV7F–öâÀ¢–ÆöE²'&ÖWFW'2%ÒÀ¢ÆÆ÷vVCÖÆÆ÷vVBÀ¢÷væW#×6÷W&6RÀ¢6÷W&6S×6÷W&6UöÆ&VÂÀ¢ ¢FVbÖ&µö6öææV7F–öâ‡6VÆbÂVW#¢7G"Â6öææV7FVC¢&ööÂÂFWF–Ã¢7G"’ÓâæöæS ¢–bVW"æ÷B–â²$""Â$2'Ó ¢&WGW&à¢6VÆbåöVç7W&UöW†—7G2‚¢F–ÖW7F×Ò÷WF5öæ÷r‚¢v—F‚ö6öææV7B‡6VÆbçF‚’26öææV7F–öã ¢&Wf–÷W2Ò6öææV7F–öâæW†V7WFR€¢%4TÄT5B6öææV7FVBe$ôÒ6öææV7F–öå÷7FGW2t„U$RVW#Óò"Â‡VW"Â¢’æfWF6†öæR‚¢'VçF–ÖRÒ6öææV7F–öâæW†V7WFR€¢%4TÄT5B6W76–öåö–BÂ7FWe$ôÒ6–×VÆF–öåö6öçG&öÂt„U$R6–ævÆWFöåö–CÓ ¢’æfWF6†öæR‚¢6öææV7F–öâæW†V7WFR€¢""%UDDR6öææV7F–öå÷7FGW24UB6öææV7FVCÓòÂÆ7E÷6VVåöE÷WF3ÓòÂFWF–ÃÓòt„U$RVW#Óò"""À¢†–çB†6öææV7FVB’ÂF–ÖW7F×ÂFWF–ÂÂVW"’À¢¢–b€¢&Wf–÷W2—2æ÷BæöæP¢æB'VçF–ÖR—2æ÷BæöæP¢æB&ööÂ‡&Wf–÷W5²&6öææV7FVB%Ò’Ò&ööÂ†6öææV7FVB¢“ ¢WfVçBÒ'VW%ö6öææV7FVB"–b6öææV7FVBVÇ6R'VW%öF—66öææV7FVB ¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂt”ädòrÂòÂòÂòÂòÂò’"À¢€¢WfVçBÀ¢b'·VW'Ó¢¶FWF–ÇÒ"À¢'VçF–ÖU²'6W76–öåö–B%ÒÀ¢'VçF–ÖU²'7FW%ÒÀ¢F–ÖW7F×À¢’À¢ ¢FVbÆör‡6VÆbÂÆWfVÃ¢7G"ÂWfVçC¢7G"ÂÖW76vS¢7G"’ÓâæöæS ¢'VçF–ÖRÒ6VÆbç'VçF–ÖR‚¢v—F‚ö6öææV7B‡6VÆbçF‚’26öææV7F–öã ¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDòÆöw2dÅTU2„åTÄÂÂòÂòÂòÂòÂòÂò’"À¢†ÆWfVÂÂWfVçBÂÖW76vRÂ'VçF–ÖU²'6W76–öåö–B%ÒÂ'VçF–ÖU²'7FW%ÒÂ÷WF5öæ÷r‚’’À¢