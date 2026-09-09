"""Tests for Student A's software-only simulator foundation."""

from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
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
from A_simulator.scenario import (
    ScenarioCurve,
    ScenarioPoint,
    load_scenario_csv,
    save_scenario_csv,
)
from A_simulator.server import (
    MessageProcessor,
    ProtocolError,
    SimulatorTCPServer,
    decode_frame,
    encode_frame,
)


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
        wind_operating_limit_kw=0.0,
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
        self.controls = ControlInputs(100, 120, True, True, True, 0, 100, 100)

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

    def test_operating_limit_excludes_b_target_and_enable(self):
        controls = replace(
            self.controls,
            wind_target_kw=0.0,
            dispatch_wind_enable=False,
            controller_wind_enable=True,
            pitch_target_deg=45.0,
        )
        state = simulate_step(
            previous=base_state(), next_step=1, next_sim_time_s=1, step_s=1,
            sampled_at_utc="2026-09-07T00:00:01.000Z",
            wind_speed_mps=12, load_power_kw=77, controls=controls,
            wind=self.wind, diesel=self.diesel,
        )
        self.assertEqual(state.wind_available_kw, 100.0)
        self.assertEqual(state.wind_operating_limit_kw, 100.0)
        self.assertEqual(state.wind_actual_kw, 0.0)

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

    def test_scenario_csv_round_trip_uses_protocol_column_names(self):
        original = load_scenario_csv(SCENARIO_PATH)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roundtrip.csv"
            save_scenario_csv(path, original)
            self.assertEqual(load_scenario_csv(path), original)


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
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
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

        conflict = self.repo.apply_command({
            **message,
            "payload": {**message["payload"], "diesel_target_kw": 71},
        })
        self.assertFalse(conflict.accepted)
        self.assertEqual(conflict.reason, "seq_conflict")
        self.assertFalse(conflict.duplicate)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 2)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM logs WHERE event='command_rejected' AND message='seq_conflict'"
                ).fetchone()[0],
                1,
            )

    def test_gui_connection_snapshot_starts_with_b_and_c_offline(self):
        statuses = self.repo.connection_statuses()
        self.assertEqual(set(statuses), {"B", "C"})
        self.assertFalse(statuses["B"]["connected"])
        self.assertFalse(statuses["C"]["connected"])

    def test_parameter_snapshot_local_update_and_gui_history_apis(self):
        snapshot = self.repo.parameter_snapshot()
        self.assertEqual(len(snapshot), 19)
        self.assertEqual(
            set(snapshot[0]),
            {"name", "value", "unit", "owner", "source", "updated_at_utc", "editable"},
        )
        by_name = {row["name"]: row for row in snapshot}
        self.assertTrue(by_name["a_step_s"]["editable"])
        self.assertFalse(by_name["reserve_kw"]["editable"])
        self.assertEqual(by_name["wind_rated_power_kw"]["owner"], "C")

        updated = self.repo.update_a_parameters({
            "a_step_s": 0.5,
            "wind_ramp_up_kw_per_s": 7.0,
        })
        updated_by_name = {row["name"]: row for row in updated}
        self.assertEqual(updated_by_name["a_step_s"]["value"], 0.5)
        self.assertEqual(updated_by_name["a_step_s"]["source"], "A_local")
        self.assertEqual(self.repo.runtime()["step_s"], 0.5)
        history = self.repo.parameter_history(2)
        self.assertEqual({row["name"] for row in history}, {
            "a_step_s", "wind_ramp_up_kw_per_s",
        })
        self.assertTrue(all(row["source"] == "A_local" for row in history))
        self.assertEqual(self.repo.logs(1)[0]["event"], "parameters_updated")

        state_rows = self.repo.state_history(1)
        self.assertEqual(len(state_rows), 1)
        self.assertTrue({
            "sampled_at_utc", "load_power_kw", "wind_available_kw",
            "wind_operating_limit_kw", "wind_target_kw", "wind_actual_kw",
            "diesel_target_kw", "diesel_actual_kw", "power_imbalance_kw",
        }.issubset(state_rows[0]))

    def test_local_parameter_update_rejects_remote_owned_and_invalid_relations(self):
        with self.assertRaisesRegex(ValueError, "unauthorized_parameter"):
            self.repo.update_a_parameters({"reserve_kw": 9.0})
        with self.assertRaises(ValueError):
            self.repo.update_a_parameters({"diesel_min_power_kw": 200.0})
        self.assertEqual(
            {row["name"]: row["value"] for row in self.repo.parameter_snapshot()}[
                "diesel_min_power_kw"
            ],
            20.0,
        )

    def test_schema_v4_is_migrated_without_losing_state_history(self):
        before = len(self.repo.state_history(100))
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("DROP TABLE parameter_history")
            connection.execute("DROP TABLE parameter_state")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()

        migrated = Repository(self.db)
        self.assertEqual(len(migrated.parameter_snapshot()), 19)
        self.assertEqual(len(migrated.state_history(100)), before)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM parameter_history").fetchone()[0],
                19,
            )

    def test_new_session_safely_resets_controls_and_preserves_history(self):
        self.repo.update_a_parameters({"a_poll_s": 0.75})
        self.repo.set_status("start")
        old_state = self.repo.step_once()
        assert old_state is not None
        self.repo.set_status("stop")
        self.repo.mark_connection("B", True, "stale test connection")
        history_before = self.repo.state_history(100)
        old_server_seq = self.repo.next_server_seq()

        new_session = self.repo.create_session()

        self.assertNotEqual(new_session, self.session)
        runtime = self.repo.runtime()
        state = self.repo.get_state()
        self.assertEqual(runtime["status"], "ready")
        self.assertEqual(runtime["step"], 0)
        self.assertEqual(state.session_id, new_session)
        self.assertEqual(state.step, 0)
        self.assertEqual(state.wind_actual_kw, 0.0)
        self.assertEqual(state.diesel_actual_kw, 0.0)
        self.assertEqual(state.wind_target_kw, 0.0)
        self.assertEqual(state.diesel_target_kw, 0.0)
        self.assertFalse(state.wind_running)
        self.assertFalse(state.diesel_running)
        self.assertEqual(state.pitch_actual_deg, 90.0)
        self.assertEqual(state.power_imbalance_kw, state.load_power_kw)
        self.assertEqual(len(self.repo.state_history(100)), len(history_before) + 1)
        self.assertEqual(
            {row["name"]: row["value"] for row in self.repo.parameter_snapshot()}[
                "a_poll_s"
            ],
            0.75,
        )
        self.assertFalse(self.repo.connection_statuses()["B"]["connected"])
        self.assertEqual(self.repo.next_server_seq(), old_server_seq + 1)
        self.assertEqual(
            {row["session_id"] for row in self.repo.state_history(100)},
            {self.session, new_session},
        )
        self.assertEqual(
            [row["session_id"] for row in self.repo.history_sessions()],
            [self.session, new_session],
        )
        self.assertEqual(
            {row["session_id"] for row in self.repo.state_history(100, self.session)},
            {self.session},
        )


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

    def test_tcp_matches_b_state_dispatch_ack_and_reconnect_flow(self):
        """Exercise the exact envelope and payload shape used by B/tcpB.py."""
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            server = SimulatorTCPServer(("127.0.0.1", 0), repo, 4096)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                state_request = {
                    "version": 1, "type": "state_request", "source": "B", "target": "A",
                    "session_id": None, "seq": 0, "step": 0, "sim_time_s": 0.0,
                    "payload": {"full": True},
                }
                with socket.create_connection(server.server_address, timeout=2) as client:
                    with client.makefile("rwb") as stream:
                        stream.write((json.dumps(state_request) + "\n").encode("utf-8"))
                        stream.flush()
                        state = json.loads(stream.readline())
                        self.assertEqual(state["type"], "state")
                        self.assertEqual(state["session_id"], session_id)
                        self.assertEqual(
                            set(state["payload"]),
                            {
                                "sampled_at_utc", "wind_speed_mps", "load_power_kw",
                                "wind_available_kw", "wind_operating_limit_kw",
                                "wind_actual_kw", "diesel_actual_kw", "wind_target_kw",
                                "diesel_target_kw", "pitch_actual_deg", "wind_running",
                                "diesel_running", "fault", "power_imbalance_kw",
                                "next_command_seq",
                            },
                        )

                        dispatch = {
                            "version": 1, "type": "dispatch", "source": "B", "target": "A",
                            "session_id": session_id, "seq": 1,
                            "step": state["step"], "sim_time_s": state["sim_time_s"],
                            "payload": {
                                "wind_target_kw": 50.0, "diesel_target_kw": 70.0,
                                "wind_enable": True, "diesel_enable": True,
                            },
                        }
                        stream.write((json.dumps(dispatch) + "\n").encode("utf-8"))
                        stream.flush()
                        ack = json.loads(stream.readline())
                        self.assertEqual(ack["type"], "ack")
                        self.assertEqual(ack["payload"], {
                            "ack_seq": 1, "accepted": True, "reason": "accepted",
                        })

                        stream.write((json.dumps(dispatch) + "\n").encode("utf-8"))
                        stream.flush()
                        duplicate_ack = json.loads(stream.readline())
                        self.assertEqual(duplicate_ack["payload"], {
                            "ack_seq": 1, "accepted": True,
                            "reason": "duplicate_accepted",
                        })

                        old_dispatch = {**dispatch, "seq": 0}
                        stream.write((json.dumps(old_dispatch) + "\n").encode("utf-8"))
                        stream.flush()
                        old_ack = json.loads(stream.readline())
                        self.assertEqual(old_ack["payload"], {
                            "ack_seq": 0, "accepted": False,
                            "reason": "out_of_order",
                        })

                        parameter_update = {
                            **dispatch,
                            "type": "parameter_update",
                            "seq": 2,
                            "payload": {"parameters": {"reserve_kw": 8.0}},
                        }
                        stream.write((json.dumps(parameter_update) + "\n").encode("utf-8"))
                        stream.flush()
                        parameter_ack = json.loads(stream.readline())
                        self.assertEqual(parameter_ack["payload"], {
                            "ack_seq": 2, "accepted": True, "reason": "accepted",
                        })

                reconnect_request = {**state_request, "session_id": session_id, "seq": 3}
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.sendall((json.dumps(reconnect_request) + "\n").encode("utf-8"))
                    with client.makefile("rb") as stream:
                        state_after_reconnect = json.loads(stream.readline())
                self.assertEqual(state_after_reconnect["type"], "state")
                self.assertEqual(state_after_reconnect["session_id"], session_id)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dispatch_rejects_fields_outside_b_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            message = {
                "version": 1, "type": "dispatch", "source": "B", "target": "A",
                "session_id": session_id, "seq": 1, "step": 0, "sim_time_s": 0.0,
                "payload": {
                    "wind_target_kw": 50.0, "diesel_target_kw": 70.0,
                    "wind_enable": True, "diesel_enable": True,
                    "pitch_target_deg": 0.0,
                },
            }
            result = repo.apply_command(message)
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "invalid_dispatch_fields")

    def test_c_wind_action_owns_capability_and_pitch_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "grid.db"
            repo = Repository(db)
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            message = {
                "version": 1,
                "type": "wind_action",
                "source": "C",
                "target": "A",
                "session_id": session_id,
                "seq": 1,
                "step": 0,
                "sim_time_s": 0.0,
                "payload": {
                    "wind_enable": True,
                    "pitch_target_deg": 12.0,
                    "wind_available_kw": 40.0,
                    "wind_operating_limit_kw": 40.0,
                },
            }
            result = repo.apply_command(message)
            self.assertTrue(result.accepted)

            state_request = {
                "version": 1,
                "type": "state_request",
                "source": "C",
                "target": "A",
                "session_id": session_id,
                "seq": 0,
                "step": 0,
                "sim_time_s": 0.0,
                "payload": {"full": True},
            }
            state_response = MessageProcessor(repo).process(state_request)
            self.assertEqual(state_response["payload"]["next_command_seq"], 2)
            self.assertLessEqual(len(encode_frame(state_response, 4096)), 1024)

            import sqlite3
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    "SELECT pitch_target_deg, controller_wind_available_kw, "
                    "controller_wind_operating_limit_kw FROM control_state WHERE singleton_id=1"
                ).fetchone()
            self.assertEqual(row, (12.0, 40.0, 40.0))

            invalid = {
                **message,
                "seq": 2,
                "payload": {"wind_enable": True, "pitch_target_deg": 12.0},
            }
            rejected = repo.apply_command(invalid)
            self.assertFalse(rejected.accepted)
            self.assertEqual(rejected.reason, "invalid_wind_action_fields")

    def test_parameter_update_enforces_owner_persists_and_is_used_by_next_step(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "grid.db"
            repo = Repository(db)
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))

            def update(source, seq, parameters):
                return repo.apply_command({
                    "version": 1,
                    "type": "parameter_update",
                    "source": source,
                    "target": "A",
                    "session_id": session_id,
                    "seq": seq,
                    "step": 0,
                    "sim_time_s": 0.0,
                    "payload": {"parameters": parameters},
                })

            self.assertTrue(update("B", 1, {
                "reserve_kw": 9.0,
                "b_poll_s": 0.5,
                "b_dispatch_s": 2.0,
            }).accepted)
            unauthorized = update("B", 2, {"wind_rated_power_kw": 80.0})
            self.assertFalse(unauthorized.accepted)
            self.assertEqual(unauthorized.reason, "unauthorized_parameter")

            bad_relation = update("C", 1, {"cut_in_speed_mps": 20.0})
            self.assertFalse(bad_relation.accepted)
            self.assertIn("wind speeds", bad_relation.reason)
            self.assertEqual(
                {row["name"]: row["value"] for row in repo.parameter_snapshot()}[
                    "cut_in_speed_mps"
                ],
                3.0,
            )

            changed = update("C", 2, {
                "wind_rated_power_kw": 20.0,
                "cut_in_speed_mps": 0.0,
                "rated_speed_mps": 1.0,
            })
            self.assertTrue(changed.accepted)
            snapshot = {row["name"]: row for row in repo.parameter_snapshot()}
            self.assertEqual(snapshot["reserve_kw"]["source"], "B_tcp")
            self.assertEqual(snapshot["wind_rated_power_kw"]["source"], "C_tcp")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM device_parameters "
                        "WHERE device_id='WT01' AND name='rated_power_kw'"
                    ).fetchone()[0],
                    20.0,
                )
                safe_control = connection.execute(
                    "SELECT controller_wind_enable, pitch_target_deg, "
                    "controller_wind_available_kw, controller_wind_operating_limit_kw "
                    "FROM control_state WHERE singleton_id=1"
                ).fetchone()
            self.assertEqual(safe_control, (0, 90.0, 0.0, 0.0))

            wind_action = {
                "version": 1, "type": "wind_action", "source": "C", "target": "A",
                "session_id": session_id, "seq": 3, "step": 0, "sim_time_s": 0.0,
                "payload": {
                    "wind_enable": True,
                    "pitch_target_deg": 0.0,
                    "wind_available_kw": 20.0,
                    "wind_operating_limit_kw": 20.0,
                },
            }
            dispatch = {
                "version": 1, "type": "dispatch", "source": "B", "target": "A",
                "session_id": session_id, "seq": 3, "step": 0, "sim_time_s": 0.0,
                "payload": {
                    "wind_target_kw": 20.0, "diesel_target_kw": 0.0,
                    "wind_enable": True, "diesel_enable": False,
                },
            }
            self.assertTrue(repo.apply_command(wind_action).accepted)
            self.assertTrue(repo.apply_command(dispatch).accepted)
            repo.update_a_parameters({"wind_ramp_up_kw_per_s": 100.0})
            repo.set_status("start")
            state = repo.step_once()
            assert state is not None
            self.assertEqual(state.wind_actual_kw, 20.0)
            self.assertEqual(state.wind_available_kw, 20.0)
            self.assertTrue(any(
                row["source"] == "C_tcp" and row["name"] == "wind_rated_power_kw"
                for row in repo.parameter_history(20)
            ))
            self.assertTrue(any(
                row["event"] == "parameters_updated" for row in repo.logs(20)
            ))

    def test_parameter_update_payload_shape_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            result = repo.apply_command({
                "version": 1, "type": "parameter_update", "source": "B", "target": "A",
                "session_id": session_id, "seq": 1, "step": 0, "sim_time_s": 0.0,
                "payload": {"parameters": {"reserve_kw": 8.0}, "source": "B"},
            })
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "invalid_parameter_update_fields")

    def test_parameter_update_with_same_value_does_not_inflate_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            session_id = repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            before = repo.parameter_history(1000)
            reserve_before = next(
                row for row in repo.parameter_snapshot() if row["name"] == "reserve_kw"
            )
            result = repo.apply_command({
                "version": 1,
                "type": "parameter_update",
                "source": "B",
                "target": "A",
                "session_id": session_id,
                "seq": 1,
                "step": 0,
                "sim_time_s": 0.0,
                "payload": {"parameters": {"reserve_kw": reserve_before["value"]}},
            })
            reserve_after = next(
                row for row in repo.parameter_snapshot() if row["name"] == "reserve_kw"
            )
            self.assertTrue(result.accepted)
            self.assertEqual(repo.parameter_history(1000), before)
            self.assertEqual(reserve_after, reserve_before)

    def test_server_tracks_multiple_connections_per_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            repo.mark_connection("B", True, "stale process state")
            server = SimulatorTCPServer(("127.0.0.1", 0), repo, 4096)
            try:
                self.assertFalse(repo.connection_statuses()["B"]["connected"])
                server.register_peer("B")
                server.register_peer("B")
                server.unregister_peer("B")
                self.assertTrue(repo.connection_statuses()["B"]["connected"])
                self.assertEqual(
                    repo.connection_statuses()["B"]["detail"],
                    "1 active connection(s)",
                )
                server.unregister_peer("B")
                self.assertFalse(repo.connection_statuses()["B"]["connected"])
                events = [row["event"] for row in repo.logs(20)]
                self.assertIn("peer_connected", events)
                self.assertIn("peer_disconnected", events)
            finally:
                server.server_close()
            self.assertFalse(repo.connection_statuses()["B"]["connected"])
            self.assertEqual(
                repo.connection_statuses()["B"]["detail"], "TCP server stopped"
            )

    def test_server_marks_idle_peer_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "grid.db")
            config = replace(load_config(CONFIG_PATH), end_s=10.0)
            repo.initialize(config, load_scenario_csv(SCENARIO_PATH))
            server = SimulatorTCPServer(
                ("127.0.0.1", 0), repo, 4096, idle_timeout_s=0.1
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = {
                    "version": 1,
                    "type": "state_request",
                    "source": "B",
                    "target": "A",
                    "session_id": None,
                    "seq": 1,
                    "step": 0,
                    "sim_time_s": 0.0,
                    "payload": {"full": True},
                }
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                    with client.makefile("rb") as stream:
                        response = json.loads(stream.readline())
                        self.assertEqual(response["type"], "state")
                        self.assertTrue(repo.connection_statuses()["B"]["connected"])
                        deadline = time.monotonic() + 2.0
                        while (
                            repo.connection_statuses()["B"]["connected"]
                            and time.monotonic() < deadline
                        ):
                            time.sleep(0.02)
                        self.assertFalse(repo.connection_statuses()["B"]["connected"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
