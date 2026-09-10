"""B-owned TCP/JSON-line transport for the connection to A."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import select
import socket
import threading
import time
from typing import Any, Callable

from .models import GridState
from .operator_core import EMSDecision

MAX_FRAME_BYTES = 4096
PROTOCOL_VERSION = 1
B_STATE_POLL_PERIOD_S = 1.0
B_DISPATCH_ACK_TIMEOUT_S = 10.0
B_CONNECT_TIMEOUT_S = 5.0
B_STATE_RESPONSE_TIMEOUT_S = 6.0
_SEQUENCE_LOCK = threading.Lock()
_LAST_DEFAULT_SEQUENCE = -1


def _allocate_default_sequence(minimum: int = 0) -> int:
    global _LAST_DEFAULT_SEQUENCE
    epoch_ms = time.time_ns() // 1_000_000
    with _SEQUENCE_LOCK:
        sequence = max(epoch_ms, minimum, _LAST_DEFAULT_SEQUENCE + 1)
        _LAST_DEFAULT_SEQUENCE = sequence
        return sequence


class ProtocolError(ValueError):
    pass


class DispatchDeliveryUnknown(ConnectionError):
    def __init__(self, seq: int, message: str) -> None:
        super().__init__(message); self.seq = seq


def parse_endpoint(host_text: str, default_port: int) -> tuple[str,int]:
    text=host_text.strip()
    if not text: raise ValueError("A server host must not be empty")
    if "://" in text or "/" in text or "\\" in text: raise ValueError("enter a host or IP only; URL schemes and paths are not supported")
    host=text; port=default_port
    if text.startswith("["):
        closing=text.find("]")
        if closing<0: raise ValueError("invalid bracketed IPv6 endpoint")
        host=text[1:closing]; suffix=text[closing+1:]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit(): raise ValueError("invalid port in endpoint")
            port=int(suffix[1:])
    elif text.count(":")==1:
        candidate_host,candidate_port=text.rsplit(":",1)
        if not candidate_port.isdigit(): raise ValueError("invalid port in endpoint")
        host,port=candidate_host.strip(),int(candidate_port)
    if not host or host in {"0.0.0.0","::"}: raise ValueError("client host must be A's reachable address, not a wildcard address")
    if not 1<=port<=65535: raise ValueError("TCP port must be in [1,65535]")
    return host,port


def _configure_connected_socket(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_KEEPALIVE,1)
        sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    except (AttributeError,OSError): return
    try:
        sock.ioctl(socket.SIO_KEEPALIVE_VALS,(1,10_000,3_000)); return
    except (AttributeError,OSError): pass
    for option_name,value in (("TCP_KEEPIDLE",10),("TCP_KEEPINTVL",3),("TCP_KEEPCNT",3)):
        option=getattr(socket,option_name,None)
        if option is not None:
            try: sock.setsockopt(socket.IPPROTO_TCP,option,value)
            except OSError: pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")


def _reject_constant(value:str)->None: raise ProtocolError(f"non-finite JSON number: {value}")


def _validate_rfc3339_utc(value:object,field_name:str)->str:
    if not isinstance(value,str) or not value or "T" not in value or not value.endswith("Z"): raise ProtocolError(f"{field_name} must be a non-empty RFC 3339 UTC string")
    try: parsed=datetime.fromisoformat(value[:-1]+"+00:00")
    except ValueError as exc: raise ProtocolError(f"{field_name} must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset()!=timezone.utc.utcoffset(parsed): raise ProtocolError(f"{field_name} must use UTC")
    return value


def encode_frame(message:dict[str,Any])->bytes:
    try: raw=json.dumps(message,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")+b"\n"
    except (TypeError,ValueError) as exc: raise ProtocolError(f"invalid JSON message: {exc}") from exc
    if len(raw)>MAX_FRAME_BYTES: raise ProtocolError("frame exceeds 4096 bytes including LF")
    return raw


class JsonLineFramer:
    def __init__(self,max_frame_bytes:int=MAX_FRAME_BYTES):
        if max_frame_bytes<2: raise ValueError("max_frame_bytes must be at least 2")
        self.max_frame_bytes=max_frame_bytes; self._buffer=bytearray()
    def feed(self,data:bytes)->list[dict[str,Any]]:
        self._buffer.extend(data); messages=[]
        while b"\n" in self._buffer:
            line,_,rest=self._buffer.partition(b"\n"); self._buffer=bytearray(rest)
            if len(line)+1>self.max_frame_bytes: raise ProtocolError("frame exceeds 4096 bytes including LF")
            if not line: raise ProtocolError("empty frame")
            try: value=json.loads(line.decode("utf-8"),parse_constant=_reject_constant)
            except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise ProtocolError(f"invalid UTF-8/JSON frame: {exc}") from exc
            if not isinstance(value,dict): raise ProtocolError("top-level JSON value must be an object")
            messages.append(value)
        if len(self._buffer)>=self.max_frame_bytes: raise ProtocolError("incomplete frame reached maximum size")
        return messages


def validate_envelope(message:dict[str,Any],*,expected_type:str|None=None,expected_source:str|None=None,expected_target:str|None=None)->dict[str,Any]:
    required=("version","type","source","target","session_id","seq","step","sim_time_s","payload")
    missing=[k for k in required if k not in message]
    if missing: raise ProtocolError(f"missing envelope fields: {', '.join(missing)}")
    if not isinstance(message["version"],int) or isinstance(message["version"],bool) or message["version"]!=PROTOCOL_VERSION: raise ProtocolError("unsupported protocol version")
    if expected_type is not None and message["type"]!=expected_type: raise ProtocolError(f"unexpected message type: {message['type']}")
    if expected_source is not None and message["source"]!=expected_source: raise ProtocolError("unexpected message source")
    if expected_target is not None and message["target"]!=expected_target: raise ProtocolError("unexpected message target")
    if message["source"] not in {"A","B","C"} or message["target"] not in {"A","B","C"}: raise ProtocolError("invalid source/target")
    if not isinstance(message["session_id"],(str,type(None))) or (isinstance(message["session_id"],str) and not message["session_id"]): raise ProtocolError("session_id must be a non-empty string or null")
    for key in ("seq","step"):
        value=message[key]
        if not isinstance(value,int) or isinstance(value,bool) or value<0: raise ProtocolError(f"{key} must be a non-negative integer")
    sim_time=message["sim_time_s"]
    if not isinstance(sim_time,(int,float)) or isinstance(sim_time,bool) or not math.isfinite(sim_time) or sim_time<0: raise ProtocolError("sim_time_s must be a finite non-negative number")
    if not isinstance(message["payload"],dict): raise ProtocolError("payload must be an object")
    return message


@dataclass(frozen=True)
class Ack:
    ack_seq:int; accepted:bool; reason:str


class EMSTcpClient:
    def __init__(self,host:str,port:int=5000,*,timeout_s:float=2.0,connect_timeout_s:float=B_CONNECT_TIMEOUT_S,state_response_timeout_s:float=B_STATE_RESPONSE_TIMEOUT_S,socket_factory:Callable[...,socket.socket]=socket.create_connection,initial_seq:int|None=None,state_poll_period_s:float=B_STATE_POLL_PERIOD_S):
        host,port=parse_endpoint(host,port)
        if any(not math.isfinite(v) or v<=0 for v in (timeout_s,connect_timeout_s,state_response_timeout_s)): raise ValueError("invalid TCP endpoint or timeout")
        if initial_seq is not None and (not isinstance(initial_seq,int) or isinstance(initial_seq,bool) or initial_seq<0): raise ValueError("initial_seq must be a non-negative integer or None")
        if not math.isfinite(state_poll_period_s) or state_poll_period_s<=0: raise ValueError("state_poll_period_s must be a positive finite number")
        self.host,self.port,self.timeout_s=host,port,float(timeout_s); self.connect_timeout_s=float(connect_timeout_s); self.state_response_timeout_s=float(state_response_timeout_s); self.socket_factory=socket_factory; self.state_poll_period_s=float(state_poll_period_s)
        self.sock=None; self.framer=JsonLineFramer(); self._uses_default_sequence=initial_seq is None; self._next_seq=time.time_ns()//1_000_000 if initial_seq is None else initial_seq; self._last_incoming_seq=None; self._pending_state_request_seq=None; self._pending_ack_seq=None; self._pending_ack_started_at=None; self._received_acks={}; self._uncertain_dispatch_seq=None; self._last_state_request_monotonic=0.0; self._pending_state_request_started_at=None; self._received_state_on_connection=False; self.session_id=None; self.latest_state=None; self.needs_full_sync=True
    @property
    def connected(self)->bool:return self.sock is not None
    def connect(self)->None:
        self.close(); self.sock=self.socket_factory((self.host,self.port),self.connect_timeout_s); _configure_connected_socket(self.sock); self.sock.settimeout(self.timeout_s); self.framer=JsonLineFramer(); self._last_incoming_seq=None; self._pending_state_request_seq=None; self._pending_ack_seq=None; self._pending_ack_started_at=None; self._received_acks.clear(); self._uncertain_dispatch_seq=None; self._last_state_request_monotonic=0.0; self._pending_state_request_started_at=None; self._received_state_on_connection=False; self.needs_full_sync=True; self.request_state(full=True)
    def close(self)->None:
        if self.sock is not None:
            try:self.sock.close()
            finally:self.sock=None
        self._pending_state_request_seq=None; self._pending_state_request_started_at=None; self._pending_ack_seq=None; self._pending_ack_started_at=None; self._received_acks.clear()
    def _send(self,message:dict[str,Any])->None:
        if self.sock is None: raise ConnectionError("B is not connected to A")
        try:self.sock.sendall(encode_frame(message))
        except OSError:self.close(); raise
    def _envelope(self,msg_type:str,*,session_id:str|None,step:int,sim_time_s:float,payload:dict[str,Any])->dict[str,Any]:
        if self._uses_default_sequence: seq=_allocate_default_sequence(self._next_seq); self._next_seq=seq+1
        else: seq=self._next_seq; self._next_seq+=1
        return {"version":1,"type":msg_type,"source":"B","target":"A","session_id":session_id,"seq":seq,"step":step,"sim_time_s":sim_time_s,"payload":payload}
    def request_state(self,*,full:bool)->int:
        if self._pending_state_request_seq is not None:return self._pending_state_request_seq
        if self._pending_ack_seq is not None: raise ProtocolError("cannot request state while a dispatch ACK is pending")
        state=self.latest_state; msg=self._envelope("state_request",session_id=self.session_id,step=state.step if state else 0,sim_time_s=state.sim_time_s if state else 0.0,payload={"full":full}); self._send(msg); self._pending_state_request_seq=int(msg["seq"]); self._last_state_request_monotonic=time.monotonic(); self._pending_state_request_started_at=self._last_state_request_monotonic; return int(msg["seq"])
    def _check_state_response_deadline(self)->None:
        started=self._pending_state_request_started_at
        if started is None or time.monotonic()-started<=self.state_response_timeout_s:return
        seq=self._pending_state_request_seq; self.close(); raise TimeoutError(f"A did not answer state_request seq={seq} within {self.state_response_timeout_s:g}s")
    def _maybe_poll_state(self)->None:
        if self.sock is None or self._pending_state_request_seq is not None or self._pending_ack_seq is not None:return
        if time.monotonic()-self._last_state_request_monotonic>=self.state_poll_period_s:self.request_state(full=self.needs_full_sync)
    def poll_state(self)->GridState|None:
        self.request_state(full=self.needs_full_sync)
        while self.latest_state is None or self._pending_state_request_seq is not None:
            for result in self.receive_once():
                if isinstance(result,GridState):return result
        return self.latest_state
    def get_ack(self,seq:int)->Ack|None:return self._received_acks.get(seq)
    def send_dispatch_nowait(self,decision:EMSDecision)->int:
        """Send one dispatch and let the event-loop polling path collect its ACK."""
        state=decision.state
        if self._pending_state_request_seq is not None: raise ProtocolError("cannot dispatch while a state request is pending")
        if self._uncertain_dispatch_seq is not None: raise ProtocolError(f"dispatch result for seq {self._uncertain_dispatch_seq} is unknown; reconcile before sending another dispatch")
        if self.session_id is not None and state.session_id!=self.session_id: raise ProtocolError("dispatch state belongs to an old or different session")
        if self._pending_ack_seq is not None: raise ProtocolError("another dispatch ACK is still pending")
        msg=self._envelope("dispatch",session_id=state.session_id,step=state.step,sim_time_s=state.sim_time_s,payload={"wind_target_kw":decision.result.wind_target_kw,"diesel_target_kw":decision.result.diesel_target_kw,"wind_enable":decision.result.wind_enable,"diesel_enable":decision.result.diesel_enable}); seq=int(msg["seq"]); self._send(msg); self._pending_ack_seq=seq; self._pending_ack_started_at=time.monotonic(); return seq
    def _check_ack_response_deadline(self)->None:
        started=self._pending_ack_started_at
        if started is None or time.monotonic()-started<=B_DISPATCH_ACK_TIMEOUT_S:return
        seq=self._pending_ack_seq
        self.close()
        if seq is not None:self._uncertain_dispatch_seq=seq
        raise DispatchDeliveryUnknown(int(seq) if seq is not None else -1,f"dispatch seq {seq} sent but ACK is unknown after {B_DISPATCH_ACK_TIMEOUT_S:g}s")
    def send_dispatch(self,decision:EMSDecision)->int:
        """Compatibility API for worker/service callers that synchronously await ACK."""
        seq=self.send_dispatch_nowait(decision)
        try:
            if self.sock is not None:self.sock.settimeout(max(self.timeout_s,B_DISPATCH_ACK_TIMEOUT_S))
            while self._pending_ack_seq is not None:
                if any(isinstance(r,Ack) and r.ack_seq==seq for r in self.receive_once()):break
        except (socket.timeout,ConnectionError,OSError) as exc:
            self.close(); self._uncertain_dispatch_seq=seq; self._pending_ack_seq=None; raise DispatchDeliveryUnknown(seq,f"dispatch seq {seq} sent but ACK is unknown: {exc}") from exc
        except Exception:
            self._pending_ack_seq=None
            raise
        finally:
            if self.sock is not None:
                try:self.sock.settimeout(self.timeout_s)
                except OSError:pass
        return seq
    def receive_once(self)->list[GridState|Ack]:
        if self.sock is None: raise ConnectionError("B is not connected to A")
        self._maybe_poll_state(); self._check_state_response_deadline()
        try:data=self.sock.recv(4096)
        except socket.timeout:raise
        except OSError:self.close();raise
        if not data:
            received_state=self._received_state_on_connection; self.close()
            if not received_state: raise ConnectionError("TCP endpoint closed before A returned its first state; check that A TCP service is running and the tunnel backend port matches A")
            raise ConnectionError("A closed the TCP connection")
        return self.receive(data)
    def receive_available(self)->list[GridState|Ack]:
        if self.sock is None: raise ConnectionError("B is not connected to A")
        self._maybe_poll_state(); self._check_state_response_deadline(); self._check_ack_response_deadline()
        try:r,_,x=select.select([self.sock],[],[self.sock],0)
        except (OSError,ValueError):self.close();raise
        if x:self.close();raise ConnectionError("A TCP socket entered an exceptional state")
        return [] if not r else self.receive_once()
    def receive(self,data:bytes)->list[GridState|Ack]:
        results=[]
        for message in self.framer.feed(data):
            validate_envelope(message,expected_source="A",expected_target="B"); seq=int(message["seq"])
            if self._last_incoming_seq is not None and seq<=self._last_incoming_seq: self.needs_full_sync=True; continue
            self._last_incoming_seq=seq
            if message["type"]=="state":
                state=self._parse_state(message); self._received_state_on_connection=True
                if self.session_id is not None and state.session_id!=self.session_id:
                    self.latest_state=None; self.session_id=state.session_id; self._pending_state_request_seq=None; self._pending_state_request_started_at=None; self.needs_full_sync=True; self.request_state(full=True); continue
                if self.latest_state is not None and state.session_id==self.latest_state.session_id and state.step<self.latest_state.step:
                    self.latest_state=None; self._pending_state_request_seq=None; self._pending_state_request_started_at=None; self.needs_full_sync=True; self.request_state(full=True); continue
                self.session_id=state.session_id; self.latest_state=state; self.needs_full_sync=False; self._pending_state_request_seq=None; self._pending_state_request_started_at=None; results.append(state)
            elif message["type"]=="ack":
                payload=message["payload"]; ack_seq=payload.get("ack_seq")
                if not isinstance(ack_seq,int) or isinstance(ack_seq,bool) or ack_seq<0: raise ProtocolError("invalid ack_seq")
                if not isinstance(payload.get("accepted"),bool) or not isinstance(payload.get("reason"),str): raise ProtocolError("invalid ack payload")
                ack=Ack(ack_seq,payload["accepted"],payload["reason"]); self._received_acks[ack_seq]=ack
                if self._pending_ack_seq==ack_seq:self._pending_ack_seq=None; self._pending_ack_started_at=None
                results.append(ack)
            else: raise ProtocolError(f"unsupported A→B message type: {message['type']}")
        return results

    @staticmethod
    def _optional_bool(payload:dict[str,Any],key:str)->bool|None:
        if key not in payload:return None
        value=payload[key]
        if not isinstance(value,bool):raise ProtocolError(f"{key} must be JSON boolean")
        return value

    @staticmethod
    def _optional_number(payload:dict[str,Any],key:str,*,minimum:float|None=None,maximum:float|None=None,allow_null:bool=False)->float|None:
        if key not in payload:return None
        value=payload[key]
        if value is None and allow_null:return None
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ProtocolError(f"{key} must be a finite number")
        if minimum is not None and value<minimum:raise ProtocolError(f"{key} must be >= {minimum}")
        if maximum is not None and value>maximum:raise ProtocolError(f"{key} must be <= {maximum}")
        return float(value)

    def _parse_state(self,message:dict[str,Any])->GridState:
        payload=message["payload"]
        required=("sampled_at_utc","wind_speed_mps","wind_available_kw","wind_operating_limit_kw","load_power_kw","wind_actual_kw","diesel_actual_kw","wind_target_kw","diesel_target_kw","pitch_actual_deg","wind_running","diesel_running","fault","power_imbalance_kw")
        missing=[k for k in required if k not in payload]
        if missing:raise ProtocolError(f"missing state payload fields: {', '.join(missing)}")
        for key in ("wind_speed_mps","wind_available_kw","wind_operating_limit_kw","load_power_kw","wind_actual_kw","diesel_actual_kw","wind_target_kw","diesel_target_kw"):
            value=payload[key]
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:raise ProtocolError(f"invalid non-negative numeric field: {key}")
        if payload["wind_operating_limit_kw"]>payload["wind_available_kw"]:raise ProtocolError("wind_operating_limit_kw must be <= wind_available_kw")
        sampled_at_utc=_validate_rfc3339_utc(payload["sampled_at_utc"],"sampled_at_utc"); received_at_utc=utc_now(); sampled_dt=datetime.fromisoformat(sampled_at_utc[:-1]+"+00:00"); received_dt=datetime.fromisoformat(received_at_utc[:-1]+"+00:00"); received_age_s=max(0.0,(received_dt-sampled_dt).total_seconds())
        imbalance=payload["power_imbalance_kw"]
        if not isinstance(imbalance,(int,float)) or isinstance(imbalance,bool) or not math.isfinite(imbalance):raise ProtocolError("power_imbalance_kw must be a finite number")
        if not isinstance(payload["wind_running"],bool) or not isinstance(payload["diesel_running"],bool) or not isinstance(payload["fault"],bool):raise ProtocolError("wind_running, diesel_running and fault must be JSON booleans")
        pitch=payload["pitch_actual_deg"]
        if not isinstance(pitch,(int,float)) or isinstance(pitch,bool) or not math.isfinite(pitch) or not 0<=pitch<=90:raise ProtocolError("pitch_actual_deg must be a finite number in [0,90]")
        session_id=message["session_id"]
        if not isinstance(session_id,str) or not session_id:raise ProtocolError("state session_id must be a non-empty string")

        # The five extension keys are optional for backward compatibility.
        ext_keys=("controller_wind_enable","pitch_target_deg","last_wind_action_seq","last_wind_action_step","wind_action_applied_at_utc")
        controller=self._optional_bool(payload,"controller_wind_enable")
        pitch_target=self._optional_number(payload,"pitch_target_deg",minimum=0,maximum=90)
        seq_value=payload.get("last_wind_action_seq") if "last_wind_action_seq" in payload else None
        if "last_wind_action_seq" in payload and seq_value is not None and (not isinstance(seq_value,int) or isinstance(seq_value,bool) or seq_value<0):raise ProtocolError("last_wind_action_seq must be a non-negative integer or null")
        step_value=payload.get("last_wind_action_step") if "last_wind_action_step" in payload else None
        if "last_wind_action_step" in payload and step_value is not None and (not isinstance(step_value,int) or isinstance(step_value,bool) or step_value<0):raise ProtocolError("last_wind_action_step must be a non-negative integer or null")
        action_time=payload.get("wind_action_applied_at_utc") if "wind_action_applied_at_utc" in payload else None
        if action_time is not None:_validate_rfc3339_utc(action_time,"wind_action_applied_at_utc")
        if set(ext_keys).issubset(payload):
            if controller is None or pitch_target is None:raise ProtocolError("controller_wind_enable and pitch_target_deg cannot be null")
            extension_status="complete"
        else:
            extension_status="legacy_or_incomplete"
        parameters=None
        raw_params=payload.get("parameters")
        if raw_params is not None:
            if not isinstance(raw_params,dict):raise ProtocolError("parameters must be an object")
            parameters={}
            for key,value in raw_params.items():
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ProtocolError("parameter values must be finite numbers")
                parameters[str(key)]=float(value)
        return GridState(session_id=session_id,step=message["step"],sim_time_s=float(message["sim_time_s"]),wind_speed_mps=float(payload["wind_speed_mps"]),wind_available_kw=float(payload["wind_available_kw"]),wind_operating_limit_kw=float(payload["wind_operating_limit_kw"]),load_power_kw=float(payload["load_power_kw"]),wind_actual_kw=float(payload["wind_actual_kw"]),diesel_actual_kw=float(payload["diesel_actual_kw"]),wind_running=payload["wind_running"],fault=payload["fault"],sampled_at_utc=sampled_at_utc,received_at_utc=received_at_utc,received_age_s=received_age_s,wind_target_kw=float(payload["wind_target_kw"]),diesel_target_kw=float(payload["diesel_target_kw"]),pitch_actual_deg=float(pitch),diesel_running=payload["diesel_running"],power_imbalance_kw=float(imbalance),controller_wind_enable=controller,pitch_target_deg=pitch_target,last_wind_action_seq=seq_value,last_wind_action_step=step_value,wind_action_applied_at_utc=action_time,extension_status=extension_status,parameters=parameters)
