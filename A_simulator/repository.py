"""SQLite persistence shared by the simulator, TCP server and future GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
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


SCHEMA_VERSION = 6


SCHEMA = """
PRAGMA user_version = 6;

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
    last_wind_action_seq INTEGER,
    last_wind_action_step INTEGER,
    wind_action_applied_at_utc TEXT,
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


def _state_from_row(
    row: sqlite3.Row, control_row: sqlite3.Row | None = None
) -> SimulationState:
    control = control_row if control_row is not None else row
    control_keys = set(control.keys())
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
        controller_wind_enable=bool(control["controller_wind_enable"])
        if "controller_wind_enable" in control_keys else False,
        pitch_target_deg=float(control["pitch_target_deg"])
        if "pitch_target_deg" in control_keys else float(row["pitch_actual_deg"]),
        last_wind_action_seq=control["last_wind_action_seq"]
        if "last_wind_action_seq" in control_keys else None,
        last_wind_action_step=control["last_wind_action_step"]
        if "last_wind_action_step" in control_keys else None,
        wind_action_applied_at_utc=control["wind_action_applied_at_utc"]
        if "wind_action_applied_at_utc" in control_keys else None,
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
            controller_wind_enable=bool(initial["controller_wind_enable"]),
            pitch_target_deg=float(initial["pitch_target_deg"]),
        )
        with _connect(self.path) as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """INSERT INTO simulation_control
                   (singleton_id, session_id, status, start_s, end_s, step_s,
                    poll_interval_s, sim_time_s, step, parameter_status)
                   VALUES (1, ?, 'ready', ?, ?, ?, ?, ?, 0, ?)""",
                (session_id, config.start_s, config.end_s, config.step_s,
                 config.poll_interval_s, config.start_s, config.parameter_status),
            )
            connection.executemany(
                "INSERT INTO scenario_points VALUES (?, ?, ?, ?)",
                [(step, p.sim_time_s, p.wind_speed_mps, p.load_power_kw)
                 for step, p in enumerate(scenario.points)],
            )
            parameter_rows: list[tuple[str, str, float, str]] = []
            for device_id, params in (("WT01", config.wind), ("DG01", config.diesel)):
                for name, value in asdict(params).items():
                    parameter_rows.append((device_id, name, float(value), PARAMETER_UNITS[name]))
            connection.executemany(
                "INSERT INTO device_parameters VALUES (?, ?, ?, ?)", parameter_rows
            )
            normalized_parameters = _configured_parameter_values(config)
            connection.executemany(
                """INSERT INTO parameter_state
                   (name, value, unit, owner, source, updated_at_utc, editable)
                   VALUES (?, ?, ?, ?, 'config', ?, ?)""",
                [
                    (
                        name,
                        value,
                        PARAMETER_SPECS[name][0],
                        PARAMETER_SPECS[name][1],
                        timestamp,
                        int(PARAMETER_SPECS[name][2]),
                    )
                    for name, value in normalized_parameters.items()
                ],
            )
            connection.executemany(
                """INSERT INTO parameter_history
                   (name, old_value, new_value, unit, owner, source, changed_at_utc)
                   VALUES (?, NULL, ?, ?, ?, 'config', ?)""",
                [
                    (
                        name,
                        value,
                        PARAMETER_SPECS[name][0],
                        PARAMETER_SPECS[name][1],
                        timestamp,
                    )
                    for name, value in normalized_parameters.items()
                ],
            )
            connection.execute(
                """INSERT INTO control_state
                   (singleton_id, wind_target_kw, diesel_target_kw,
                    dispatch_wind_enable, controller_wind_enable, diesel_enable,
                    pitch_target_deg, controller_wind_available_kw,
                    controller_wind_operating_limit_kw, last_wind_action_seq,
                    last_wind_action_step, wind_action_applied_at_utc, updated_at_utc)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)""",
                (
                    float(initial["wind_target_kw"]),
                    float(initial["diesel_target_kw"]),
                    int(initial["dispatch_wind_enable"]),
                    int(initial["controller_wind_enable"]),
                    int(initial["diesel_enable"]),
                    float(initial["pitch_target_deg"]),
                    float(initial["controller_wind_available_kw"]),
                    float(initial["controller_wind_operating_limit_kw"]),
                    timestamp,
                ),
            )
            connection.execute(
                f"INSERT INTO current_state (singleton_id, {STATE_COLUMNS}, updated_at_utc) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _state_values(state, timestamp),
            )
            self._append_history(connection, state, timestamp)
            self._write_scada(connection, state, timestamp, initial)
            connection.executemany(
                "INSERT INTO connection_status VALUES (?, 0, NULL, 'not connected')",
                [("B",), ("C",)],
            )
            connection.execute(
                "INSERT INTO logs VALUES (NULL, 'INFO', 'database_initialized', ?, ?, 0, ?)",
                (f"parameter_status={config.parameter_status}", session_id, timestamp),
            )
        return session_id

    def reinitialize(
        self, config: SimulationConfig, scenario: ScenarioCurve
    ) -> tuple[str, Path | None]:
        """Back up an existing database and create a fresh one safely.

        The original database and its WAL sidecars are restored if creating the
        replacement fails.  Callers must obtain explicit user confirmation
        before using this operation on an existing database.
        """

        existing = [
            path
            for path in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
            if path.exists()
        ]
        if not existing:
            return self.initialize(config, scenario), None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.name}.{stamp}.bak")
        counter = 1
        while backup.exists():
            backup = self.path.with_name(
                f"{self.path.name}.{stamp}.{counter}.bak"
            )
            counter += 1

        moved: list[tuple[Path, Path]] = []
        replacement_started = False
        try:
            for original in existing:
                suffix = original.name[len(self.path.name):]
                destination = Path(f"{backup}{suffix}")
                original.replace(destination)
                moved.append((original, destination))
            replacement_started = True
            session_id = self.initialize(config, scenario)
        except Exception:
            if replacement_started:
                for generated in (
                    self.path,
                    Path(f"{self.path}-wal"),
                    Path(f"{self.path}-shm"),
                ):
                    if generated.exists():
                        generated.unlink()
            for original, destination in reversed(moved):
                if destination.exists():
                    destination.replace(original)
            raise
        return session_id, backup

    def _ensure_exists(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"database not initialized: {self.path}")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Migrate supported databases in place without discarding history."""

        with _connect(self.path) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema v{version} is newer than supported v{SCHEMA_VERSION}"
                )
            if version < 4:
                raise RuntimeError(
                    f"database schema v{version} cannot be migrated automatically; "
                    "back it up and initialize a new database"
                )
            if version == SCHEMA_VERSION:
                try:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM parameter_state"
                    ).fetchone()[0]
                    control_columns = {
                        str(row["name"])
                        for row in connection.execute("PRAGMA table_info(control_state)")
                    }
                except sqlite3.OperationalError as exc:
                    raise RuntimeError(
                        "schema v6 parameter tables are missing"
                    ) from exc
                if count == 0:
                    raise RuntimeError("schema v6 parameter_state is unexpectedly empty")
                required = {
                    "last_wind_action_seq",
                    "last_wind_action_step",
                    "wind_action_applied_at_utc",
                }
                if not required.issubset(control_columns):
                    raise RuntimeError("schema v6 control_state extension columns are missing")
                return

            # Only the one-time v4 migration needs a write lock. Re-check the
            # version after acquiring it so concurrent A processes cannot seed
            # the same database twice.
            connection.execute("BEGIN IMMEDIATE")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == SCHEMA_VERSION:
                return
            if version == 4:
                for statement in PARAMETER_SCHEMA:
                    connection.execute(statement)
                self._seed_migrated_parameters(connection)
                version = 5
            if version == 5:
                existing = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(control_state)")
                }
                for name, declaration in (
                    ("last_wind_action_seq", "INTEGER"),
                    ("last_wind_action_step", "INTEGER"),
                    ("wind_action_applied_at_utc", "TEXT"),
                ):
                    if name not in existing:
                        connection.execute(
                            f"ALTER TABLE control_state ADD COLUMN {name} {declaration}"
                        )
                runtime = connection.execute(
                    "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
                ).fetchone()
                control = connection.execute(
                    "SELECT * FROM control_state WHERE singleton_id=1"
                ).fetchone()
                current = connection.execute(
                    "SELECT sampled_at_utc FROM current_state WHERE singleton_id=1"
                ).fetchone()
                if runtime is not None and control is not None and current is not None:
                    migration_time = _utc_now()
                    for point in (
                        (
                            "WT01.dispatch_wind_enable",
                            "WT01",
                            bool(control["dispatch_wind_enable"]),
                        ),
                        (
                            "WT01.controller_wind_enable",
                            "WT01",
                            bool(control["controller_wind_enable"]),
                        ),
                        ("DG01.diesel_enable", "DG01", bool(control["diesel_enable"])),
                    ):
                        connection.execute(
                            """INSERT INTO scada_points
                               (point_id, device_id, category, value_json, unit, version,
                                updated_step, sampled_at_utc, updated_at_utc)
                               VALUES (?, ?, 'control', ?, 'bool', 1, ?, ?, ?)
                               ON CONFLICT(point_id) DO UPDATE SET
                               value_json=excluded.value_json,
                               version=scada_points.version+1,
                               updated_step=excluded.updated_step,
                               sampled_at_utc=excluded.sampled_at_utc,
                               updated_at_utc=excluded.updated_at_utc""",
                            (
                                point[0],
                                point[1],
                                _json_value(point[2]),
                                runtime["step"],
                                current["sampled_at_utc"],
                                migration_time,
                            ),
                        )
                if runtime is not None:
                    connection.execute(
                        "INSERT INTO logs VALUES (NULL, 'INFO', 'schema_migrated', ?, ?, ?, ?)",
                        (
                            "schema v5 -> v6; wind action trace metadata added",
                            runtime["session_id"], runtime["step"], _utc_now(),
                        ),
                    )
                version = 6
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"database schema changed during migration: v{version}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _seed_migrated_parameters(connection: sqlite3.Connection) -> None:
        runtime = connection.execute(
            """SELECT session_id, step, step_s, poll_interval_s
               FROM simulation_control WHERE singleton_id=1"""
        ).fetchone()
        if runtime is None:
            raise RuntimeError("cannot migrate: simulation_control row is missing")
        raw_device = {
            (row["device_id"], row["name"]): float(row["value"])
            for row in connection.execute(
                "SELECT device_id, name, value FROM device_parameters"
            )
        }
        values: dict[str, float] = {
            "a_step_s": float(runtime["step_s"]),
            "a_poll_s": float(runtime["poll_interval_s"]),
            **BASELINE_REMOTE_PARAMETERS,
        }
        for name, storage in DEVICE_PARAMETER_STORAGE.items():
            try:
                values[name] = raw_device[storage]
            except KeyError as exc:
                raise RuntimeError(
                    f"cannot migrate: device parameter {storage[0]}.{storage[1]} is missing"
                ) from exc
        timestamp = _utc_now()
        rows = [
            (
                name,
                values[name],
                PARAMETER_SPECS[name][0],
                PARAMETER_SPECS[name][1],
                "migration_v4",
                timestamp,
                int(PARAMETER_SPECS[name][2]),
            )
            for name in PARAMETER_SPECS
        ]
        connection.executemany(
            """INSERT INTO parameter_state
               (name, value, unit, owner, source, updated_at_utc, editable)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO NOTHING""",
            rows,
        )
        connection.executemany(
            """INSERT INTO parameter_history
               (name, old_value, new_value, unit, owner, source, changed_at_utc)
               VALUES (?, NULL, ?, ?, ?, ?, ?)""",
            [
                (name, value, unit, owner, source, timestamp)
                for name, value, unit, owner, source, timestamp, _editable in rows
            ],
        )
        connection.execute(
            "INSERT INTO logs VALUES (NULL, 'INFO', 'schema_migrated', ?, ?, ?, ?)",
            (
                "schema v4 -> v5; existing state history preserved",
                runtime["session_id"],
                runtime["step"],
                timestamp,
            ),
        )

    def runtime(self) -> dict[str, object]:
        self._ensure_exists()
        with _connect(self.path) as connection:
            row = connection.execute("SELECT * FROM simulation_control WHERE singleton_id=1").fetchone()
            if row is None:
                raise RuntimeError("simulation_control row is missing")
            return dict(row)

    def get_state(self) -> SimulationState:
        self._ensure_exists()
        with _connect(self.path) as connection:
            row = connection.execute("SELECT * FROM current_state WHERE singleton_id=1").fetchone()
            if row is None:
                raise RuntimeError("current_state row is missing")
            control = connection.execute(
                "SELECT * FROM control_state WHERE singleton_id=1"
            ).fetchone()
            if control is None:
                raise RuntimeError("control_state row is missing")
            return _state_from_row(row, control)

    def get_scenario(self) -> ScenarioCurve:
        """Return the scenario snapshot stored for the active simulation."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            points = tuple(
                ScenarioPoint(row[0], row[1], row[2])
                for row in connection.execute(
                    "SELECT sim_time_s, wind_speed_mps, load_power_kw "
                    "FROM scenario_points ORDER BY sim_time_s"
                )
            )
            return ScenarioCurve(points)

    def replace_scenario_values(self, scenario: ScenarioCurve) -> dict[str, object]:
        """Atomically apply edited wind/load values from the next simulation step.

        Runtime updates deliberately keep the active session's time axis unchanged.
        ``step_once`` reads ``scenario_points`` inside its own transaction, so a
        concurrent calculation observes either the complete old curve or the
        complete new curve and never a partially updated set of points.
        """

        self._ensure_exists()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT * FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            if runtime is None:
                raise RuntimeError("simulation_control row is missing")
            if runtime["status"] not in {"ready", "running", "paused"}:
                raise RuntimeError(
                    "runtime scenario update requires a ready, running or paused simulation"
                )

            stored_rows = connection.execute(
                "SELECT step, sim_time_s, wind_speed_mps, load_power_kw "
                "FROM scenario_points ORDER BY step"
            ).fetchall()
            if len(stored_rows) != len(scenario.points):
                raise ValueError(
                    "runtime scenario update may change wind/load values only; "
                    "reinitialize grid.db to change the time axis or point count"
                )

            updates: list[tuple[float, float, int]] = []
            for step, (stored, point) in enumerate(zip(stored_rows, scenario.points)):
                if int(stored["step"]) != step or not math.isclose(
                    float(stored["sim_time_s"]),
                    point.sim_time_s,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    raise ValueError(
                        "runtime scenario update may change wind/load values only; "
                        "reinitialize grid.db to change the time axis or point count"
                    )
                if (
                    float(stored["wind_speed_mps"]) != point.wind_speed_mps
                    or float(stored["load_power_kw"]) != point.load_power_kw
                ):
                    updates.append(
                        (point.wind_speed_mps, point.load_power_kw, step)
                    )

            if updates:
                connection.executemany(
                    "UPDATE scenario_points "
                    "SET wind_speed_mps=?, load_power_kw=? WHERE step=?",
                    updates,
                )

            effective_step = int(runtime["step"]) + 1
            effective_sim_time_s = float(runtime["sim_time_s"]) + float(
                runtime["step_s"]
            )
            if updates:
                connection.execute(
                    "INSERT INTO logs VALUES (NULL, 'INFO', 'scenario_runtime_updated', ?, ?, ?, ?)",
                    (
                        f"changed_points={len(updates)}; "
                        f"effective_step={effective_step}; "
                        f"effective_sim_time_s={effective_sim_time_s:.9g}",
                        runtime["session_id"],
                        runtime["step"],
                        _utc_now(),
                    ),
                )

            return {
                "session_id": str(runtime["session_id"]),
                "status": str(runtime["status"]),
                "effective_step": effective_step,
                "effective_sim_time_s": effective_sim_time_s,
                "changed_points": len(updates),
            }

    @staticmethod
    def _checked_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100_000:
            raise ValueError("limit must be an integer from 1 to 100000")
        return limit

    def parameter_snapshot(self) -> list[dict[str, object]]:
        """Return normalized A/B/C parameters for direct GUI presentation."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            rows = connection.execute(
                """SELECT name, value, unit, owner, source, updated_at_utc, editable
                   FROM parameter_state
                   ORDER BY CASE owner WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, name"""
            ).fetchall()
        return [
            {
                "name": str(row["name"]),
                "value": float(row["value"]),
                "unit": str(row["unit"]),
                "owner": str(row["owner"]),
                "source": str(row["source"]),
                "updated_at_utc": str(row["updated_at_utc"]),
                "editable": bool(row["editable"]),
            }
            for row in rows
        ]

    def parameter_history(self, limit: int = 200) -> list[dict[str, object]]:
        """Return newest parameter changes first."""

        self._ensure_exists()
        checked_limit = self._checked_limit(limit)
        with _connect(self.path) as connection:
            rows = connection.execute(
                """SELECT id, name, old_value, new_value, unit, owner, source,
                          changed_at_utc
                   FROM parameter_history ORDER BY id DESC LIMIT ?""",
                (checked_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def history_sessions(self) -> list[dict[str, object]]:
        """Return every persisted session independently of the plot row limit."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            rows = connection.execute(
                """SELECT session_id, COUNT(*) AS state_count,
                          MIN(sampled_at_utc) AS first_sampled_at_utc,
                          MAX(sampled_at_utc) AS last_sampled_at_utc,
                          MAX(id) AS last_id
                   FROM state_history GROUP BY session_id ORDER BY last_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def state_history(
        self, limit: int = 600, session_id: str | None = None
    ) -> list[dict[str, object]]:
        """Return a recent chronological state window, optionally for one session."""

        self._ensure_exists()
        checked_limit = self._checked_limit(limit)
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id must be a non-empty string or None")
        with _connect(self.path) as connection:
            if session_id is None:
                rows = connection.execute(
                    f"""SELECT * FROM (
                           SELECT id, {STATE_COLUMNS}, recorded_at_utc
                           FROM state_history ORDER BY id DESC LIMIT ?
                       ) ORDER BY id ASC""",
                    (checked_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""SELECT * FROM (
                           SELECT id, {STATE_COLUMNS}, recorded_at_utc
                           FROM state_history WHERE session_id=?
                           ORDER BY id DESC LIMIT ?
                       ) ORDER BY id ASC""",
                    (session_id, checked_limit),
                ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            for name in ("wind_running", "diesel_running", "fault"):
                item[name] = bool(item[name])
            result.append(item)
        return result

    @staticmethod
    def _checked_time_range(start_utc: str, end_utc: str) -> tuple[str, str]:
        """Validate an inclusive UTC range in the protocol timestamp format."""

        for name, value in (("start_utc", start_utc), ("end_utc", end_utc)):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a UTC timestamp string")
            try:
                parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError as exc:
                raise ValueError(
                    f"{name} must use YYYY-MM-DDTHH:MM:SS.mmmZ"
                ) from exc
            if parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != value:
                raise ValueError(f"{name} must use millisecond UTC precision")
        if start_utc > end_utc:
            raise ValueError("start_utc must not be later than end_utc")
        return start_utc, end_utc

    def state_history_between(
        self,
        start_utc: str,
        end_utc: str,
        *,
        limit: int = 10_000,
        session_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Return chronological SCADA state rows inside an inclusive time range."""

        self._ensure_exists()
        start, end = self._checked_time_range(start_utc, end_utc)
        checked_limit = self._checked_limit(limit)
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id must be a non-empty string or None")
        clauses = ["sampled_at_utc >= ?", "sampled_at_utc <= ?"]
        values: list[object] = [start, end]
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        values.append(checked_limit)
        with _connect(self.path) as connection:
            rows = connection.execute(
                f"""SELECT id, {STATE_COLUMNS}, recorded_at_utc
                    FROM state_history WHERE {' AND '.join(clauses)}
                    ORDER BY sampled_at_utc, id LIMIT ?""",
                values,
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            for name in ("wind_running", "diesel_running", "fault"):
                item[name] = bool(item[name])
        return result

    def logs(self, limit: int = 200) -> list[dict[str, object]]:
        """Return newest application log records first."""

        self._ensure_exists()
        checked_limit = self._checked_limit(limit)
        with _connect(self.path) as connection:
            rows = connection.execute(
                """SELECT id, level, event, message, session_id, step, created_at_utc
                   FROM logs ORDER BY id DESC LIMIT ?""",
                (checked_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def logs_between(
        self,
        start_utc: str,
        end_utc: str,
        *,
        limit: int = 10_000,
        session_id: str | None = None,
        level: str | None = None,
    ) -> list[dict[str, object]]:
        """Return newest operation logs inside an inclusive time range."""

        self._ensure_exists()
        start, end = self._checked_time_range(start_utc, end_utc)
        checked_limit = self._checked_limit(limit)
        clauses = ["created_at_utc >= ?", "created_at_utc <= ?"]
        values: list[object] = [start, end]
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session_id must be a non-empty string or None")
            clauses.append("session_id = ?")
            values.append(session_id)
        if level is not None:
            normalized_level = str(level).upper()
            if normalized_level not in {"INFO", "WARNING", "ERROR"}:
                raise ValueError("level must be INFO, WARNING, ERROR or None")
            clauses.append("level = ?")
            values.append(normalized_level)
        values.append(checked_limit)
        with _connect(self.path) as connection:
            rows = connection.execute(
                f"""SELECT id, level, event, message, session_id, step, created_at_utc
                    FROM logs WHERE {' AND '.join(clauses)}
                    ORDER BY created_at_utc DESC, id DESC LIMIT ?""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def trace_events_between(
        self,
        start_utc: str,
        end_utc: str,
        *,
        limit: int = 10_000,
        session_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Return scenario/control/parameter events for acceptance traceability."""

        self._ensure_exists()
        start, end = self._checked_time_range(start_utc, end_utc)
        checked_limit = self._checked_limit(limit)
        with _connect(self.path) as connection:
            command_clauses = ["received_at_utc >= ?", "received_at_utc <= ?"]
            command_values: list[object] = [start, end]
            if session_id is not None:
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("session_id must be a non-empty string or None")
                command_clauses.append("session_id = ?")
                command_values.append(session_id)
            commands = connection.execute(
                f"""SELECT id, received_at_utc AS occurred_at_utc, session_id,
                           source AS object, message_type AS event, payload_json AS detail,
                           accepted, reason
                    FROM commands WHERE {' AND '.join(command_clauses)}""",
                command_values,
            ).fetchall()
            parameters = connection.execute(
                """SELECT id, changed_at_utc AS occurred_at_utc, NULL AS session_id,
                          owner || ':' || name AS object, 'parameter_update' AS event,
                          'source=' || source || '; old=' || COALESCE(CAST(old_value AS TEXT), 'NULL') ||
                          '; new=' || CAST(new_value AS TEXT) || ' ' || unit AS detail,
                          1 AS accepted, 'accepted' AS reason
                   FROM parameter_history
                   WHERE changed_at_utc >= ? AND changed_at_utc <= ?""",
                (start, end),
            ).fetchall()
            log_clauses = [
                "created_at_utc >= ?",
                "created_at_utc <= ?",
                "event IN ('session_created','status_changed','scenario_saved','scenario_loaded',"
                "'scenario_duration_changed','scenario_runtime_updated')",
            ]
            log_values: list[object] = [start, end]
            if session_id is not None:
                log_clauses.append("session_id = ?")
                log_values.append(session_id)
            scenario_logs = connection.execute(
                f"""SELECT id, created_at_utc AS occurred_at_utc, session_id,
                           'A' AS object, event, message AS detail,
                           1 AS accepted, 'accepted' AS reason
                    FROM logs WHERE {' AND '.join(log_clauses)}""",
                log_values,
            ).fetchall()
        merged = [dict(row) for row in (*commands, *parameters, *scenario_logs)]
        merged.sort(key=lambda row: (str(row["occurred_at_utc"]), int(row["id"])))
        return merged[-checked_limit:]

    def database_categories(self, limit: int = 1000) -> dict[str, list[dict[str, object]]]:
        """Return explicitly separated four-remote and operational database views."""

        self._ensure_exists()
        checked_limit = self._checked_limit(limit)
        category_names = {
            "YC": "telemetry",
            "YX": "signal",
            "YT": "setpoint",
            "YK": "control",
        }
        with _connect(self.path) as connection:
            result: dict[str, list[dict[str, object]]] = {}
            for label, category in category_names.items():
                rows = connection.execute(
                    """SELECT point_id, device_id, category, value_json, unit, version,
                              updated_step, sampled_at_utc, updated_at_utc
                       FROM scada_points WHERE category=? ORDER BY point_id LIMIT ?""",
                    (category, checked_limit),
                ).fetchall()
                result[label] = [dict(row) for row in rows]
            result["device_parameters"] = [
                dict(row)
                for row in connection.execute(
                    """SELECT device_id, name, value, unit
                       FROM device_parameters ORDER BY device_id, name LIMIT ?""",
                    (checked_limit,),
                ).fetchall()
            ]
            result["environment_scenario"] = [
                dict(row)
                for row in connection.execute(
                    """SELECT step, sim_time_s, wind_speed_mps, load_power_kw
                       FROM scenario_points ORDER BY step LIMIT ?""",
                    (checked_limit,),
                ).fetchall()
            ]
            result["simulation_history"] = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT id, {STATE_COLUMNS}, recorded_at_utc
                       FROM state_history ORDER BY id DESC LIMIT ?""",
                    (checked_limit,),
                ).fetchall()
            ]
            result["logs"] = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, level, event, message, session_id, step, created_at_utc
                       FROM logs ORDER BY id DESC LIMIT ?""",
                    (checked_limit,),
                ).fetchall()
            ]
        return result

    def export_logs_jsonl(
        self,
        destination: str | Path,
        start_utc: str,
        end_utc: str,
        *,
        session_id: str | None = None,
        level: str | None = None,
    ) -> Path:
        """Export selected logs with explicit occurrence time and object fields."""

        target = Path(destination)
        rows = self.logs_between(
            start_utc,
            end_utc,
            session_id=session_id,
            level=level,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for row in reversed(rows):
                    object_name = row.get("session_id") or "A"
                    if row.get("step") is not None:
                        object_name = f"{object_name}/step:{row['step']}"
                    record = {
                        "occurred_at_utc": row["created_at_utc"],
                        "object": object_name,
                        **row,
                    }
                    handle.write(_json_value(record) + "\n")
            os.replace(temporary_name, target)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def update_a_parameters(
        self, parameters: Mapping[str, object]
    ) -> list[dict[str, object]]:
        """Validate and atomically update only parameters owned by A."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_parameters(
                connection,
                parameters,
                allowed=A_PARAMETER_NAMES,
                owner="A",
                source="A_local",
            )
        return self.parameter_snapshot()

    def connection_statuses(self) -> dict[str, dict[str, object]]:
        """Return B/C TCP connection indicators for the GUI."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            rows = connection.execute(
                "SELECT peer, connected, last_seen_at_utc, detail "
                "FROM connection_status ORDER BY peer"
            ).fetchall()
        return {
            str(row["peer"]): {
                "connected": bool(row["connected"]),
                "last_seen_at_utc": row["last_seen_at_utc"],
                "detail": row["detail"],
            }
            for row in rows
        }

    def connection_status(self) -> dict[str, dict[str, object]]:
        """Return B/C link status for GUI display without sharing a connection."""

        self._ensure_exists()
        with _connect(self.path) as connection:
            rows = connection.execute(
                "SELECT peer, connected, last_seen_at_utc, detail "
                "FROM connection_status ORDER BY peer"
            ).fetchall()
        return {
            str(row["peer"]): {
                "connected": bool(row["connected"]),
                "last_seen_at_utc": row["last_seen_at_utc"],
                "detail": row["detail"],
            }
            for row in rows
        }

    def reset_connections(self, detail: str) -> None:
        """Mark B/C offline when the A TCP service starts or stops."""

        self._ensure_exists()
        timestamp = _utc_now()
        with _connect(self.path) as connection:
            runtime = connection.execute(
                "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            connected_peers = [
                str(row["peer"])
                for row in connection.execute(
                    "SELECT peer FROM connection_status WHERE connected=1"
                )
            ]
            connection.execute(
                "UPDATE connection_status SET connected=0, detail=?", (str(detail),)
            )
            if runtime is not None:
                connection.executemany(
                    "INSERT INTO logs VALUES (NULL, 'INFO', 'peer_disconnected', ?, ?, ?, ?)",
                    [
                        (
                            f"{peer}: {detail}",
                            runtime["session_id"],
                            runtime["step"],
                            timestamp,
                        )
                        for peer in connected_peers
                    ],
                )

    def set_status(self, action: str) -> str:
        self._ensure_exists()
        transitions = {
            "start": ({"ready"}, "running"),
            "pause": ({"running"}, "paused"),
            "resume": ({"paused"}, "running"),
            "stop": ({"running", "paused"}, "stopped"),
        }
        if action not in transitions:
            raise ValueError(f"unsupported action: {action}")
        allowed, target = transitions[action]
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, session_id, step FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["status"] not in allowed:
                actual = None if row is None else row["status"]
                raise RuntimeError(f"cannot {action} simulation while status is {actual}")
            connection.execute(
                "UPDATE simulation_control SET status=? WHERE singleton_id=1", (target,)
            )
            connection.execute(
                "INSERT INTO logs VALUES (NULL, 'INFO', 'status_changed', ?, ?, ?, ?)",
                (f"{row['status']} -> {target}", row["session_id"], row["step"], _utc_now()),
            )
        return target

    def create_session(self) -> str:
        """Create a fresh safe session while preserving scenario and history."""

        self._ensure_exists()
        session_id = str(uuid.uuid4())
        timestamp = _utc_now()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT * FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            if runtime is None:
                raise RuntimeError("simulation_control row is missing")
            if runtime["status"] not in {"stopped", "completed"}:
                raise RuntimeError(
                    "a new session requires the current simulation to be stopped or completed"
                )
            points = tuple(
                ScenarioPoint(row[0], row[1], row[2])
                for row in connection.execute(
                    "SELECT sim_time_s, wind_speed_mps, load_power_kw "
                    "FROM scenario_points ORDER BY sim_time_s"
                )
            )
            environment = ScenarioCurve(points).at(float(runtime["start_s"]))
            pitch_row = connection.execute(
                "SELECT value FROM device_parameters "
                "WHERE device_id='WT01' AND name='pitch_feather_deg'"
            ).fetchone()
            if pitch_row is None:
                raise RuntimeError("WT01.pitch_feather_deg is missing")
            state = SimulationState(
                session_id=session_id,
                step=0,
                sim_time_s=float(runtime["start_s"]),
                sampled_at_utc=timestamp,
                wind_speed_mps=environment.wind_speed_mps,
                load_power_kw=environment.load_power_kw,
                wind_available_kw=0.0,
                wind_operating_limit_kw=0.0,
                wind_target_kw=0.0,
                wind_actual_kw=0.0,
                diesel_target_kw=0.0,
                diesel_actual_kw=0.0,
                pitch_actual_deg=float(pitch_row["value"]),
                wind_running=False,
                diesel_running=False,
                fault=False,
                power_imbalance_kw=environment.load_power_kw,
                controller_wind_enable=False,
                pitch_target_deg=float(pitch_row["value"]),
            )
            connection.execute(
                """UPDATE simulation_control
                   SET session_id=?, status='ready', sim_time_s=?, step=0
                   WHERE singleton_id=1""",
                (session_id, runtime["start_s"]),
            )
            connection.execute(
                """UPDATE control_state
                   SET wind_target_kw=0, diesel_target_kw=0,
                       dispatch_wind_enable=0, controller_wind_enable=0,
                       diesel_enable=0, pitch_target_deg=?,
                       controller_wind_available_kw=0,
                       controller_wind_operating_limit_kw=0,
                       last_wind_action_seq=NULL,
                       last_wind_action_step=NULL,
                       wind_action_applied_at_utc=NULL,
                       updated_at_utc=? WHERE singleton_id=1""",
                (state.pitch_actual_deg, timestamp),
            )
            connection.execute(
                f"""UPDATE current_state SET
                    session_id=?, step=?, sim_time_s=?, sampled_at_utc=?, wind_speed_mps=?,
                    load_power_kw=?, wind_available_kw=?, wind_operating_limit_kw=?,
                    wind_target_kw=?, wind_actual_kw=?, diesel_target_kw=?, diesel_actual_kw=?,
                    pitch_actual_deg=?, wind_running=?, diesel_running=?, fault=?,
                    power_imbalance_kw=?, updated_at_utc=? WHERE singleton_id=1""",
                _state_values(state, timestamp),
            )
            self._append_history(connection, state, timestamp)
            self._write_scada(
                connection,
                state,
                timestamp,
                {
                    "dispatch_wind_enable": False,
                    "controller_wind_enable": False,
                    "diesel_enable": False,
                    "pitch_target_deg": state.pitch_target_deg,
                },
            )
            connection.execute(
                "UPDATE connection_status SET connected=0, detail='new session; waiting for peer'"
            )
            connection.execute(
                "INSERT INTO logs VALUES (NULL, 'INFO', 'session_created', ?, ?, 0, ?)",
                ("new safe session; existing history preserved", session_id, timestamp),
            )
        return session_id

    def step_once(self) -> SimulationState | None:
        self._ensure_exists()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT * FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            if runtime is None:
                raise RuntimeError("simulation_control row is missing")
            if runtime["status"] != "running":
                raise RuntimeError(f"simulation is not running (status={runtime['status']})")
            next_time = runtime["sim_time_s"] + runtime["step_s"]
            if next_time > runtime["end_s"] + 1e-9:
                connection.execute(
                    "UPDATE simulation_control SET status='completed' WHERE singleton_id=1"
                )
                return None

            points = tuple(
                ScenarioPoint(row[0], row[1], row[2])
                for row in connection.execute(
                    "SELECT sim_time_s, wind_speed_mps, load_power_kw FROM scenario_points ORDER BY sim_time_s"
                )
            )
            environment = ScenarioCurve(points).at(next_time)
            previous_row = connection.execute(
                "SELECT * FROM current_state WHERE singleton_id=1"
            ).fetchone()
            control_row = connection.execute(
                "SELECT * FROM control_state WHERE singleton_id=1"
            ).fetchone()
            if previous_row is None or control_row is None:
                raise RuntimeError("database is missing current state or controls")
            parameters = {
                (row["device_id"], row["name"]): row["value"]
                for row in connection.execute("SELECT device_id, name, value FROM device_parameters")
            }
            wind = WindTurbineParameters(**{
                field.name: parameters[("WT01", field.name)]
                for field in fields(WindTurbineParameters)
            })
            diesel = DieselGeneratorParameters(**{
                field.name: parameters[("DG01", field.name)]
                for field in fields(DieselGeneratorParameters)
            })
            controls = ControlInputs(
                wind_target_kw=control_row["wind_target_kw"],
                diesel_target_kw=control_row["diesel_target_kw"],
                dispatch_wind_enable=bool(control_row["dispatch_wind_enable"]),
                controller_wind_enable=bool(control_row["controller_wind_enable"]),
                diesel_enable=bool(control_row["diesel_enable"]),
                pitch_target_deg=control_row["pitch_target_deg"],
                controller_wind_available_kw=control_row["controller_wind_available_kw"],
                controller_wind_operating_limit_kw=control_row[
                    "controller_wind_operating_limit_kw"
                ],
                last_wind_action_seq=control_row["last_wind_action_seq"],
                last_wind_action_step=control_row["last_wind_action_step"],
                wind_action_applied_at_utc=control_row["wind_action_applied_at_utc"],
            )
            previous_state = _state_from_row(previous_row, control_row)
            timestamp = _utc_now()
            if timestamp < previous_state.sampled_at_utc:
                connection.execute(
                    "INSERT INTO logs VALUES (NULL, 'WARNING', 'clock_adjusted_backwards', ?, ?, ?, ?)",
                    (f"previous={previous_state.sampled_at_utc}, current={timestamp}",
                     runtime["session_id"], runtime["step"], timestamp),
                )
            state = simulate_step(
                previous=previous_state,
                next_step=runtime["step"] + 1,
                next_sim_time_s=next_time,
                sampled_at_utc=timestamp,
                step_s=runtime["step_s"],
                wind_speed_mps=environment.wind_speed_mps,
                load_power_kw=environment.load_power_kw,
                controls=controls,
                wind=wind,
                diesel=diesel,
            )
            values = _state_values(state, timestamp)
            connection.execute(
                f"""UPDATE current_state SET
                    session_id=?, step=?, sim_time_s=?, sampled_at_utc=?, wind_speed_mps=?, load_power_kw=?,
                    wind_available_kw=?, wind_operating_limit_kw=?, wind_target_kw=?, wind_actual_kw=?, diesel_target_kw=?,
                    diesel_actual_kw=?, pitch_actual_deg=?, wind_running=?, diesel_running=?, fault=?,
                    power_imbalance_kw=?, updated_at_utc=? WHERE singleton_id=1""",
                values,
            )
            self._append_history(connection, state, timestamp)
            self._write_scada(connection, state, timestamp, controls)
            status = "completed" if next_time >= runtime["end_s"] - 1e-9 else "running"
            connection.execute(
                "UPDATE simulation_control SET sim_time_s=?, step=?, status=? WHERE singleton_id=1",
                (next_time, state.step, status),
            )
            return state

    @staticmethod
    def _append_history(connection: sqlite3.Connection, state: SimulationState, timestamp: str) -> None:
        connection.execute(
            f"INSERT INTO state_history ({STATE_COLUMNS}, recorded_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _state_values(state, timestamp),
        )

    @staticmethod
    def _write_scada(
        connection: sqlite3.Connection,
        state: SimulationState,
        timestamp: str,
        controls: Mapping[str, object] | ControlInputs,
    ) -> None:
        def control_value(name: str) -> object:
            if isinstance(controls, ControlInputs):
                return getattr(controls, name)
            return controls[name]

        points = (
            ("WT01.wind_speed_mps", "WT01", "telemetry", state.wind_speed_mps, "m/s"),
            ("LOAD01.load_power_kw", "LOAD01", "telemetry", state.load_power_kw, "kW"),
            ("WT01.wind_available_kw", "WT01", "telemetry", state.wind_available_kw, "kW"),
            ("WT01.wind_operating_limit_kw", "WT01", "telemetry", state.wind_operating_limit_kw, "kW"),
            ("WT01.wind_actual_kw", "WT01", "telemetry", state.wind_actual_kw, "kW"),
            ("DG01.diesel_actual_kw", "DG01", "telemetry", state.diesel_actual_kw, "kW"),
            ("GRID.power_imbalance_kw", "GRID", "telemetry", state.power_imbalance_kw, "kW"),
            ("WT01.wind_running", "WT01", "signal", state.wind_running, "bool"),
            ("DG01.diesel_running", "DG01", "signal", state.diesel_running, "bool"),
            ("GRID.fault", "GRID", "signal", state.fault, "bool"),
            ("WT01.wind_target_kw", "WT01", "setpoint", state.wind_target_kw, "kW"),
            ("DG01.diesel_target_kw", "DG01", "setpoint", state.diesel_target_kw, "kW"),
            ("WT01.pitch_target_deg", "WT01", "setpoint", control_value("pitch_target_deg"), "deg"),
            ("WT01.dispatch_wind_enable", "WT01", "control", bool(control_value("dispatch_wind_enable")), "bool"),
            ("WT01.controller_wind_enable", "WT01", "control", bool(control_value("controller_wind_enable")), "bool"),
            ("DG01.diesel_enable", "DG01", "control", bool(control_value("diesel_enable")), "bool"),
            ("WT01.pitch_actual_deg", "WT01", "telemetry", state.pitch_actual_deg, "deg"),
        )
        for point_id, device_id, category, value, unit in points:
            value_json = _json_value(value)
            connection.execute(
                """INSERT INTO scada_points
                   (point_id, device_id, category, value_json, unit, version, updated_step,
                    sampled_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(point_id) DO UPDATE SET value_json=excluded.value_json,
                   version=scada_points.version+1, updated_step=excluded.updated_step,
                   sampled_at_utc=excluded.sampled_at_utc,
                   updated_at_utc=excluded.updated_at_utc""",
                (point_id, device_id, category, value_json, unit, state.step,
                 state.sampled_at_utc, timestamp),
            )
            connection.execute(
                "INSERT INTO scada_history VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (state.session_id, state.step, point_id, value_json,
                 state.sampled_at_utc, timestamp),
            )

    @staticmethod
    def _normalized_updates(
        parameters: Mapping[str, object], allowed: frozenset[str]
    ) -> dict[str, float]:
        if not isinstance(parameters, Mapping) or not parameters:
            raise ValueError("invalid_parameters")
        if any(not isinstance(name, str) for name in parameters):
            raise ValueError("invalid_parameter_name")
        if not set(parameters).issubset(allowed):
            raise ValueError("unauthorized_parameter")
        normalized: dict[str, float] = {}
        for name, raw_value in parameters.items():
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"invalid_parameter_value:{name}")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid_parameter_value:{name}")
            normalized[name] = value
        return normalized

    @staticmethod
    def _validate_parameter_relationships(values: Mapping[str, float]) -> None:
        positive = (
            "a_step_s",
            "a_poll_s",
            "wind_ramp_up_kw_per_s",
            "wind_ramp_down_kw_per_s",
            "diesel_ramp_up_kw_per_s",
            "diesel_ramp_down_kw_per_s",
            "b_poll_s",
            "b_dispatch_s",
            "wind_rated_power_kw",
            "c_control_s",
            "c_timeout_s",
        )
        if any(values[name] <= 0 for name in positive):
            raise ValueError("parameter_must_be_positive")

        # Reuse the model constructors so GUI and TCP updates cannot bypass the
        # same physical relationships used by step_once.
        WindTurbineParameters(
            rated_power_kw=values["wind_rated_power_kw"],
            cut_in_speed_mps=values["cut_in_speed_mps"],
            rated_speed_mps=values["rated_speed_mps"],
            cut_out_speed_mps=values["cut_out_speed_mps"],
            pitch_full_output_deg=values["pitch_full_output_deg"],
            pitch_feather_deg=values["pitch_feather_deg"],
            ramp_up_kw_per_s=values["wind_ramp_up_kw_per_s"],
            ramp_down_kw_per_s=values["wind_ramp_down_kw_per_s"],
        )
        DieselGeneratorParameters(
            min_power_kw=values["diesel_min_power_kw"],
            max_power_kw=values["diesel_max_power_kw"],
            ramp_up_kw_per_s=values["diesel_ramp_up_kw_per_s"],
            ramp_down_kw_per_s=values["diesel_ramp_down_kw_per_s"],
        )
        if values["reserve_kw"] > values["diesel_max_power_kw"]:
            raise ValueError("reserve_kw_must_not_exceed_diesel_max_power_kw")
        if values["c_timeout_s"] < values["c_control_s"]:
            raise ValueError("c_timeout_s_must_not_be_below_c_control_s")

    def _update_parameters(
        self,
        connection: sqlite3.Connection,
        parameters: Mapping[str, object],
        *,
        allowed: frozenset[str],
        owner: str,
        source: str,
    ) -> None:
        updates = self._normalized_updates(parameters, allowed)
        rows = connection.execute(
            "SELECT name, value FROM parameter_state"
        ).fetchall()
        current = {str(row["name"]): float(row["value"]) for row in rows}
        if set(current) != set(PARAMETER_SPECS):
            raise RuntimeError("parameter_state does not match the v5 parameter registry")
        prospective = {**current, **updates}
        self._validate_parameter_relationships(prospective)
        changed_names = {name for name, value in updates.items() if value != current[name]}

        # Re-sending an already synchronized snapshot is valid, but it is not
        # a parameter change and must not inflate the history table.
        if not changed_names:
            return

        timestamp = _utc_now()
        for name in sorted(changed_names):
            unit, expected_owner, _ = PARAMETER_SPECS[name]
            if expected_owner != owner:
                raise ValueError("unauthorized_parameter")
            old_value = current[name]
            new_value = updates[name]
            connection.execute(
                """UPDATE parameter_state
                   SET value=?, source=?, updated_at_utc=? WHERE name=?""",
                (new_value, source, timestamp, name),
            )
            connection.execute(
                """INSERT INTO parameter_history
                   (name, old_value, new_value, unit, owner, source, changed_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, old_value, new_value, unit, owner, source, timestamp),
            )
            if name in DEVICE_PARAMETER_STORAGE:
                device_id, storage_name = DEVICE_PARAMETER_STORAGE[name]
                connection.execute(
                    """UPDATE device_parameters SET value=?
                       WHERE device_id=? AND name=?""",
                    (new_value, device_id, storage_name),
                )
            elif name in SIMULATION_PARAMETER_STORAGE:
                column = SIMULATION_PARAMETER_STORAGE[name]
                connection.execute(
                    f"UPDATE simulation_control SET {column}=? WHERE singleton_id=1",
                    (new_value,),
                )

        c_physical_names = C_PARAMETER_NAMES - {"c_control_s", "c_timeout_s"}
        c_action_invalidated = owner == "C" and bool(
            changed_names.intersection(c_physical_names)
        )
        if c_action_invalidated:
            # A cannot recompute a new C action.  Invalidate the cached action
            # until C publishes a wind_action based on the updated parameters.
            connection.execute(
                """UPDATE control_state
                   SET controller_wind_enable=0, pitch_target_deg=?,
                       controller_wind_available_kw=0,
                       controller_wind_operating_limit_kw=0,
                       last_wind_action_seq=NULL,
                       last_wind_action_step=NULL,
                       wind_action_applied_at_utc=NULL,
                       updated_at_utc=? WHERE singleton_id=1""",
                (prospective["pitch_feather_deg"], timestamp),
            )

        runtime = connection.execute(
            "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
        ).fetchone()
        if runtime is None:
            raise RuntimeError("simulation_control row is missing")
        names = ",".join(sorted(changed_names))
        connection.execute(
            "INSERT INTO logs VALUES (NULL, 'INFO', 'parameters_updated', ?, ?, ?, ?)",
            (f"source={source}; names={names}", runtime["session_id"], runtime["step"], timestamp),
        )
        if c_action_invalidated:
            connection.execute(
                "INSERT INTO logs VALUES (NULL, 'INFO', 'c_action_invalidated', ?, ?, ?, ?)",
                (
                    "waiting for wind_action calculated with updated C parameters",
                    runtime["session_id"],
                    runtime["step"],
                    timestamp,
                ),
            )

    def next_server_seq(self) -> int:
        self._ensure_exists()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE simulation_control SET server_seq=server_seq+1 WHERE singleton_id=1"
            )
            return connection.execute(
                "SELECT server_seq FROM simulation_control WHERE singleton_id=1"
            ).fetchone()[0]

    def next_client_command_seq(self, source: str, session_id: str) -> int:
        """Return a safe lower bound for the client's next command sequence.

        STM32 has no trustworthy wall clock and may reboot while A keeps the
        same simulation session. Returning the persisted high-water mark in a
        state response lets C resume without weakening duplicate detection.
        """
        if source not in {"B", "C"}:
            raise ValueError("invalid client source")
        self._ensure_exists()
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT MAX(seq) FROM commands WHERE session_id=? AND source=?",
                (session_id, source),
            ).fetchone()
        highest = row[0] if row is not None else None
        return 0 if highest is None else int(highest) + 1

    def apply_command(self, message: dict[str, object]) -> CommandResult:
        self._ensure_exists()
        payload = message["payload"]
        assert isinstance(payload, dict)
        session_id = message.get("session_id")
        source = str(message["source"])
        seq = int(message["seq"])
        message_type = str(message["type"])
        timestamp = _utc_now()
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            if runtime is None:
                raise RuntimeError("simulation_control row is missing")
            payload_json = _json_value(payload)
            existing = connection.execute(
                """SELECT message_type, payload_json, accepted, reason FROM commands
                   WHERE session_id IS ? AND source=? AND seq=?""",
                (session_id, source, seq),
            ).fetchone()
            if existing is not None:
                try:
                    existing_payload = _json_value(json.loads(existing["payload_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    existing_payload = str(existing["payload_json"])
                if existing["message_type"] == message_type and existing_payload == payload_json:
                    return CommandResult(bool(existing["accepted"]), existing["reason"], True)
                connection.execute(
                    "INSERT INTO logs VALUES (NULL, 'WARNING', 'command_rejected', ?, ?, ?, ?)",
                    ("seq_conflict", runtime["session_id"], runtime["step"], timestamp),
                )
                return CommandResult(False, "seq_conflict")
            result = self._validate_and_apply(
                connection,
                runtime,
                message_type,
                source,
                session_id,
                seq,
                payload,
                timestamp,
            )
            connection.execute(
                """INSERT INTO commands
                   (session_id, source, seq, message_type, payload_json, accepted, reason, received_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, source, seq, message_type, payload_json,
                 int(result.accepted), result.reason, timestamp),
            )
            if not result.accepted:
                connection.execute(
                    "INSERT INTO logs VALUES (NULL, 'WARNING', 'command_rejected', ?, ?, ?, ?)",
                    (result.reason, runtime["session_id"], runtime["step"], timestamp),
                )
            return result

    def _validate_and_apply(
        self,
        connection: sqlite3.Connection,
        runtime: sqlite3.Row,
        message_type: str,
        source: str,
        session_id: object,
        seq: int,
        payload: dict[str, object],
        applied_at_utc: str,
    ) -> CommandResult:
        if session_id != runtime["session_id"]:
            return CommandResult(False, "stale_session")
        expected_source = {"dispatch": "B", "wind_action": "C"}.get(message_type)
        if message_type == "parameter_update":
            if source not in {"B", "C"}:
                return CommandResult(False, "unauthorized_message_type")
        elif expected_source is None or source != expected_source:
            return CommandResult(False, "unauthorized_message_type")
        max_seq = connection.execute(
            "SELECT MAX(seq) FROM commands WHERE session_id=? AND source=?",
            (session_id, source),
        ).fetchone()[0]
        if max_seq is not None and seq <= max_seq:
            return CommandResult(False, "out_of_order")
        try:
            if message_type == "dispatch":
                self._apply_dispatch(
                    connection,
                    payload,
                    session_id=str(runtime["session_id"]),
                    applied_step=int(runtime["step"]),
                    applied_at_utc=applied_at_utc,
                )
            elif message_type == "wind_action":
                self._apply_wind_action(
                    connection,
                    payload,
                    seq=seq,
                    session_id=str(runtime["session_id"]),
                    applied_step=int(runtime["step"]),
                    applied_at_utc=applied_at_utc,
                )
            else:
                self._apply_parameter_update(connection, payload, source)
        except ValueError as exc:
            return CommandResult(False, str(exc))
        return CommandResult(True, "accepted")

    @staticmethod
    def _strict_bool(payload: dict[str, object], name: str) -> bool:
        value = payload.get(name)
        if type(value) is not bool:
            raise ValueError(f"invalid_{name}")
        return value

    @staticmethod
    def _strict_number(payload: dict[str, object], name: str) -> float:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid_{name}")
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise ValueError(f"invalid_{name}")
        return result

    def _apply_dispatch(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, object],
        *,
        session_id: str,
        applied_step: int,
        applied_at_utc: str,
    ) -> None:
        expected = {"wind_target_kw", "diesel_target_kw", "wind_enable", "diesel_enable"}
        if set(payload) != expected:
            raise ValueError("invalid_dispatch_fields")
        wind_target = self._strict_number(payload, "wind_target_kw")
        diesel_target = self._strict_number(payload, "diesel_target_kw")
        wind_enable = self._strict_bool(payload, "wind_enable")
        diesel_enable = self._strict_bool(payload, "diesel_enable")
        limits = {
            row["device_id"]: row["value"]
            for row in connection.execute(
                "SELECT device_id, value FROM device_parameters WHERE name IN ('rated_power_kw', 'max_power_kw')"
            )
        }
        if wind_target > limits["WT01"]:
            raise ValueError("wind_target_out_of_range")
        if diesel_target > limits["DG01"]:
            raise ValueError("diesel_target_out_of_range")
        connection.execute(
            """UPDATE control_state SET wind_target_kw=?, diesel_target_kw=?,
               dispatch_wind_enable=?, diesel_enable=?, updated_at_utc=? WHERE singleton_id=1""",
            (
                wind_target,
                diesel_target,
                int(wind_enable),
                int(diesel_enable),
                applied_at_utc,
            ),
        )
        for point in (
            ("WT01.wind_target_kw", "WT01", "setpoint", wind_target, "kW"),
            ("DG01.diesel_target_kw", "DG01", "setpoint", diesel_target, "kW"),
            ("WT01.dispatch_wind_enable", "WT01", "control", wind_enable, "bool"),
            ("DG01.diesel_enable", "DG01", "control", diesel_enable, "bool"),
        ):
            self._record_scada_change(
                connection,
                *point,
                session_id=session_id,
                step=applied_step,
                timestamp=applied_at_utc,
            )

    def _apply_wind_action(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, object],
        *,
        seq: int,
        session_id: str,
        applied_step: int,
        applied_at_utc: str,
    ) -> None:
        expected = {
            "wind_enable",
            "pitch_target_deg",
            "wind_available_kw",
            "wind_operating_limit_kw",
        }
        if set(payload) != expected:
            raise ValueError("invalid_wind_action_fields")
        wind_enable = self._strict_bool(payload, "wind_enable")
        pitch = self._strict_number(payload, "pitch_target_deg")
        available = self._strict_number(payload, "wind_available_kw")
        operating_limit = self._strict_number(payload, "wind_operating_limit_kw")
        row = connection.execute(
            "SELECT value FROM device_parameters WHERE device_id='WT01' AND name='pitch_feather_deg'"
        ).fetchone()
        if row is None or pitch > row["value"]:
            raise ValueError("pitch_target_out_of_range")
        rated_row = connection.execute(
            "SELECT value FROM device_parameters WHERE device_id='WT01' AND name='rated_power_kw'"
        ).fetchone()
        if rated_row is None or available > rated_row["value"]:
            raise ValueError("wind_available_out_of_range")
        if operating_limit > available:
            raise ValueError("wind_operating_limit_out_of_range")
        connection.execute(
            """UPDATE control_state SET controller_wind_enable=?, pitch_target_deg=?,
               controller_wind_available_kw=?, controller_wind_operating_limit_kw=?,
               last_wind_action_seq=?, last_wind_action_step=?,
               wind_action_applied_at_utc=?,
               updated_at_utc=? WHERE singleton_id=1""",
            (
                int(wind_enable),
                pitch,
                available,
                operating_limit,
                seq,
                applied_step,
                applied_at_utc,
                applied_at_utc,
            ),
        )
        for point in (
            ("WT01.controller_wind_enable", "WT01", "control", wind_enable, "bool"),
            ("WT01.pitch_target_deg", "WT01", "setpoint", pitch, "deg"),
            ("WT01.wind_available_kw", "WT01", "telemetry", available, "kW"),
            (
                "WT01.wind_operating_limit_kw",
                "WT01",
                "telemetry",
                operating_limit,
                "kW",
            ),
        ):
            self._record_scada_change(
                connection,
                *point,
                session_id=session_id,
                step=applied_step,
                timestamp=applied_at_utc,
            )

    @staticmethod
    def _record_scada_change(
        connection: sqlite3.Connection,
        point_id: str,
        device_id: str,
        category: str,
        value: object,
        unit: str,
        *,
        session_id: str,
        step: int,
        timestamp: str,
    ) -> None:
        value_json = _json_value(value)
        connection.execute(
            """INSERT INTO scada_points
               (point_id, device_id, category, value_json, unit, version, updated_step,
                sampled_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(point_id) DO UPDATE SET value_json=excluded.value_json,
               version=scada_points.version+1, updated_step=excluded.updated_step,
               sampled_at_utc=excluded.sampled_at_utc,
               updated_at_utc=excluded.updated_at_utc""",
            (point_id, device_id, category, value_json, unit, step, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO scada_history VALUES (NULL, ?, ?, ?, ?, ?, ?)",
            (session_id, step, point_id, value_json, timestamp, timestamp),
        )

    def _apply_parameter_update(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, object],
        source: str,
    ) -> None:
        if set(payload) != {"parameters"} or not isinstance(payload["parameters"], dict):
            raise ValueError("invalid_parameter_update_fields")
        if source == "B":
            allowed = B_PARAMETER_NAMES
            source_label = "B_tcp"
        elif source == "C":
            allowed = C_PARAMETER_NAMES
            source_label = "C_tcp"
        else:  # Defense in depth; the envelope validator already rejects this.
            raise ValueError("unauthorized_message_type")
        self._update_parameters(
            connection,
            payload["parameters"],
            allowed=allowed,
            owner=source,
            source=source_label,
        )

    def mark_connection(self, peer: str, connected: bool, detail: str) -> None:
        if peer not in {"B", "C"}:
            return
        self._ensure_exists()
        timestamp = _utc_now()
        with _connect(self.path) as connection:
            previous = connection.execute(
                "SELECT connected FROM connection_status WHERE peer=?", (peer,)
            ).fetchone()
            runtime = connection.execute(
                "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            connection.execute(
                """UPDATE connection_status SET connected=?, last_seen_at_utc=?, detail=? WHERE peer=?""",
                (int(connected), timestamp, detail, peer),
            )
            if (
                previous is not None
                and runtime is not None
                and bool(previous["connected"]) != bool(connected)
            ):
                event = "peer_connected" if connected else "peer_disconnected"
                connection.execute(
                    "INSERT INTO logs VALUES (NULL, 'INFO', ?, ?, ?, ?, ?)",
                    (
                        event,
                        f"{peer}: {detail}",
                        runtime["session_id"],
                        runtime["step"],
                        timestamp,
                    ),
                )

    def log(self, level: str, event: str, message: str) -> None:
        runtime = self.runtime()
        with _connect(self.path) as connection:
            connection.execute(
                "INSERT INTO logs VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (level, event, message, runtime["session_id"], runtime["step"], _utc_now()),
            )
