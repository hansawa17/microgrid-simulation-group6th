"""Tests for B-owned TCP/JSON-line transport."""

import json
import socket
import unittest

from B_dispatch.models import DispatchConfig, GridState
from B_dispatch.operator_core import EMSCore
from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, JsonLineFramer, ProtocolError, encode_frame


class FakeSocket:
    def __init__(self, recv_data=None):
        self.sent = []
        self.timeout = None
        self.closed = False
        self.recv_queue = [recv_data] if isinstance(recv_data, bytes) else list(recv_data or [])

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


def state_message(step=5, session="s1", seq=10, **overrides):
    payload = {
        "sampled_at_utc": "2026-09-07T08:03:25.417Z", "wind_speed_mps": 8.2,
        "wind_available_kw": 70.0, "wind_operating_limit_kw": 60.0,
        "load_power_kw": 76.0, "wind_actual_kw": 0.0, "diesel_actual_kw": 34.0,
        "wind_target_kw": 45.0, "pitch_actual_deg": 0.0, "wind_running": True, "fault": False,
    }
    payload.update(overrides)
    return {
        "version": 1, "type": "state", "source": "A", "target": "B",
        "session_id": session, "seq": seq, "step": step, "sim_time_s": float(step),
        "payload": payload,
    }


def ack_message(ack_seq, seq=20, accepted=True, reason="accepted"):
    return {
        "version": 1, "type": "ack", "source": "A", "target": "B",
        "session_id": "s1", "seq": seq, "step": 5, "sim_time_s": 5.0,
        "payload": {"ack_seq": ack_seq, "accepted": accepted, "reason": reason},
    }


def ready_state():
    return GridState(
        session_id="s1", step=5, sim_time_s=5.0, wind_speed_mps=8.0,
        wind_available_kw=70.0, wind_operating_limit_kw=60.0,
        load_power_kw=60.0, wind_actual_kw=0.0, diesel_actual_kw=0.0,
        wind_running=True, sampled_at_utc="2026-09-07T08:03:25.417Z",
    )


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

    def test_default_sequence_does_not_go_backwards_across_new_clients(self):
        first_socket = FakeSocket()
        first = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: first_socket)
        first.connect()
        first_seq = json.loads(first_socket.sent[0])["seq"]

        second_socket = FakeSocket()
        second = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: second_socket)
        second.connect()
        second_seq = json.loads(second_socket.sent[0])["seq"]

        self.assertGreater(second_seq, first_seq)

    def test_initial_sequence_can_be_injected_for_deterministic_tests(self):
        fake = FakeSocket()
        client = EMSTcpClient(
            "192.168.1.20", socket_factory=lambda *args: fake, initial_seq=41,
        )
        client.connect()
        self.assertEqual(json.loads(fake.sent[0])["seq"], 41)
        self.assertEqual(client._next_seq, 42)

    def test_poll_state_does_not_send_second_request_while_first_is_pending(self):
        fake = FakeSocket([encode_frame(state_message())])
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        state = client.poll_state()
        requests = [json.loads(item) for item in fake.sent if json.loads(item)["type"] == "state_request"]
        self.assertEqual(state.step, 5)
        self.assertEqual(len(requests), 1)

    def test_client_parses_all_current_state_fields_and_keeps_both_times(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        result = client.receive(encode_frame(state_message()))
        state = result[0]
        self.assertEqual(state.wind_available_kw, 70.0)
        self.assertEqual(state.wind_operating_limit_kw, 60.0)
        self.assertEqual(state.wind_actual_kw, 0.0)
        self.assertEqual(state.sampled_at_utc, "2026-09-07T08:03:25.417Z")
        self.assertTrue(state.received_at_utc.endswith("Z"))
        self.assertNotEqual(state.sampled_at_utc, state.received_at_utc)

    def test_state_rejects_operating_limit_above_available(self):
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: FakeSocket())
        client.connect()
        with self.assertRaises(ProtocolError):
            client.receive(encode_frame(state_message(wind_operating_limit_kw=71.0)))

    def test_state_rejects_invalid_timestamp(self):
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: FakeSocket())
        client.connect()
        with self.assertRaises(ProtocolError):
            client.receive(encode_frame(state_message(sampled_at_utc="not-a-timestamp")))

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
        client.receive(encode_frame(state_message()))
        state = ready_state()
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(state)
        dispatch_seq = client._next_seq
        fake.recv_queue.append(encode_frame(ack_message(dispatch_seq)))
        result = client.send_dispatch(decision)
        self.assertEqual(result, dispatch_seq)
        self.assertIsNone(client._pending_ack_seq)
        self.assertEqual(client.get_ack(dispatch_seq), Ack(dispatch_seq, True, "accepted"))

    def test_rejected_ack_is_retained_as_rejection(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        client.receive(encode_frame(state_message()))
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(ready_state())
        dispatch_seq = client._next_seq
        fake.recv_queue.append(encode_frame(ack_message(dispatch_seq, accepted=False, reason="stale command")))
        client.send_dispatch(decision)
        self.assertEqual(client.get_ack(dispatch_seq), Ack(dispatch_seq, False, "stale command"))

    def test_dispatch_contains_only_allowed_b_targets(self):
        fake = FakeSocket()
        client = EMSTcpClient("192.168.1.20", socket_factory=lambda *args: fake)
        client.connect()
        client.receive(encode_frame(state_message()))
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(ready_state())
        dispatch_seq = client._next_seq
        fake.recv_queue.append(encode_frame(ack_message(dispatch_seq)))
        client.send_dispatch(decision)
        message = json.loads(fake.sent[-1])
        self.assertEqual(set(message["payload"]), {"wind_target_kw", "diesel_target_kw", "wind_enable", "diesel_enable"})
        self.assertNotIn("pitch_target_deg", message["payload"])

    def test_dispatch_timeout_marks_delivery_unknown(self):
        fake = FakeSocket()
        client = EMSTcpClient(
            "192.168.1.20", socket_factory=lambda *args: fake, timeout_s=0.1, initial_seq=0,
        )
        client.connect()
        client.receive(encode_frame(state_message()))
        decision = EMSCore(DispatchConfig(wind_max_kw=100.0, diesel_max_kw=100.0)).decide(ready_state())
        with self.assertRaises(DispatchDeliveryUnknown) as ctx:
            client.send_dispatch(decision)
        self.assertEqual(ctx.exception.seq, 1)
        self.assertFalse(client.connected)
        self.assertEqual(client._uncertain_dispatch_seq, 1)

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

    def test_client_rejects_invalid_initial_sequence(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EMSTcpClient("127.0.0.1", initial_seq=value)


if __name__ == "__main__":
    unittest.main()
