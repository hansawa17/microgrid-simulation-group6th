"""Tests for B-owned TCP/JSON-line transport."""

import json
import socket
import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore
from B_dispatch.tcpB import Ack, EMSTcpClient, JsonLineFramer, ProtocolError, encode_frame


class FakeSocket:
    def __init__(self, recv_data=b""):
        self.sent = []
        self.timeout = None
        self.closed = False
        self.recv_queue = [recv_data] if isinstance(recv_data, bytes) else list(recv_data)

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self.recv_queue:
            raise socket.timeout()
        return self.recv_queue.pop(0)

    def close(self):
        self.closed = True


def state_message(step=5, session="s1", seq=10):
    return {
        "version": 1, "type": "state", "source": "A", "target": "B",
        "session_id": session, "seq": seq, "step": step, "sim_time_s": float(step),
        "payload": {
            "sampled_at_utc": "2026-09-07T08:03:25.417Z", "wind_speed_mps": 8.2,
            "load_power_kw": 76.0, "wind_actual_kw": 42.0, "diesel_actual_kw": 34.0,
            "wind_target_kw": 45.0, "pitch_actual_deg": 0.0, "wind_running": True, "fault": False,
        },
    }


def ack_message(ack_seq, seq=20, accepted=True, reason="accepted"):
    return {
        "version": 1, "type": "ack", "source": "A", "target": "B",
        "session_id": "s1", "seq": seq, "step": 5, "sim_time_s": 5.0,
        "payload": {"ack_seq": ack_seq, "accepted": accepted, "reason": reason},
    }


class TcpBTests(unittest.TestCase):
    def test_framer_handles_split_and_multiple_frames(self):
        first = encode_frame(state_message())
        second = encode_frame(state_message(step=6, seq=11))
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
        message = json.loads(fake.sent[0])
        self.assertEqual(message["type"], "state_request")
        self.assertTrue(message["payload"]["full"])

    def test_poll_state_does_not_send_second_request_while_first_is_pending(self):
        fake = FakeSocket([encode_frame(state_message())])
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        state = client.poll_state()
        requests = [json.loads(item) for item in fake.sent if json.loads(item)["type"] == "state_request"]
        self.assertEqual(state.step, 5)
        self.assertEqual(len(requests), 1)

    def test_client_parses_state_and_keeps_both_times(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        result = client.receive(encode_frame(state_message()))
        state = result[0]
        self.assertEqual(state.sampled_at_utc, "2026-09-07T08:03:25.417Z")
        self.assertTrue(state.received_at_utc.endswith("Z"))
        self.assertNotEqual(state.sampled_at_utc, state.received_at_utc)

    def test_duplicate_or_old_sequence_is_ignored(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        client.receive(encode_frame(state_message(seq=10)))
        result = client.receive(encode_frame(state_message(step=6, seq=10)))
        self.assertEqual(result, [])
        self.assertTrue(client.needs_full_sync)
        self.assertEqual(client.latest_state.step, 5)

    def test_step_rollback_requests_resync(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        client.receive(encode_frame(state_message(step=5, seq=10)))
        client.receive(encode_frame(state_message(step=4, seq=11)))
        self.assertTrue(client.needs_full_sync)
        self.assertIsNone(client.latest_state)
        self.assertGreaterEqual(len(fake.sent), 2)

    def test_dispatch_waits_for_and_consumes_matching_ack(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        state = GridState(session_id="s1", step=5, sim_time_s=5.0, wind_speed_mps=8.0,
                          load_power_kw=60.0, wind_actual_kw=40.0, diesel_actual_kw=0.0,
                          wind_running=True, sampled_at_utc="2026-09-07T08:03:25.417Z")
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(state)
        dispatch_seq = client._next_seq
        fake.recv_queue.append(encode_frame(ack_message(dispatch_seq)))
        result = client.send_dispatch(decision)
        self.assertEqual(result, dispatch_seq)
        self.assertIsNone(client._pending_ack_seq)
        self.assertIsInstance(client._received_acks[dispatch_seq], Ack)

    def test_dispatch_contains_only_allowed_b_targets(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        state = GridState(session_id="s1", step=5, sim_time_s=5.0, wind_speed_mps=8.0,
                          load_power_kw=60.0, wind_actual_kw=40.0, diesel_actual_kw=0.0,
                          wind_running=True, sampled_at_utc="2026-09-07T08:03:25.417Z")
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(state)
        dispatch_seq = client._next_seq
        fake.recv_queue.append(encode_frame(ack_message(dispatch_seq)))
        client.send_dispatch(decision)
        message = json.loads(fake.sent[-1])
        self.assertEqual(set(message["payload"]), {"wind_target_kw", "diesel_target_kw", "wind_enable", "diesel_enable"})
        self.assertNotIn("pitch_target_deg", message["payload"])

    def test_receive_once_timeout_is_not_silently_converted_to_state(self):
        class TimeoutSocket(FakeSocket):
            def recv(self, size):
                raise socket.timeout()
        fake = TimeoutSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        with self.assertRaises(socket.timeout):
            client.receive_once()

    def test_receive_once_eof_closes_connection(self):
        fake = FakeSocket(recv_data=b"")
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        with self.assertRaises(ConnectionError):
            client.receive_once()
        self.assertFalse(client.connected)

    def test_client_rejects_zero_bind_address(self):
        with self.assertRaises(ValueError):
            EMSTcpClient("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
