"""Tests for B's TCP framing and A-facing protocol client."""

import json
import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore
from B_dispatch.tcp import EMSTcpClient, JsonLineFramer, ProtocolError, encode_frame


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def state_message(step=5, session="s1"):
    return {
        "version": 1, "type": "state", "source": "A", "target": "B",
        "session_id": session, "seq": 10, "step": step, "sim_time_s": float(step),
        "payload": {
            "sampled_at_utc": "2026-09-07T08:03:25.417Z", "wind_speed_mps": 8.2,
            "load_power_kw": 76.0, "wind_actual_kw": 42.0, "diesel_actual_kw": 34.0,
            "wind_target_kw": 45.0, "pitch_actual_deg": 0.0, "wind_running": True, "fault": False,
        },
    }


class TcpTests(unittest.TestCase):
    def test_framer_handles_split_and_multiple_frames(self):
        first = encode_frame(state_message())
        second = encode_frame(state_message(step=6))
        framer = JsonLineFramer()
        self.assertEqual(framer.feed(first[:10]), [])
        messages = framer.feed(first[10:] + second)
        self.assertEqual([m["step"] for m in messages], [5, 6])

    def test_framer_rejects_non_json_and_oversize(self):
        with self.assertRaises(ProtocolError):
            JsonLineFramer().feed(b"not-json\n")
        with self.assertRaises(ProtocolError):
            JsonLineFramer().feed(b"{" + b"x" * 4096)

    def test_encode_rejects_nan(self):
        with self.assertRaises(ProtocolError):
            encode_frame({"value": float("nan")})

    def test_client_connect_sends_full_state_request(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        self.assertTrue(client.connected)
        message = json.loads(fake.sent[0])
        self.assertEqual(message["type"], "state_request")
        self.assertEqual(message["source"], "B")
        self.assertEqual(message["target"], "A")
        self.assertTrue(message["payload"]["full"])

    def test_client_parses_state_and_records_local_receive_time(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        result = client.receive(encode_frame(state_message()))
        self.assertEqual(len(result), 1)
        state = result[0]
        self.assertEqual(state.sampled_at_utc, "2026-09-07T08:03:25.417Z")
        self.assertTrue(state.received_at_utc.endswith("Z"))
        self.assertEqual(client.latest_state.step, 5)

    def test_client_rejects_bad_direction_and_requests_resync_on_rollback(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        client.receive(encode_frame(state_message(step=5)))
        bad = state_message(step=4)
        client.receive(encode_frame(bad))
        self.assertTrue(client.needs_full_sync)
        self.assertIsNone(client.latest_state)
        self.assertGreaterEqual(len(fake.sent), 2)

    def test_dispatch_contains_only_allowed_b_targets(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        state = GridState(session_id="s1", step=5, sim_time_s=5.0, wind_speed_mps=8.0,
                          load_power_kw=60.0, wind_actual_kw=40.0, diesel_actual_kw=0.0,
                          wind_running=True, sampled_at_utc="2026-09-07T08:03:25.417Z")
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(state)
        client.send_dispatch(decision)
        message = json.loads(fake.sent[-1])
        self.assertEqual(message["type"], "dispatch")
        self.assertEqual(set(message["payload"]), {"wind_target_kw", "diesel_target_kw", "wind_enable", "diesel_enable"})
        self.assertNotIn("pitch_target_deg", message["payload"])

    def test_client_rejects_zero_bind_address(self):
        with self.assertRaises(ValueError):
            EMSTcpClient("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
