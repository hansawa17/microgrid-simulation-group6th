"""TCP/JSON-line transport for B's connection to A."""
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
    """Raised when a TCP frame violates common/protocol.md."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-finite JSON number: {value}")


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


def validate_envelope(message: dict[str, Any], *, expected_type: str | None = None,
                      expected_source: str | None = None, expected_target: str | None = None) -> dict[str, Any]:
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
    if not isinstance(message["session_id"], (str, type(None))):
        raise ProtocolError("session_id must be a string or null for first request")
    if not isinstance(message["seq"], int) or isinstance(message["seq"], bool) or message["seq"] < 0:
        raise ProtocolError("seq must be a non-negative integer")
    if not isinstance(message["step"], int) or isinstance(message["step"], bool) or message["step"] < 0:
        raise ProtocolError("step must be a non-negative integer")
    if not isinstance(message["sim_time_s"], (int, float)) or isinstance(message["sim_time_s"], bool) or not math.isfinite(message["sim_time_s"]) or message["sim_time_s"] < 0:
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
    """Blocking B client with explicit framing, timeout and reconnect support."""
    def __init__(self, host: str, port: int = 5000, *, timeout_s: float = 2.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection) -> None:
        if not host or host == "0.0.0.0":
            raise ValueError("client host must be A's reachable address, not 0.0.0.0")
        if not 1 <= port <= 65535 or timeout_s <= 0:
            raise ValueError("invalid TCP endpoint or timeout")
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.socket_factory = socket_factory
        self.sock: socket.socket | None = None
        self.framer = JsonLineFramer()
        self._next_seq = 0
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
        self.needs_full_sync = True
        self.request_state(full=True)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, message: dict[str, Any]) -> None:
        if self.sock is None:
            raise ConnectionError("B is not connected to A")
        self.sock.sendall(encode_frame(message))

    def _envelope(self, msg_type: str, *, session_id: str | None, step: int,
                  sim_time_s: float, payload: dict[str, Any]) -> dict[str, Any]:
        seq = self._next_seq
        self._next_seq += 1
        return {"version": 1, "type": msg_type, "source": "B", "target": "A",
                "session_id": session_id, "seq": seq, "step": step,
                "sim_time_s": sim_time_s, "payload": payload}

    def request_state(self, *, full: bool) -> int:
        state = self.latest_state
        message = self._envelope("state_request", session_id=self.session_id,
                                 step=state.step if state else 0,
                                 sim_time_s=state.sim_time_s if state else 0.0,
                                 payload={"full": full})
        self._send(message)
        return int(message["seq"])

    def send_dispatch(self, decision: EMSDecision) -> int:
        state = decision.state
        message = self._envelope("dispatch", session_id=state.session_id, step=state.step,
                                 sim_time_s=state.sim_time_s,
                                 payload={"wind_target_kw": decision.result.wind_target_kw,
                                          "diesel_target_kw": decision.result.diesel_target_kw,
                                          "wind_enable": decision.result.wind_enable,
                                          "diesel_enable": decision.result.diesel_enable})
        self._send(message)
        return int(message["seq"])

    def receive(self, data: bytes) -> list[GridState | Ack]:
        results: list[GridState | Ack] = []
        for message in self.framer.feed(data):
            validate_envelope(message, expected_source="A", expected_target="B")
            if message["type"] == "state":
                state = self._parse_state(message)
                if self.session_id is not None and state.session_id != self.session_id:
                    self.latest_state = None
                    self.session_id = state.session_id
                    self.needs_full_sync = True
                    self.request_state(full=True)
                    continue
                if self.latest_state is not None and state.session_id == self.latest_state.session_id and state.step < self.latest_state.step:
                    self.latest_state = None
                    self.needs_full_sync = True
                    self.request_state(full=True)
                    continue
                self.session_id = state.session_id
                self.latest_state = state
                self.needs_full_sync = False
                results.append(state)
            elif message["type"] == "ack":
                payload = message["payload"]
                if not isinstance(payload.get("ack_seq"), int) or not isinstance(payload.get("accepted"), bool) or not isinstance(payload.get("reason"), str):
                    raise ProtocolError("invalid ack payload")
                results.append(Ack(payload["ack_seq"], payload["accepted"], payload["reason"]))
            else:
                raise ProtocolError(f"unsupported A→B message type: {message['type']}")
        return results

    def _parse_state(self, message: dict[str, Any]) -> GridState:
        payload = message["payload"]
        required = ("sampled_at_utc", "wind_speed_mps", "load_power_kw", "wind_actual_kw",
                    "diesel_actual_kw", "wind_target_kw", "pitch_actual_deg", "wind_running", "fault")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ProtocolError(f"missing state payload fields: {', '.join(missing)}")
        for key in ("wind_speed_mps", "load_power_kw", "wind_actual_kw", "diesel_actual_kw", "wind_target_kw"):
            value = payload[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ProtocolError(f"invalid non-negative numeric field: {key}")
        if not isinstance(payload["sampled_at_utc"], str) or not payload["sampled_at_utc"]:
            raise ProtocolError("sampled_at_utc must be a non-empty RFC 3339 string")
        if not isinstance(payload["wind_running"], bool) or not isinstance(payload["fault"], bool):
            raise ProtocolError("wind_running and fault must be JSON booleans")
        pitch = payload["pitch_actual_deg"]
        if pitch is not None and (not isinstance(pitch, (int, float)) or isinstance(pitch, bool) or not math.isfinite(pitch)):
            raise ProtocolError("pitch_actual_deg must be finite or null")
        return GridState(session_id=message["session_id"], step=message["step"],
                         sim_time_s=float(message["sim_time_s"]),
                         wind_speed_mps=float(payload["wind_speed_mps"]), load_power_kw=float(payload["load_power_kw"]),
                         wind_actual_kw=float(payload["wind_actual_kw"]), diesel_actual_kw=float(payload["diesel_actual_kw"]),
                         wind_running=payload["wind_running"], fault=payload["fault"],
                         sampled_at_utc=payload["sampled_at_utc"], received_at_utc=utc_now(),
                         wind_target_kw=float(payload["wind_target_kw"]), pitch_actual_deg=pitch)
