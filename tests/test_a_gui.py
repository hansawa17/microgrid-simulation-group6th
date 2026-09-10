"""Headless checks for A's integrated PyQt6 monitoring console."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from A_simulator.config import load_config
from A_simulator.gui.main_window import LINK_REFRESH_MS, PARAMETER_NAMES, SimulatorWindow
from A_simulator.repository import Repository
from A_simulator.scenario import load_scenario_csv, save_scenario_csv


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

    def _wait_for_scenario_apply(self) -> None:
        deadline = time.monotonic() + 3.0
        while self.window._scenario_apply_in_progress and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(
            self.window._scenario_apply_in_progress,
            "scenario apply worker did not finish",
        )

    def test_dashboard_pages_endpoint_and_owner_editors(self) -> None:
        self.assertEqual(self.window.pageStack.count(), 5)
        self.assertEqual(
            [button.text() for button in self.window.navButtons],
            ["运行监控", "参数设置", "历史数据", "数据库分类", "报警与通信"],
        )
        self.assertEqual(self.window.bindCombo.currentText(), "127.0.0.1")
        self.assertEqual(self.window.portSpin.value(), 54321)
        self.assertEqual(len(self.window.kpi_labels), 8)

        self.window._tcp_state_changed(QProcess.ProcessState.Running)
        self.assertTrue(self.window.portSpin.isEnabled())
        self.assertTrue(self.window.bindCombo.isEnabled())
        previous_port = self.window.portSpin.value()
        self.window.portSpin.stepUp()
        self.assertEqual(self.window.portSpin.value(), previous_port + 1)

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
        self.assertEqual(self.window._link_health_timer.interval(), LINK_REFRESH_MS)

    def test_database_tabs_explicitly_separate_four_remotes(self) -> None:
        self.window.refresh_database_categories(force=True)
        self.assertEqual(
            [self.window.databaseTabs.tabText(index) for index in range(4)],
            ["YC 遥测", "YX 遥信", "YT 遥调", "YK 遥控"],
        )
        self.assertGreater(self.window.databaseTables["YC"].rowCount(), 0)
        self.assertGreater(self.window.databaseTables["YX"].rowCount(), 0)
        self.assertGreater(self.window.databaseTables["YT"].rowCount(), 0)
        self.assertGreater(self.window.databaseTables["YK"].rowCount(), 0)
        self.assertIn("YK=", self.window.databaseSummaryLabel.text())

    def test_simulation_duration_rescales_scenario_axis(self) -> None:
        original_count = len(self.window.wind_editor.times_s())
        self.window.simulationDurationSpin.setValue(300.0)
        self.window.apply_simulation_duration()
        self.assertEqual(len(self.window.wind_editor.times_s()), original_count)
        self.assertAlmostEqual(self.window.wind_editor.times_s()[0], 0.0)
        self.assertAlmostEqual(self.window.wind_editor.times_s()[-1], 300.0)
        self.assertAlmostEqual(self.window.config.end_s, 300.0)
        self.assertTrue(self.window._scenario_dirty)

    def test_stale_connection_never_remains_visually_normal(self) -> None:
        old = "2000-01-01T00:00:00.000Z"
        with patch.object(
            self.window.repository,
            "connection_statuses",
            return_value={
                "B": {"connected": True, "last_seen_at_utc": old, "detail": "stale"},
                "C": {"connected": False, "last_seen_at_utc": None, "detail": "closed"},
            },
        ):
            self.window._refresh_link_health()
        self.window.refresh_state()
        self.assertIn("离线", self.window.bPill.text())
        self.assertIn("Ping(心跳龄)", self.window.bPill.text())
        self.assertIn("连接记录已过期", self.window.monitor_status["B"][1].text())

    def test_log_export_and_new_database_warning_popup(self) -> None:
        self.window.refresh_history()
        self._wait_for_history()
        self.window.export_selected_logs()
        assert self.window._log_export_path is not None
        self.assertTrue(self.window._log_export_path.is_file())
        self.assertTrue(self.window.revealLogsButton.isEnabled())
        first = self.window._log_export_path.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn('"occurred_at_utc"', first)
        self.assertIn('"object"', first)

        self.window._refresh_alerts()
        self.repository.log("WARNING", "manufactured_fault", "test fault")
        with patch.object(self.window, "_show_fault_popup") as popup:
            self.window._refresh_alerts()
        popup.assert_called_once()
        self.assertIn("manufactured_fault", popup.call_args.args[0])

    def test_starting_simulation_also_ensures_tcp_service(self) -> None:
        with (
            patch.object(self.window, "_ensure_tcp_server", return_value=True) as ensure_tcp,
            patch.object(self.window, "_ensure_runner") as ensure_runner,
        ):
            self.window.control_simulation("start")
        ensure_tcp.assert_called_once_with()
        ensure_runner.assert_called_once_with()
        self.assertEqual(self.repository.runtime()["status"], "running")

    def test_simulation_does_not_start_when_tcp_startup_is_rejected(self) -> None:
        with (
            patch.object(self.window, "_ensure_tcp_server", return_value=False),
            patch.object(self.window.repository, "set_status") as set_status,
        ):
            self.window.control_simulation("start")
        set_status.assert_not_called()
        self.assertEqual(self.repository.runtime()["status"], "ready")

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
        self.assertIn("SCADA 1 条", self.window.historyRangeSummary.text())
        self.assertIn("UTC+8", self.window.historyTrend.title)

    def test_history_page_traces_control_change_in_selected_range(self) -> None:
        state = self.repository.get_state()
        result = self.repository.apply_command(
            {
                "version": 1,
                "type": "wind_action",
                "source": "C",
                "target": "A",
                "session_id": self.session_id,
                "seq": 1,
                "step": state.step,
                "sim_time_s": state.sim_time_s,
                "payload": {
                    "wind_enable": True,
                    "pitch_target_deg": 10.0,
                    "wind_available_kw": 40.0,
                    "wind_operating_limit_kw": 35.0,
                },
            }
        )
        self.assertTrue(result.accepted)
        self.window.refresh_history()
        self._wait_for_history()
        self.assertGreater(self.window.traceTable.rowCount(), 0)
        events = {
            self.window.traceTable.item(row, 2).text()
            for row in range(self.window.traceTable.rowCount())
        }
        self.assertIn("wind_action", events)
        self.assertIn("追溯", self.window.historyRangeSummary.text())

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
                self.assertTrue(window.initialize_button.isEnabled())
                self.assertEqual(window.initialize_button.text(), "修复 grid.db")

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    window.initialize_database()
                self.app.processEvents()
                self.assertEqual(Repository(corrupt_db).runtime()["status"], "ready")
                self.assertEqual(window.dbPill.text(), "grid.db 正常")
                self.assertEqual(len(list(corrupt_db.parent.glob("grid.db.*.bak"))), 1)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_curve_edits_sync_source_csv_and_can_save_as(self) -> None:
        source = Path(self.directory.name) / "editable.csv"
        save_scenario_csv(source, load_scenario_csv(SCENARIO))
        self.window._set_scenario(load_scenario_csv(source), source)

        self.window.wind_editor._values[0] = 7.25
        self.window.wind_editor.curveChanged.emit(self.window.wind_editor.values())
        self.assertTrue(self.window._scenario_dirty)
        self.window.wind_editor.editingFinished.emit()
        self.app.processEvents()
        self.assertEqual(load_scenario_csv(source).points[0].wind_speed_mps, 7.25)
        self.assertFalse(self.window._scenario_dirty)

        self.window.load_editor._values[0] = 88.5
        self.window.load_editor.curveChanged.emit(self.window.load_editor.values())
        destination_without_suffix = Path(self.directory.name) / "copy"
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(destination_without_suffix), "CSV (*.csv)"),
        ):
            self.window.save_csv()
        destination = destination_without_suffix.with_suffix(".csv")
        self.assertTrue(destination.is_file())
        self.assertEqual(load_scenario_csv(destination).points[0].load_power_kw, 88.5)
        self.assertEqual(self.window._scenario_source_path, destination.resolve())

    def test_apply_curve_button_updates_next_step_without_stopping_simulation(self) -> None:
        self.assertEqual(self.window.apply_scenario_button.text(), "应用曲线到仿真")
        self.assertIsNot(self.window.apply_scenario_button, self.window.sync_button)
        self.repository.set_status("start")

        self.window.wind_editor._values[1] = 7.25
        self.window.load_editor._values[1] = 88.5
        self.window.wind_editor.curveChanged.emit(self.window.wind_editor.values())
        self.assertTrue(self.window._scenario_runtime_dirty)

        self.window.apply_scenario_button.click()
        self._wait_for_scenario_apply()

        self.assertFalse(self.window._scenario_runtime_dirty)
        self.assertEqual(self.repository.runtime()["status"], "running")
        state = self.repository.step_once()
        assert state is not None
        self.assertEqual(state.step, 1)
        self.assertEqual(state.wind_speed_mps, 7.25)
        self.assertEqual(state.load_power_kw, 88.5)
        self.assertEqual(self.repository.runtime()["status"], "running")

    def test_runner_exit_recovers_running_database_to_paused(self) -> None:
        self.repository.set_status("start")
        self.window._runner_finished(1)
        self.app.processEvents()
        self.assertEqual(self.repository.runtime()["status"], "paused")
        self.assertTrue(self.window.resume_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
