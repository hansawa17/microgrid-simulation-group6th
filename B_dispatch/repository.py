"""SQLite repository for B's local EMS state, parameters, commands and history."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Optional

from .models import DispatchResult, GridState

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
    command_timeout_s REAL DEFAULT 3.0 CHECK (command_timeout_s IS NULL OR command_timeout_s > 0),
    max_state_age_s REAL NOT NULL DEFAULT 2.0 CHECK (max_state_age_s > 0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK (step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0), wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    diesel_target_kw REAL NOT NULL DEFAULT 0 CHECK (diesel_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    diesel_running INTEGER NOT NULL DEFAULT 0 CHECK (diesel_running IN (0,1)),
    power_imbalance_kw REAL NOT NULL DEFAULT 0,
    sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);
CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK (step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0), wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    diesel_target_kw REAL NOT NULL DEFAULT 0 CHECK (diesel_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    diesel_running INTEGER NOT NULL DEFAULT 0 CHECK (diesel_running IN (0,1)),
    power_imbalance_kw REAL NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS dispatch_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    state_step INTEGER NOT NULL CHECK (state_step >= 0),
    sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    diesel_target_kw REAL NOT NULL CHECK (diesel_target_kw >= 0),
    wind_enable INTEGER NOT NULL CHECK (wind_enable IN (0,1)),
    diesel_enable INTEGER NOT NULL CHECK (diesel_enable IN (0,1)),
    target_unserved_kw REAL NOT NULL CHECK (target_unserved_kw >= 0),
    target_surplus_kw REAL NOT NULL CHECK (target_surplus_kw >= 0),
    reason TEXT NOT NULL,
    executable INTEGER NOT NULL CHECK (executable IN (0,1)),
    status TEXT NOT NULL CHECK (status IN (
        'open_loop','pending','sending','accepted','rejected',
        'delivery_unknown','cancelled','local_error'
    )),
    protocol_seq INTEGER CHECK (protocol_seq IS NULL OR protocol_seq >= 0),
    command_id INTEGER REFERENCES dispatch_commands(id),
    ack_accepted INTEGER CHECK (ack_accepted IN (0,1)),
    ack_reason TEXT,
    detail TEXT,
    created_at_utc TEXT NOT NULL,
    claimed_at_utc TEXT,
    completed_at_utc TEXT,
    UNIQUE(session_id, state_step, executable)
);
CREATE INDEX IF NOT EXISTS idx_dispatch_outbox_status_id
    ON dispatch_outbox(status, id);
CREATE TABLE IF NOT EXISTS process_status (
    process_name TEXT PRIMARY KEY,
    pid INTEGER,
    state TEXT NOT NULL,
    detail TEXT NOT NULL,
    heartbeat_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS communication_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    updated_at_utc TEXT NOT NULL
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
UNIFIED_RUNTIME = {
    "poll_period_s": 1.0,
    "dispatch_period_s": 5.0,
    "closed_loop": 1,
    "command_timeout_s": 3.0,
    "max_state_age_s": 2.0,
}


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

    def _migrate_to_v5(self, conn: sqlite3.Connection) -> None:
        """Add the fields and queue used by the database-decoupled B runtime."""
        for table in ("current_state", "state_history"):
            columns = self._columns(conn, table)
            if "diesel_target_kw" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN diesel_target_kw "
                    "REAL NOT NULL DEFAULT 0 CHECK (diesel_target_kw >= 0)"
                )
            if "diesel_running" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN diesel_running "
                    "INTEGER NOT NULL DEFAULT 0 CHECK (diesel_running IN (0,1))"
                )
            if "power_imbalance_kw" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN power_imbalance_kw "
                    "REAL NOT NULL DEFAULT 0"
                )
        runtime_columns = self._columns(conn, "ems_runtime_config")
        if "max_state_age_s" not in runtime_columns:
            conn.execute(
                "ALTER TABLE ems_runtime_config ADD COLUMN max_state_age_s "
                "REAL NOT NULL DEFAULT 2.0 CHECK (max_state_age_s > 0)"
            )
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES ('schema_version','5') "
            "ON CONFLICT(key) DO UPDATE SET value='5'"
        )

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            version_row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            version = 0 if version_row is None else int(version_row[0])
            if version < 3:
                self._migrate_to_v3(conn)
            if version < 4:
                self._migrate_to_v4(conn)
            if version < 5:
                self._migrate_to_v5(conn)
            now = utc_now()
            cols = ", ".join(UNIFIED_PHYSICAL.keys())
            placeholders = ", ".join("?" for _ in UNIFIED_PHYSICAL)
            values = tuple(UNIFIED_PHYSICAL.values()) + (now,)
            conn.execute(
                f"INSERT INTO physical_parameters(id,{cols},updated_at) "
                f"VALUES(1,{placeholders},?) ON CONFLICT(id) DO NOTHING",
                values,
            )
            conn.execute(
                """INSERT INTO dispatch_parameters
                   (id, wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw, updated_at)
                   VALUES (1, 0.0, 100.0, 120.0, 10.0, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (now,),
            )
            conn.execute(
                """INSERT INTO ems_runtime_config
                   (id, poll_period_s, dispatch_period_s, closed_loop,
                    command_timeout_s, max_state_age_s, updated_at)
                   VALUES (1, 1.0, 5.0, 1, 3.0, 2.0, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (now,),
            )
            conn.execute(
                """INSERT INTO communication_config(id,host,port,enabled,updated_at_utc)
                   VALUES(1,'127.0.0.1',5000,1,?) ON CONFLICT(id) DO NOTHING""",
                (now,),
            )
            conn.execute(
                "INSERT INTO schema_meta(key,value) VALUES ('schema_version','5') "
                "ON CONFLICT(key) DO UPDATE SET value='5'"
            )

    def set_parameters(self, *, wind_min_kw: float, wind_max_kw: float, diesel_max_kw: float, reserve_kw: float = 10.0) -> None:
        wind_min_kw = float(wind_min_kw)
        wind_max_kw = float(wind_max_kw)
        diesel_max_kw = float(diesel_max_kw)
        reserve_kw = float(reserve_kw)
        values = (wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("dispatch parameters must be finite non-negative numbers")
        if wind_max_kw < wind_min_kw:
            raise ValueError("wind_max_kw must be >= wind_min_kw")
        if diesel_max_kw < reserve_kw:
            raise ValueError("diesel_max_kw must be >= reserve_kw")
        if reserve_kw <= 0:
            raise ValueError("reserve_kw must be > 0")
        self.initialize()
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE dispatch_parameters
                   SET wind_min_kw=?, wind_max_kw=?, diesel_max_kw=?, reserve_kw=?, updated_at=?
                   WHERE id=1""",
                (wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw, utc_now()),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("dispatch parameters are not initialized")

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
                           closed_loop: bool = True, command_timeout_s: Optional[float] = 3.0,
                           max_state_age_s: float = 2.0) -> None:
        poll_period_s = float(poll_period_s)
        dispatch_period_s = float(dispatch_period_s)
        if not math.isfinite(poll_period_s) or poll_period_s <= 0:
            raise ValueError("poll_period_s must be > 0")
        if not math.isfinite(dispatch_period_s) or dispatch_period_s <= 0:
            raise ValueError("dispatch_period_s must be > 0")
        command_timeout = None if command_timeout_s is None else float(command_timeout_s)
        if command_timeout is not None and (
            not math.isfinite(command_timeout) or command_timeout <= 0
        ):
            raise ValueError("command_timeout_s must be > 0 or None")
        max_state_age_s = float(max_state_age_s)
        if not math.isfinite(max_state_age_s) or max_state_age_s <= 0:
            raise ValueError("max_state_age_s must be > 0")
        self.initialize()
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE ems_runtime_config
                   SET poll_period_s=?, dispatch_period_s=?, closed_loop=?, command_timeout_s=?,
                       max_state_age_s=?, updated_at=?
                   WHERE id=1""",
                (
                    poll_period_s, dispatch_period_s, int(bool(closed_loop)),
                    command_timeout, max_state_age_s, utc_now(),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("runtime config is not initialized")

    def get_runtime_config(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM ems_runtime_config WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("runtime config is not initialized")
        return row

    def set_communication_config(self, *, host: str, port: int, enabled: bool = True) -> None:
        host = str(host).strip()
        if not host or any(char.isspace() for char in host):
            raise ValueError("host must be a non-empty hostname or IP address")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be an integer in [1, 65535]")
        self.initialize()
        with self.connection() as conn:
            conn.execute(
                """UPDATE communication_config
                   SET host=?,port=?,enabled=?,updated_at_utc=? WHERE id=1""",
                (host, port, int(bool(enabled)), utc_now()),
            )

    def get_communication_config(self) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM communication_config WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("communication config is not initialized")
        return row

    def save_state(self, state: Mapping[str, object]) -> None:
        if is_dataclass(state):
            state = asdict(state)
        required = (
            "session_id", "step", "sim_time_s", "wind_speed_mps", "wind_available_kw",
            "wind_operating_limit_kw", "load_power_kw", "wind_actual_kw", "diesel_actual_kw",
            "wind_target_kw", "diesel_target_kw", "wind_running", "diesel_running", "fault",
            "power_imbalance_kw", "sampled_at_utc", "received_at_utc", "received_age_s",
        )
        missing = [key for key in required if key not in state]
        if missing:
            raise ValueError(f"missing state fields: {', '.join(missing)}")
        pitch = state.get("pitch_actual_deg")
        if pitch is None:
            raise ValueError("pitch_actual_deg is required by the current TCP state contract")
        db_values = (
            state["session_id"], state["step"], state["sim_time_s"],
            state["wind_speed_mps"], state["wind_available_kw"],
            state["wind_operating_limit_kw"], state["load_power_kw"],
            state["wind_actual_kw"], state["diesel_actual_kw"],
            state["wind_target_kw"], state["diesel_target_kw"], pitch,
            int(bool(state["wind_running"])), int(bool(state["fault"])),
            int(bool(state["diesel_running"])), state["power_imbalance_kw"],
            state["sampled_at_utc"], state["received_at_utc"], state["received_age_s"],
        )
        with self.connection() as conn:
            previous = conn.execute(
                "SELECT session_id,step FROM current_state WHERE id=1"
            ).fetchone()
            if (
                previous is not None
                and previous["session_id"] == state["session_id"]
                and int(state["step"]) < int(previous["step"])
            ):
                raise ValueError("state step moved backwards within the current session")
            conn.execute(
                """INSERT INTO current_state(id,session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,
                   load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,diesel_target_kw,pitch_actual_deg,wind_running,fault,
                   diesel_running,power_imbalance_kw,sampled_at_utc,received_at_utc,received_age_s)
                   VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,
                   step=excluded.step,sim_time_s=excluded.sim_time_s,wind_speed_mps=excluded.wind_speed_mps,
                   wind_available_kw=excluded.wind_available_kw,wind_operating_limit_kw=excluded.wind_operating_limit_kw,
                   load_power_kw=excluded.load_power_kw,wind_actual_kw=excluded.wind_actual_kw,diesel_actual_kw=excluded.diesel_actual_kw,
                   wind_target_kw=excluded.wind_target_kw,diesel_target_kw=excluded.diesel_target_kw,
                   pitch_actual_deg=excluded.pitch_actual_deg,wind_running=excluded.wind_running,
                   fault=excluded.fault,diesel_running=excluded.diesel_running,
                   power_imbalance_kw=excluded.power_imbalance_kw,sampled_at_utc=excluded.sampled_at_utc,
                   received_at_utc=excluded.received_at_utc,received_age_s=excluded.received_age_s""",
                db_values,
            )
            exists = conn.execute(
                """SELECT 1 FROM state_history
                   WHERE session_id=? AND step=? AND sampled_at_utc=? LIMIT 1""",
                (state["session_id"], state["step"], state["sampled_at_utc"]),
            ).fetchone()
            if exists is None:
                conn.execute(
                """INSERT INTO state_history(session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,
                   load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,diesel_target_kw,pitch_actual_deg,wind_running,fault,
                   diesel_running,power_imbalance_kw,sampled_at_utc,received_at_utc,received_age_s)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    db_values,
                )

    def get_current_state(self) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM current_state WHERE id=1").fetchone()

    @staticmethod
    def _utc_datetime(value: str) -> datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("UTC timestamp must end with Z")
        return datetime.fromisoformat(value[:-1] + "+00:00")

    @classmethod
    def _grid_state_from_row(cls, row: Mapping[str, object]) -> GridState:
        received_at = str(row["received_at_utc"])
        local_elapsed_s = max(
            0.0,
            (datetime.now(timezone.utc) - cls._utc_datetime(received_at)).total_seconds(),
        )
        age_s = float(row["received_age_s"]) + local_elapsed_s
        return GridState(
            session_id=str(row["session_id"]),
            step=int(row["step"]),
            sim_time_s=float(row["sim_time_s"]),
            wind_speed_mps=float(row["wind_speed_mps"]),
            wind_available_kw=float(row["wind_available_kw"]),
            wind_operating_limit_kw=float(row["wind_operating_limit_kw"]),
            load_power_kw=float(row["load_power_kw"]),
            wind_actual_kw=float(row["wind_actual_kw"]),
            diesel_actual_kw=float(row["diesel_actual_kw"]),
            wind_running=bool(row["wind_running"]),
            fault=bool(row["fault"]),
            received_age_s=age_s,
            sampled_at_utc=str(row["sampled_at_utc"]),
            received_at_utc=received_at,
            wind_target_kw=float(row["wind_target_kw"]),
            diesel_target_kw=float(row["diesel_target_kw"]),
            pitch_actual_deg=float(row["pitch_actual_deg"]),
            diesel_running=bool(row["diesel_running"]),
            power_imbalance_kw=float(row["power_imbalance_kw"]),
        )

    def get_current_grid_state(self) -> Optional[GridState]:
        row = self.get_current_state()
        return None if row is None else self._grid_state_from_row(row)

    def get_grid_state(self, session_id: str, step: int) -> Optional[GridState]:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM state_history
                   WHERE session_id=? AND step=? ORDER BY id DESC LIMIT 1""",
                (session_id, step),
            ).fetchone()
        return None if row is None else self._grid_state_from_row(row)

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

    def queue_decision(
        self,
        state: GridState,
        result: DispatchResult,
        *,
        executable: bool,
    ) -> tuple[int, bool]:
        """Persist one decision; only closed-loop rows enter the send queue."""
        status = "pending" if executable else "open_loop"
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO dispatch_outbox(
                       session_id,state_step,sim_time_s,wind_target_kw,diesel_target_kw,
                       wind_enable,diesel_enable,target_unserved_kw,target_surplus_kw,
                       reason,executable,status,created_at_utc)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id,state_step,executable) DO NOTHING""",
                (
                    state.session_id, state.step, state.sim_time_s,
                    result.wind_target_kw, result.diesel_target_kw,
                    int(result.wind_enable), int(result.diesel_enable),
                    result.target_unserved_kw, result.target_surplus_kw,
                    result.reason, int(executable), status, utc_now(),
                ),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                """SELECT id FROM dispatch_outbox
                   WHERE session_id=? AND state_step=? AND executable=?""",
                (state.session_id, state.step, int(executable)),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to persist EMS decision")
        return int(row["id"]), created

    def claim_next_outbox(self) -> Optional[sqlite3.Row]:
        """Atomically claim the oldest pending command for the I/O process."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dispatch_outbox WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """UPDATE dispatch_outbox
                   SET status='sending',claimed_at_utc=?
                   WHERE id=? AND status='pending'""",
                (utc_now(), row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            return conn.execute(
                "SELECT * FROM dispatch_outbox WHERE id=?", (row["id"],)
            ).fetchone()

    def finish_outbox(
        self,
        outbox_id: int,
        *,
        status: str,
        protocol_seq: Optional[int] = None,
        command_id: Optional[int] = None,
        ack_accepted: Optional[bool] = None,
        ack_reason: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        terminal = {
            "accepted", "rejected", "delivery_unknown", "cancelled", "local_error"
        }
        if status not in terminal:
            raise ValueError(f"invalid terminal outbox status: {status}")
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE dispatch_outbox
                   SET status=?,protocol_seq=?,command_id=?,ack_accepted=?,ack_reason=?,
                       detail=?,completed_at_utc=?
                   WHERE id=? AND status='sending'""",
                (
                    status, protocol_seq, command_id,
                    None if ack_accepted is None else int(ack_accepted),
                    ack_reason, detail, utc_now(), outbox_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"outbox row {outbox_id} is not in sending state")

    def recover_abandoned_outbox(self) -> int:
        """Never resend a row that may have crossed the network before a crash."""
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE dispatch_outbox
                   SET status='delivery_unknown',
                       detail='I/O process restarted while command was in sending state',
                       completed_at_utc=?
                   WHERE status='sending'""",
                (utc_now(),),
            )
            return int(cursor.rowcount)

    def get_outbox(self, outbox_id: int) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM dispatch_outbox WHERE id=?", (outbox_id,)
            ).fetchone()

    def heartbeat(
        self,
        process_name: str,
        *,
        pid: Optional[int],
        state: str,
        detail: str = "",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO process_status(process_name,pid,state,detail,heartbeat_at_utc)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(process_name) DO UPDATE SET pid=excluded.pid,
                       state=excluded.state,detail=excluded.detail,
                       heartbeat_at_utc=excluded.heartbeat_at_utc""",
                (process_name, pid, state, detail, utc_now()),
            )

    def get_process_status(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute("SELECT * FROM process_status ORDER BY process_name")
            )

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
