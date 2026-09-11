"""Threaded TCP server for the draft LF-delimited JSON protocol."""

from __future__ import annotations

import json
import math
import socket
import socketserver
import threading

from .repository import Repository


class ProtocolError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-finite JSON number is not allowed: {value}")


def decode_frame(frame: bytes, max_frame_bytes: int) -> dict[str, object]:
    if len(frame) > max_frame_bytes:
        raise ProtocolError("frame_too_large")
    if not frame.endswith(b"\n"):
        raise ProtocolError("incomplete_frame")
    try:
        text = frame[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8") from exc
    try:
        message = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ProtocolError) as exc:
        raise ProtocolError("invalid_json") from exc
    if not isinstance(message, dict):
        raise ProtocolError("frame_root_must_be_object")
    validate_envelope(message)
    return message


def encode_frame(message: dict[str, object], max_frame_bytes: int) -> bytes:
    try:
        frame = (json.dumps(
            message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("response_not_serializable") from exc
    if len(frame) > max_frame_bytes:
        raise ProtocolError("response_frame_too_large")
    return frame


def validate_envelope(message: dict[str, object]) -> None:
    required = {"version", "type", "source", "target", "session_id", "seq", "step", "sim_time_s", "payload"}
    if not required.issubset(message):
        raise ProtocolError("missing_envelope_field")
    if (
        isinstance(message["version"], bool)
        or not isinstance(message["version"], int)
        or message["version"] != 1
    ):
        raise ProtocolError("unsupported_version")
    if not isinstance(message["type"], str):
        raise ProtocolError("invalid_type")
    if message["source"] not in {"B", "C"} or message["target"] != "A":
        raise ProtocolError("invalid_direction")
    if isinstance(message["seq"], bool) or not isinstance(message["seq"], int) or message["seq"] < 0:
        raise ProtocolError("invalid_seq")
    if isinstance(message["step"], bool) or not isinstance(message["step"], int) or message["step"] < 0:
        raise ProtocolError("invalid_step")
    sim_time = message["sim_time_s"]
    if isinstance(sim_time, bool) or not isinstance(sim_time, (int, float)):
        raise ProtocolError("invalid_sim_time_s")
    if not math.isfinite(float(sim_time)) or sim_time < 0:
        raise ProtocolError("invalid_sim_time_s")
    if not isinstance(message["payload"], dict):
        raise ProtocolError("invalid_payload")
    session_id = message["session_id"]
    if session_id is not None and not isinstance(session_id, str):
        raise ProtocolError("invalid_session_id")
    if session_id == "":
        raise ProtocolError("invalid_session_id")


class MessageProcessor:
    def __init__(self, repository: Repository):
        self.repository = repository

    def process(self, message: dict[str, object]) -> dict[str, object]:
        validate_envelope(message)
        message_type = message["type"]
        source = str(message["source"])
        if message_type == "state_request":
            payload = message["payload"]
            assert isinstance(payload, dict)
            if set(payload) != {"full"} or type(payload["full"]) is not bool:
                raise ProtocolError("invalid_full")
            state = self.repository.get_state()
            state_payload = state.protocol_payload()
            state_payload["next_command_seq"] = self.repository.next_client_command_seq(
                source, state.session_id
            )
            if source == "B":
                state_payload["parameters"] = {
                    str(row["name"]): float(row["value"])
                    for row in self.repository.parameter_snapshot()
                }
            return {
                "version": 1,
                "type": "state",
                "source": "A",
                "target": source,
                "session_id": state.session_id,
                "seq": self.repository.next_server_seq(),
                "step": state.step,
                "sim_time_s": state.sim_time_s,
                "payload": state_payload,
            }
        if message_type not in {"dispatch", "wind_action", "parameter_update"}:
            raise ProtocolError("unsupported_message_type")
        result = self.repository.apply_command(message)
        state = self.repository.get_state()
        reason = f"duplicate_{result.reason}" if result.duplicate else result.reason
        return {
            "version": 1,
            "type": "ack",
            "source": "A",
            "target": source,
            "session_id": state.session_id,
            "seq": self.repository.next_server_seq(),
            "step": state.step,
            "sim_time_s": state.sim_time_s,
            "payload": {
                "ack_seq": message["seq"],
                "accepted": result.accepted,
                "reason": reason,
            },
        }

    def error_ack(self, message: object, reason: str) -> dict[str, object] | None:
        if not isinstance(message, dict) or message.get("source") not in {"B", "C"}:
            return None
        state = self.repository.get_state()
        seq = message.get("seq")
        ack_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else -1
        return {
            "version": 1,
            "type": "ack",
            "source": "A",
            "target": message["source"],
            "session_id": state.session_id,
            "seq": self.repository.next_server_seq(),
            "step": state.step,
            "sim_time_s": state.sim_time_s,
            "payload": {"ack_seq": ack_seq, "accepted": False, "reason": reason},
        }


class _RequestHandler(socketserver.StreamRequestHandler):
    server: "SimulatorTCPServer"

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10_000, 3_000))
        except (AttributeError, OSError):
            pass
        self.connection.settimeout(self.server.idle_timeout_s)

    def handle(self) -> None:
        peer: str | None = None
        disconnect_event = "peer_connection_closed"
        disconnect_detail = "TCP connection closed"
        try:
            while True:
                try:
                    frame = self.rfile.readline(self.server.max_frame_bytes + 1)
                except TimeoutError:
                    disconnect_event = "peer_timeout"
                    disconnect_detail = "idle TCP timeout"
                    break
                except (ConnectionError, OSError) as exc:
                    disconnect_event = "peer_connection_error"
                    disconnect_detail = f"TCP receive failed: {type(exc).__name__}"
                    break
                if not frame:
                    disconnect_event = "peer_connection_closed"
                    disconnect_detail = "peer closed the TCP connection"
                    break
                if len(frame) > self.server.max_frame_bytes:
                    if not frame.endswith(b"\n"):
                        while True:
                            remainder = self.rfile.readline(self.server.max_frame_bytes + 1)
                            if not remainder or remainder.endswith(b"\n"):
                                break
                    self.server.repository.log("WARNING", "frame_rejected", "frame_too_large")
                    continue
                decoded: object = None
                try:
                    decoded = decode_frame(frame, self.server.max_frame_bytes)
                    message_peer = str(decoded["source"])
                    if peer is None:
                        peer = message_peer
                        self.server.register_peer(peer)
                    elif message_peer != peer:
                        raise ProtocolError("source_changed_on_connection")
                    self.server.touch_peer(peer)
                    response = self.server.processor.process(decoded)
                except ProtocolError as exc:
                    self.server.repository.log("WARNING", "frame_rejected", str(exc))
                    response = self.server.processor.error_ack(decoded, str(exc))
                    if response is None:
                        continue
                try:
                    self.wfile.write(encode_frame(response, self.server.max_frame_bytes))
                    self.wfile.flush()
                except (ConnectionError, OSError) as exc:
                    disconnect_event = "peer_connection_error"
                    disconnect_detail = f"TCP send failed: {type(exc).__name__}"
                    break
        finally:
            if peer is not None:
                self.server.unregister_peer(
                    peer, event=disconnect_event, detail=disconnect_detail
                )


class SimulatorTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        address: tuple[str, int],
        repository: Repository,
        max_frame_bytes: int,
        idle_timeout_s: float = 30.0,
    ):
        if not math.isfinite(idle_timeout_s) or idle_timeout_s <= 0:
            raise ValueError("idle_timeout_s must be a positive finite number")
        self.repository = repository
        self.processor = MessageProcessor(repository)
        self.max_frame_bytes = max_frame_bytes
        self.idle_timeout_s = float(idle_timeout_s)
        self._peer_lock = threading.Lock()
        self._peer_counts = {"B": 0, "C": 0}
        super().__init__(address, _RequestHandler)
        try:
            self.repository.reset_connections("TCP server started; waiting for peer")
        except Exception:
            super().server_close()
            raise

    def server_close(self) -> None:
        try:
            self.repository.reset_connections("TCP server stopped")
        finally:
            super().server_close()

    def register_peer(self, peer: str) -> None:
        with self._peer_lock:
            self._peer_counts[peer] += 1
            count = self._peer_counts[peer]
            self.repository.mark_connection(peer, True, f"{count} active connection(s)")

    def touch_peer(self, peer: str) -> None:
        with self._peer_lock:
            count = self._peer_counts[peer]
            self.repository.mark_connection(peer, count > 0, f"{count} active connection(s)")

    def unregister_peer(
        self,
        peer: str,
        *,
        event: str = "peer_connection_closed",
        detail: str = "connection closed",
    ) -> None:
        with self._peer_lock:
            self._peer_counts[peer] = max(0, self._peer_counts[peer] - 1)
            count = self._peer_counts[peer]
            status_detail = f"{count} active connection(s)" if count else detail
            self.repository.mark_connection(peer, count > 0, status_detail)
            if count == 0:
                self.repository.log("WARNING", event, f"{peer}: {detail}")


def serve(repository: Repository, bind: str, port: int, max_frame_bytes: int) -> None:
    with SimulatorTCPServer((bind, port), repository, max_frame_bytes) as server:
        actual_host, actual_port = server.server_address
        print(f"A simulator TCP server listening on {actual_host}:{actual_port}", flush=True)
        server.serve_forever(poll_interval=0.2)
