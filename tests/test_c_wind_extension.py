# -*- coding: utf-8 -*-
"""
C 风电子站新版验收扩展单元测试。

覆盖（对应 docs/wind-execution-status-extension.md 第 6/7 节）：
  1. $WIND（旧 10 字段）与 $WIND2（新 11 字段，含 last_wind_action_seq）均可解析；
     字段不足/过多/非法数字不抛异常、不使串口线程崩溃。
  2. $PARAM2 帧构建、$PARAMGET? 查询帧构建、$PARAMGET 读回解析（request_id / revision）。
  3. $SYNC 同步状态帧解析。
  4. parameter_update 白名单只含 7 个 C 物理参数，control_mode 不发送。
  5. 旧 wind.db 原位迁移保留数据并补齐新列。
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1] / "C_controller" / "host"
sys.path.insert(0, str(HOST_DIR))

import config
import protocol
from database import Database

CRLF = chr(13) + chr(10)


class Wind2ProtocolTests(unittest.TestCase):
    """$WIND / $WIND2 遥测帧解析。"""

    def test_wind_legacy_10_fields_parses_with_null_seq(self):
        frame = "$WIND,101,9.50,722.20,722.20,500.00,500.00,1,8.50,1,1"
        kind, data = protocol.parse_frame(frame)
        self.assertEqual(kind, "wind")
        self.assertIsNone(data["last_wind_action_seq"])
        self.assertEqual(data["cycle"], 101)
        self.assertAlmostEqual(data["wind_speed_mps"], 9.5)

    def test_wind2_11_fields_parses_seq(self):
        frame = "$WIND2,101,9.50,722.20,722.20,500.00,500.00,1,8.50,1,1,42"
        kind, data = protocol.parse_frame(frame)
        self.assertEqual(kind, "wind")
        self.assertEqual(data["last_wind_action_seq"], 42)
        self.assertEqual(data["control_mode"], 1)

    def test_wind2_negative_seq_becomes_none(self):
        frame = "$WIND2,101,9.50,722.20,722.20,500.00,500.00,1,8.50,1,1,-1"
        kind, data = protocol.parse_frame(frame)
        self.assertEqual(kind, "wind")
        self.assertIsNone(data["last_wind_action_seq"])

    def test_wind_bad_field_count_rejected_without_crash(self):
        self.assertIsNone(protocol.parse_frame("$WIND,1,2,3"))
        self.assertIsNone(protocol.parse_frame("$WIND2,1,2,3,4,5,6,7,8,9,10"))
        self.assertIsNone(protocol.parse_frame(
            "$WIND2,1,2,3,4,5,6,7,8,9,10,11,12"))

    def test_wind_illegal_number_rejected_without_crash(self):
        self.assertIsNone(protocol.parse_frame(
            "$WIND,abc,9.50,722.20,722.20,500.00,500.00,1,8.50,1,1"))
        self.assertIsNone(protocol.parse_frame(
            "$WIND2,101,x,722.20,722.20,500.00,500.00,1,8.50,1,1,42"))


class Param2ProtocolTests(unittest.TestCase):
    """$PARAM2 / $PARAMGET? / $PARAMGET 帧编解码。"""

    def _params(self, **overrides):
        p = dict(config.DEFAULT_PARAMS)
        p.update(overrides)
        return p

    def test_build_param2_frame(self):
        params = self._params(cut_in_speed_mps=3.5, wind_rated_power_kw=120.0)
        frame = protocol.build_param2_frame(5, params)
        # 期望帧从参数字典推导，避免 config 默认值调整（如 c_timeout_s）时误报
        expected = ("$PARAM2,5,"
                    f"{params['cut_in_speed_mps']:.2f},{params['rated_speed_mps']:.2f},"
                    f"{params['cut_out_speed_mps']:.2f},{params['wind_rated_power_kw']:.2f},"
                    f"{params['pitch_feather_deg']:.2f},{params['c_control_s']:.2f},"
                    f"{params['c_timeout_s']:.2f},{int(params['control_mode'])}" + CRLF)
        self.assertEqual(frame, expected.encode("ascii"))

    def test_build_paramget_query_frame(self):
        self.assertEqual(
            protocol.build_paramget_query_frame(9),
            ("$PARAMGET?,9" + CRLF).encode("ascii"),
        )

    def test_paramget_roundtrip_has_request_id_and_revision(self):
        frame = "$PARAMGET,9,4,3.50,12.00,25.00,120.00,90.00,1.00,3.00,1"
        kind, data = protocol.parse_frame(frame)
        self.assertEqual(kind, "paramget")
        self.assertEqual(data["request_id"], 9)
        self.assertEqual(data["parameter_revision"], 4)
        self.assertAlmostEqual(data["cut_in_speed_mps"], 3.5)
        self.assertEqual(data["control_mode"], 1)

    def test_paramget_bad_field_count_rejected(self):
        self.assertIsNone(protocol.parse_frame("$PARAMGET,9,4,3.50,12.00"))
        self.assertIsNone(protocol.parse_frame(
            "$PARAMGET,9,4,3.50,12.00,25.00,120.00,90.00,1.00,3.00,1,99"))

    def test_paramget_illegal_request_id_rejected(self):
        self.assertIsNone(protocol.parse_frame(
            "$PARAMGET,abc,4,3.50,12.00,25.00,120.00,90.00,1.00,3.00,1"))


class SyncFrameTests(unittest.TestCase):
    """$SYNC 同步状态帧解析。"""

    def test_sync_accepted(self):
        kind, data = protocol.parse_frame("$SYNC,3,12,accepted")
        self.assertEqual(kind, "sync")
        self.assertEqual(data["a_sync_status"], 3)
        self.assertEqual(data["a_sync_seq"], 12)
        self.assertEqual(data["a_sync_reason"], "accepted")

    def test_sync_rejected_reason(self):
        kind, data = protocol.parse_frame("$SYNC,4,12,invalid_parameter_value:x")
        self.assertEqual(kind, "sync")
        self.assertEqual(data["a_sync_status"], 4)
        self.assertEqual(data["a_sync_reason"], "invalid_parameter_value:x")

    def test_sync_negative_seq_becomes_none(self):
        kind, data = protocol.parse_frame("$SYNC,0,-1,")
        self.assertEqual(kind, "sync")
        self.assertIsNone(data["a_sync_seq"])

    def test_sync_bad_status_rejected(self):
        self.assertIsNone(protocol.parse_frame("$SYNC,abc,1,"))


class ParameterUpdateWhitelistTests(unittest.TestCase):
    """parameter_update 只含 C 白名单字段，control_mode 不发送。"""

    def test_whitelist_excludes_control_mode_and_pitch_full_output(self):
        self.assertEqual(set(config.PARAM_WHITELIST), {
            "wind_rated_power_kw",
            "cut_in_speed_mps",
            "rated_speed_mps",
            "cut_out_speed_mps",
            "pitch_feather_deg",
            "c_control_s",
            "c_timeout_s",
        })
        self.assertNotIn("control_mode", config.PARAM_WHITELIST)
        self.assertNotIn("pitch_full_output_deg", config.PARAM_WHITELIST)


class DatabaseMigrationTests(unittest.TestCase):
    """旧 wind.db 原位迁移保留数据并补齐新列。"""

    def test_legacy_db_migration_preserves_data_and_adds_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "wind.db")
            conn = sqlite3.connect(path)
            # 旧版 telemetry（无 last_wind_action_seq）
            conn.execute(
                "CREATE TABLE telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, "
                "cycle INTEGER, timestamp TEXT, wind_speed_mps REAL, wind_available_kw REAL, "
                "wind_operating_limit_kw REAL, wind_target_kw REAL, wind_actual_kw REAL, "
                "wind_running INTEGER, pitch_target_deg REAL, control_mode INTEGER, "
                "communication_status INTEGER)"
            )
            conn.execute(
                "INSERT INTO telemetry (device_id, cycle, timestamp, wind_speed_mps, "
                "wind_available_kw, wind_target_kw, wind_actual_kw, wind_running, control_mode, "
                "communication_status) VALUES (1,1,'2026-01-01 00:00:00.000',9.5,100,50,50,1,1,1)"
            )
            # 旧版 parameter（无验证/同步列）
            conn.execute(
                "CREATE TABLE parameter (param_id INTEGER PRIMARY KEY, device_id INTEGER, "
                "cut_in_speed_mps REAL, rated_speed_mps REAL, cut_out_speed_mps REAL, "
                "wind_rated_power_kw REAL, pitch_feather_deg REAL, c_control_s REAL, "
                "c_timeout_s REAL, control_mode INTEGER, update_time TEXT)"
            )
            conn.execute(
                "INSERT INTO parameter (param_id, device_id, cut_in_speed_mps, rated_speed_mps, "
                "cut_out_speed_mps, wind_rated_power_kw, pitch_feather_deg, c_control_s, "
                "c_timeout_s, control_mode, update_time) "
                "VALUES (1,1,3,12,25,100,90,1,3,1,'2026-01-01 00:00:00.000')"
            )
            conn.commit()
            conn.close()

            db = Database(path)
            try:
                telemetry_cols = [r[1] for r in db._conn.execute(
                    "PRAGMA table_info(telemetry)").fetchall()]
                param_cols = [r[1] for r in db._conn.execute(
                    "PRAGMA table_info(parameter)").fetchall()]
                self.assertIn("last_wind_action_seq", telemetry_cols)
                for col in ("parameter_revision", "parameter_verified", "verified_at_utc",
                            "verify_reason", "a_sync_status", "a_sync_seq", "a_sync_reason"):
                    self.assertIn(col, param_cols)

                # 历史数据保留
                rows = db.query_telemetry("2026-01-01 00:00:00", "2026-12-31 23:59:59")
                self.assertEqual(len(rows), 1)
                self.assertEqual(db.get_params()["wind_rated_power_kw"], 100.0)
                meta = db.get_param_meta()
                self.assertIn("parameter_revision", meta)
            finally:
                db.close()

    def test_param_verify_and_sync_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "wind.db"))
            try:
                db.mark_param_verified(1, revision=3)
                meta = db.get_param_meta()
                self.assertEqual(meta["parameter_verified"], 1)
                self.assertEqual(meta["parameter_revision"], 3)
                self.assertIsNotNone(meta["verified_at_utc"])

                db.mark_param_verified(0, revision=3, reason="回读不一致: wind_rated_power_kw")
                meta = db.get_param_meta()
                self.assertEqual(meta["parameter_verified"], 0)
                self.assertIn("回读不一致", meta["verify_reason"])

                db.record_param_sync(config.A_SYNC_ACCEPTED, 12, "accepted")
                meta = db.get_param_meta()
                self.assertEqual(meta["a_sync_status"], config.A_SYNC_ACCEPTED)
                self.assertEqual(meta["a_sync_seq"], 12)
                self.assertEqual(meta["a_sync_reason"], "accepted")
            finally:
                db.close()


class ParamReadbackDbTests(unittest.TestCase):
    """「读回 MCU 参数」链路：数据库以 MCU 串口读回值为准，GUI 离线改动可被覆盖并留痕。"""

    def _adjust_rows(self, db, source=None):
        sql = "SELECT parameter_name, old_value, new_value, source FROM remote_adjust"
        args = ()
        if source is not None:
            sql += " WHERE source=?"
            args = (source,)
        return [tuple(r) for r in db._conn.execute(sql, args).fetchall()]

    def test_gui_apply_records_source_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "wind.db"))
            try:
                params = db.get_params()
                params["wind_rated_power_kw"] = 120.0
                changes = db.update_params(params, source="GUI")
                self.assertEqual([c[0] for c in changes], ["wind_rated_power_kw"])
                rows = self._adjust_rows(db, source="GUI")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][1], 100.0)   # old
                self.assertEqual(rows[0][2], 120.0)   # new
                self.assertEqual(db.get_params()["wind_rated_power_kw"], 120.0)
            finally:
                db.close()

    def test_sync_from_mcu_overrides_gui_values_with_audit_trail(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "wind.db"))
            try:
                # 模拟"MCU 断开时 GUI 改参数"：库中被写成 GUI 意图值（未下发）
                gui_params = db.get_params()
                gui_params["wind_rated_power_kw"] = 120.0
                gui_params["cut_in_speed_mps"] = 4.0
                db.update_params(gui_params, source="GUI")
                db.mark_param_verified(0, reason="未下发：串口未连接，仅写入本地数据库")

                # 模拟重连后 $PARAMGET 读回：MCU 实际生效值仍是旧参数
                mcu_payload = dict(gui_params)
                mcu_payload["wind_rated_power_kw"] = 100.0
                mcu_payload["cut_in_speed_mps"] = 3.0
                changes = db.sync_params_from_mcu(mcu_payload, revision=7)

                # 数据库以 MCU 值为准
                self.assertEqual(db.get_params()["wind_rated_power_kw"], 100.0)
                self.assertEqual(db.get_params()["cut_in_speed_mps"], 3.0)
                # 被覆盖字段逐项留痕：source=MCU，old=GUI 值，new=MCU 值
                mcu_rows = self._adjust_rows(db, source="MCU")
                self.assertEqual({r[0] for r in mcu_rows},
                                 {"wind_rated_power_kw", "cut_in_speed_mps"})
                for name, old_v, new_v, _ in mcu_rows:
                    if name == "wind_rated_power_kw":
                        self.assertEqual((old_v, new_v), (120.0, 100.0))
                    else:
                        self.assertEqual((old_v, new_v), (4.0, 3.0))
                # verified 置 1（库值 == MCU 值），revision 刷新
                meta = db.get_param_meta()
                self.assertEqual(meta["parameter_verified"], 1)
                self.assertEqual(meta["parameter_revision"], 7)
                self.assertIn("MCU 读回同步", meta["verify_reason"])
                self.assertEqual(len(changes), 2)
            finally:
                db.close()

    def test_sync_from_mcu_identical_values_no_adjust_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "wind.db"))
            try:
                params = db.get_params()
                changes = db.sync_params_from_mcu(params, revision=2)
                self.assertEqual(changes, [])
                self.assertEqual(self._adjust_rows(db, source="MCU"), [])
                self.assertEqual(db.get_param_meta()["parameter_revision"], 2)
            finally:
                db.close()

    def test_sync_from_mcu_partial_payload_keeps_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "wind.db"))
            try:
                before = db.get_params()
                changes = db.sync_params_from_mcu({"wind_rated_power_kw": 90.0}, revision=5)
                after = db.get_params()
                self.assertEqual(changes, [("wind_rated_power_kw", 100.0, 90.0)])
                for k in config.PARAM_FIELDS:
                    if k != "wind_rated_power_kw":
                        self.assertEqual(after[k], before[k])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
