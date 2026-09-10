"""Tests for B's database-decoupled compute and TCP processes."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from B_dispatch.communication_service import EMSCommunicationService
from B_dispatch.compute_service import EMSComputeService
from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore
from B_dispatch.repository import EMSRepository, utc_now
from B_dispatch.tcpB import Ack


def fresh_state(*, step: int = 1, age_s: float = 0.0) -> GridState:
    now = utc_now()
    return GridState(
        session_id="process-test",
        step=step,
        sim_time_s=float(step),
        wind_speed_mps=8.0,
        wind_available_kw=70.0,
        wind_operating_limit_kw=60.0,
        load_power_kw=80.0,
        wind_actual_kw=55.0,
        diesel_actual_kw=25.0,
        wind_running=True,
        diesel_running=True,
        sampled_at_utc=now,
        received_at_utc=now,
        received_age_s=age_s,
        wind_target_kw=55.0,
        diesel_target_kw=25.0,
        pitch_actual_deg=0.0,
        power_imbalance_kw=0.0,
    )


class FakeClient:
    def __init__(self, state: GridState, *, accepted: bool = True) -> None:
        self.connected = False
        self.latest_state = state
        self._pending_state_request_seq = None
        self._pending_ack_seq = None
        self._accepted = accepted
        self.sent = []

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def receive_available(self):
        return []

    def send_dispatch(self, decision) -> int:
        self.sent.append(decision)
        return 77

    def get_ack(self, seq: int):
        return Ack(ack_seq=seq, accepted=self._accepted, reason="accepted")


class ProcessServiceTests(unittest.TestCase):
    def make_repo(self, tmp: str) -> EMSRepository:
        repo = EMSRepository(Path(tmp) / "ems.db")
        repo.initialize()
        return repo

    def test_compute_closed_loop_creates_one_executable_outbox_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            repo.save_state(fresh_state())
            service = EMSComputeService(repo, clock=lambda: 0.0, sleeper=lambda _: None)
            service.initialize()

            outbox_id = service.run_cycle(now=0.0)
            self.assertIsNotNone(outbox_id)
            row = repo.get_outbox(outbox_id)
            self.assertEqual((row["executable"], row["status"]), (1, "pending"))
            self.assertIsNone(service.run_cycle(now=1.0))
            with repo.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM dispatch_outbox").fetchone()[0], 1)

    def test_compute_open_loop_records_but_never_queues_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            repo.set_runtime_config(closed_loop=False)
            repo.save_state(fresh_state())
            service = EMSComputeService(repo, clock=lambda: 0.0, sleeper=lambda _: None)
            service.initialize()

            outbox_id = service.run_cycle(now=0.0)
            row = repo.get_outbox(outbox_id)
            self.assertEqual((row["executable"], row["status"]), (0, "open_loop"))
            self.assertIsNone(repo.claim_next_outbox())

    def test_dynamic_state_age_blocks_stale_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            received = (
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            old = replace(fresh_state(), received_at_utc=received, received_age_s=0.0)
            repo.save_state(old)
            service = EMSComputeService(repo, clock=lambda: 0.0, sleeper=lambda _: None)
            service.initialize()

            self.assertIsNone(service.run_cycle(now=0.0))
            with repo.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM dispatch_outbox").fetchone()[0], 0)

    def test_io_process_claims_and_records_accepted_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            state = fresh_state()
            repo.save_state(state)
            params = repo.get_parameters()
            physical = repo.get_physical_parameters()
            runtime = repo.get_runtime_config()
            decision = EMSCore(DispatchConfig(
                wind_min_kw=float(params["wind_min_kw"]),
                wind_max_kw=float(params["wind_max_kw"]),
                diesel_max_kw=float(params["diesel_max_kw"]),
                reserve_kw=float(params["reserve_kw"]),
                diesel_min_kw=float(physical["diesel_min_kw"]),
                max_state_age_s=float(runtime["max_state_age_s"]),
            )).decide(state)
            outbox_id, _ = repo.queue_decision(state, decision.result, executable=True)
            fake = FakeClient(state)
            service = EMSCommunicationService(
                repo,
                client_factory=lambda *args, **kwargs: fake,
                clock=lambda: 0.0,
                sleeper=lambda _: None,
            )
            service.initialize()

            self.assertEqual(service.run_cycle(), outbox_id)
            row = repo.get_outbox(outbox_id)
            self.assertEqual((row["status"], row["protocol_seq"], row["ack_accepted"]),
                             ("accepted", 77, 1))
            self.assertEqual(len(fake.sent), 1)

    def test_io_restart_marks_claimed_command_delivery_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            state = fresh_state()
            repo.save_state(state)
            result = EMSCore(DispatchConfig(
                wind_min_kw=0.0, wind_max_kw=100.0, diesel_max_kw=120.0,
                reserve_kw=10.0, diesel_min_kw=20.0, max_state_age_s=2.0,
            )).decide(state).result
            outbox_id, _ = repo.queue_decision(state, result, executable=True)
            self.assertIsNotNone(repo.claim_next_outbox())

            EMSCommunicationService(repo).initialize()
            self.assertEqual(repo.get_outbox(outbox_id)["status"], "delivery_unknown")


if __name__ == "__main__":
    unittest.main()
