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

    def test_firmware_uses_configured_c_timeout_for_tcp_responses(self):
        firmware = HOST_DIR.parent / "firmware" / "Core"
        wifi_source = (firmware / "Src" / "wifi_client.c").read_text(encoding="utf-8")
        turbine_source = (firmware / "Src" / "wind_turbine.c").read_text(encoding="utf-8")
        turbine_header = (firmware / "Inc" / "wind_turbine.h").read_text(encoding="utf-8")
        self.assertIn("now - wf_response_tick > WindTurbine_GetTimeoutMs()", wifi_source)
        self.assertIn("uint32_t WindTurbine_GetTimeoutMs(void)", turbine_source)
        self.assertIn("uint32_t WindTurbine_GetTimeoutMs(void);", turbine_header)
        self.assertNotIn("WF_RESPONSE_TIMEOUT_MS", wifi_source)

    def test_firmware_recovers_uart_and_resets_stalled_esp8266(self):
        firmware = HOST_DIR.parent / "firmware" / "Core"
        wifi_source = (firmware / "Src" / "wifi_client.c").read_text(encoding="utf-8")
        esp_source = (firmware / "Src" / "esp8266.c").read_text(encoding="utf-8")
        turbine_source = (firmware / "Src" / "wind_turbine.c").read_text(encoding="utf-8")

        self.assertIn('Esp8266_SendCmd("AT+RST")', wifi_source)
        self.assertIn('Esp8266_SendCmd("AT+CIPMUX=0")', wifi_source)
        self.assertIn('Esp8266_SendCmd("AT+CIFSR")', wifi_source)
        self.assertIn("WF_MAX_STATE_RETRIES", wifi_source)
        self.assertIn("ESP_EVT_UART_ERROR", wifi_source)
        self.assertIn("ESP_EVT_STA_IP", wifi_source)
        self.assertIn('",CONNECT"', esp_source)
        self.assertIn('",CLOSED"', esp_source)
        self.assertIn("void Esp8266_RecoverRx(void)", esp_source)
        self.assertIn("void HAL_UART_ErrorCallback", turbine_source)
        self.assertIn("#define WF_PROMPT_TIMEOUT_MS      3000u", wifi_source)
        self.assertIn("#define WF_SEND_OK_TIMEOUT_MS     6000u", wifi_source)
        self.assertIn("#define WF_MAX_STATE_RETRIES         5u", wifi_source)

        turbine_header = (firmware / "Inc" / "wind_turbine.h").read_text(encoding="utf-8")
        host_config = (HOST_DIR / "config.py").read_text(encoding="utf-8")
        self.assertIn("WT_DEFAULT_C_TIMEOUT_S         8.0f", turbine_header)
        self.assertIn('"c_timeout_s": 8.0', host_config)

    def test_host_serial_worker_reopens_after_disconnect(self):
        source = (HOST_DIR / "serial_comm.py").read_text(encoding="utf-8")
        self.assertIn("SERIAL_RECONNECT_DELAY_S", source)
        self.assertIn("正在自动重连", source)
        self.assertIn("while self._running", source)
        self.assertNotIn("串口读取异常：{e}\")\n                break", source)


if __name__ == "__main__":
    unittest.main()
