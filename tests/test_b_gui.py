"""Headless checks for B's connection lifecycle."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from B_dispatch import gui_b


class FakeRepository:
    def __init__(self, path):
        self.path = path

    def initialize(self):
        return None

    def get_parameters(self):
        return {
            "wind_min_kw": 0.0,
            "wind_max_kw": 100.0,
            "diesel_max_kw": 120.0,
        }

    def get_runtime_config(self):
        return {
            "poll_period_s": 1.0,
            "dispatch_period_s": 5.0,
            "closed_loop": False,
        }


class FakeClient:
    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.connected = False
        self.needs_full_sync = True
        self._pending_state_request_seq = None
        self._pending_ack_seq = None
        self._uncertain_dispatch_seq = None
        self._last_incoming_seq = None
        self._receive_error = None

    def connect(self):
        time.sleep(0.2)
        self.connected = True

    def close(self):
        self.connected = False

    def receive_available(self):
        if self._receive_error is not None:
            raise self._receive_error
        return []


class BGuiConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        repo_patch = patch.object(gui_b, "EMSRepository", FakeRepository)
        client_patch = patch.object(gui_b, "EMSTcpClient", FakeClient)
        self.addCleanup(repo_patch.stop)
        self.addCleanup(client_patch.stop)
        repo_patch.start()
        client_patch.start()
        self.window = gui_b.MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _wait_connected(self):
        deadline = time.monotonic() + 1.0
        while not self.window.client and time.monotonic() < deadline:
            self.window.poll_socket()
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNotNone(self.window.client)

    def test_inline_public_port_connects_in_background(self):
        self.window.host.setText("frp.example.com:38243")
        started = time.monotonic()
        self.window.toggle_connection()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(self.window._connection_desired)
        self._wait_connected()
        self.assertEqual((self.window.client.host, self.window.client.port), ("frp.example.com", 38243))
        self.assertEqual(self.window.port.value(), 38243)

    def test_unexpected_disconnect_schedules_bounded_reconnect(self):
        self.window.toggle_connection()
        self._wait_connected()
        self.window.client._receive_error = ConnectionError("peer reset")
        self.window.poll_socket()
        self.assertIsNone(self.window.client)
        self.assertTrue(self.window._connection_desired)
        self.assertEqual(self.window._reconnect_attempt, 1)
        self.assertGreater(self.window._reconnect_due, time.monotonic())


if __name__ == "__main__":
    unittest.main()
