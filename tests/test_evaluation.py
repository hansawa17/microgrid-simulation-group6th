import tempfile
import unittest
from pathlib import Path

from B_dispatch.evaluation import EVALUATION_WEIGHTS, _score_balance, evaluate_repository
from B_dispatch.repository import EMSRepository


class EvaluationTests(unittest.TestCase):
    def test_balance_score_uses_the_v1_0_2_thresholds(self):
        expected = (
            (3.0, 100.0),
            (3.001, 80.0),
            (10.0, 80.0),
            (10.001, 60.0),
            (50.0, 60.0),
            (50.001, 40.0),
        )
        for error_kw, score in expected:
            with self.subTest(error_kw=error_kw):
                self.assertEqual(_score_balance(error_kw), score)

    def test_empty_database_is_read_only_and_uses_five_item_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            result = evaluate_repository(repo, "5m")
            self.assertEqual(result.sample_count, 0)
            self.assertEqual(result.constraint_violations, 0)
            self.assertEqual(result.dispatch_count, 0)
            self.assertAlmostEqual(sum(EVALUATION_WEIGHTS.values()), 1.0)
            self.assertAlmostEqual(result.overall_score, 85.0)

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
                "diesel_target_kw": 20.0,
                "pitch_actual_deg": 0.0,
                "wind_running": True,
                "diesel_running": True,
                "fault": False,
                "power_imbalance_kw": 0.0,
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
            self.assertEqual(result.dispatch_count, 0)


if __name__ == "__main__":
    unittest.main()
