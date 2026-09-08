"""Tests for B's protocol-aligned dispatch constraints."""

import unittest

from B_dispatch.dispatch import DispatchError, calculate_dispatch
from B_dispatch.models import DispatchConfig, GridState


class DispatchTests(unittest.TestCase):
    def state(self, **overrides):
        data = dict(session_id="test-session", step=1, sim_time_s=1.0, wind_speed_mps=8.0,
                    wind_available_kw=70.0, wind_operating_limit_kw=70.0,
                    load_power_kw=60.0, wind_actual_kw=40.0, diesel_actual_kw=0.0,
                    wind_running=True, received_age_s=0.1)
        data.update(overrides)
        return GridState(**data)

    def cfg(self, **overrides):
        data = dict(wind_max_kw=100.0, diesel_max_kw=100.0, reserve_kw=10.0)
        data.update(overrides)
        return DispatchConfig(**data)

    def test_wind_first_and_diesel_off_when_wind_covers_load(self):
        result = calculate_dispatch(self.state(load_power_kw=30.0, wind_actual_kw=0.0), self.cfg())
        self.assertEqual(result.wind_target_kw, 30.0)
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertFalse(result.diesel_enable)

    def test_cold_start_uses_operating_limit_not_actual_power(self):
        result = calculate_dispatch(
            self.state(load_power_kw=50.0, wind_available_kw=70.0, wind_operating_limit_kw=70.0, wind_actual_kw=0.0),
            self.cfg(),
        )
        self.assertEqual(result.wind_target_kw, 50.0)
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertTrue(result.wind_enable)

    def test_operating_limit_caps_wind_target(self):
        result = calculate_dispatch(
            self.state(load_power_kw=70.0, wind_available_kw=80.0, wind_operating_limit_kw=30.0, wind_actual_kw=5.0),
            self.cfg(),
        )
        self.assertEqual(result.wind_target_kw, 30.0)
        self.assertEqual(result.diesel_target_kw, 40.0)

    def test_available_power_does_not_override_lower_operating_limit(self):
        result = calculate_dispatch(
            self.state(load_power_kw=100.0, wind_available_kw=90.0, wind_operating_limit_kw=20.0),
            self.cfg(),
        )
        self.assertEqual(result.wind_target_kw, 20.0)
        self.assertEqual(result.diesel_target_kw, 80.0)

    def test_actual_power_is_not_used_as_capability(self):
        result = calculate_dispatch(
            self.state(load_power_kw=60.0, wind_available_kw=80.0, wind_operating_limit_kw=80.0, wind_actual_kw=0.0),
            self.cfg(),
        )
        self.assertEqual(result.wind_target_kw, 60.0)

    def test_diesel_compensates_but_keeps_10kw_reserve(self):
        result = calculate_dispatch(self.state(load_power_kw=150.0, wind_operating_limit_kw=20.0), self.cfg())
        self.assertEqual(result.wind_target_kw, 20.0)
        self.assertEqual(result.diesel_target_kw, 90.0)
        self.assertEqual(result.target_unserved_kw, 40.0)

    def test_diesel_can_be_completely_off(self):
        result = calculate_dispatch(self.state(load_power_kw=0.0, wind_available_kw=0.0, wind_operating_limit_kw=0.0, wind_actual_kw=0.0), self.cfg())
        self.assertEqual(result.diesel_target_kw, 0.0)
        self.assertFalse(result.diesel_enable)

    def test_c_fault_has_priority_over_b_wind_request(self):
        result = calculate_dispatch(self.state(load_power_kw=50.0, fault=True), self.cfg())
        self.assertEqual(result.wind_target_kw, 0.0)
        self.assertFalse(result.wind_enable)
        self.assertEqual(result.diesel_target_kw, 50.0)

    def test_invalid_operating_limit_is_rejected(self):
        with self.assertRaises(DispatchError):
            calculate_dispatch(self.state(wind_available_kw=30.0, wind_operating_limit_kw=31.0), self.cfg())

    def test_stale_state_is_rejected(self):
        with self.assertRaises(DispatchError):
            calculate_dispatch(self.state(received_age_s=2.1), self.cfg())

    def test_unconfirmed_ratings_are_not_hidden_defaults(self):
        with self.assertRaises(TypeError):
            DispatchConfig()

    def test_config_cannot_consume_reserve(self):
        with self.assertRaises(DispatchError):
            calculate_dispatch(self.state(), self.cfg(diesel_max_kw=10.0, reserve_kw=10.1))


if __name__ == "__main__":
    unittest.main()
