"""Software checks for C host runtime Wi-Fi and parameter configuration."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path


HOST_DIR = Path(__file__).resolve().parents[1] / "C_controller" / "host"


class CRuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HOST_DIR))
        cls.config = importlib.import_module("config")
        cls.database_module = importlib.import_module("database")
        cls.protocol = importlib.import_module("protocol")
        cls.simulator_module = importlib.import_module("simulator")

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(str(HOST_DIR))
        except ValueError:
            pass
        for name in ("simulator", "protocol", "database", "config"):
            sys.modules.pop(name, None)

    def test_wifi_frames_set_query_and_report_server(self):
        self.assertEqual(
            self.protocol.build_wifi_frame("frp.example.com", 38243),
            b"$WIFI,frp.example.com,38243\r\n",
        )
        self.assertEqual(self.protocol.build_wifi_query_frame(), b"$WIFI?\r\n")
        self.assertEqual(
            self.protocol.parse_frame("$WIFIGET,frp.example.com,38243\r\n"),
            ("wifiget", ("frp.example.com", "38243")),
        )

    def test_database_and_running_simulator_accept_current_parameters(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = self.database_module.Database(str(Path(tmp_dir) / "wind.db"))
            try:
                params = db.get_params()
                params["wind_rated_power_kw"] = 95.0
                changes = db.update_params(params)
                self.assertIn(("wind_rated_power_kw", 100.0, 95.0), changes)
                self.assertEqual(db.get_params()["wind_rated_power_kw"], 95.0)

                simulator = self.simulator_module.Simulator()
                simulator.set_params(db.get_params())
                self.assertEqual(simulator.params["wind_rated_power_kw"], 95.0)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
