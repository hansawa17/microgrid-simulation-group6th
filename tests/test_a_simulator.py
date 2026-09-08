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
            {×Î}¶‰Ëkºwµç}‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹™±ÕÍ  ¤(€€€€€€€€€€€€€€€€€€€€€€€…¬€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡…­l‰ÑåÁ”‰t°€‰…¬ˆ¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡…­l‰Á…å±½…‰t°ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…­}Í•Äˆè€Ä°€‰…•ÁÑ•ˆèQÉÕ”°€‰É•…Í½¸ˆè€‰…•ÁÑ•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€ô¤((€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹İÉ¥Ñ” ¡©Í½¸¹‘ÕµÁÌ¡‘¥ÍÁ…Ñ ¤€¬€‰q¸ˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹™±ÕÍ  ¤(€€€€€€€€€€€€€€€€€€€€€€€‘ÕÁ±¥…Ñ•}…¬€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘ÕÁ±¥…Ñ•}…­l‰Á…å±½…‰t°ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…­}Í•Äˆè€Ä°€‰…•ÁÑ•ˆèQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•…Í½¸ˆè€‰‘ÕÁ±¥…Ñ•}…•ÁÑ•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€ô¤((€€€€€€€€€€€€€€€€€€€€€€€½±‘}‘¥ÍÁ…Ñ €ôì¨©‘¥ÍÁ…Ñ °€‰Í•Äˆè€Áô(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹İÉ¥Ñ” ¡©Í½¸¹‘ÕµÁÌ¡½±‘}‘¥ÍÁ…Ñ ¤€¬€‰q¸ˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹™±ÕÍ  ¤(€€€€€€€€€€€€€€€€€€€€€€€½±‘}…¬€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡½±‘}…­l‰Á…å±½…‰t°ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…­}Í•Äˆè€À°€‰…•ÁÑ•ˆè…±Í”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•…Í½¸ˆè€‰½ÕÑ}½™}½É‘•Èˆ°(€€€€€€€€€€€€€€€€€€€€€€€ô¤((€€€€€€€€€€€€€€€€€€€€€€€Á…É…µ•Ñ•É}ÕÁ‘…Ñ”€ôì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¨©‘¥ÍÁ…Ñ °(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰Á…É…µ•Ñ•É}ÕÁ‘…Ñ”ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í•Äˆè€È°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰Á…É…µ•Ñ•ÉÌˆèì‰É•Í•ÉÙ•}­Üˆè€à¸Áõô°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹İÉ¥Ñ” ¡©Í½¸¹‘ÕµÁÌ¡Á…É…µ•Ñ•É}ÕÁ‘…Ñ”¤€¬€‰q¸ˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€€€€€ÍÑÉ•…´¹™±ÕÍ  ¤(€€€€€€€€€€€€€€€€€€€€€€€Á…É…µ•Ñ•É}…¬€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Á…É…µ•Ñ•É}…­l‰Á…å±½…‰t°ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…­}Í•Äˆè€È°€‰…•ÁÑ•ˆèQÉÕ”°€‰É•…Í½¸ˆè€‰…•ÁÑ•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€ô¤((€€€€€€€€€€€€€€€É•½¹¹•Ñ}É•ÅÕ•ÍĞ€ôì¨©ÍÑ…Ñ•}É•ÅÕ•ÍĞ°€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°€‰Í•Äˆè€Íô(€€€€€€€€€€€€€€€İ¥Ñ Í½­•Ğ¹É•…Ñ•}½¹¹•Ñ¥½¸¡Í•ÉÙ•È¹Í•ÉÙ•É}…‘‘É•ÍÌ°Ñ¥µ•½ÕĞôÈ¤…Ì±¥•¹Ğè(€€€€€€€€€€€€€€€€€€€±¥•¹Ğ¹Í•¹‘…±° ¡©Í½¸¹‘ÕµÁÌ¡É•½¹¹•Ñ}É•ÅÕ•ÍĞ¤€¬€‰q¸ˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€İ¥Ñ ±¥•¹Ğ¹µ…­•™¥±” ‰Éˆˆ¤…ÌÍÑÉ•…´è(€€€€€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•}…™Ñ•É}É•½¹¹•Ğ€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡ÍÑ…Ñ•}…™Ñ•É}É•½¹¹•Ñl‰ÑåÁ”‰t°€‰ÍÑ…Ñ”ˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡ÍÑ…Ñ•}…™Ñ•É}É•½¹¹•Ñl‰Í•ÍÍ¥½¹}¥‰t°Í•ÍÍ¥½¹}¥¤(€€€€€€€€€€€™¥¹…±±äè(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Í¡ÕÑ‘½İ¸ ¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Í•ÉÙ•É}±½Í” ¤(€€€€€€€€€€€€€€€Ñ¡É•…¹©½¥¸¡Ñ¥µ•½ÕĞôÈ¤((€€€‘•˜Ñ•ÍÑ}‘¥ÍÁ…Ñ¡}É•©•ÑÍ}™¥•±‘Í}½ÕÑÍ¥‘•}‰}½¹ÑÉ…Ğ¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡A…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€Í•ÍÍ¥½¹}¥€ôÉ•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€µ•ÍÍ…”€ôì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°€‰ÑåÁ”ˆè€‰‘¥ÍÁ…Ñ ˆ°€‰Í½ÕÉ”ˆè€‰ˆ°€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°€‰Í•Äˆè€Ä°€‰ÍÑ•Àˆè€À°€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}Ñ…É•Ñ}­Üˆè€ÔÀ¸À°€‰‘¥•Í•±}Ñ…É•Ñ}­Üˆè€ÜÀ¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}•¹…‰±”ˆèQÉÕ”°€‰‘¥•Í•±}•¹…‰±”ˆèQÉÕ”°(€€€€€€€€€€€€€€€€€€€€‰Á¥Ñ¡}Ñ…É•Ñ}‘•œˆè€À¸À°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€ô(€€€€€€€€€€€É•ÍÕ±Ğ€ôÉ•Á¼¹…ÁÁ±å}½µµ…¹¡µ•ÍÍ…”¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•ÍÕ±Ğ¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•ÍÕ±Ğ¹É•…Í½¸°€‰¥¹Ù…±¥‘}‘¥ÍÁ…Ñ¡}™¥•±‘Ìˆ¤((€€€‘•˜Ñ•ÍÑ}}İ¥¹‘}…Ñ¥½¹}½İ¹Í}…Á…‰¥±¥Ñå}…¹‘}Á¥Ñ¡}™¥•±‘Ì¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€‘ˆ€ôA…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡‘ˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€Í•ÍÍ¥½¹}¥€ôÉ•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€µ•ÍÍ…”€ôì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰İ¥¹‘}…Ñ¥½¸ˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°(€€€€€€€€€€€€€€€€‰Í•Äˆè€Ä°(€€€€€€€€€€€€€€€€‰ÍÑ•Àˆè€À°(€€€€€€€€€€€€€€€€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}•¹…‰±”ˆèQÉÕ”°(€€€€€€€€€€€€€€€€€€€€‰Á¥Ñ¡}Ñ…É•Ñ}‘•œˆè€ÄÈ¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}…Ù…¥±…‰±•}­Üˆè€ĞÀ¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}½Á•É…Ñ¥¹}±¥µ¥Ñ}­Üˆè€ĞÀ¸À°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€ô(€€€€€€€€€€€É•ÍÕ±Ğ€ôÉ•Á¼¹…ÁÁ±å}½µµ…¹¡µ•ÍÍ…”¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•ÍÕ±Ğ¹…•ÁÑ•¤((€€€€€€€€€€€¥µÁ½ÉĞÍÅ±¥Ñ”Ì(€€€€€€€€€€€İ¥Ñ ±½Í¥¹œ¡ÍÅ±¥Ñ”Ì¹½¹¹•Ğ¡‘ˆ¤¤…Ì½¹¹•Ñ¥½¸è(€€€€€€€€€€€€€€€É½Ü€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€‰M1PÁ¥Ñ¡}Ñ…É•Ñ}‘•œ°½¹ÑÉ½±±•É}İ¥¹‘}…Ù…¥±…‰±•}­Ü°€ˆ(€€€€€€€€€€€€€€€€€€€€‰½¹ÑÉ½±±•É}İ¥¹‘}½Á•É…Ñ¥¹}±¥µ¥Ñ}­ÜI=4½¹ÑÉ½±}ÍÑ…Ñ”]!IÍ¥¹±•Ñ½¹}¥ôÄˆ(€€€€€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É½Ü°€ ÄÈ¸À°€ĞÀ¸À°€ĞÀ¸À¤¤((€€€€€€€€€€€¥¹Ù…±¥€ôì(€€€€€€€€€€€€€€€€¨©µ•ÍÍ…”°(€€€€€€€€€€€€€€€€‰Í•Äˆè€È°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰İ¥¹‘}•¹…‰±”ˆèQÉÕ”°€‰Á¥Ñ¡}Ñ…É•Ñ}‘•œˆè€ÄÈ¸Áô°(€€€€€€€€€€€ô(€€€€€€€€€€€É•©•Ñ•€ôÉ•Á¼¹…ÁÁ±å}½µµ…¹¡¥¹Ù…±¥¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•©•Ñ•¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•©•Ñ•¹É•…Í½¸°€‰¥¹Ù…±¥‘}İ¥¹‘}…Ñ¥½¹}™¥•±‘Ìˆ¤((€€€‘•˜Ñ•ÍÑ}Á…É…µ•Ñ•É}ÕÁ‘…Ñ•}•¹™½É•Í}½İ¹•É}Á•ÉÍ¥ÍÑÍ}…¹‘}¥Í}ÕÍ•‘}‰å}¹•áÑ}ÍÑ•À¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€‘ˆ€ôA…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡‘ˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€Í•ÍÍ¥½¹}¥€ôÉ•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤((€€€€€€€€€€€‘•˜ÕÁ‘…Ñ”¡Í½ÕÉ”°Í•Ä°Á…É…µ•Ñ•ÉÌ¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸É•Á¼¹…ÁÁ±å}½µµ…¹¡ì(€€€€€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰Á…É…µ•Ñ•É}ÕÁ‘…Ñ”ˆ°(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆèÍ½ÕÉ”°(€€€€€€€€€€€€€€€€€€€€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€‰Í•ÄˆèÍ•Ä°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ•Àˆè€À°(€€€€€€€€€€€€€€€€€€€€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰Á…É…µ•Ñ•ÉÌˆèÁ…É…µ•Ñ•ÉÍô°(€€€€€€€€€€€€€€€ô¤((€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡ÕÁ‘…Ñ” ‰ˆ°€Ä°ì(€€€€€€€€€€€€€€€€‰É•Í•ÉÙ•}­Üˆè€ä¸À°(€€€€€€€€€€€€€€€€‰‰}Á½±±}Ìˆè€À¸Ô°(€€€€€€€€€€€€€€€€‰‰}‘¥ÍÁ…Ñ¡}Ìˆè€È¸À°(€€€€€€€€€€€ô¤¹…•ÁÑ•¤(€€€€€€€€€€€Õ¹…ÕÑ¡½É¥é•€ôÕÁ‘…Ñ” ‰ˆ°€È°ì‰İ¥¹‘}É…Ñ•‘}Á½İ•É}­Üˆè€àÀ¸Áô¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡Õ¹…ÕÑ¡½É¥é•¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Õ¹…ÕÑ¡½É¥é•¹É•…Í½¸°€‰Õ¹…ÕÑ¡½É¥é•‘}Á…É…µ•Ñ•Èˆ¤((€€€€€€€€€€€‰…‘}É•±…Ñ¥½¸€ôÕÁ‘…Ñ” ‰ˆ°€Ä°ì‰ÕÑ}¥¹}ÍÁ••‘}µÁÌˆè€ÈÀ¸Áô¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡‰…‘}É•±…Ñ¥½¸¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ ‰İ¥¹ÍÁ••‘Ìˆ°‰…‘}É•±…Ñ¥½¸¹É•…Í½¸¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€íÉ½İl‰¹…µ”‰tèÉ½İl‰Ù…±Õ”‰t™½ÈÉ½Ü¥¸É•Á¼¹Á…É…µ•Ñ•É}Í¹…ÁÍ¡½Ğ ¥õl(€€€€€€€€€€€€€€€€€€€€‰ÕÑ}¥¹}ÍÁ••‘}µÁÌˆ(€€€€€€€€€€€€€€€t°(€€€€€€€€€€€€€€€€Ì¸À°(€€€€€€€€€€€€¤((€€€€€€€€€€€¡…¹•€ôÕÁ‘…Ñ” ‰ˆ°€È°ì(€€€€€€€€€€€€€€€€‰İ¥¹‘}É…Ñ•‘}Á½İ•É}­Üˆè€ÈÀ¸À°(€€€€€€€€€€€€€€€€‰ÕÑ}¥¹}ÍÁ••‘}µÁÌˆè€À¸À°(€€€€€€€€€€€€€€€€‰É…Ñ•‘}ÍÁ••‘}µÁÌˆè€Ä¸À°(€€€€€€€€€€€ô¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡¡…¹•¹…•ÁÑ•¤(€€€€€€€€€€€Í¹…ÁÍ¡½Ğ€ôíÉ½İl‰¹…µ”‰tèÉ½Ü™½ÈÉ½Ü¥¸É•Á¼¹Á…É…µ•Ñ•É}Í¹…ÁÍ¡½Ğ ¥ô(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Í¹…ÁÍ¡½Ñl‰É•Í•ÉÙ•}­Ü‰ul‰Í½ÕÉ”‰t°€‰	}ÑÀˆ¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Í¹…ÁÍ¡½Ñl‰İ¥¹‘}É…Ñ•‘}Á½İ•É}­Ü‰ul‰Í½ÕÉ”‰t°€‰}ÑÀˆ¤(€€€€€€€€€€€İ¥Ñ ±½Í¥¹œ¡ÍÅ±¥Ñ”Ì¹½¹¹•Ğ¡‘ˆ¤¤…Ì½¹¹•Ñ¥½¸è(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€€€€€‰M1PÙ…±Õ”I=4‘•Ù¥•}Á…É…µ•Ñ•ÉÌ€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰]!I‘•Ù¥•}¥ô]PÀÄœ9¹…µ”ôÉ…Ñ•‘}Á½İ•É}­Üœˆ(€€€€€€€€€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¥lÁt°(€€€€€€€€€€€€€€€€€€€€ÈÀ¸À°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Í…™•}½¹ÑÉ½°€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€‰M1P½¹ÑÉ½±±•É}İ¥¹‘}•¹…‰±”°Á¥Ñ¡}Ñ…É•Ñ}‘•œ°€ˆ(€€€€€€€€€€€€€€€€€€€€‰½¹ÑÉ½±±•É}İ¥¹‘}…Ù…¥±…‰±•}­Ü°½¹ÑÉ½±±•É}İ¥¹‘}½Á•É…Ñ¥¹}±¥µ¥Ñ}­Ü€ˆ(€€€€€€€€€€€€€€€€€€€€‰I=4½¹ÑÉ½±}ÍÑ…Ñ”]!IÍ¥¹±•Ñ½¹}¥ôÄˆ(€€€€€€€€€€€€€€€€¤¹™•Ñ¡½¹” ¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Í…™•}½¹ÑÉ½°°€ À°€äÀ¸À°€À¸À°€À¸À¤¤((€€€€€€€€€€€İ¥¹‘}…Ñ¥½¸€ôì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°€‰ÑåÁ”ˆè€‰İ¥¹‘}…Ñ¥½¸ˆ°€‰Í½ÕÉ”ˆè€‰ˆ°€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°€‰Í•Äˆè€Ì°€‰ÍÑ•Àˆè€À°€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}•¹…‰±”ˆèQÉÕ”°(€€€€€€€€€€€€€€€€€€€€‰Á¥Ñ¡}Ñ…É•Ñ}‘•œˆè€À¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}…Ù…¥±…‰±•}­Üˆè€ÈÀ¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}½Á•É…Ñ¥¹}±¥µ¥Ñ}­Üˆè€ÈÀ¸À°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€ô(€€€€€€€€€€€‘¥ÍÁ…Ñ €ôì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°€‰ÑåÁ”ˆè€‰‘¥ÍÁ…Ñ ˆ°€‰Í½ÕÉ”ˆè€‰ˆ°€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°€‰Í•Äˆè€Ì°€‰ÍÑ•Àˆè€À°€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}Ñ…É•Ñ}­Üˆè€ÈÀ¸À°€‰‘¥•Í•±}Ñ…É•Ñ}­Üˆè€À¸À°(€€€€€€€€€€€€€€€€€€€€‰İ¥¹‘}•¹…‰±”ˆèQÉÕ”°€‰‘¥•Í•±}•¹…‰±”ˆè…±Í”°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€ô(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•Á¼¹…ÁÁ±å}½µµ…¹¡İ¥¹‘}…Ñ¥½¸¤¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•Á¼¹…ÁÁ±å}½µµ…¹¡‘¥ÍÁ…Ñ ¤¹…•ÁÑ•¤(€€€€€€€€€€€É•Á¼¹ÕÁ‘…Ñ•}…}Á…É…µ•Ñ•ÉÌ¡ì‰İ¥¹‘}É…µÁ}ÕÁ}­İ}Á•É}Ìˆè€ÄÀÀ¸Áô¤(€€€€€€€€€€€É•Á¼¹Í•Ñ}ÍÑ…ÑÕÌ ‰ÍÑ…ÉĞˆ¤(€€€€€€€€€€€ÍÑ…Ñ”€ôÉ•Á¼¹ÍÑ•Á}½¹” ¤(€€€€€€€€€€€…ÍÍ•ÉĞÍÑ…Ñ”¥Ì¹½Ğ9½¹”(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡ÍÑ…Ñ”¹İ¥¹‘}…ÑÕ…±}­Ü°€ÈÀ¸À¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡ÍÑ…Ñ”¹İ¥¹‘}…Ù…¥±…‰±•}­Ü°€ÈÀ¸À¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡…¹ä (€€€€€€€€€€€€€€€É½İl‰Í½ÕÉ”‰t€ôô€‰}ÑÀˆ…¹É½İl‰¹…µ”‰t€ôô€‰İ¥¹‘}É…Ñ•‘}Á½İ•É}­Üˆ(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸É•Á¼¹Á…É…µ•Ñ•É}¡¥ÍÑ½Éä ÈÀ¤(€€€€€€€€€€€€¤¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡…¹ä (€€€€€€€€€€€€€€€É½İl‰•Ù•¹Ğ‰t€ôô€‰Á…É…µ•Ñ•ÉÍ}ÕÁ‘…Ñ•ˆ™½ÈÉ½Ü¥¸É•Á¼¹±½Ì ÈÀ¤(€€€€€€€€€€€€¤¤((€€€‘•˜Ñ•ÍÑ}Á…É…µ•Ñ•É}ÕÁ‘…Ñ•}Á…å±½…‘}Í¡…Á•}¥Í}ÍÑÉ¥Ğ¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡A…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€Í•ÍÍ¥½¹}¥€ôÉ•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€É•ÍÕ±Ğ€ôÉ•Á¼¹…ÁÁ±å}½µµ…¹¡ì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°€‰ÑåÁ”ˆè€‰Á…É…µ•Ñ•É}ÕÁ‘…Ñ”ˆ°€‰Í½ÕÉ”ˆè€‰ˆ°€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°€‰Í•Äˆè€Ä°€‰ÍÑ•Àˆè€À°€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰Á…É…µ•Ñ•ÉÌˆèì‰É•Í•ÉÙ•}­Üˆè€à¸Áô°€‰Í½ÕÉ”ˆè€‰‰ô°(€€€€€€€€€€€ô¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•ÍÕ±Ğ¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•ÍÕ±Ğ¹É•…Í½¸°€‰¥¹Ù…±¥‘}Á…É…µ•Ñ•É}ÕÁ‘…Ñ•}™¥•±‘Ìˆ¤((€€€‘•˜Ñ•ÍÑ}Á…É…µ•Ñ•É}ÕÁ‘…Ñ•}İ¥Ñ¡}Í…µ•}Ù…±Õ•}‘½•Í}¹½Ñ}¥¹™±…Ñ•}¡¥ÍÑ½Éä¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡A…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€Í•ÍÍ¥½¹}¥€ôÉ•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€‰•™½É”€ôÉ•Á¼¹Á…É…µ•Ñ•É}¡¥ÍÑ½Éä ÄÀÀÀ¤(€€€€€€€€€€€É•Í•ÉÙ•}‰•™½É”€ô¹•áĞ (€€€€€€€€€€€€€€€É½Ü™½ÈÉ½Ü¥¸É•Á¼¹Á…É…µ•Ñ•É}Í¹…ÁÍ¡½Ğ ¤¥˜É½İl‰¹…µ”‰t€ôô€‰É•Í•ÉÙ•}­Üˆ(€€€€€€€€€€€€¤(€€€€€€€€€€€É•ÍÕ±Ğ€ôÉ•Á¼¹…ÁÁ±å}½µµ…¹¡ì(€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰Á…É…µ•Ñ•É}ÕÁ‘…Ñ”ˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆèÍ•ÍÍ¥½¹}¥°(€€€€€€€€€€€€€€€€‰Í•Äˆè€Ä°(€€€€€€€€€€€€€€€€‰ÍÑ•Àˆè€À°(€€€€€€€€€€€€€€€€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰Á…É…µ•Ñ•ÉÌˆèì‰É•Í•ÉÙ•}­ÜˆèÉ•Í•ÉÙ•}‰•™½É•l‰Ù…±Õ”‰uõô°(€€€€€€€€€€€ô¤(€€€€€€€€€€€É•Í•ÉÙ•}…™Ñ•È€ô¹•áĞ (€€€€€€€€€€€€€€€É½Ü™½ÈÉ½Ü¥¸É•Á¼¹Á…É…µ•Ñ•É}Í¹…ÁÍ¡½Ğ ¤¥˜É½İl‰¹…µ”‰t€ôô€‰É•Í•ÉÙ•}­Üˆ(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•ÍÕ±Ğ¹…•ÁÑ•¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•Á¼¹Á…É…µ•Ñ•É}¡¥ÍÑ½Éä ÄÀÀÀ¤°‰•™½É”¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•Í•ÉÙ•}…™Ñ•È°É•Í•ÉÙ•}‰•™½É”¤((€€€‘•˜Ñ•ÍÑ}Í•ÉÙ•É}ÑÉ…­Í}µÕ±Ñ¥Á±•}½¹¹•Ñ¥½¹Í}Á•É}Á••È¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡A…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€É•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€É•Á¼¹µ…É­}½¹¹•Ñ¥½¸ ‰ˆ°QÉÕ”°€‰ÍÑ…±”ÁÉ½•ÍÌÍÑ…Ñ”ˆ¤(€€€€€€€€€€€Í•ÉÙ•È€ôM¥µÕ±…Ñ½ÉQAM•ÉÙ•È  ˆÄÈÜ¸À¸À¸Äˆ°€À¤°É•Á¼°€ĞÀäØ¤(€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹É•¥ÍÑ•É}Á••È ‰ˆ¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹É•¥ÍÑ•É}Á••È ‰ˆ¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Õ¹É•¥ÍÑ•É}Á••È ‰ˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰‘•Ñ…¥°‰t°(€€€€€€€€€€€€€€€€€€€€ˆÄ…Ñ¥Ù”½¹¹•Ñ¥½¸¡Ì¤ˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Õ¹É•¥ÍÑ•É}Á••È ‰ˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€€€€€•Ù•¹ÑÌ€ômÉ½İl‰•Ù•¹Ğ‰t™½ÈÉ½Ü¥¸É•Á¼¹±½Ì ÈÀ¥t(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ ‰Á••É}½¹¹•Ñ•ˆ°•Ù•¹ÑÌ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ ‰Á••É}‘¥Í½¹¹•Ñ•ˆ°•Ù•¹ÑÌ¤(€€€€€€€€€€€™¥¹…±±äè(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Í•ÉÙ•É}±½Í” ¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰‘•Ñ…¥°‰t°€‰Q@Í•ÉÙ•ÈÍÑ½ÁÁ•ˆ(€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}Í•ÉÙ•É}µ…É­Í}¥‘±•}Á••É}½™™±¥¹”¡Í•±˜¤è(€€€€€€€İ¥Ñ Ñ•µÁ™¥±”¹Q•µÁ½É…Éå¥É•Ñ½Éä ¤…Ì‘¥É•Ñ½Éäè(€€€€€€€€€€€É•Á¼€ôI•Á½Í¥Ñ½Éä¡A…Ñ ¡‘¥É•Ñ½Éä¤€¼€‰É¥¹‘ˆˆ¤(€€€€€€€€€€€½¹™¥œ€ôÉ•Á±…”¡±½…‘}½¹™¥œ¡=9%}AQ ¤°•¹‘}ÌôÄÀ¸À¤(€€€€€€€€€€€É•Á¼¹¥¹¥Ñ¥…±¥é”¡½¹™¥œ°±½…‘}Í•¹…É¥½}ÍØ¡M9I%=}AQ ¤¤(€€€€€€€€€€€Í•ÉÙ•È€ôM¥µÕ±…Ñ½ÉQAM•ÉÙ•È (€€€€€€€€€€€€€€€€ ˆÄÈÜ¸À¸À¸Äˆ°€À¤°É•Á¼°€ĞÀäØ°¥‘±•}Ñ¥µ•½ÕÑ}ÌôÀ¸Ä(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ¡É•…€ôÑ¡É•…‘¥¹œ¹Q¡É•…¡Ñ…É•ĞõÍ•ÉÙ•È¹Í•ÉÙ•}™½É•Ù•È°‘…•µ½¸õQÉÕ”¤(€€€€€€€€€€€Ñ¡É•…¹ÍÑ…ÉĞ ¤(€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€É•ÅÕ•ÍĞ€ôì(€€€€€€€€€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€€€€€€€€€€€€€‰ÑåÁ”ˆè€‰ÍÑ…Ñ•}É•ÅÕ•ÍĞˆ°(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰ˆ°(€€€€€€€€€€€€€€€€€€€€‰Ñ…É•Ğˆè€‰ˆ°(€€€€€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}¥ˆè9½¹”°(€€€€€€€€€€€€€€€€€€€€‰Í•Äˆè€Ä°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ•Àˆè€À°(€€€€€€€€€€€€€€€€€€€€‰Í¥µ}Ñ¥µ•}Ìˆè€À¸À°(€€€€€€€€€€€€€€€€€€€€‰Á…å±½…ˆèì‰™Õ±°ˆèQÉÕ•ô°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€İ¥Ñ Í½­•Ğ¹É•…Ñ•}½¹¹•Ñ¥½¸¡Í•ÉÙ•È¹Í•ÉÙ•É}…‘‘É•ÍÌ°Ñ¥µ•½ÕĞôÈ¤…Ì±¥•¹Ğè(€€€€€€€€€€€€€€€€€€€±¥•¹Ğ¹Í•¹‘…±° ¡©Í½¸¹‘ÕµÁÌ¡É•ÅÕ•ÍĞ¤€¬€‰q¸ˆ¤¹•¹½‘” ‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€€€€€€€€€İ¥Ñ ±¥•¹Ğ¹µ…­•™¥±” ‰Éˆˆ¤…ÌÍÑÉ•…´è(€€€€€€€€€€€€€€€€€€€€€€€É•ÍÁ½¹Í”€ô©Í½¸¹±½…‘Ì¡ÍÑÉ•…´¹É•…‘±¥¹” ¤¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•ÍÁ½¹Í•l‰ÑåÁ”‰t°€‰ÍÑ…Ñ”ˆ¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑQÉÕ”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€€€€€€€€€€€€€‘•…‘±¥¹”€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€È¸À(€€€€€€€€€€€€€€€€€€€€€€€İ¡¥±”€ (€€€€€€€€€€€€€€€€€€€€€€€€€€€É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t(€€€€€€€€€€€€€€€€€€€€€€€€€€€…¹Ñ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€ğ‘•…‘±¥¹”(€€€€€€€€€€€€€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ¥µ”¹Í±••À À¸ÀÈ¤(€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡É•Á¼¹½¹¹•Ñ¥½¹}ÍÑ…ÑÕÍ•Ì ¥l‰‰ul‰½¹¹•Ñ•‰t¤(€€€€€€€€€€€™¥¹…±±äè(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Í¡ÕÑ‘½İ¸ ¤(€€€€€€€€€€€€€€€Í•ÉÙ•È¹Í•ÉÙ•É}±½Í” ¤(€€€€€€€€€€€€€€€Ñ¡É•…¹©½¥¸¡Ñ¥µ•½ÕĞôÈ¤(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€Õ¹¥ÑÑ•ÍĞ¹µ…¥¸ ¤(