"""Headless checks for A's integrated PyQt6 monitoring console."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from A_simulator.config import load_config
from A_simulator.gui.main_window import PARAMETER_NAMES, SimulatorWindow
from A_simulator.repository import Repository
from A_simulator.scenario import load_scenario_csv


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "A_simulator" / "config.example.json"
SCENARIO = ROOT / "A_simulator" / "scenarios" / "antarctic_10min.csv"


class SimulatorWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "grid.db"
        self.repository = Repository(self.db_path)
        self.session_id = self.repository.initialize(
            load_config(CONFIG), load_scenario_csv(SCENARIO)
        )
        self.window = SimulatorWindow(
            db_path=self.db_path,
            config_path=CONFIG,
            scenario_path=SCENARIO,
            bind_address="127.0.0.1",
            port=54321,
        )
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def _wait_for_history(self) -> None:
        deadline = time.monotonic() + 3.0
        while self.window._history_loading and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.window._history_loading, "history worker did not finish")

    def test_dashboard_pages_endpoint_and_owner_editors(self) -> None:
        self.assertEqual(self.window.pageStack.count(), 4)
        self.assertEqual(
            [button.text() for button in self.window.navButtons],
            ["运行监控", "参数设置", "历史数据", "报警与通信"],
        )
        self.assertEqual(self.window.bindCombo.currentText(), "127.0.0.1")
        self.assertEqual(self.window.portSpin.value(), 54321)
        self.assertEqual(len(self.window.kpi_labels), 8)

        self.window.refresh_parameters(force=True)
        self.assertEqual(self.window.parameterTable.rowCount(), 19)
        self.assertEqual(
            set(self.window._parameter_editors),
            {
                "a_step_s",
                "a_poll_s",
                "wind_ramp_up_kw_per_s",
                "wind_ramp_down_kw_per_s",
                "diesel_min_power_kw",
                "diesel_max_power_kw",
                "diesel_ramp_up_kw_per_s",
                "diesel_ramp_down_kw_per_s",
            },
        )
        self.assertIn("wind_rated_power_kw", PARAMETER_NAMES)

    def test_local_parameter_edit_and_remote_read_only_sync(self) -> None:
        self.window.refresh_parameters(force=True)
        editor = self.window._parameter_editors["a_poll_s"]
        editor.setValue(0.75)
        self.window.save_a_parameters()
        snapshot = {row["name"]: row for row in self.repository.parameter_snapshot()}
        self.assertEqual(snapshot["a_poll_s"]["value"], 0.75)
        self.assertEqual(snapshot["a_poll_s"]["source"], "A_local")

        state = self.repository.get_state()
        result = self.repository.apply_command(
            {
                "version": 1,
                "type": "parameter_update",
                "source": "B",
                "target": "A",
                "session_id": self.session_id,
                "seq": 1,
                "step": state.step,
                "sim_time_s": state.sim_time_s,
                "payload": {"parameters": {"reserve_kw": 12.0}},
            }
        )
        self.assertTrue(result.accepted)
        self.window.refresh_parameters(force=True)
        reserve_row = next(
            row
            for row in range(self.window.parameterTable.rowCount())
            if self.window.parameterTable.item(row, 1).text() == "reserve_kw"
        )
        self.assertIsNone(self.window.parameterTable.cellWidget(reserve_row, 3))
        self.assertEqual(self.window.parameterTable.item(reserve_row, 3).text(), "12.0")
        self.assertEqual(self.window.parameterTable.item(reserve_row, 5).text(), "B_tcp")
        self.assertGreaterEqual(self.window.parameterHistoryTable.rowCount(), 2)

    def test_realtime_and_history_charts_use_sampled_utc(self) -> None:
        state = self.repository.get_state()
        self.window._refresh_live_charts(state)
        self.assertEqual(self.window.windTrend._timestamps, [state.sampled_at_utc])
        self.assertEqual(self.window.powerTrend._timestamps, [state.sampled_at_utc])

        self.window.refresh_history()
        self._wait_for_history()
        self.assertEqual(self.window.historyTrend._timestamps, [state.sampled_at_utc])
        self.assertTrue(self.window.historyTable.item(0, 0).text().endswith("UTC+8"))
        self.assertIn("UTC+8 北京时间", self.window.clockLabel.text())
        self.assertEqual(
            len(self.window.wind_editor._time_labels),
            len(self.window.wind_editor.times_s()),
        )

    def test_stopped_run_can_create_a_new_safe_session(self) -> None:
        self.repository.set_status("start")
        self.assertIsNotNone(self.repository.step_once())
        self.repository.set_status("stop")
        self.window.refresh_state()
        self.assertTrue(self.window.new_session_button.isEnabled())

        self.window.create_new_session()
        self.app.processEvents()

        self.assertEqual(self.repository.runtime()["status"], "ready")
        self.assertNotEqual(self.repository.get_state().session_id, self.session_id)
        self.assertTrue(self.window.start_button.isEnabled())
        state = self.repository.get_state()
        self.window._refresh_live_charts(state)
        self.assertEqual(self.window.windTrend._timestamps, [state.sampled_at_utc])
        self.window.refresh_history()
        self._wait_for_history()
        self.assertEqual(self.window.historySessionCombo.count(), 2)
        self.assertEqual(
            self.window.historySessionCombo.currentData(), state.session_id
        )
        self.assertEqual(self.window.historyTable.rowCount(), 1)

        self.window.historyLimitSpin.setValue(10)
        self.repository.set_status("start")
        for _ in range(12):
            self.assertIsNotNone(self.repository.step_once())
        self.repository.set_status("pause")
        self.window.refresh_history()
        self._wait_for_history()
        self.assertEqual(self.window.historySessionCombo.count(), 2)
        self.assertEqual(self.window.historyTable.rowCount(), 10)

    def test_corrupt_database_opens_in_visible_error_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corrupt_db = Path(directory) / "grid.db"
            corrupt_db.write_bytes(b"not a sqlite database")
            window = SimulatorWindow(
                db_path=corrupt_db,
                config_path=CONFIG,
                scenario_path=SCENARIO,
                bind_address="127.0.0.1",
                port=54322,
            )
            try:
                self.app.processEvents()
                self.assertEqual(window.dbPill.text(), "grid.db 异常")
                self.assertIn("数据库读取失败", window.runtime_detail.text())
                self.assertEqual(
                    window.communicationLabels["database"].text(),
                    "grid.db　异常",
                )
                self.assertFalse(window.start_button.isEnabled())
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_runner_exit_recovers_running_database_to_paused(self) -> None:
        self.repository.set_status("start")
        self.window._runner_finished(1)
        self.app.processEvents()
        self.assertEqual(self.repository.runtime()["status"], "paused")
        self.assertTrue(self.window.resume_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
