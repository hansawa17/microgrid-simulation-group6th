import tempfile
import unittest
from pathlib import Path

from B_dispatch.evaluation import evaluate_repository
from B_dispatch.repository import EMSRepository


class EvaluationTests(unittest.TestCase):
    def test_empty_database_is_read_only_and_returns_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            result = evaluate_repository(repo, "5m")
            self.assertEqual(result.sample_count, 0)
            self.assertEqual(result.constraint_violations, 0)
            self.assertEqual(result.overall_score, 60.0)

    def test_evaluation_reads_existing_state_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            state = {
                "session_id": "EVAL",
                "step": 1,
                "sim_time_s": 1.0,
                "wind_speed_mps": 10.0,
                "wind_available_kw": 80.0,
                "wind_operating_limit_kw": 80.0,
                "load_power_kw": 100.0,
                "wind_actual_kw": 80.0,
                "diesel_actual_kw": 20.0,
                "wind_target_kw": 80.0,
                "pitch_actual_deg": 0.0,
                "wind_running": True,
                "fault": False,
                "sampled_at_utc": "2026-09-09T07:00:00.000Z",
                "received_at_utc": "2026-09-09T07:00:00.000Z",
                "received_age_s": 0.0,
            }
            repo.save_state(state)
            result = evaluate_repository(repo, "session")
            self.assertEqual(result.sample_count, 1)
            self.assertAlmostEqual(result.avg_balance_error_kw, 0.0)
            self.assertEqual(result.constraint_violations, 0)
            self.assertAlmostEqual(result.wind_utilization_pct, 100.0)
            self.assertAlmostEqual(result.unserved_kw, 0.0)


if __name__ == "__main__":
    unittest.main()
