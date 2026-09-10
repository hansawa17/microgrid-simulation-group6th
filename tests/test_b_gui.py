"""Headless checks for B's direct-socket operator GUI and connection controls."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from B_dispatch import gui_b, gui_b_legacy
from B_dispatch import __main__ as b_main
from B_dispatch.models import GridState
from B_dispatch.repository import utc_now


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
    connected = True
    needs_full_sync = True
    _pending_state_request_seq = None
    _pending_ack_seq = None
    _uncertain_dispatch_seq = None

    def close(self):
        self.connected = False


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


if __name__ == "__main__":
    unittest.main()
