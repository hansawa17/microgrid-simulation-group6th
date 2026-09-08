# -*- coding: utf-8 -*-
"""
数据库层 —— wind.db（风电子站 PC-C 本地数据库）

按《数据库目标.txt》建立 8 张核心表：
    device / parameter / telemetry / control_history
    remote_command / remote_adjust / communication_log / system_log

设计要点：
  - 当前参数看 parameter，历史运行看 telemetry，每轮闭环看 control_history，
    用户操作看 remote_command / remote_adjust，通信与故障看日志。
  - 时间统一存 TEXT "YYYY-MM-DD HH:MM:SS.fff"（字典序可比较）。
  - 高频表（telemetry / control_history）与日志表按 timestamp 建索引。
  - 所有写操作加锁，供串口线程 + GUI 线程并发安全调用。
"""

import os
import sqlite3
import threading
from datetime import datetime

import config


def now_ms():
    """返回当前时间字符串（毫秒精度）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# --------------------------------------------------------------------------- #
#  建表 SQL
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    device_id    INTEGER PRIMARY KEY,
    device_name  TEXT NOT NULL,
    device_type  TEXT NOT NULL,
    model        TEXT,
    location     TEXT,
    rated_power  REAL NOT NULL,
    create_time  TEXT NOT NULL,
    update_time  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter (
    param_id              INTEGER PRIMARY KEY,
    device_id             INTEGER NOT NULL,
    cut_in_speed          REAL NOT NULL,
    rated_speed           REAL NOT NULL,
    cut_out_speed         REAL NOT NULL,
    rated_power           REAL NOT NULL,
    deg_max               REAL NOT NULL,
    control_period        REAL NOT NULL,
    communication_timeout REAL NOT NULL,
    control_mode          INTEGER NOT NULL,
    update_time           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id               INTEGER NOT NULL,
    cycle                   INTEGER NOT NULL,
    timestamp               TEXT NOT NULL,
    wind_speed_mps          REAL NOT NULL,
    wind_available_kw       REAL NOT NULL,
    wind_operating_limit_kw REAL,
    wind_target_kw          REAL NOT NULL,
    wind_actual_kw          REAL NOT NULL,
    wind_running            INTEGER NOT NULL,
    pitch_target_deg        REAL,
    control_mode            INTEGER NOT NULL,
    communication_status    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS control_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle                   INTEGER NOT NULL,
    timestamp               TEXT NOT NULL,
    wind_speed_mps          REAL NOT NULL,
    wind_available_kw       REAL NOT NULL,
    wind_operating_limit_kw REAL,
    wind_target_kw          REAL NOT NULL,
    pitch_target_deg        REAL,
    wind_actual_kw          REAL,
    wind_running            INTEGER NOT NULL,
    power_diesel_set        REAL,
    load                    REAL,
    decision_status         TEXT
);

CREATE TABLE IF NOT EXISTS remote_command (
    command_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL,
    command_type  TEXT NOT NULL,
    command_value TEXT,
    send_time     TEXT NOT NULL,
    execute_time  TEXT,
    result        INTEGER,
    error_code    TEXT,
    source        TEXT
);

CREATE TABLE IF NOT EXISTS remote_adjust (
    adjust_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id      INTEGER NOT NULL,
    parameter_name TEXT NOT NULL,
    old_value      REAL,
    new_value      REAL,
    unit           TEXT,
    adjust_time    TEXT NOT NULL,
    source         TEXT,
    result         INTEGER,
    error_code     TEXT
);

CREATE TABLE IF NOT EXISTS communication_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    interface    TEXT NOT NULL,
    direction    TEXT NOT NULL,
    message_type TEXT,
    cycle        INTEGER,
    data_length  INTEGER,
    result       INTEGER,
    latency      REAL,
    error_code   TEXT,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS system_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    level       TEXT NOT NULL,
    event_type  TEXT,
    event_code  TEXT,
    description TEXT,
    source      TEXT,
    cycle       INTEGER,
    status      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp  ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_cycle      ON telemetry(cycle);
CREATE INDEX IF NOT EXISTS idx_control_ts           ON control_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_control_cycle        ON control_history(cycle);
CREATE INDEX IF NOT EXISTS idx_commlog_timestamp    ON communication_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_syslog_timestamp     ON system_log(timestamp);
"""


class Database:
    """wind.db 访问封装。"""

    def __init__(self, path=config.DATABASE_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed()

    # ------------------------------------------------------------------ #
    #  初始化
    # ------------------------------------------------------------------ #
    def _init_schema(self):
        with self._lock:
            # 迁移：旧 schema 使用 wind_speed / power_available / power_set / power_actual / status / deg，
            # 已改为对齐仓库的 wind_*_kw / *_mps / wind_running / pitch_target_deg 并新增
            # wind_operating_limit_kw。检测到旧列时重建两张数据表（本地 mock 历史不保留）。
            old_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(telemetry)").fetchall()]
            if "wind_speed" in old_cols:
                self._conn.execute("DROP TABLE IF EXISTS telemetry")
                self._conn.execute("DROP TABLE IF EXISTS control_history")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _seed(self):
        """写入默认设备与默认参数（若不存在）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM device")
            if cur.fetchone()[0] == 0:
                ts = now_ms()
                cur.execute(
                    "INSERT INTO device (device_id, device_name, device_type, model, location, rated_power, create_time, update_time) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (1, "WT001", "Wind Turbine", "WT-100kW", "南极考察站", 100.0, ts, ts),
                )
            cur.execute("SELECT COUNT(*) FROM parameter")
            if cur.fetchone()[0] == 0:
                p = config.DEFAULT_PARAMS
                cur.execute(
                    "INSERT INTO parameter (param_id, device_id, cut_in_speed, rated_speed, cut_out_speed, rated_power, deg_max, control_period, communication_timeout, control_mode, update_time) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (1, 1, p["cut_in_speed"], p["rated_speed"], p["cut_out_speed"],
                     p["rated_power"], p["deg_max"], p["control_period"],
                     p["communication_timeout"], p["control_mode"], now_ms()),
                )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    #  参数
    # ------------------------------------------------------------------ #
    def get_params(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM parameter WHERE device_id=1 ORDER BY param_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return dict(config.DEFAULT_PARAMS)
        return {k: row[k] for k in config.PARAM_FIELDS}

    def update_params(self, new_params):
        """更新参数表，并把改动逐项写入 remote_adjust。返回改动项列表。"""
        with self._lock:
            old = self.get_params()
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE parameter SET cut_in_speed=?, rated_speed=?, cut_out_speed=?, rated_power=?, "
                "deg_max=?, control_period=?, communication_timeout=?, control_mode=?, update_time=? WHERE device_id=1",
                (
                    new_params["cut_in_speed"], new_params["rated_speed"], new_params["cut_out_speed"],
                    new_params["rated_power"], new_params["deg_max"], new_params["control_period"],
                    new_params["communication_timeout"], int(new_params["control_mode"]), now_ms(),
                ),
            )
            changes = []
            for k in config.PARAM_FIELDS:
                old_v = old.get(k)
                new_v = new_params.get(k)
                if old_v != new_v:
                    unit = config.PARAM_LABELS.get(k, ("", ""))[1]
                    cur.execute(
                        "INSERT INTO remote_adjust (device_id, parameter_name, old_value, new_value, unit, adjust_time, source, result) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (1, k, old_v, new_v, unit, now_ms(), "GUI", 1),
                    )
                    changes.append((k, old_v, new_v))
            self._conn.commit()
            return changes

    # ------------------------------------------------------------------ #
    #  遥测 / 控制历史
    # ------------------------------------------------------------------ #
    def insert_telemetry(self, data, communication_status=1):
        """插入一条遥测。data 含对齐仓库命名的字段（见 config.WIND_FIELDS / WIND_EXTRA_FIELDS）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO telemetry (device_id, cycle, timestamp, wind_speed_mps, wind_available_kw, "
                "wind_operating_limit_kw, wind_target_kw, wind_actual_kw, wind_running, pitch_target_deg, "
                "control_mode, communication_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, int(data["cycle"]), now_ms(), data["wind_speed_mps"], data["wind_available_kw"],
                 data.get("wind_operating_limit_kw"), data["wind_target_kw"], data["wind_actual_kw"],
                 int(data["wind_running"]), data.get("pitch_target_deg"), int(data["control_mode"]),
                 communication_status),
            )
            # 同步写入闭环控制历史（本阶段 B/A 未接入，柴发/负荷字段留空）
            self._conn.execute(
                "INSERT INTO control_history (cycle, timestamp, wind_speed_mps, wind_available_kw, "
                "wind_operating_limit_kw, wind_target_kw, pitch_target_deg, wind_actual_kw, wind_running, "
                "power_diesel_set, load, decision_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(data["cycle"]), now_ms(), data["wind_speed_mps"], data["wind_available_kw"],
                 data.get("wind_operating_limit_kw"), data["wind_target_kw"], data.get("pitch_target_deg"),
                 data["wind_actual_kw"], int(data["wind_running"]), None, None, "本地闭环"),
            )
            self._conn.commit()

    def insert_control_history(self, data):
        """单独写入一条控制历史。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO control_history (cycle, timestamp, wind_speed_mps, wind_available_kw, "
                "wind_operating_limit_kw, wind_target_kw, pitch_target_deg, wind_actual_kw, wind_running, "
                "power_diesel_set, load, decision_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(data["cycle"]), now_ms(), data["wind_speed_mps"], data["wind_available_kw"],
                 data.get("wind_operating_limit_kw"), data["wind_target_kw"], data.get("pitch_target_deg"),
                 data["wind_actual_kw"], int(data["wind_running"]),
                 data.get("power_diesel_set"), data.get("load"), data.get("decision_status")),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    #  遥控 / 遥调 / 日志
    # ------------------------------------------------------------------ #
    def insert_remote_command(self, command_type, command_value="", source="GUI", result=1, error_code=""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO remote_command (device_id, command_type, command_value, send_time, result, error_code, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (1, command_type, command_value, now_ms(), result, error_code, source),
            )
            self._conn.commit()

    def insert_communication_log(self, interface, direction, message_type, cycle=None,
                                 data_length=0, result=1, latency=None, error_code="", description=""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO communication_log (timestamp, interface, direction, message_type, cycle, data_length, result, latency, error_code, description) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now_ms(), interface, direction, message_type, cycle, data_length,
                 result, latency, error_code, description),
            )
            self._conn.commit()

    def insert_system_log(self, level, event_type, description, source="System", event_code="", cycle=None, status=0):
        with self._lock:
            self._conn.execute(
                "INSERT INTO system_log (timestamp, level, event_type, event_code, description, source, cycle, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (now_ms(), level, event_type, event_code, description, source, cycle, status),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #
    def query_telemetry(self, start, end, limit=1000):
        """按时间区间查询遥测，返回 [(列值元组), ...]，列序与 GUI 表头一致。"""
        sql = ("SELECT timestamp, cycle, wind_speed_mps, wind_available_kw, wind_target_kw, wind_actual_kw, "
               "wind_running, pitch_target_deg, control_mode, communication_status FROM telemetry "
               "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?")
        with self._lock:
            rows = self._conn.execute(sql, (start, end + ".999", limit)).fetchall()
        return [tuple(r) for r in rows]

    def query_control_history(self, start, end, limit=1000):
        sql = ("SELECT timestamp, cycle, wind_speed_mps, wind_available_kw, wind_target_kw, wind_actual_kw, "
               "pitch_target_deg, wind_running, power_diesel_set, load, decision_status FROM control_history "
               "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?")
        with self._lock:
            rows = self._conn.execute(sql, (start, end + ".999", limit)).fetchall()
        return [tuple(r) for r in rows]

    def query_system_log(self, limit=200):
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, level, event_type, source, description FROM system_log "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [tuple(r) for r in rows]

    def query_communication_log(self, limit=200):
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, interface, direction, message_type, cycle, data_length, "
                "result, latency, error_code FROM communication_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [tuple(r) for r in rows]

    def latest_telemetry(self, limit=200):
        """最近 N 条遥测（用于启动时初始化曲线/表格），按时间升序返回。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, cycle, wind_speed_mps, wind_available_kw, wind_target_kw, wind_actual_kw, "
                "wind_running, pitch_target_deg, control_mode FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [tuple(r) for r in reversed(rows)]

    # ------------------------------------------------------------------ #
    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
