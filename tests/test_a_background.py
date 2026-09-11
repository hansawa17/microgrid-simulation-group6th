"""Process-level checks for A's GUI-independent services."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

from A_simulator.background import (
    ServiceAlreadyRunning,
    ServiceLease,
    launch_background_process,
    read_service_status,
    request_service_stop,
    service_log_path,
)
from A_simulator.config import load_config
from A_simulator.repository import Repository
from A_simulator.scenario import load_scenario_csv


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "A_simulator" / "config.example.json"
SCENARIO = ROOT / "A_simulator" / "scenarios" / "demo.csv"


def wait_until(predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.03)
    return bool(predicate())


class ServiceLeaseTests(unittest.TestCase):
    def test_lock_heartbeat_and_stop_request_are_instance_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "grid.db"
            db.touch()
            with ServiceLease(db, "simulation", detail="test") as service:
                status = read_service_status(db, "simulation")
                self.assertTrue(status.active)
                self.assertEqual(status.pid, os.getpid())
                with self.assertRaises(ServiceAlreadyRunning):
                    with ServiceLease(db, "simulation"):
                        pass
                request_service_stop(db, "simulation")
                self.assertTrue(service.stop_requested())
            status = read_service_status(db, "simulation")
            self.assertFalse(status.active)
            self.assertEqual(status.state, "stopped")


class DetachedServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "grid.db"
        self.repository = Repository(self.db)
        config = replace(load_config(CONFIG), end_s=10.0, poll_interval_s=0.05)
        self.repository.initialize(config, load_scenario_csv(SCENARIO))

    def tearDown(self) -> None:
        for role in ("simulation", "tcp"):
            if read_service_status(self.db, role).active:
                request_service_stop(self.db, role)
                wait_until(lambda role=role: not read_service_status(self.db, role).active)
        self.directory.cleanup()

    def test_detached_simulation_advances_without_gui_owner(self) -> None:
        self.repository.set_status("start")
        pid = launch_background_process(
            self.db,
            "simulation",
            [sys.executable, "-m", "A_simulator", "run", "--db", str(self.db)],
            working_directory=ROOT,
        )
        self.assertIsInstance(pid, int)
        advanced = wait_until(
            lambda: read_service_status(self.db, "simulation").active
            and int(self.repository.runtime()["step"]) >= 2
        )
        log_path = service_log_path(self.db)
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        self.assertTrue(
            advanced,
            "detached calculation process did not advance the database; "
            f"status={read_service_status(self.db, 'simulation')}; log={log_text[-1000:]}",
        )
        request_service_stop(self.db, "simulation")
        stopped = wait_until(lambda: not read_service_status(self.db, "simulation").active)
        self.assertTrue(
            stopped,
            f"simulation service did not stop: {read_service_status(self.db, 'simulation')}",
        )
        self.assertEqual(self.repository.runtime()["status"], "paused")

    def test_detached_tcp_service_answers_after_launcher_returns(self) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = int(reservation.getsockname()[1])
        pid = launch_background_process(
            self.db,
            "tcp",
            [
                sys.executable,
                "-m",
                "A_simulator",
                "serve",
                "--db",
                str(self.db),
                "--config",
                str(CONFIG),
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            working_directory=ROOT,
        )
        self.assertIsInstance(pid, int)
        self.assertTrue(wait_until(lambda: read_service_status(self.db, "tcp").active))
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
        response = None
        deadline = time.monotonic() + 3.0
        while response is None and time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                    client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                    with client.makefile("rb") as stream:
                        response = json.loads(stream.readline())
            except (ConnectionError, OSError):
                time.sleep(0.03)
        self.assertIsNotNone(response, "detached TCP service did not accept connections")
        self.assertEqual(response["type"], "state")
        self.assertEqual(response["target"], "B")
        request_service_stop(self.db, "tcp")
        self.assertTrue(wait_until(lambda: not read_service_status(self.db, "tcp").active))


if __name__ == "__main__":
    unittest.main()
