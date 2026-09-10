"""Headless checks for B's database-only operator GUI boundary."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from B_dispatch import gui_b, gui_b_legacy
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


class BGuiDatabaseBoundaryTests(unittest.TestCase):
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

    def test_inline_public_port_is_persisted_without_opening_socket(self):
        self.window.host.setText("frp.example.com:38243")
        self.window.toggle_connection()

        self.assertEqual(
            self.window.repo.communication,
            {"host": "frp.example.com", "port": 38243, "enabled": 1},
        )
        self.assertIsNone(self.window.client)
        self.assertFalse(self.window._connection_desired)

    def test_io_heartbeat_is_displayed_but_gui_never_owns_socket(self):
        self.window.repo.statuses = [{
            "process_name": "B_IO", "state": "ONLINE",
            "heartbeat_at_utc": utc_now(),
        }]
        self.window.poll_socket()

        self.assertIn("B_IO", self.window.a_status.text())
        self.assertIsNone(self.window.client)
        self.assertTrue(any(row[0] == "B_GUI" for row in self.window.repo.heartbeats))

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


if __name__ == "__main__":
    unittest.main()
