"""SQLite persistence shared by the simulator, TCP server and future GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
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


SCHEMA = """
PRAGMA user_version = 4;

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
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


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
            connection.execute(
                """INSERT INTO control_state VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            self._write_scada(connection, state, timestamp)
            connection.executemany(
                "INSERT INTO connection_status VALUES (?, 0, NULL, 'not connected')",
                [("B",), ("C",)],
            )
            connection.execute(
                "INSERT INTO logs VALUES (NULL, 'INFO', 'database_initialized', ?, ?, 0, ?)",
                (f"parameter_status={config.parameter_status}", session_id, timestamp),
            )
        return session_id

    def _ensure_exists(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"database not initialized: {self.path}")

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
            return _state_from_row(row)

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
            )
            previous_state = _state_from_row(previous_row)
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
            self._write_scada(connection, state, timestamp)
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
    def _write_scada(connection: sqlite3.Connection, state: SimulationState, timestamp: str) -> None:
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
            existing = connection.execute(
                "SELECT accepted, reason FROM commands WHERE session_id IS ? AND source=? AND seq=?",
                (session_id, source, seq),
            ).fetchone()
            if existing is not None:
                return CommandResult(bool(existing["accepted"]), existing["reason"], True)
            runtime = connection.execute(
                "SELECT session_id, step FROM simulation_control WHERE singleton_id=1"
            ).fetchone()
            result = self._validate_and_apply(connection, runtime, message_type, source, session_id, seq, payload)
            connection.execute(
                """INSERT INTO commands
                   (session_id, source, seq, message_type, payload_json, accepted, reason, received_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, source, seq, message_type, _json_value(payload),
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
    ) -> CommandResult:
        if session_id != runtime["session_id"]:
            return CommandResult(False, "stale_session")
        expected_source = {"dispatch": "B", "wind_action": "C"}.get(message_type)
        if expected_source is None or source != expected_source:
            return CommandResult(False, "unauthorized_message_type")
        max_seq = connection.execute(
            "SELECT MAX(seq) FROM commands WHERE session_id=? AND source=?",
            (session_id, source),
        ).fetchone()[0]
        if max_seq is not None and seq <= max_seq:
            return CommandResult(False, "out_of_order")
        try:
            if message_type == "dispatch":
                self._apply_dispatch(connection, payload)
            else:
                self._apply_wind_action(connection, payload)
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

    def _apply_dispatch(self, connection: sqlite3.Connection, payload: dict[str, object]) -> None:
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
            (wind_target, diesel_target, int(wind_enable), int(diesel_enable), _utc_now()),
        )

    def _apply_wind_action(self, connection: sqlite3.Connection, payload: dict[str, object]) -> None:
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
               updated_at_utc=? WHERE singleton_id=1""",
            (int(wind_enable), pitch, available, operating_limit, _utc_now()),
        )

    def mark_connection(self, peer: str, connected: bool, detail: str) -> None:
        if peer not in {"B", "C"}:
            return
        self._ensure_exists()
        with _connect(self.path) as connection:
            connection.execute(
                """UPDATE connection_status SET connected=?, last_seen_at_utc=?, detail=? WHERE peer=?""",
                (int(connected), _utc_now(), detail, peer),
            )

    def log(self, level: str, event: str, message: str) -> None:
        runtime = self.runtime()
        with _connect(self.path) as connection:
            connection.execute(
                "INSERT INTO logs VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (level, event, message, runtime["session_id"], runtime["step"], _utc_now()),
            )
