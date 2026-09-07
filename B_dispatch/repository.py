"""SQLite repository for B's local EMS state, parameters, commands and history."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispatch_parameters (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wind_min_kw REAL NOT NULL CHECK (wind_min_kw >= 0),
    wind_max_kw REAL NOT NULL CHECK (wind_max_kw >= wind_min_kw),
    diesel_max_kw REAL NOT NULL CHECK (diesel_max_kw >= 0),
    reserve_kw REAL NOT NULL DEFAULT 10.0 CHECK (reserve_kw >= 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ems_runtime_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    poll_period_s REAL NOT NULL DEFAULT 1.0 CHECK (poll_period_s > 0),
    dispatch_period_s REAL NOT NULL DEFAULT 5.0 CHECK (dispatch_period_s > 0),
    closed_loop INTEGER NOT NULL DEFAULT 1 CHECK (closed_loop IN (0, 1)),
    command_timeout_s REAL CHECK (command_timeout_s IS NULL OR command_timeout_s > 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0), load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0),
    wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0), diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0), pitch_actual_deg REAL,
    wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);
CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0), load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0),
    wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0), diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0), pitch_actual_deg REAL,
    wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);
CREATE TABLE IF NOT EXISTS dispatch_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    source TEXT NOT NULL, seq INTEGER NOT NULL CHECK (seq >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0), diesel_target_kw REAL NOT NULL CHECK (diesel_target_kw >= 0),
    wind_enable INTEGER NOT NULL CHECK (wind_enable IN (0,1)), diesel_enable INTEGER NOT NULL CHECK (diesel_enable IN (0,1)),
    status TEXT NOT NULL, reason TEXT NOT NULL, created_at_utc TEXT NOT NULL,
    UNIQUE(session_id, source, seq)
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

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

class EMSRepository:
    """Short-transaction SQLite repository; each operation gets its own connection."""
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

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO schema_meta(key,value) VALUES (?,?)", ("schema_version", "1"))
            conn.execute("INSERT OR IGNORE INTO ems_runtime_config(id,updated_at) VALUES (1,?)", (utc_now(),))

    def set_parameters(self, *, wind_min_kw: float, wind_max_kw: float, diesel_max_kw: float, reserve_kw: float = 10.0) -> None:
        if diesel_max_kw < reserve_kw:
            raise ValueError("diesel_max_kw must be >= reserve_kw")
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO dispatch_parameters(id,wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw,updated_at)
                   VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   wind_min_kw=excluded.wind_min_kw,wind_max_kw=excluded.wind_max_kw,
                   diesel_max_kw=excluded.diesel_max_kw,reserve_kw=excluded.reserve_kw,updated_at=excluded.updated_at""",
                (wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw, utc_now()),
            )

    def get_parameters(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM dispatch_parameters WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("dispatch parameters are not initialized")
        return row

    def set_runtime_config(self, *, poll_period_s: float = 1.0, dispatch_period_s: float = 5.0,
                           closed_loop: bool = True, command_timeout_s: Optional[float] = None) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO ems_runtime_config(id,poll_period_s,dispatch_period_s,closed_loop,command_timeout_s,updated_at)
                   VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET poll_period_s=excluded.poll_period_s,
                   dispatch_period_s=excluded.dispatch_period_s,closed_loop=excluded.closed_loop,
                   command_timeout_s=excluded.command_timeout_s,updated_at=excluded.updated_at""",
                (poll_period_s, dispatch_period_s, int(closed_loop), command_timeout_s, utc_now()),
            )

    def save_state(self, state: Mapping[str, object]) -> None:
        required = ("session_id","step","sim_time_s","wind_speed_mps","load_power_kw","wind_actual_kw",
                    "diesel_actual_kw","wind_target_kw","wind_running","fault","received_at_utc","received_age_s")
        missing = [key for key in required if key not in state]
        if missing:
            raise ValueError(f"missing state fields: {', '.join(missing)}")
        values = tuple(state[key] for key in required)
        pitch = state.get("pitch_actual_deg")
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO current_state(id,session_id,step,sim_time_s,wind_speed_mps,load_power_kw,
                   wind_actual_kw,diesel_actual_kw,wind_target_kw,pitch_actual_deg,wind_running,fault,received_at_utc,received_age_s)
                   VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   session_id=excluded.session_id,step=excluded.step,sim_time_s=excluded.sim_time_s,
                   wind_speed_mps=excluded.wind_speed_mps,load_power_kw=excluded.load_power_kw,
                   wind_actual_kw=excluded.wind_actual_kw,diesel_actual_kw=excluded.diesel_actual_kw,
                   wind_target_kw=excluded.wind_target_kw,pitch_actual_deg=excluded.pitch_actual_deg,
                   wind_running=excluded.wind_running,fault=excluded.fault,received_at_utc=excluded.received_at_utc,
                   received_age_s=excluded.received_age_s""",
                values[:9] + (pitch,) + values[9:],
            )
            conn.execute(
                """INSERT INTO state_history(session_id,step,sim_time_s,wind_speed_mps,load_power_kw,wind_actual_kw,
                   diesel_actual_kw,wind_target_kw,pitch_actual_deg,wind_running,fault,received_at_utc,received_age_s)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values[:9] + (pitch,) + values[9:],
            )

    def record_command(self, command: Mapping[str, object]) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO dispatch_commands(session_id,step,sim_time_s,source,seq,wind_target_kw,diesel_target_kw,
                   wind_enable,diesel_enable,status,reason,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (command["session_id"],command["step"],command["sim_time_s"],command.get("source","B"),command["seq"],
                 command["wind_target_kw"],command["diesel_target_kw"],int(command["wind_enable"]),int(command["diesel_enable"]),
                 command.get("status","generated"),command.get("reason",""),command.get("created_at_utc",utc_now())),
            )
            return int(cur.lastrowid)

    def record_log(self, level: str, event_type: str, message: str, *, session_id: Optional[str] = None,
                   step: Optional[int] = None) -> None:
        with self.connection() as conn:
            conn.execute("INSERT INTO event_log(level,event_type,message,session_id,step,created_at_utc) VALUES(?,?,?,?,?,?)",
                         (level,event_type,message,session_id,step,utc_now()))
