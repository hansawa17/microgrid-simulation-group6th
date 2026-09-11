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
    param_id            INTEGER PRIMARY KEY,
    device_id           INTEGER NOT NULL,
    cut_in_speed_mps    REAL NOT NULL,
    rated_speed_mps     REAL NOT NULL,
    cut_out_speed_mps   REAL NOT NULL,
    wind_rated_power_kw REAL NOT NULL,
    pitch_feather_deg   REAL NOT NULL,
    c_control_s         REAL NOT NULL,
    c_timeout_s         REAL NOT NULL,
    control_mode        INTEGER NOT NULL,
    parameter_revision  INTEGER,
    parameter_verified  INTEGER,
    verified_at_utc     TEXT,
    verify_reason       TEXT,
    a_sync_status       INTEGER,
    a_sync_seq          INTEGER,
    a_sync_reason       TEXT,
    update_time         TEXT NOT NULL
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
    communication_status    INTEGER NOT NULL,
    last_wind_action_seq    INTEGER
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
        self._lock = threading.RLock()  # 可重入：update_params 内部会再调 get_params，避免同线程死锁
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed()

    # ------------------------------------------------------------------ #
    #  初始化
    # ------------------------------------------------------------------ #
    def _init_schema(self):
        with self._lock:
            # 迁移：telemetry/control_history 旧列 wind_speed 时重建两张数据表
            old_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(telemetry)").fetchall()]
            if "wind_speed" in old_cols:
                self._conn.execute("DROP TABLE IF EXISTS telemetry")
                self._conn.execute("DROP TABLE IF EXISTS control_history")
            # 迁移：parameter 旧列 cut_in_speed 时重建（列名已对齐 A canonical 名）
            param_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(parameter)").fetchall()]
            if "cut_in_speed" in param_cols:
                self._conn.execute("DROP TABLE IF EXISTS parameter")
            self._conn.executescript(_SCHEMA)
            # 迁移：新版本列原位补齐（保留已有遥测/参数历史）
            self._ensure_column("telemetry", "last_wind_action_seq", "INTEGER")
            self._ensure_column("parameter", "parameter_revision", "INTEGER")
            self._ensure_column("parameter", "parameter_verified", "INTEGER")
            self._ensure_column("parameter", "verified_at_utc", "TEXT")
            self._ensure_column("parameter", "verify_reason", "TEXT")
            self._ensure_column("parameter", "a_sync_status", "INTEGER")
            self._ensure_column("parameter", "a_sync_seq", "INTEGER")
            self._ensure_column("parameter", "a_sync_reason", "TEXT")
            self._conn.commit()

    def _ensure_column(self, table, column, decl):
        """原位补齐列（若不存在），避免重建表丢失历史数据。"""
        cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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
                    "INSERT INTO parameter (param_id, device_id, cut_in_speed_mps, rated_speed_mps, cut_out_speed_mps, wind_rated_power_kw, pitch_feather_deg, c_control_s, c_timeout_s, control_mode, parameter_revision, parameter_verified, a_sync_status, update_time) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (1, 1, p["cut_in_speed_mps"], p["rated_speed_mps"], p["cut_out_speed_mps"],
                     p["wind_rated_power_kw"], p["pitch_feather_deg"], p["c_control_s"],
                     p["c_timeout_s"], p["control_mode"], 0, 0, 0, now_ms()),
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

    def get_param_meta(self):
        """返回参数验证与 A 同步元数据（parameter_revision / verified / a_sync_*）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT parameter_revision, parameter_verified, verified_at_utc, verify_reason, "
                "a_sync_status, a_sync_seq, a_sync_reason FROM parameter WHERE device_id=1 "
                "ORDER BY param_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {
                "parameter_revision": 0, "parameter_verified": 0,
                "verified_at_utc": None, "verify_reason": None,
                "a_sync_status": 0, "a_sync_seq": None, "a_sync_reason": None,
            }
        return {
            "parameter_revision": row["parameter_revision"],
            "parameter_verified": row["parameter_verified"],
            "verified_at_utc": row["verified_at_utc"],
            "verify_reason": row["verify_reason"],
            "a_sync_status": row["a_sync_status"],
            "a_sync_seq": row["a_sync_seq"],
            "a_sync_reason": row["a_sync_reason"],
        }

    def mark_param_verified(self, verified, revision=None, reason=None):
        """记录 $PARAMGET 回读验证结果：verified=1 表示 MCU 已生效。"""
        with self._lock:
            sql = "UPDATE parameter SET parameter_verified=?, verified_at_utc=?, verify_reason=?"
            args = [int(verified), now_ms(), reason]
            if revision is not None:
                sql += ", parameter_revision=?"
                args.append(int(revision))
            sql += " WHERE device_id=1"
            self._conn.execute(sql, args)
            self._conn.commit()

    def record_param_sync(self, status, seq=None, reason=None):
        """记录 STM32->A parameter_update 的同步状态（$SYNC 帧）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE parameter SET a_sync_status=?, a_sync_seq=?, a_sync_reason=? WHERE device_id=1",
                (int(status), seq, reason),
            )
            self._conn.commit()

    def update_params(self, new_params, source="GUI"):
        """更新参数表，并把改动逐项写入 remote_adjust。返回改动项列表。

        source 记录改动来源（GUI=界面下发；MCU=从单片机读回覆盖），
        供审计区分"本地意图"与"MCU 实际生效值"。
        """
        with self._lock:
            old = self.get_params()
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE parameter SET cut_in_speed_mps=?, rated_speed_mps=?, cut_out_speed_mps=?, wind_rated_power_kw=?, "
                "pitch_feather_deg=?, c_control_s=?, c_timeout_s=?, control_mode=?, update_time=? WHERE device_id=1",
                (
                    new_params["cut_in_speed_mps"], new_params["rated_speed_mps"], new_params["cut_out_speed_mps"],
                    new_params["wind_rated_power_kw"], new_params["pitch_feather_deg"], new_params["c_control_s"],
                    new_params["c_timeout_s"], int(new_params["control_mode"]), now_ms(),
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
                        (1, k, old_v, new_v, unit, now_ms(), source, 1),
                    )
                    changes.append((k, old_v, new_v))
            self._conn.commit()
            return changes

    def sync_params_from_mcu(self, params, revision=None):
        """用 MCU 经 $PARAMGET 串口读回的生效参数覆盖本地 parameter 表。

        - 库中参数以 MCU 返回值为准（证明数据库参数来自单片机而非 GUI 面板）；
        - 有差异的字段逐项写 remote_adjust（source="MCU"，old=本地值，new=MCU 值），
          作为可审计证据；
        - 同时刷新 parameter_revision 并置 parameter_verified=1（库值 == MCU 值）。
        返回被覆盖的改动项列表 [(key, old, new), ...]。
        """
        with self._lock:
            old = self.get_params()
            new_params = {k: params.get(k, old.get(k)) for k in config.PARAM_FIELDS}
            changes = self.update_params(new_params, source="MCU")
            sql = "UPDATE parameter SET parameter_verified=1, verified_at_utc=?, verify_reason=?"
            args = [now_ms(), "MCU 读回同步"]
            if revision is not None:
                sql += ", parameter_revision=?"
                args.append(int(revision))
            sql += " WHERE device_id=1"
            self._conn.execute(sql, args)
            self._conn.commit()
            return changes

    # ------------------------------------------------------------------ #
    #  遥测 / 控制历史
    # ------------------------------------------------------------------ #
    def insert_telemetry(self, data, communication_status=1):
        """插入一条遥测。data 含对齐仓库命名的字段（见 config.WIND_FIELDS）。"""
        last_seq = data.get("last_wind_action_seq")
        with self._lock:
            self._conn.execute(
                "INSERT INTO telemetry (device_id, cycle, timestamp, wind_speed_mps, wind_available_kw, "
                "wind_operating_limit_kw, wind_target_kw, wind_actual_kw, wind_running, pitch_target_deg, "
                "control_mode, communication_status, last_wind_action_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, int(data["cycle"]), now_ms(), data["wind_speed_mps"], data["wind_available_kw"],
                 data.get("wind_operating_limit_kw"), data["wind_target_kw"], data["wind_actual_kw"],
                 int(data["wind_running"]), data.get("pitch_target_deg"), int(data["control_mode"]),
                 communication_status, last_seq),
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

    def query_remote_adjust(self, start, end, limit=1000):
        """按时间区间查询参数修改历史（remote_adjust），返回 [(列值元组), ...]。"""
        sql = ("SELECT adjust_time, parameter_name, old_value, new_value, unit, source, result "
               "FROM remote_adjust WHERE adjust_time >= ? AND adjust_time <= ? "
               "ORDER BY adjust_time ASC LIMIT ?")
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
