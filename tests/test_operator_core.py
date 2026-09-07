"""Tests for the stateless B closed-loop operator core."""

import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore


class OperatorCoreTests(unittest.TestCase):
    def test_decide_returns_dispatch_result(self):
        state = GridState(
            session_id="session-1",
            step=3,
            sim_time_s=3.0,
            wind_speed_mps=8.0,
            load_power_kw=80.0,
            wind_actual_kw=50.0,
            diesel_actual_kw=0.0,
            wind_running=True,
            received_age_s=0.1,
        )
        core = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0))

        decision = core.decide(state)

        self.assertIs(decision.state, state)
        self.assertEqual(decision.result.wind_target_kw, 50.0)
        self.assertEqual(decision.result.diesel_target_kw, 30.0)

    def test_stale_state_is_rejected_by_core(self):
        state = GridState(
            session_id="session-1",
            step=3,
            sim_time_s=3.0,
            wind_speed_mps=8.0,
            load_power_kw=80.0,
            wind_actual_kw=50.0,
            diesel_actual_kw=0.0,
            wind_running=True,
            received_age_s=3.0,
        )
        core = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0))

        with self.assertRaises(ValueError):
            core.decide(state)


if __name__ == "__main__":
    unittest.main()
