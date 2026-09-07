"""Tests for the network-free B EMS runtime scheduler."""

import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore
from B_dispatch.runtime import EMSRuntime, RuntimeConfig


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RuntimeTests(unittest.TestCase):
    def _state(self, step: int) -> GridState:
        return GridState(
            session_id="session-1",
            step=step,
            sim_time_s=float(step),
            wind_speed_mps=8.0,
            load_power_kw=80.0,
            wind_actual_kw=50.0,
            diesel_actual_kw=0.0,
            wind_running=True,
            received_age_s=0.1,
        )

    def test_first_poll_and_dispatch_happen_immediately(self):
        clock = FakeClock()
        states = [self._state(1)]
        decisions = []
        runtime = EMSRuntime(
            EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)),
            lambda: states[-1],
            decisions.append,
            clock=clock,
        )

        decision = runtime.run_cycle()

        self.assertIsNotNone(decision)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].state.step, 1)

    def test_polling_is_one_second_and_dispatch_is_five_seconds(self):
        clock = FakeClock()
        provider_calls = []
        decisions = []

        def provider():
            step = len(provider_calls) + 1
            provider_calls.append(step)
            return self._state(step)

        runtime = EMSRuntime(
            EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)),
            provider,
            decisions.append,
            RuntimeConfig(poll_period_s=1.0, dispatch_period_s=5.0),
            clock=clock,
        )

        runtime.run_cycle(0.0)
        clock.advance(0.5)
        runtime.run_cycle()
        self.assertEqual(len(provider_calls), 1)
        self.assertEqual(len(decisions), 1)

        clock.advance(0.5)
        runtime.run_cycle()
        self.assertEqual(len(provider_calls), 2)
        self.assertEqual(len(decisions), 1)

        clock.advance(3.0)
        runtime.run_cycle()
        self.assertEqual(len(provider_calls), 5)
        self.assertEqual(len(decisions), 1)

        clock.advance(1.0)
        runtime.run_cycle()
        self.assertEqual(len(provider_calls), 6)
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[-1].state.step, 6)

    def test_latest_polled_state_is_used_for_dispatch(self):
        clock = FakeClock()
        states = [self._state(1), self._state(2)]
        calls = []
        decisions = []

        def provider():
            calls.append(1)
            return states[min(len(calls) - 1, len(states) - 1)]

        runtime = EMSRuntime(
            EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)),
            provider,
            decisions.append,
            clock=clock,
        )

        runtime.run_cycle(0.0)
        clock.advance(1.0)
        runtime.run_cycle()
        self.assertEqual(len(decisions), 1)
        clock.advance(4.0)
        runtime.run_cycle()

        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[-1].state.step, 2)

    def test_invalid_runtime_config_is_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(poll_period_s=0.0)
        with self.assertRaises(ValueError):
            RuntimeConfig(dispatch_period_s=-1.0)


if __name__ == "__main__":
    unittest.main()
