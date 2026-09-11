"""Headless checks for B's direct-socket operator GUI and connection controls."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from B_dispatch import gui_b, gui_b_legacy
from B_dispatch import __main__ as b_main
from B_dispatch.models import DispatchResult, GridState
from B_dispatch.operator_core import EMSDecision
from B_dispatch.repository import EMSRepository, utc_now


class FakeRepository:
    def __init__(self, path):
        self.path = path
        self.communication = {
            "host": "127.0.0.1", "port": 5000, "enabled": 0,
        }
        self.statuses = []
        self.heartbeats = []

    def initialize(self):
        return None

    def get_parameters(self):
        return {
            "wind_min_kw": 0.0, "wind_max_kw": 100.0,
            "diesel_max_kw": 120.0, "reserve_kw": 10.0,
        }

    def get_runtime_config(self):
        return {
            "poll_period_s": 1.0, "dispatch_period_s": 5.0,
            "closed_loop": False, "command_timeout_s": 3.0,
            "max_state_age_s": 2.0,
        }

    def get_physical_parameters(self):
        return {
            "wind_rated_kw": 100.0, "wind_cut_in_mps": 3.0,
            "wind_rated_speed_mps": 12.0, "wind_cut_out_mps": 25.0,
            "pitch_min_deg": 0.0, "pitch_max_deg": 90.0,
            "wind_ramp_up_kw_s": 40.0, "wind_ramp_down_kw_s": 60.0,
            "diesel_min_kw": 20.0, "diesel_max_kw": 120.0,
        }

    def get_communication_config(self):
        return self.communication

    def set_communication_config(self, *, host, port, enabled=True):
        self.communication = {"host": host, "port": port, "enabled": int(enabled)}

    def get_current_grid_state(self):
        return None

    def get_process_status(self):
        return self.statuses

    def heartbeat(self, process_name, *, pid, state, detail=""):
        self.heartbeats.append((process_name, pid, state, detail))


class FakeConnectedClient:
    def __init__(self):
        self.connected = True
        self.needs_full_sync = True
        self._pending_state_request_seq = None
        self._pending_ack_seq = None
        self._uncertain_dispatch_seq = None
        self._last_incoming_seq = None
        self.sent = []

    def close(self):
        self.connected = False

    def send_dispatch_nowait(self, decision):
        self.sent.append(decision)
        return len(self.sent)

    def get_ack(self, _seq):
        return None


class BGuiConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        repo_patch = patch.object(gui_b_legacy, "EMSRepository", FakeRepository)
        self.addCleanup(repo_patch.stop)
        repo_patch.start()
        self.window = gui_b.MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_inline_public_port_is_persisted_and_connection_is_desired(self):
        with patch.object(self.window, "_begin_connection") as begin:
            self.window.host.setText("frp.example.com:38243")
            self.window.toggle_connection()
            begin.assert_called_once()
        self.assertEqual(
            self.window.repo.communication,
            {"host": "frp.example.com", "port": 38243, "enabled": 1},
        )
        self.assertTrue(self.window._connection_desired)
        self.assertEqual(self.window._connection_endpoint, ("frp.example.com", 38243))

    def test_disconnected_button_is_red_and_requests_are_disabled(self):
        self.window.set_connection_state(False)
        self.assertEqual(self.window.connect_btn.text(), "连接 A 服务器")
        self.assertEqual(self.window.connect_btn.property("kind"), "danger")
        self.assertEqual(self.window.a_status.text(), "A 未在线")
        self.assertFalse(self.window.request_btn.isEnabled())

    def test_connected_button_turns_green_and_requests_are_enabled(self):
        self.window.client = FakeConnectedClient()
        self.window._connection_desired = True
        self.window.set_connection_state(True)
        self.assertEqual(self.window.connect_btn.text(), "断开 A 服务器")
        self.assertEqual(self.window.connect_btn.property("kind"), "success")
        self.assertEqual(self.window.a_status.text(), "A 在线（GUI 直连）")
        self.assertTrue(self.window.request_btn.isEnabled())

    def test_direct_gui_connection_does_not_depend_on_b_io_heartbeat(self):
        self.window.repo.communication["enabled"] = 0
        self.window.client = FakeConnectedClient()
        self.window._connection_desired = True
        self.window.set_connection_state(True)
        self.assertTrue(self.window.client.connected)
        self.assertEqual(self.window.connect_btn.property("kind"), "success")
        self.assertEqual(self.window.a_status.text(), "A 在线（GUI 直连）")

    def test_scada_table_explicitly_labels_the_four_remote_categories(self):
        now = utc_now()
        self.window.state = GridState(
            session_id="gui-test", step=1, sim_time_s=1.0,
            wind_speed_mps=8.0, wind_available_kw=70.0,
            wind_operating_limit_kw=60.0, load_power_kw=80.0,
            wind_actual_kw=55.0, diesel_actual_kw=25.0,
            wind_running=True, diesel_running=True, fault=False,
            sampled_at_utc=now, received_at_utc=now,
            wind_target_kw=60.0, diesel_target_kw=20.0,
            pitch_actual_deg=0.0, power_imbalance_kw=0.0,
        )
        self.window.refresh_state_views()
        categories = {
            self.window.scada_table.item(row, 0).text()
            for row in range(self.window.scada_table.rowCount())
        }
        self.assertTrue({"YC 遥测", "YX 遥信", "YT 遥调", "YK 遥控"} <= categories)

    def test_default_runtime_launches_only_the_gui_tcp_owner(self):
        with patch.object(b_main, "_launch_gui", return_value=17) as launch_gui, patch(
            "B_dispatch.repository.EMSRepository.initialize"
        ) as initialize:
            self.assertEqual(b_main._launch_full_runtime(), 17)
        initialize.assert_called_once()
        launch_gui.assert_called_once_with()

    def test_real_sqlite_row_can_refresh_physical_parameters(self):
        original_repo = self.window.repo
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = EMSRepository(Path(temp_dir) / "ems.db")
                repo.initialize()
                self.assertFalse(hasattr(repo.get_physical_parameters(), "items"))
                self.window.repo = repo

                self.window._reload_physical_widgets()
                self.assertEqual(self.window._physical["wind_rated_kw"], 100.0)
                self.assertEqual(self.window.c_physical_widgets["wind_cut_out_mps"].value(), 25.0)

                self.window.load_db_config()
                self.assertEqual(self.window._physical["diesel_max_kw"], 120.0)
                self.assertEqual(self.window.a_physical_widgets["diesel_min_kw"].value(), 20.0)
        finally:
            self.window.repo = original_repo

    def test_real_default_closed_loop_enables_automatic_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = EMSRepository(Path(temp_dir) / "ems.db")
            repo.initialize()
            with patch.object(gui_b_legacy, "EMSRepository", return_value=repo):
                window = gui_b.MainWindow()
            try:
                self.assertTrue(window.params["closed_loop"])
                self.assertTrue(window.auto_dispatch)
                self.assertTrue(window.auto_box.isChecked())
                self.assertEqual(window.runtime_timer.interval(), 1000)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_runtime_tick_sends_only_when_yk_yt_changes(self):
        client = FakeConnectedClient()
        self.window.client = client
        self.window.state = object()
        self.window.auto_dispatch = True
        self.window.params["closed_loop"] = True
        self.window._last_decision_monotonic = time.monotonic()
        self.window.last_decision = SimpleNamespace(
            result=SimpleNamespace(
                wind_target_kw=60.0,
                diesel_target_kw=20.0,
                wind_enable=True,
                diesel_enable=True,
            )
        )
        self.window._last_sent_command = (60.0, 20.0, True, True)

        with patch.object(self.window, "calculate_current") as calculate:
            self.window.runtime_tick()
        calculate.assert_not_called()
        self.assertEqual(client.sent, [])

        self.window.last_decision.result.diesel_target_kw = 25.0
        self.window.runtime_tick()
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(self.window._last_sent_command, (60.0, 25.0, True, True))

    def test_ack_records_dispatch_and_later_state_persists_wind_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = EMSRepository(Path(temp_dir) / "ems.db")
            repo.initialize()
            with patch.object(gui_b_legacy, "EMSRepository", return_value=repo):
                window = gui_b.MainWindow()
            try:
                sent_at = datetime.now(timezone.utc)
                sent_at_utc = sent_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                command_state = GridState(
                    session_id="gui-evaluation", step=1, sim_time_s=1.0,
                    wind_speed_mps=8.0, wind_available_kw=70.0,
                    wind_operating_limit_kw=60.0, load_power_kw=80.0,
                    wind_actual_kw=55.0, diesel_actual_kw=25.0,
                    wind_running=True, diesel_running=True, fault=False,
                    sampled_at_utc=sent_at_utc, received_at_utc=sent_at_utc,
                    wind_target_kw=60.0, diesel_target_kw=20.0,
                    pitch_actual_deg=5.0, power_imbalance_kw=0.0,
                )
                decision = EMSDecision(
                    state=command_state,
                    result=DispatchResult(
                        wind_target_kw=60.0, diesel_target_kw=20.0,
                        wind_enable=True, diesel_enable=True,
                        target_unserved_kw=0.0, target_surplus_kw=0.0,
                        reason="test dispatch",
                    ),
                )
                window._pending_dispatch_record[77] = decision
                window.handle_ack(gui_b.Ack(ack_seq=77, accepted=True, reason="accepted"))

                with repo.connection() as conn:
                    command = conn.execute(
                        "SELECT * FROM dispatch_commands WHERE seq=77"
                    ).fetchone()
                self.assertIsNotNone(command)
                self.assertEqual((command["status"], command["ack_accepted"]), ("accepted", 1))

                feedback_at = sent_at + timedelta(seconds=1)
                feedback_at_utc = feedback_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                window.client = FakeConnectedClient()
                window.set_state_snapshot(GridState(
                    session_id="gui-evaluation", step=2, sim_time_s=2.0,
                    wind_speed_mps=8.0, wind_available_kw=70.0,
                    wind_operating_limit_kw=60.0, load_power_kw=80.0,
                    wind_actual_kw=59.0, diesel_actual_kw=21.0,
                    wind_running=True, diesel_running=True, fault=False,
                    sampled_at_utc=feedback_at_utc, received_at_utc=feedback_at_utc,
                    wind_target_kw=60.0, diesel_target_kw=20.0,
                    pitch_actual_deg=4.0, power_imbalance_kw=0.0,
                    controller_wind_enable=True, pitch_target_deg=4.0,
                    last_wind_action_seq=88, last_wind_action_step=2,
                    wind_action_applied_at_utc=feedback_at_utc,
                    extension_status="complete",
                ))

                with repo.connection() as conn:
                    evaluation = conn.execute(
                        "SELECT * FROM wind_execution_evaluation WHERE dispatch_command_id=?",
                        (command["id"],),
                    ).fetchone()
                self.assertIsNotNone(evaluation)
                self.assertTrue(evaluation["evaluated_at_utc"])
                self.assertEqual(evaluation["feedback_step"], 2)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
