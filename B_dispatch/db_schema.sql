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
    closed_loop INTEGER NOT NULL DEFAULT 1 CHECK (closed_loop IN (0,1)),
    command_timeout_s REAL CHECK (command_timeout_s IS NULL OR command_timeout_s > 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS current_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);

CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    wind_speed_mps REAL NOT NULL CHECK (wind_speed_mps >= 0),
    wind_available_kw REAL NOT NULL CHECK (wind_available_kw >= 0),
    wind_operating_limit_kw REAL NOT NULL CHECK (wind_operating_limit_kw >= 0 AND wind_operating_limit_kw <= wind_available_kw),
    load_power_kw REAL NOT NULL CHECK (load_power_kw >= 0), wind_actual_kw REAL NOT NULL CHECK (wind_actual_kw >= 0),
    diesel_actual_kw REAL NOT NULL CHECK (diesel_actual_kw >= 0), wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0),
    pitch_actual_deg REAL NOT NULL, wind_running INTEGER NOT NULL CHECK (wind_running IN (0,1)), fault INTEGER NOT NULL CHECK (fault IN (0,1)),
    sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK (received_age_s >= 0)
);

CREATE TABLE IF NOT EXISTS dispatch_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0), sim_time_s REAL NOT NULL CHECK (sim_time_s >= 0),
    source TEXT NOT NULL, seq INTEGER NOT NULL CHECK (seq >= 0),
    wind_target_kw REAL NOT NULL CHECK (wind_target_kw >= 0), diesel_target_kw REAL NOT NULL CHECK (diesel_target_kw >= 0),
    wind_enable INTEGER NOT NULL CHECK (wind_enable IN (0,1)), diesel_enable INTEGER NOT NULL CHECK (diesel_enable IN (0,1)),
    status TEXT NOT NULL, reason TEXT NOT NULL,
    ack_accepted INTEGER CHECK (ack_accepted IN (0,1)), ack_reason TEXT, ack_received_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
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
