"""First-stage tests for B's dispatch constraints."""

import unittest

from B_dispatch.dispatch import DispatchConfig, DispatchError, GridState, calculate_dispatch


class DispatchTests(unittest.TestCase):
    def state(self, **overrides):
        data = dict(
            session_id="test-session",
            step=1,
            sim_time_s=1.0,
            wind_speed_mps=8.0,
            load_power_kw=60.0,
            wind_actual_kw=40.0,
            diesel_actual_kw=0.0,
            wind_running=True,
            received_age_s=0.1,
        )
        data.update(overrides)
        return GridState(**data)

    def test_wind_first_and_diesel_off_when_wind_covers_load(self):
        result = calculate_dispatch(self.state(load_power_kw=30.0, wind_actual_kw=40.0))
        self.assertEqual(result.wind_target_kw, 30.0)
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertTrue(result.wind_enable)
        self.assertFalse(result.diesel_enable)

    def test_diesel_compensates_but_keeps_10kw_reserve(self):
        cfg = DispatchConfig(diesel_max_kw=100.0, reserve_kw=10.0)
        result = calculate_dispatch(self.state(load_power_kw=150.0, wind_actual_kw=20.0), cfg)
        self.assertEqual(result.wind_target_kw, 20.0)
        self.assertEqual(result.diesel_target_kw, 90.0)
        self.assertEqual(result.target_unserved_kw, 40.0)

    def test_diesel_can_be_completely_off(self):
        result = calculate_dispatch(self.state(load_power_kw=0.0, wind_actual_kw=0.0))
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertFalse(result.diesel_enable)

    def test_c_fault_has_priority_over_b_wind_request(self):
        result = calculate_dispatch(self.state(load_power_kw=50.0, wind_actual_kw=50.0, fault=True))
        self.assertEqual(result.wind_target_kw, 0.0)
        self.assertFalse(result.wind_enable)
        self.assertEqual(result.diesel_target_kw, 50.0)

    def test_stale_state_is_rejected(self):
        with self.assertRaises(DispatchError):
            calculate_dispatch(self.state(received_age_s=2.1))

    def test_config_cannot_consume_reserve(self):
        with self.assertRaises(DispatchError):
            calculate_dispatch(self.state(), DispatchConfig(diesel_max_kw=10.0, reserve_kw=10.1))


if __name__ == "__main__":
    unittest.main()
