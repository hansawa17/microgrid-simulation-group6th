"""SQLite repository for B's local EMS state, parameters, commands and history."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physical_parameters (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wind_rated_kw REAL NOT NULL DEFAULT 100.0 CHECK (wind_rated_kw > 0),
    wind_cut_in_mps REAL NOT NULL DEFAULT 3.0 CHECK (wind_cut_in_mps >= 0),
    wind_rated_speed_mps REAL NOT NULL DEFAULT 12.0 CHECK (wind_rated_speed_mps > wind_cut_in_mps),
    wind_cut_out_mps REAL NOT NULL DEFAULT 25.0 CHECK (wind_cut_out_mps > wind_rated_speed_mps),
    pitch_min_deg REAL NOT NULL DEFAULT 0.0 CHECK (pitch_min_deg >= 0),
    pitch_max_deg REAL NOT NULL DEFAULT 90.0 CHECK (pitch_max_deg >= pitch_min_deg),
    wind_ramp_up_kw_s REAL NOT NULL DEFAULT 40.0 CHECK (wind_ramp_up_kw_s > 0),
    wind_ramp_down_kw_s REAL NOT NULL DEFAULT 60.0 CHECK (wind_ramp_down_kw_s > 0),
    diesel_min_kw REAL NOT NULL DEFAULT 20.0 CHECK (diesel_min_kw >= 0),
    diesel_max_kw REAL NOT NULL DEFAULT 120.0 CHECK (diesel_max_kw >= diesel_min_kw),
    diesel_ramp_up_kw_s REAL NOT NULL DEFAULT 30.0 CHECK (diesel_ramp_up_kw_s > 0),
    diesel_ramp_down_kw_s REAL NOT NULL DEFAULT 40.0 CHECK (diesel_ramp_down_kw_s > 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatch_parameters (
    id INTEGER PRIMARY KEY CHECK (id = 1), wind_min_kw REAL NOT NULL CHECK (wind_min_kw >= 0),
    wind_max_kw REAL NOT NULL CHECK (wind_max_kw >= wind_min_kw), diesel_max_kw REAL NOT NULL CHECK (diesel_max_kw >= 0),
    reserve_kw REAL NOT NULL DEFAULT 10.0 CHECK (reserve_kw >= 0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ems_runtime_config (
    id INTEGER PRIMARY KEY CHECK (id = 1), poll_period_s REAL NOT NULL DEFAULT 1.0 CHECK (poll_period_s > 0),
    dispatch_period_s REAL NOT NULL DEFAULT 5.0 CHECK (dispatch_period_s > 0), closed_loop INTEGER NOT NULL DEFAULT 1 CHECK (closed_loop IN (0, 1)),
    command_timeout_s REAL DEFAULT 3.0 CHECK (command_timeout_s IS NULL OR command_timeout_s > 0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK (step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0), wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);
CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK (step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0), wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);
CREATE TABLE IF NOT EXISTS dispatch_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK (step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0), source TEXT NOT NULL, seq INTEGER NOT NULL CHECK (seq >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0), diesel_target_kw REAL NOT NULL CHECK (diesel_target_kw >= 0),
    wind_enable INTEGER NOT NULL CHECK (wind_enable IN (0,1)), diesel_enable INTEGER NOT NULL CHECK (diesel_enable IN (0,1)),
    status TEXT NOT NULL, reason TEXT NOT NULL, ack_accepted INTEGER CHECK (ack_accepted IN (0,1)),
    ack_reason TEXT, ack_received_at_utc TEXT, created_at_utc TEXT NOT NULL, UNIQUE(session_id, source, seq)
);
CREATE TABLE IF NOT EXISTS dispatch_evaluation (
    id INTEGER PRIMARY KEY AUTOINCREMENT, command_id INTEGER NOT NULL REFERENCES dispatch_commands(id),
    target_unserved_kw REAL NOT NULL CHECK (target_unserved_kw >= 0), target_surplus_kw REAL NOT NULL CHECK (target_surplus_kw >= 0),
    actual_unserved_kw REAL, actual_surplus_kw REAL, evaluated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL,
    session_id TEXT, step INTEGER, created_at_utc TEXT NOT NULL
);
"""

UNIFIED_PHYSICAL = {
    "wind_rated_kw": 100.0,
    "wind_cut_in_mps": 3.0,
    "wind_rated_speed_mps": 12.0,
    "wind_cut_out_mps": 25.0,
    "pitch_min_deg": 0.0,
    "pitch_max_deg": 90.0,
    "wind_ramp_up_kw_s": 40.0,
    "wind_ramp_down_kw_s": 60.0,
    "diesel_min_kw": 20.0,
    "diesel_max_kw": 120.0,
    "diesel_ramp_up_kw_s": 30.0,
    "diesel_ramp_down_kw_s": 40.0,
}
UNIFIED_DISPATCH = {"wind_min_kw": 0.0, "wind_max_kw": 100.0, "diesel_max_kw": 120.0, "reserve_kw": 10.0}
UNIFIED_RUNTIME = {"poll_period_s": 1.0, "dispatch_period_s": 5.0, "closed_loop": 1, "command_timeout_s": 3.0}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EMSRepository:
    """Short-transaction SQLite repository; every operation gets its own connection."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _migrate_to_v3(self, conn: sqlite3.Connection) -> None:
        current = self._columns(conn, "current_state")
        if "wind_available_kw" not in current:
            conn.execute("ALTER TABLE current_state ADD COLUMN wind_available_kw REAL NOT NULL DEFAULT 0.0")
        if "wind_operating_limit_kw" not in current:
            conn.execute("ALTER TABLE current_state ADD COLUMN wind_operating_limit_kw REAL NOT NULL DEFAULT 0.0")
        history = self._columns(conn, "state_history")
        if "wind_available_kw" not in history:
            conn.execute("ALTER TABLE state_history ADD COLUMN wind_available_kw REAL NOT NULL DEFAULT 0.0")
        if "wind_operating_limit_kw" not in history:
            conn.execute("ALTER TABLE state_history ADD COLUMN wind_operating_limit_kw REAL NOT NULL DEFAULT 0.0")
        commands = self._columns(conn, "dispatch_commands")
        if "ack_accepted" not in commands:
            conn.execute("ALTER TABLE dispatch_commands ADD COLUMN ack_accepted INTEGER CHECK (ack_accepted IN (0,1))")
        if "ack_reason" not in commands:
            conn.execute("ALTER TABLE dispatch_commands ADD COLUMN ack_reason TEXT")
        if "ack_received_at_utc" not in commands:
            conn.execute("ALTER TABLE dispatch_commands ADD COLUMN ack_received_at_utc TEXT")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version','3') ON CONFLICT(key) DO UPDATE SET value='3'")

    def _migrate_to_v4(self, conn: sqlite3.Connection) -> None:
        cols = self._columns(conn, "physical_parameters")
        if not cols:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS physical_parameters (
                id INTEGER PRIMARY KEY CHECK (id = 1), wind_rated_kw REAL NOT NULL DEFAULT 100.0,
                wind_cut_in_mps REAL NOT NULL DEFAULT 3.0, wind_rated_speed_mps REAL NOT NULL DEFAULT 12.0,
                wind_cut_out_mps REAL NOT NULL DEFAULT 25.0, pitch_min_deg REAL NOT NULL DEFAULT 0.0,
                pitch_max_deg REAL NOT NULL DEFAULT 90.0, wind_ramp_up_kw_s REAL NOT NULL DEFAULT 40.0,
                wind_ramp_down_kw_s REAL NOT NULL DEFAULT 60.0, diesel_min_kw REAL NOT NULL DEFAULT 20.0,
                diesel_max_kw REAL NOT NULL DEFAULT 120.0, diesel_ramp_up_kw_s REAL NOT NULL DEFAULT 30.0,
                diesel_ramp_down_kw_s REAL NOT NULL DEFAULT 40.0, updated_at TEXT NOT NULL
            );
            """)
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version','4') ON CONFLICT(key) DO UPDATE SET value='4'")

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            version_row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            version = 0 if version_row is None else int(version_row[0])
            if version < 3:
                self._migrate_to_v3(conn)
            if version < 4:
                self._migrate_to_v4(conn)
            now = utc_now()
            cols = ", ".join(UNIFIED_PHYSICAL.keys())
            placeholders = ", ".join("?" for _ in UNIFIED_PHYSICAL)
            values = tuple(UNIFIED_PHYSICAL.values()) + (now,)
            updates = ", ".join(f"{k}=excluded.{k}" for k in UNIFIED_PHYSICAL)
            conn.execute(f"INSERT INTO physical_parameters(id,{cols},updated_at) VALUES(1,{placeholders},?) ON CONFLICT(id) DO UPDATE SET {updates},updated_at=excluded.updated_at", values)
            conn.execute(
                """INSERT INTO dispatch_parameters
                   (id, wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw, updated_at)
                   VALUES (1, 0.0, 100.0, 120.0, 10.0, ?)
                   ON CONFLICT(id) DO UPDATE SET wind_min_kw=0.0,
                   wind_max_kw=100.0, diesel_max_kw=120.0, reserve_kw=10.0, updated_at=excluded.updated_at""",
                (now,),
            )
            conn.execute(
                """INSERT INTO ems_runtime_config
                   (id, poll_period_s, dispatch_period_s, closed_loop, command_timeout_s, updated_at)
                   VALUES (1, 1.0, 5.0, 1, 3.0, ?)
                   ON CONFLICT(id) DO UPDATE SET poll_period_s=1.0,
                   dispatch_period_s=5.0, closed_loop=1, command_timeout_s=3.0, updated_at=excluded.updated_at""",
                (now,),
            )
            conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version','4') ON CONFLICT(key) DO UPDATE SET value='4'")

    def set_parameters(self, *, wind_min_kw: float, wind_max_kw: float, diesel_max_kw: float, reserve_kw: float = 10.0) -> None:
        requested = {"wind_min_kw": float(wind_min_kw), "wind_max_kw": float(wind_max_kw), "diesel_max_kw": float(diesel_max_kw), "reserve_kw": float(reserve_kw)}
        if requested != UNIFIED_DISPATCH:
            raise ValueError("EMS dispatch parameters are fixed to the unified project baseline")
        self.initialize()

    def get_parameters(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM dispatch_parameters WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("dispatch parameters are not initialized")
        return row

    def get_physical_parameters(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM physical_parameters WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("physical parameters are not initialized")
        return row

    def set_runtime_config(self, *, poll_period_s: float = 1.0, dispatch_period_s: float = 5.0,
                           closed_loop: bool = True, command_timeout_s: Optional[float] = 3.0) -> None:
        requested = {
            "poll_period_s": float(poll_period_s), "dispatch_period_s": float(dispatch_period_s),
            "closed_loop": int(closed_loop), "command_timeout_s": None if command_timeout_s is None else float(command_timeout_s),
        }
        if requested != UNIFIED_RUNTIME:
            raise ValueError("EMS runtime configuration is fixed to the unified project baseline")
        self.initialize()

    def get_runtime_config(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM ems_runtime_config WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("runtime config is not initialized")
        return row

    def save_state(self, state: Mapping[str, object]) -> None:
        if is_dataclass(state):
            state = asdict(state)
        required = (
            "session_id", "step", "sim_time_s", "wind_speed_mps", "wind_available_kw",
            "wind_operating_limit_kw", "load_power_kw", "wind_actual_kw", "diesel_actual_kw",
            "wind_target_kw", "wind_running", "fault", "sampled_at_utc", "received_at_utc", "received_age_s",
        )
        missing = [key for key in required if key not in state]
        if missing:
            raise ValueError(f"missing state fields: {', '.join(missing)}")
        values = tuple(state[key] for key in required)
        pitch = state.get("pitch_actual_deg")
        if pitch is None:
            raise ValueError("pitch_actual_deg is required by the current TCP state contract")
        db_values = values[:10] + (pitch,) + values[10:]
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO current_state(id,session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,
                   load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,pitch_actual_deg,wind_running,fault,
                   sampled_at_utc,received_at_utc,received_age_s)
                   VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,
                   step=excluded.step,sim_time_s=excluded.sim_time_s,wind_speed_mps=excluded.wind_speed_mps,
                   wind_available_kw=excluded.wind_available_kw,wind_operating_limit_kw=excluded.wind_operating_limit_kw,
                   load_power_kw=excluded.load_power_kw,wind_actual_kw=excluded.wind_actual_kw,diesel_actual_kw=excluded.diesel_actual_kw,
                   wind_target_kw=excluded.wind_target_kw,pitch_actual_deg=excluded.pitch_actual_deg,wind_running=excluded.wind_running,
                   fault=excluded.fault,sampled_at_utc=excluded.sampled_at_utc,received_at_utc=excluded.received_at_utc,received_age_s=excluded.received_age_s""",
                db_values,
            )
            conn.execute(
                """INSERT INTO state_history(session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,
                   load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,pitch_actual_deg,wind_running,fault,
                   sampled_at_utc,received_at_utc,received_age_s) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                db_values,
            )

    def get_current_state(self) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM current_state WHERE id=1").fetchone()

    def record_command(self, command: Mapping[str, object]) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO dispatch_commands(session_id,step,sim_time_s,source,seq,wind_target_kw,diesel_target_kw,
                   wind_enable,diesel_enable,status,reason,ack_accepted,ack_reason,ack_received_at_utc,created_at_utc)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (command["session_id"], command["step"], command["sim_time_s"], command.get("source", "B"), command["seq"],
                 command["wind_target_kw"], command["diesel_target_kw"], int(command["wind_enable"]), int(command["diesel_enable"]),
                 command.get("status", "generated"), command.get("reason", ""), command.get("ack_accepted"),
                 command.get("ack_reason"), command.get("ack_received_at_utc"), command.get("created_at_utc", utc_now())),
            )
            return int(cur.lastrowid)

    def record_evaluation(self, *, command_id: int, target_unserved_kw: float, target_surplus_kw: float,
                          actual_unserved_kw: Optional[float] = None, actual_surplus_kw: Optional[float] = None) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO dispatch_evaluation(command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw,
                   actual_surplus_kw,evaluated_at_utc) VALUES(?,?,?,?,?,?)""",
                (command_id, target_unserved_kw, target_surplus_kw, actual_unserved_kw, actual_surplus_kw, utc_now()),
            )
            return int(cur.lastrowid)

    def record_log(self, level: str, event_type: str, message: str, *, session_id: Optional[str] = None,
                   step: Optional[int] = None) -> None:
        with self.connection() as conn:
            conn.execute("INSERT INTO event_log(level,event_type,message,session_id,step,created_at_utc) VALUES(?,?,?,?,?,?)",
                         (level, event_type, message, session_id, step, utc_now()))
