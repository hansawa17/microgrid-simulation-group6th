"""TCP-only communication process for B.

The process owns the A socket, stores received state in ``ems.db``, and sends
only rows claimed from ``dispatch_outbox``.  It never calculates targets.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

from .models import DispatchResult
from .operator_core import EMSDecision
from .repository import EMSRepository, utc_now
from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError

COMMAND_CHECK_PERIOD_S = 1.0


class EMSCommunicationService:
    """Exchange A/B protocol frames while using SQLite for all local handoff."""

    def __init__(
        self,
        repository: EMSRepository,
        *,
        client_factory: Callable[..., EMSTcpClient] = EMSTcpClient,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repository = repository
        self.client_factory = client_factory
        self.clock = clock
        self.sleeper = sleeper
        self.client: Optional[EMSTcpClient] = None
        self._endpoint: Optional[tuple[str, int]] = None
        self._next_connect_at = 0.0
        self._reconnect_attempt = 0
        self._last_command_check_at: Optional[float] = None

    def initialize(self) -> None:
        self.repository.initialize()
        abandoned = self.repository.recover_abandoned_outbox()
        if abandoned:
            self.repository.record_log(
                "ERROR", "outbox_recovery",
                f"marked {abandoned} in-flight command(s) delivery_unknown",
            )
        self.repository.heartbeat(
            "B_IO", pid=os.getpid(), state="STARTING", detail="TCP owner"
        )

    def _disconnect(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None

    def _schedule_reconnect(self, error: Exception) -> None:
        self._disconnect()
        delay = min(15.0, float(2 ** min(self._reconnect_attempt, 4)))
        self._reconnect_attempt += 1
        self._next_connect_at = self.clock() + delay
        self.repository.record_log("WARNING", "tcp_disconnected", str(error))
        self.repository.heartbeat(
            "B_IO", pid=os.getpid(), state="OFFLINE",
            detail=f"retry in {delay:g}s: {error}",
        )

    def _ensure_connected(self) -> bool:
        config = self.repository.get_communication_config()
        endpoint = (str(config["host"]), int(config["port"]))
        if not bool(config["enabled"]):
            self._disconnect()
            self.repository.heartbeat(
                "B_IO", pid=os.getpid(), state="DISABLED", detail="communication disabled"
            )
            return False
        if self._endpoint != endpoint:
            self._disconnect()
            self._endpoint = endpoint
            self._next_connect_at = 0.0
            self._reconnect_attempt = 0
        if self.client is not None and self.client.connected:
            return True
        if self.clock() < self._next_connect_at:
            return False

        runtime = self.repository.get_runtime_config()
        timeout = runtime["command_timeout_s"]
        timeout_s = 3.0 if timeout is None else float(timeout)
        try:
            self.client = self.client_factory(
                endpoint[0], endpoint[1], timeout_s=timeout_s,
                state_poll_period_s=float(runtime["poll_period_s"]),
            )
            self.client.connect()
        except Exception as error:
            self._schedule_reconnect(error)
            return False
        self._reconnect_attempt = 0
        self._next_connect_at = 0.0
        self.repository.record_log(
            "INFO", "tcp_connected", f"connected to A at {endpoint[0]}:{endpoint[1]}"
        )
        self.repository.heartbeat(
            "B_IO", pid=os.getpid(), state="ONLINE", detail=f"{endpoint[0]}:{endpoint[1]}"
        )
        return True

    @staticmethod
    def _decision_from_row(state, row) -> EMSDecision:
        result = DispatchResult(
            wind_target_kw=float(row["wind_target_kw"]),
            diesel_target_kw=float(row["diesel_target_kw"]),
            wind_enable=bool(row["wind_enable"]),
            diesel_enable=bool(row["diesel_enable"]),
            target_unserved_kw=float(row["target_unserved_kw"]),
            target_surplus_kw=float(row["target_surplus_kw"]),
            reason=str(row["reason"]),
        )
        return EMSDecision(state=state, result=result)

    def _record_network_result(
        self,
        row,
        *,
        seq: int,
        status: str,
        ack: Optional[Ack],
        reason: str,
    ) -> int:
        command_id = self.repository.record_command(
            {
                "session_id": row["session_id"],
                "step": row["state_step"],
                "sim_time_s": row["sim_time_s"],
                "source": "B",
                "seq": seq,
                "wind_target_kw": row["wind_target_kw"],
                "diesel_target_kw": row["diesel_target_kw"],
                "wind_enable": bool(row["wind_enable"]),
                "diesel_enable": bool(row["diesel_enable"]),
                "status": status,
                "reason": reason,
                "ack_accepted": None if ack is None else ack.accepted,
                "ack_reason": None if ack is None else ack.reason,
                "ack_received_at_utc": None if ack is None else utc_now(),
            }
        )
        self.repository.record_evaluation(
            command_id=command_id,
            target_unserved_kw=float(row["target_unserved_kw"]),
            target_surplus_kw=float(row["target_surplus_kw"]),
        )
        return command_id

    def _send_next_outbox(self) -> Optional[int]:
        client = self.client
        if client is None or not client.connected:
            return None
        if client._pending_state_request_seq is not None or client._pending_ack_seq is not None:
            return None

        row = self.repository.claim_next_outbox()
        if row is None:
            return None
        outbox_id = int(row["id"])
        state = self.repository.get_grid_state(str(row["session_id"]), int(row["state_step"]))
        runtime = self.repository.get_runtime_config()
        current = client.latest_state
        if state is None:
            self.repository.finish_outbox(
                outbox_id, status="cancelled", detail="referenced state is missing"
            )
            return outbox_id
        if (
            current is None
            or current.session_id != state.session_id
            or current.step != state.step
        ):
            self.repository.finish_outbox(
                outbox_id, status="cancelled",
                detail="A state advanced or session changed before send",
            )
            return outbox_id
        if state.received_age_s > float(runtime["max_state_age_s"]):
            self.repository.finish_outbox(
                outbox_id, status="cancelled",
                detail=f"state became stale before send: {state.received_age_s:.3f}s",
            )
            return outbox_id

        decision = self._decision_from_row(state, row)
        try:
            seq = client.send_dispatch(decision)
            ack = client.get_ack(seq)
            if ack is None:
                raise DispatchDeliveryUnknown(
                    seq, f"dispatch seq {seq} returned without a recorded ACK"
                )
            status = "accepted" if ack.accepted else "rejected"
            command_id = self._record_network_result(
                row, seq=seq, status=status, ack=ack, reason=decision.result.reason
            )
            self.repository.finish_outbox(
                outbox_id, status=status, protocol_seq=seq, command_id=command_id,
                ack_accepted=ack.accepted, ack_reason=ack.reason,
            )
            self.repository.record_log(
                "INFO" if ack.accepted else "WARNING", "dispatch_result",
                f"outbox={outbox_id} seq={seq} accepted={ack.accepted} reason={ack.reason}",
                session_id=state.session_id, step=state.step,
            )
        except DispatchDeliveryUnknown as error:
            command_id = self._record_network_result(
                row, seq=error.seq, status="delivery_unknown", ack=None, reason=str(error)
            )
            self.repository.finish_outbox(
                outbox_id, status="delivery_unknown", protocol_seq=error.seq,
                command_id=command_id, detail=str(error),
            )
            self._schedule_reconnect(error)
        except (ProtocolError, ValueError, RuntimeError) as error:
            self.repository.finish_outbox(
                outbox_id, status="local_error", detail=str(error)
            )
            self.repository.record_log(
                "ERROR", "dispatch_local_error", str(error),
                session_id=state.session_id, step=state.step,
            )
        return outbox_id

    def run_cycle(self) -> Optional[int]:
        if not self._ensure_connected():
            return None
        assert self.client is not None
        try:
            for message in self.client.receive_available():
                if not isinstance(message, Ack):
                    self.repository.save_state(message)
            endpoint = self._endpoint or ("?", 0)
            self.repository.heartbeat(
                "B_IO", pid=os.getpid(), state="ONLINE",
                detail=f"{endpoint[0]}:{endpoint[1]}",
            )
            now = self.clock()
            if (
                self._last_command_check_at is None
                or now - self._last_command_check_at >= COMMAND_CHECK_PERIOD_S
            ):
                self._last_command_check_at = now
                return self._send_next_outbox()
            return None
        except Exception as error:
            self._schedule_reconnect(error)
            return None

    def run(self, *, max_cycles: Optional[int] = None, idle_sleep_s: float = 0.1) -> None:
        self.initialize()
        cycles = 0
        try:
            while max_cycles is None or cycles < max_cycles:
                self.run_cycle()
                cycles += 1
                self.sleeper(idle_sleep_s)
        finally:
            self._disconnect()
            self.repository.heartbeat(
                "B_IO", pid=os.getpid(), state="STOPPED", detail="process stopped"
            )
