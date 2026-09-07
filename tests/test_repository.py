"""Tests for the local EMS SQLite repository."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from B_dispatch.repository import EMSRepository


class RepositoryTests(unittest.TestCase):
    def test_initialize_creates_expected_tables_without_physical_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ems.db"
            repo = EMSRepository(db)
            repo.initialize()
            conn = sqlite3.connect(db)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"schema_meta", "dispatch_parameters", "ems_runtime_config", "current_state",
                                 "state_history", "dispatch_commands", "dispatch_evaluation", "event_log"} <= tables)
                self.assertIsNone(conn.execute("SELECT 1 FROM dispatch_parameters WHERE id=1").fetchone())
            finally:
                conn.close()

    def test_parameters_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            repo.set_parameters(wind_min_kw=0.0, wind_max_kw=80.0, diesel_max_kw=100.0, reserve_kw=10.0)
            row = repo.get_parameters()
            self.assertEqual(row["wind_max_kw"], 80.0)
            self.assertEqual(row["diesel_max_kw"], 100.0)
            self.assertEqual(row["reserve_kw"], 10.0)

    def test_state_and_history_are_written_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            state = {
                "session_id": "s1", "step": 1, "sim_time_s": 1.0, "wind_speed_mps": 8.0,
                "load_power_kw": 50.0, "wind_actual_kw": 30.0, "diesel_actual_kw": 20.0,
                "wind_target_kw": 30.0, "pitch_actual_deg": 2.0, "wind_running": True,
                "fault": False, "received_at_utc": "2026-09-07T03:00:00Z", "received_age_s": 0.1,
            }
            repo.save_state(state)
            current = repo.get_current_state()
            self.assertEqual(current["pitch_actual_deg"], 2.0)
            self.assertEqual(current["wind_running"], 1)
            conn = sqlite3.connect(repo.db_path)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM current_state").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM state_history").fetchone()[0], 1)
                history = conn.execute("SELECT pitch_actual_deg, wind_running, fault FROM state_history").fetchone()
                self.assertEqual(history, (2.0, 1, 0))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
