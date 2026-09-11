"""Tests for the local EMS SQLite repository."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from B_dispatch.repository import EMSRepository


class RepositoryTests(unittest.TestCase):
    def test_initialize_creates_expected_tables_and_unified_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ems.db"
            repo = EMSRepository(db)
            repo.initialize()
            conn = sqlite3.connect(db)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"schema_meta", "physical_parameters", "dispatch_parameters", "ems_runtime_config", "current_state",
                                 "state_history", "dispatch_commands", "dispatch_evaluation", "event_log",
                                 "dispatch_outbox", "process_status", "communication_config"} <= tables)
                physical = conn.execute(
                    "SELECT wind_rated_kw, wind_cut_in_mps, wind_rated_speed_mps, wind_cut_out_mps, "
                    "pitch_min_deg, pitch_max_deg, wind_ramp_up_kw_s, wind_ramp_down_kw_s, "
                    "diesel_min_kw, diesel_max_kw, diesel_ramp_up_kw_s, diesel_ramp_down_kw_s "
                    "FROM physical_parameters WHERE id=1"
                ).fetchone()
                self.assertEqual(physical, (100.0, 3.0, 12.0, 25.0, 0.0, 90.0, 40.0, 60.0, 20.0, 120.0, 30.0, 40.0))
                params = conn.execute("SELECT wind_min_kw, wind_max_kw, diesel_max_kw, reserve_kw FROM dispatch_parameters WHERE id=1").fetchone()
                self.assertEqual(params, (0.0, 100.0, 120.0, 10.0))
                runtime = conn.execute("SELECT poll_period_s, dispatch_period_s, closed_loop, command_timeout_s, max_state_age_s FROM ems_runtime_config WHERE id=1").fetchone()
                self.assertEqual(runtime, (1.0, 5.0, 1, 3.0, 8.0))
                self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "7")
                state_columns = {row[1] for row in conn.execute("PRAGMA table_info(current_state)")}
                self.assertTrue({"wind_available_kw", "wind_operating_limit_kw", "diesel_target_kw",
                                 "diesel_running", "power_imbalance_kw"} <= state_columns)
                command_columns = {row[1] for row in conn.execute("PRAGMA table_info(dispatch_commands)")}
                self.assertTrue({"ack_accepted", "ack_reason", "ack_received_at_utc"} <= command_columns)
            finally:
                conn.close()

    def test_parameters_are_editable_and_survive_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            repo.set_parameters(wind_min_kw=0.0, wind_max_kw=100.0, diesel_max_kw=120.0, reserve_kw=10.0)
            row = repo.get_parameters()
            self.assertEqual(tuple(row[k] for k in ("wind_min_kw", "wind_max_kw", "diesel_max_kw", "reserve_kw")),
                             (0.0, 100.0, 120.0, 10.0))
            repo.set_parameters(wind_min_kw=5.0, wind_max_kw=80.0, diesel_max_kw=100.0, reserve_kw=12.0)
            repo.initialize()
            row = repo.get_parameters()
            self.assertEqual(tuple(row[k] for k in ("wind_min_kw", "wind_max_kw", "diesel_max_kw", "reserve_kw")),
                             (5.0, 80.0, 100.0, 12.0))
            physical = repo.get_physical_parameters()
            self.assertEqual(physical["wind_rated_kw"], 100.0)
            self.assertEqual(physical["wind_cut_in_mps"], 3.0)
            self.assertEqual(physical["wind_cut_out_mps"], 25.0)
            self.assertEqual(physical["diesel_min_kw"], 20.0)
            self.assertEqual(physical["diesel_max_kw"], 120.0)

    def test_invalid_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            with self.assertRaises(ValueError):
                repo.set_parameters(wind_min_kw=20.0, wind_max_kw=10.0, diesel_max_kw=100.0)
            with self.assertRaises(ValueError):
                repo.set_parameters(wind_min_kw=0.0, wind_max_kw=80.0, diesel_max_kw=5.0, reserve_kw=10.0)

    def test_runtime_config_is_editable_and_survives_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            repo.set_runtime_config(poll_period_s=1.0, dispatch_period_s=5.0,
                                    closed_loop=True, command_timeout_s=3.0)
            row = repo.get_runtime_config()
            self.assertEqual((row["poll_period_s"], row["dispatch_period_s"], row["closed_loop"], row["command_timeout_s"]),
                             (1.0, 5.0, 1, 3.0))
            repo.set_runtime_config(poll_period_s=2.0, dispatch_period_s=7.0,
                                    closed_loop=False, command_timeout_s=4.0,
                                    max_state_age_s=3.0)
            repo.initialize()
            row = repo.get_runtime_config()
            self.assertEqual((row["poll_period_s"], row["dispatch_period_s"], row["closed_loop"],
                              row["command_timeout_s"], row["max_state_age_s"]),
                             (2.0, 7.0, 0, 4.0, 3.0))

    def test_schema_v6_default_state_age_is_migrated_without_overwriting_custom_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ems.db"
            repo = EMSRepository(db)
            repo.initialize()
            with repo.connection() as conn:
                conn.execute("UPDATE schema_meta SET value='6' WHERE key='schema_version'")
                conn.execute("UPDATE ems_runtime_config SET max_state_age_s=2 WHERE id=1")
            repo.initialize()
            self.assertEqual(repo.get_runtime_config()["max_state_age_s"], 8.0)

            repo.set_runtime_config(max_state_age_s=3.0)
            with repo.connection() as conn:
                conn.execute("UPDATE schema_meta SET value='6' WHERE key='schema_version'")
            repo.initialize()
            self.assertEqual(repo.get_runtime_config()["max_state_age_s"], 3.0)

    def test_runtime_config_rejects_non_positive_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            with self.assertRaises(ValueError):
                repo.set_runtime_config(poll_period_s=0.0)
            with self.assertRaises(ValueError):
                repo.set_runtime_config(dispatch_period_s=-1.0)
            with self.assertRaises(ValueError):
                repo.set_runtime_config(command_timeout_s=0.0)

    def test_state_history_keeps_protocol_power_fields_and_two_times_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            state = {
                "session_id": "s1", "step": 1, "sim_time_s": 1.0, "wind_speed_mps": 8.0,
                "wind_available_kw": 70.0, "wind_operating_limit_kw": 60.0,
                "load_power_kw": 50.0, "wind_actual_kw": 30.0, "diesel_actual_kw": 20.0,
                "wind_target_kw": 30.0, "diesel_target_kw": 20.0,
                "pitch_actual_deg": 2.0, "wind_running": True, "diesel_running": True,
                "power_imbalance_kw": 0.0,
                "fault": False, "sampled_at_utc": "2026-09-07T03:00:00.123Z",
                "received_at_utc": "2026-09-07T03:00:00.456Z", "received_age_s": 0.1,
            }
            repo.save_state(state)
            current = repo.get_current_state()
            self.assertEqual(current["wind_available_kw"], 70.0)
            self.assertEqual(current["wind_operating_limit_kw"], 60.0)
            self.assertEqual(current["diesel_target_kw"], 20.0)
            self.assertEqual(current["diesel_running"], 1)
            self.assertEqual(current["power_imbalance_kw"], 0.0)
            self.assertEqual(current["pitch_actual_deg"], 2.0)
            self.assertEqual(current["sampled_at_utc"], "2026-09-07T03:00:00.123Z")
            self.assertEqual(current["received_at_utc"], "2026-09-07T03:00:00.456Z")
            conn = sqlite3.connect(repo.db_path)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM current_state").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM state_history").fetchone()[0], 1)
                history = conn.execute("SELECT wind_available_kw, wind_operating_limit_kw, pitch_actual_deg, wind_running, fault, sampled_at_utc FROM state_history").fetchone()
                self.assertEqual(history, (70.0, 60.0, 2.0, 1, 0, "2026-09-07T03:00:00.123Z"))
            finally:
                conn.close()

    def test_command_evaluation_and_log_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            command_id = repo.record_command({
                "session_id": "s1", "step": 2, "sim_time_s": 2.0, "seq": 7,
                "wind_target_kw": 40.0, "diesel_target_kw": 30.0,
                "wind_enable": True, "diesel_enable": True,
                "status": "accepted", "reason": "wind-first",
                "ack_accepted": True, "ack_reason": "accepted",
            })
            evaluation_id = repo.record_evaluation(command_id=command_id, target_unserved_kw=0.0,
                                                    target_surplus_kw=0.0, actual_unserved_kw=2.0,
                                                    actual_surplus_kw=0.0)
            self.assertGreater(evaluation_id, 0)
            repo.record_log("INFO", "dispatch", "command accepted", session_id="s1", step=2)
            conn = sqlite3.connect(repo.db_path)
            try:
                command = conn.execute("SELECT session_id, seq, wind_target_kw, diesel_target_kw, wind_enable, diesel_enable, status, ack_accepted, ack_reason FROM dispatch_commands").fetchone()
                self.assertEqual(command, ("s1", 7, 40.0, 30.0, 1, 1, "accepted", 1, "accepted"))
                evaluation = conn.execute("SELECT command_id, target_unserved_kw, actual_unserved_kw FROM dispatch_evaluation").fetchone()
                self.assertEqual(evaluation, (command_id, 0.0, 2.0))
                log = conn.execute("SELECT level, event_type, session_id, step FROM event_log").fetchone()
                self.assertEqual(log, ("INFO", "dispatch", "s1", 2))
            finally:
                conn.close()

    def test_missing_state_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            with self.assertRaises(ValueError):
                repo.save_state({"session_id": "s1"})


if __name__ == "__main__":
    unittest.main()
