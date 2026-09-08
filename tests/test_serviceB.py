"""Tests for B's TCP/SQLite/runtime integration service."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore, EMSDecision
from B_dispatch.repository import EMSRepository
from B_dispatch.runtime import RuntimeConfig
from B_dispatch.serviceB import EMSServiceB
from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient


class ServiceBTests(unittest.TestCase):
    def make_state(self):
        return GridState(
            session_id="s1", step=5, sim_time_s=5.0, wind_speed_mps=8.0,
            wind_available_kw=70.0, wind_operating_limit_kw=60.0,
            load_power_kw=60.0, wind_actual_kw=0.0, diesel_actual_kw=0.0,
            wind_running=True, fault=False,
            sampled_at_utc="2026-09-07T08:03:25.417Z",
            pitch_actual_deg=0.0,
        )

    def test_poll_state_persists_protocol_power_fields_and_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            client = Mock(spec=EMSTcpClient)
            client.poll_state.return_value = self.make_state()
            service = EMSServiceB(client, repo, EMSCore(DispatchConfig(wind_max_kw=100, diesel_max_kw=100)))

            state = service._poll_state()

            self.assertEqual(state.step, 5)
            row = repo.get_current_state()
            self.assertEqual(row["wind_available_kw"], 70.0)
            self.assertEqual(row["wind_operating_limit_kw"], 60.0)
            self.assertEqual(row["sampled_at_utc"], "2026-09-07T08:03:25.417Z")

    def test_send_decision_persists_accepted_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            client = Mock(spec=EMSTcpClient)
            client.send_dispatch.return_value = 7
            client.get_ack.return_value = Ack(7, True, "accepted")
            core = EMSCore(DispatchConfig(wind_max_kw=100, diesel_max_kw=100))
            service = EMSServiceB(client, repo, core)
            decision = core.decide(self.make_state())

            service._send_decision(decision)

            client.send_dispatch.assert_called_once_with(decision)
            client.get_ack.assert_called_once_with(7)
            with repo.connection() as conn:
                command = conn.execute("SELECT * FROM dispatch_commands WHERE seq=7").fetchone()
                evaluation = conn.execute("SELECT * FROM dispatch_evaluation WHERE command_id=?", (command["id"],)).fetchone()
            self.assertEqual(command["status"], "accepted")
            self.assertEqual(command["ack_accepted"], 1)
            self.assertEqual(command["ack_reason"], "accepted")
            self.assertEqual(evaluation["target_unserved_kw"], decision.result.target_unserved_kw)

    def test_send_decision_persists_rejected_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            client = Mock(spec=EMSTcpClient)
            client.send_dispatch.return_value = 7
            client.get_ack.return_value = Ack(7, False, "stale command")
            core = EMSCore(DispatchConfig(wind_max_kw=100, diesel_max_kw=100))
            service = EMSServiceB(client, repo, core)

            service._send_decision(core.decide(self.make_state()))

            with repo.connection() as conn:
                command = conn.execute("SELECT status, ack_accepted, ack_reason FROM dispatch_commands WHERE seq=7").fetchone()
            self.assertEqual(tuple(command), ("rejected", 0, "stale command"))

    def test_unknown_delivery_is_logged_as_unknown_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            client = Mock(spec=EMSTcpClient)
            client.send_dispatch.side_effect = DispatchDeliveryUnknown(7, "ACK timeout")
            core = EMSCore(DispatchConfig(wind_max_kw=100, diesel_max_kw=100))
            service = EMSServiceB(client, repo, core)

            with self.assertRaises(DispatchDeliveryUnknown):
                service._send_decision(core.decide(self.make_state()))

            with repo.connection() as conn:
                command = conn.execute("SELECT status, reason FROM dispatch_commands WHERE seq=7").fetchone()
            self.assertEqual(command["status"], "delivery_unknown")
            self.assertIn("ACK timeout", command["reason"])

    def test_runtime_cycle_emits_decision_through_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = EMSRepository(Path(tmp) / "ems.db")
            repo.initialize()
            client = Mock(spec=EMSTcpClient)
            client.poll_state.return_value = self.make_state()
            client.send_dispatch.return_value = 8
            client.get_ack.return_value = Ack(8, True, "accepted")
            service = EMSServiceB(
                client, repo, EMSCore(DispatchConfig(wind_max_kw=100, diesel_max_kw=100)),
                runtime_config=RuntimeConfig(poll_period_s=1, dispatch_period_s=5),
            )

            decision = service.run_cycle(now=0.0)

            self.assertIsInstance(decision, EMSDecision)
            client.poll_state.assert_called_once()
            client.send_dispatch.assert_called_once_with(decision)
            self.assertIsNotNone(repo.get_current_state())


if __name__ == "__main__":
    unittest.main()
