"""Tests for the stateless B closed-loop operator core."""

import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore


class OperatorCoreTests(unittest.TestCase):
    def _state(self, **overrides):
        values = {
            "session_id": "session-1",
            "step": 3,
            "sim_time_s": 3.0,
            "wind_speed_mps": 8.0,
            "load_power_kw": 80.0,
            "wind_actual_kw": 50.0,
            "diesel_actual_kw": 0.0,
            "wind_running": True,
            "received_age_s": 0.1,
        }
        values.update(overrides)
        return GridState(**values)

    def _core(self, **overrides):
        values = {"wind_max_kw": 100.0, "diesel_max_kw": 100.0}
        values.update(overrides)
        return EMSCore(DispatchConfig(**values))

    def test_decide_returns_dispatch_result(self):
        state = self._state()
        decision = self._core().decide(state)
        self.assertIs(decision.state, state)
        self.assertEqual(decision.result.wind_target_kw, 50.0)
        self.assertEqual(decision.result.diesel_target_kw, 30.0)
        self.assertTrue(decision.result.wind_enable)
        self.assertTrue(decision.result.diesel_enable)

    def test_wind_can_cover_load_and_diesel_is_off(self):
        state = self._state(load_power_kw=40.0, wind_actual_kw=50.0)
        result = self._core().decide(state).result
        self.assertEqual(result.wind_target_kw, 40.0)
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertTrue(result.wind_enable)
        self.assertFalse(result.diesel_enable)
        self.assertEqual(result.target_unserved_kw, 0.0)

    def test_diesel_reserve_is_not_consumed(self):
        state = self._state(load_power_kw=150.0, wind_actual_kw=20.0)
        result = self._core().decide(state).result
        self.assertEqual(result.diesel_target_kw, 80.0)
        self.assertEqual(result.target_unserved_kw, 0.0)

    def test_load_above_normal_capacity_is_reported_as_unserved(self):
        state = self._state(load_power_kw=200.0, wind_actual_kw=20.0)
        result = self._core().decide(state).result
        self.assertEqual(result.diesel_target_kw, 90.0)
        self.assertEqual(result.target_unserved_kw, 90.0)

    def test_c_priority_fault_suppresses_normal_wind_dispatch(self):
        state = self._state(load_power_kw=80.0, wind_actual_kw=50.0, fault=True)
        result = self._core().decide(state).result
        self.assertEqual(result.wind_target_kw, 0.0)
        self.assertFalse(result.wind_enable)
        self.assertEqual(result.diesel_target_kw, 80.0)
        self.assertIn("C-priority", result.reason)

    def test_stale_state_is_rejected_by_core(self):
        state = self._state(received_age_s=3.0)
        with self.assertRaises(ValueError):
            self._core().decide(state)


if __name__ == "__main__":
    unittest.main()
