"""B-owned TCP/JSON-line transport for the connection to A."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import socket
from typing import Any, Callable

from .models import GridState
from .operator_core import EMSDecision

MAX_FRAME_BYTES = 4096
PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a TCP frame violates the common protocol."""


class DispatchDeliveryUnknown(ConnectionError):
    """Dispatch was sent but its ACK could not be established safely."""

    def __init__(self, seq: int, message: str) -> None:
        super().__init__(message)
        self.seq = seq


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-finite JSON number: {value}")


def _validate_rfc3339_utc(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or "T" not in value or not value.endswith("Z"):
        raise ProtocolError(f"{field_name} must be a non-empty RFC 3339 UTC string")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProtocolError(f"{field_name} must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ProtocolError(f"{field_name} must use UTC")
    return value


def encode_frame(message: dict[str, Any]) -> bytes:
    try:
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"invalid JSON message: {exc}") from exc
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds 4096 bytes including LF")
    return raw


class JsonLineFramer:
    """Handle TCP half-frames, multi-frames and frame-size limits."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if max_frame_bytes < 2:
            raise ValueError("max_frame_bytes must be at least 2")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            if len(line) + 1 > self.max_frame_bytes:
                raise ProtocolError("frame exceeds 4096 bytes including LF")
            if not line:
                raise ProtocolError("empty frame")
            try:
                value = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(f"invalid UTF-8/JSON frame: {exc}") from exc
            if not isinstance(value, dict):
                raise ProtocolError("top-level JSON value must be an object")
            messages.append(value)
        if len(self._buffer) >= self.max_frame_bytes:
            raise ProtocolError("incomplete frame reached maximum size")
        return messages


def validate_envelope(
    message: dict[str, Any],
    *,
    expected_type: str | None = None,
    expected_source: str | None = None,
    expected_target: str | None = None,
) -> dict[str, Any]:
    required = ("version", "type", "source", "target", "session_id", "seq", "step", "sim_time_s", "payload")
    missing = [key for key in required if key not in message]
    if missing:
        raise ProtocolError(f"missing envelope fields: {', '.join(missing)}")
    if not isinstance(message["version"], int) or isinstance(message["version"], bool) or message["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if expected_type is not None and message["type"] != expected_type:
        raise ProtocolError(f"unexpected message type: {message['type']}")
    if expected_source is not None and message["source"] != expected_source:
        raise ProtocolError("unexpected message source")
    if expected_target is not None and message["target"] != expected_target:
        raise ProtocolError("unexpected message target")
    if message["source"] not in {"A", "B", "C"} or message["target"] not in {"A", "B", "C"}:
        raise ProtocolError("invalid source/target")
    session_id = message["session_id"]
    if not isinstance(session_id, (str, type(None))):
        raise ProtocolError("session_id must be a string or null")
    if isinstance(session_id, str) and not session_id:
        raise ProtocolError("session_id must not be empty")
    for key in ("seq", "step"):
        value = message[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ProtocolError(f"{key} must be a non-negative integer")
    sim_time = message["sim_time_s"]
    if not isinstance(sim_time, (int, float)) or isinstance(sim_time, bool) or not math.isfinite(sim_time) or sim_time < 0:
        raise ProtocolError("sim_time_s must be a finite non-negative number")
    if not isinstance(message["payload"], dict):
        raise ProtocolError("payload must be an object")
    return message


@dataclass(frozen=True)
class Ack:
    ack_seq: int
    accepted: bool
    reason: str


class EMSTcpClient:
    """Blocking B client with framing, timeout, ACK and reconnect safeguards."""

    def __init__(
        self,
        host: str,
        port: int = 5000,
        *,
        timeout_s: float = 2.0,
        socket_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        if not host or host == "0.0.0.0":
            raise ValueError("client host must be A's reachable address, not 0.0.0.0")
        if not 1 <= port <= 65535 or timeout_s <= 0:
            raise ValueError("invalid TCP endpoint or timeout")
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.socket_factory = socket_factory
        self.sock: socket.socket | None = None
        self.framer = JsonLineFramer()
        self._next_seq = 0
        self._last_incoming_seq: int | None = None
        self._pending_state_request_seq: int | None = None
        self._pending_ack_seq: int | None = None
        self._received_acks: dict[int, Ack] = {}
        self._uncertain_dispatch_seq: int | None = None
        self.session_id: str | None = None
        self.latest_state: GridState | None = None
        self.needs_full_sync = True

    @property
    def connected(self) -> bool:
        return self.sock is not None

    def connect(self) -> None:
        self.close()
        self.sock = self.socket_factory((self.host, self.port), self.timeout_s)
        self.sock.settimeout(self.timeout_s)
        self.framer = JsonLineFramer()
        self._last_incoming_seq = None
        self._pending_state_request_seq = None
        self._pending_ack_seq = None
        self._received_acks.clear()
        self._uncertain_dispatch_seq = None
        self.needs_full_sync = True
        self.request_state(full=True)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
        self._pending_state_request_seq = None
        self._pending_ack_seq = None
        self._received_acks.clear()

    def _send(self, message: dict[str, Any]) -> None:
        if self.sock is None:
            raise ConnectionError("B is not connected to A")
        try:
            self.sock.sendall(encode_frame(message))
        except OSError:
            self.close()
            raise

    def _envelope(
        self,
        msg_type: str,
        *,
        session_id: str | None,
        step: int,
        sim_time_s: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        seq = self._next_seq
        self._next_seq += 1
        return {
            "version": PROTOCOL_VERSION,
            "type": msg_type,
            "source": "B",
            "target": "A",
            "session_id": session_id,
            "seq": seq,
            "step": step,
            "sim_time_s": sim_time_s,
            "payload": payload,
        }

    def request_state(self, *, full: bool) -> int:
        if self._pending_state_request_seq is not None:
            return self._pending_state_request_seq
        if self._pending_ack_seq is not None:
            raise ProtocolError("cannot request state while a dispatch ACK is pending")
        state = self.latest_state
        message = self._envelope(
            "state_request",
            session_id=self.session_id,
            step=state.step if state else 0,
            sim_time_s=state.sim_time_s if state else 0.0,
            payload={"full": full},
        )
        self._send(message)
        self._pending_state_request_seq = int(message["seq"])
        return int(message["seq"])

    def poll_state(self) -> GridState | None:
        """Keep one outstanding state request and consume frames until state arrives."""
        self.request_state(full=self.needs_full_sync)
        while self.latest_state is None or self._pending_state_request_seq is not None:
            results = self.receive_once()
            for result in results:
                if isinstance(result, GridState):
                    return result
        return self.latest_state

    def get_ack(self, seq: int) -> Ack | None:
        """Return a received ACK without inventing success from transport delivery."""
        return self._received_acks.get(seq)

    def send_dispatch(self, decision: EMSDecision) -> int:
        state = decision.state
        if self._pending_state_request_seq is not None:
            raise ProtocolError("cannot dispatch while a state request is pending")
        if self._uncertain_dispatch_seq is not None:
            raise ProtocolError(
                f"dispatch result for seq {self._uncertain_dispatch_seq} is unknown; reconcile before sending another dispatch"
            )
        if self.session_id is not None and state.session_id != self.session_id:
            raise ProtocolError("dispatch state belongs to an old or different session")
        if self._pending_ack_seq is not None:
            raise ProtocolError("another dispatch ACK is still pending")
        message = self._envelope(
            "dispatch",
            session_id=state.session_id,
            step=state.step,
            sim_time_s=state.sim_time_s,
            payload={
                "wind_target_kw": decision.result.wind_target_kw,
                "diesel_target_kw": decision.result.diesel_target_kw,
                "wind_enable": decision.result.wind_enable,
                "diesel_enable": decision.result.diesel_enable,
            },
        )
        seq = int(message["seq"])
        self._send(message)
        self._pending_ack_seq = seq
        try:
            while self._pending_ack_seq is not None:
                results = self.receive_once()
                if any(isinstance(result, Ack) and result.ack_seq == seq for result in results):
                    break
        except (socket.timeout, ConnectionError, OSError) as exc:
            self._uncertain_dispatch_seq = seq
            self._pending_ack_seq = None
            raise DispatchDeliveryUnknown(seq, f"dispatch seq {seq} sent but ACK is unknown: {exc}") from exc
        return seq

    def receive_once(self) -> list[GridState | Ack]:
        if self.sock is None:
            raise ConnectionError("B is not connected to A")
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            raise
        except OSError:
            self.close()
            raise
        if not data:
            self.close()
            raise ConnectionError("A closed the TCP connection")
        return self.receive(data)

    def receive(self, data: bytes) -> list[GridState | Ack]:
        results: list[GridState | Ack] = []
        for message in self.framer.feed(data):
            validate_envelope(message, expected_source="A", expected_target="B")
            seq = int(message["seq"])
            if self._last_incoming_seq is not None and seq <= self._last_incoming_seq:
                self.needs_full_sync = True
                continue
            self._last_incoming_seq = seq
            if message["type"] == "state":
                state = self._parse_state(message)
                if self.session_id is not None and state.session_id != self.session_id:
                    self.latest_state = None
                    self.session_id = state.session_id
                    self._pending_state_request_seq = None
                    self.needs_full_sync = True
                    self.request_state(full=True)
                    continue
                if self.latest_state is not None and state.session_id == self.latest_state.session_id and state.step < self.latest_state.step:
                    self.latest_state = None
                    self._pending_state_request_seq = None
                    self.needs_full_sync = True
                    self.request_state(full=True)
                    continue
                self.session_id = state.session_id
                self.latest_state = state
                self.needs_full_sync = False
                self._pending_state_request_seq = None
                results.append(state)
            elif message["type"] == "ack":
                payload = message["payload"]
                ack_seq = payload.get("ack_seq")
                if not isinstance(ack_seq, int) or isinstance(ack_seq, bool) or ack_seq < 0:
                    raise ProtocolError("invalid ack_seq")
                if not isinstance(payload.get("accepted"), bool) or not isinstance(payload.get("reason"), str):
                    raise ProtocolError("invalid ack payload")
                ack = Ack(ack_seq, payload["accepted"], payload["reason"])
                self._received_acks[ack_seq] = ack
                if self._pending_ack_seq == ack_seq:
                    self._pending_ack_seq = None
                results.append(ack)
            else:
                raise ProtocolError(f"unsupported A→B message type: {message['type']}")
        return results

    def _parse_state(self, message: dict[str, Any]) -> GridState:
        payload = message["payload"]
        required = (
            "sampled_at_utc", "wind_speed_mps", "wind_available_kw", "wind_operating_limit_kw",
            "load_power_kw", "wind_actual_kw", "diesel_actual_kw", "wind_target_kw",
            "pitch_actual_deg", "wind_running", "fault",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ProtocolError(f"missing state payload fields: {', '.join(missing)}")
        for key in (
            "wind_speed_mps", "wind_available_kw", "wind_operating_limit_kw", "load_power_kw",
            "wind_actual_kw", "diesel_actual_kw", "wind_target_kw",
        ):
            value = payload[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ProtocolError(f"invalid non-negative numeric field: {key}")
        if payload["wind_operating_limit_kw"] > payload["wind_available_kw"]:
            raise ProtocolError("wind_operating_limit_kw must be <= wind_available_kw")
        sampled_at_utc = _validate_rfc3339_utc(payload["sampled_at_utc"], "sampled_at_utc")
        if not isinstance(payload["wind_running"], bool) or not isinstance(payload["fault"], bool):
            raise ProtocolError("wind_running and fault must be JSON booleans")
        pitch = payload["pitch_actual_deg"]
        if not isinstance(pitch, (int, float)) or isinstance(pitch, bool) or not math.isfinite(pitch):
            raise ProtocolError("pitch_actual_deg must be a finite number")
        session_id = message["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise ProtocolError("state session_id must be a non-empty string")
        return GridState(
            session_id=session_id,
            step=message["step"],
            sim_time_s=float(message["sim_time_s"]),
            wind_speed_mps=float(payload["wind_speed_mps"]),
            wind_available_kw=float(payload["wind_available_kw"]),
            wind_operating_limit_kw=float(payload["wind_operating_limit_kw"]),
            load_power_kw=float(payload["load_power_kw"]),
            wind_actual_kw=float(payload["wind_actual_kw"]),
            diesel_actual_kw=float(payload["diesel_actual_kw"]),
            wind_running=payload["wind_running"],
            fault=payload["fault"],
            sampled_at_utc=sampled_at_utc,
            received_at_utc=utc_now(),
            wind_target_kw=float(payload["wind_target_kw"]),
            pitch_actual_deg=float(pitch),
        )
