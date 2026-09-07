"""Tests for Student A's software-only simulator foundation."""

from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from A_simulator.config import load_config
from A_simulator.models import (
    ControlInputs,
    DieselGeneratorParameters,
    SimulationState,
    WindTurbineParameters,
    simulate_step,
    wind_available_power,
)
from A_simulator.repository import Repository
from A_simulator.scenario import ScenarioCurve, ScenarioPoint, load_scenario_csv
from A_simulator.server import ProtocolError, SimulatorTCPServer, decode_frame


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "A_simulator" / "config.example.json"
SCENARIO_PATH = ROOT / "A_simulator" / "scenarios" / "demo.csv"
DEFAULT_SCENARIO_PATH = ROOT / "A_simulator" / "scenarios" / "antarctic_10min.csv"


def base_state() -> SimulationState:
    return SimulationState(
        session_id="test-session",
        step=0,
        sim_time_s=0.0,
        sampled_at_utc="2026-09-07T00:00:00.000Z",
        wind_speed_mps=0.0,
        load_power_kw=0.0,
        wind_available_kw=0.0,
        wind_target_kw=0.0,
        wind_actual_kw=0.0,
        diesel_target_kw=0.0,
        diesel_actual_kw=0.0,
        pitch_actual_deg=0.0,
        wind_running=False,
        diesel_running=False,
        fault=False,
        power_imbalance_kw=0.0,
    )


class ModelTests(unittest.TestCase):
    def setUp(self):
        config = load_config(CONFIG_PATH)
        self.wind = config.wind
        self.diesel = config.diesel
        self.controls = ControlInputs(100, 120, True, True, True, 0)

    def test_wind_curve_covers_no_wind_normal_and_cut_out(self):
        self.assertEqual(wind_available_power(0, self.wind), 0)
        self.assertGreater(wind_available_power(8, self.wind), 0)
        self.assertEqual(wind_available_power(self.wind.rated_speed_mps, self.wind), 100)
        self.assertEqual(wind_available_power(self.wind.cut_out_speed_mps, self.wind), 0)

    def test_targets_are_not_copied_to_actual_outputs(self):
        state = simulate_step(
            previous=base_state(), next_step=1, next_sim_time_s=1, step_s=1,
            sampled_at_utc="2026-09-07T00:00:01.000Z",
            wind_speed_mps=8, load_power_kw=150, controls=self.controls,
            wind=self.wind, diesel=self.diesel,
        )
        self.assertLess(state.wind_actual_kw, state.wind_target_kw)
        self.assertEqual(state.diesel_actual_kw, self.diesel.ramp_up_kw_per_s)
        self.assertAlmostEqual(
            state.power_imbalance_kw,
            state.load_power_kw - state.wind_actual_kw - state.diesel_actual_kw,
        )

    def test_diesel_limits_and_shutdown_ramp(self):
        previous = replace(base_state(), diesel_actual_kw=80, diesel_running=True)
        low_target = replace(self.controls, diesel_target_kw=0)
        low = simulate_step(
            previous=previous, next_step=1, next_sim_time_s=1, step_s=1,
            sampled_at_utc="2026-09-07T00:00:01.000Z",
            wind_speed_mps=0, load_power_kw=10, controls=low_target,
            wind=self.wind, diesel=self.diesel,
        )
        self.assertEqual(low.diesel_actual_kw, 40)
        off = simulate_step(
            previous=low, next_step=2, next_sim_time_s=2, step_s=1,
            sampled_at_utc="2026-09-07T00:00:02.000Z",
            wind_speed_mps=0, load_power_kw=10,
            controls=replace(low_target, diesel_enable=False), wind=self.wind, diesel=self.diesel,
        )
        self.assertEqual(off.diesel_actual_kw, 0)

    def test_controller_enable_and_pitch_are_actions_not_load_changes(self):
        controls = replace(self.controls, controller_wind_enable=False, pitch_target_deg=45)
        state = simulate_step(
            previous=base_state(), next_step=1, next_sim_time_s=1, step_s=1,
            sampled_at_utc="2026-09-07T00:00:01.000Z",
            wind_speed_mps=12, load_power_kw=77, controls=controls,
            wind=self.wind, diesel=self.diesel,
        )
        self.assertEqual(state.wind_actual_kw, 0)
        self.assertEqual(state.load_power_kw, 77)

    def test_sample_timestamp_requires_fixed_utc_format(self):
        with self.assertRaisesRegex(ValueError, "sampled_at_utc"):
            replace(base_state(), sampled_at_utc="2026-09-07 08:03:25")


class ScenarioTests(unittest.TestCase):
    def test_load_and_interpolate_independent_columns(self):
        curve = load_scenario_csv(SCENARIO_PATH)
        point = curve.at(2.5)
        self.assertEqual(point.wind_speed_mps, 6)
        self.assertEqual(point.load_power_kw, 62.5)

    def test_reject_duplicate_times(self):
        with self.assertRaises(ValueError):
            ScenarioCurve((ScenarioPoint(0, 1, 2), ScenarioPoint(0, 2, 3)))

    def test_csv_requires_an_explicit_sequential_step_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text(
                "step,sim_time_s,wind_speed_mps,load_power_kw\n0,0,1,2\n2,1,2,3\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_scenario_csv(path)

    def test_default_scenario_has_a_complete_ten_minute_axis(self):
        curve = load_scenario_csv(DEFAULT_SCENARIO_PATH)
        self.assertEqual(len(curve.points), 601)
        self.assertEqual(curve.points[0].sim_time_s, 0.0)
        self.assertEqual(curve.points[-1].sim_time_s, 600.0)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "grid.db"
        self.repo = Repository(self.db)
        config = replace(load_config(CONFIG_PATH), end_s=10.0)
        self.session = self.repo.initialize(config, load_scenario_csv(SCENARIO_PATH))

    def tearDown(self):
        self.temp.cleanup()

    def test_step_persists_current_history_and_scada_atomically(self):
        self.repo.set_status("start")
        sampled_at = "2099-01-01T00:00:01.000Z"
        with patch("A_simulator.repository._utc_now", return_value=sampled_at) as clock:
            state = self.repo.step_once()
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.sampled_at_utc, sampled_at)
        clock.assert_called_once_with()
        self.assertEqual(self.repo.get_state().step, 1)
        import sqlite3
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM state_history").fetchone()[0], 2)
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM scada_history").fetchone()[0], 11)
            current_time = connection.execute(
                "SELECT sampled_at_utc FROM current_state WHERE singleton_id=1"
            ).fetchone()[0]
            history_times = {
                row[0] for row in connection.execute(
                    "SELECT sampled_at_utc FROM state_history WHERE step=1"
                )
            }
            scada_times = {
                row[0] for row in connection.execute(
                    "SELECT sampled_at_utc FROM scada_history WHERE step=1"
                )
            }
            self.assertEqual(current_time, sampled_at)
            self.assertEqual(history_times, {sampled_at})
            self.assertEqual(scada_times, {sampled_at})

    def test_command_is_validated_deduplicated_and_ordered(self):
        message = {
            "version": 1, "type": "dispatch", "source": "B", "target": "A",
            "session_id": self.session, "seq": 1, "step": 0, "sim_time_s": 0,
            "payload": {"wind_target_kw": 50, "diesel_target_kw": 70,
                        "wind_enable": True, "diesel_enable": True},
        }
        first = self.repo.apply_command(message)
        duplicate = self.repo.apply_command(message)
        older = self.repo.apply_command({**message, "seq": 0})
        self.assertTrue(first.accepted)
        self.assertTrue(duplicate.accepted)
        self.assertTrue(duplicate.duplicate)
        self.assertFalse(older.accepted)
        self.assertEqual(older.reason, "out_of_order")


class ProtocolTests(unittest.TestCase):
    def test_rejects_nan_and_oversize(self):
        raw = (b'{"version":1,"type":"state_request","source":"B","target":"A",'
               b'"session_id":null,"seq":1,"step":0,"sim_time_s":NaN,"payload":{"full":true}}\n')
        with self.assertRaises(ProtocolError):
            decode_frame(raw, 4096)
        with self.assertRaises(ProtocolError):
            decode_frame(b"{}\n", 2)

    def test_tcp_handles_split_and_coalesced_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            server = SimulatorTCPServer(("127.0.0.1", 0), repo, 4096)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                address = server.server_address
                request = {
                    "version": 1, "type": "state_request", "source": "B", "target": "A",
                    "session_id": None, "seq": 1, "step": 0, "sim_time_s": 0,
                    "payload": {"full": True},
                }
                frame1 = (json.dumps(request) + "\n").encode()
                frame2 = (json.dumps({**request, "seq": 2}) + "\n").encode()
                with socket.create_connection(address, timeout=2) as client:
                    client.sendall(frame1[:17])
                    client.sendall(frame1[17:] + frame2)
                    with client.makefile("rb") as stream:
                        responses = [json.loads(stream.readline()), json.loads(stream.readline())]
                self.assertEqual([item["type"] for item in responses], ["state", "state"])
                self.assertEqual([item["target"] for item in responses], ["B", "B"])
                for item in responses:
                    self.assertRegex(
                        item["payload"]["sampled_at_utc"],
                        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

