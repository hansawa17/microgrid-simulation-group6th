"""Regression tests for B transport freshness normalization."""

import unittest

from B_dispatch.communication_service import EMSCommunicationService
from B_dispatch.models import GridState


class StateFreshnessTests(unittest.TestCase):
    def test_newly_received_state_starts_with_zero_transport_age(self):
        state = GridState(
            session_id="s1",
            step=7,
            sim_time_s=7.0,
            wind_speed_mps=8.0,
            wind_available_kw=70.0,
            wind_operating_limit_kw=60.0,
            load_power_kw=80.0,
            wind_actual_kw=50.0,
            diesel_actual_kw=30.0,
            wind_running=True,
            received_age_s=56.74,
            sampled_at_utc="2026-09-07T08:03:25.417Z",
            received_at_utc="2026-09-07T08:04:22.157Z",
        )

        fresh = EMSCommunicationService._freshly_received_state(state)

        self.assertEqual(fresh.received_age_s, 0.0)
        self.assertEqual(fresh.sampled_at_utc, state.sampled_at_utc)
        self.assertEqual(fresh.received_at_utc, state.received_at_utc)
        self.assertEqual(fresh.session_id, state.session_id)
        self.assertEqual(fresh.step, state.step)


if __name__ == "__main__":
    unittest.main()
