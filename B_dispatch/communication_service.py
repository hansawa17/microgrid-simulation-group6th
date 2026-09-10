"""TCP-only communication process for B.

B_IO owns the A socket, persists state/ACKs, and is deliberately independent
from the EMS calculation process. Freshness is measured from the time B
received the TCP state, while sampled_at_utc remains the source timestamp for
traceability. A delayed source timestamp must not make an otherwise newly
received TCP state appear 50+ seconds old to the dispatch gate.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Callable, Optional

from .models import DispatchResult, GridState
from .operator_core import EMSDecision
from .repository import EMSRepository, utc_now
from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError
from .wind_execution import evaluate_pair, find_feedback

COMMAND_CHECK_PERIOD_S = 0.2


class EMSCommunicationService:
    def __init__(self, repository: EMSRepository, *, client_factory: Callable[..., EMSTcpClient] = EMSTcpClient, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep):
        self.repository = repository
        self.client_factory = client_factory
        self.clock = clock
        self.sleeper = sleeper
        self.client: Optional[EMSTcpClient] = None
        self._endpoint = None
        self._next_connect_at = 0.0
        self._reconnect_attempt = 0
        self._last_command_check_at = None

    def initialize(self):
        self.repository.initialize()
        abandoned = self.repository.recover_abandoned_outbox()
        if abandoned:
            self.repository.record_log("ERROR", "outbox_recovery", f"marked {abandoned} in-flight command(s) delivery_unknown")
        self.repository.heartbeat("B_IO", pid=os.getpid(), state="STARTING", detail="TCP owner")

    def _disconnect(self):
        if self.client is not None:
            self.client.close()
        self.client = None

    def _schedule_reconnect(self, error):
        self._disconnect()
        delay = min(15.0, float(2 ** min(self._reconnect_attempt, 4)))
        self._reconnect_attempt += 1
        self._next_connect_at = self.clock() + delay
        self.repository.record_log("WARNING", "tcp_disconnected", str(error))
        self.repository.heartbeat("B_IO", pid=os.getpid(), state="OFFLINE", detail=f"retry in {delay:g}s: {error}")

    def _ensure_connected(self):
        config = self.repository.get_communication_config()
        endpoint = (str(config["host"]), int(config["port"]))
        if not bool(config["enabled"]):
            self._disconnect()
            self.repository.heartbeat("B_IO", pid=os.getpid(), state="DISABLED", detail="communication disabled")
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
            self.client = self.client_factory(endpoint[0], endpoint[1], timeout_s=timeout_s, state_poll_period_s=float(runtime["poll_period_s"]))
            self.client.connect()
        except Exception as error:
            self._schedule_reconnect(error)
            return False
        self._reconnect_attempt = 0
        self._next_connect_at = 0.0
        self.repository.record_log("INFO", "tcp_connected", f"connected to A at {endpoint[0]}:{endpoint[1]}")
        self.repository.heartbeat("B_IO", pid=os.getpid(), state="ONLINE", detail=f"{endpoint[0]}:{endpoint[1]}")
        return True

    @staticmethod
    def _decision_from_row(state, row):
        result = DispatchResult(wind_target_kw=float(row["wind_target_kw"]), diesel_target_kw=float(row["diesel_target_kw"]), wind_enable=bool(row["wind_enable"]), diesel_enable=bool(row["diesel_enable"]), target_unserved_kw=float(row["target_unserved_kw"]), target_surplus_kw=float(row["target_surplus_kw"]), reason=str(row["reason"]))
        return EMSDecision(state=state, result=result)

    @staticmethod
    def _freshly_received_state(state: GridState) -> GridState:
        """Normalize transport freshness at the moment B consumes the TCP frame.

        sampled_at_utc is retained unchanged for simulation-time provenance.
        received_age_s is the wall-clock age of the copy held by B and therefore
        starts at zero when the frame is received. repository._grid_state_from_row
        continues aging it from received_at_utc while it sits in SQLite.
        """
        return replace(state, received_age_s=0.0)

    def _record_network_result(self, row, *, seq, status, ack: Optional[Ack], reason):
        command_id = self.repository.record_command({"session_id": row["session_id"], "step": row["state_step"], "sim_time_s": row["sim_time_s"], "source": "B", "seq": seq, "wind_target_kw": row["wind_target_kw"], "diesel_target_kw": row["diesel_target_kw"], "wind_enable": bool(row["wind_enable"]), "diesel_enable": bool(row["diesel_enable"]), "status": status, "reason": reason, "ack_accepted": None if ack is None else ack.accepted, "ack_reason": None if ack is None else ack.reason, "ack_received_at_utc": None if ack is None else utc_now()})
        if ack is not None:
            self.repository.record_evaluation(command_id=command_id, target_unserved_kw=float(row["target_unserved_kw"]), target_surplus_kw=float(row["target_surplus_kw"]))
        return command_id

    def _persist_wind_execution_for_new_states(self):
        runtime = self.repository.get_runtime_config()
        max_age = float(runtime["max_state_age_s"])
        with self.repository.connection() as conn:
            cmds = conn.execute("SELECT c.* FROM dispatch_commands c WHERE c.status='accepted' AND c.ack_accepted=1 AND NOT EXISTS(SELECT 1 FROM wind_execution_evaluation w WHERE w.dispatch_command_id=c.id) ORDER BY c.id").fetchall()
            for cmd in cmds:
                feedback, _ = find_feedback(conn, cmd, max_age_s=max_age)
                if feedback is None:
                    continue
                try:
                    result = evaluate_pair(cmd, feedback)
                except ValueError:
                    continue
                keep = {"session_id", "dispatch_command_id", "dispatch_step", "feedback_step", "c_wind_action_seq", "dispatch_created_at_utc", "feedback_sampled_at_utc", "wind_action_applied_at_utc", "response_latency_s", "b_wind_enable", "b_wind_target_kw", "c_controller_wind_enable", "c_pitch_target_deg", "c_wind_available_kw", "c_wind_operating_limit_kw", "a_wind_running", "a_wind_actual_kw", "a_pitch_actual_deg", "a_fault", "start_stop_score", "power_tracking_score", "pitch_response_score", "capability_safety_score", "total_score", "verdict", "reason"}
                data = {key: getattr(result, key) for key in result.__dataclass_fields__ if key in keep}
                data["outbox_id"] = None
                self.repository.record_wind_execution_evaluation(data)
                self.repository.record_log("INFO", "wind_execution_evaluated", f"dispatch={cmd['id']} feedback_step={feedback['step']} score={result.total_score:.1f}", session_id=cmd["session_id"], step=feedback["step"])

    def _peek_next_outbox(self):
        with self.repository.connection() as conn:
            return conn.execute("SELECT * FROM dispatch_outbox WHERE status='pending' ORDER BY id LIMIT 1").fetchone()

    def _cancel_pending_outbox(self, outbox_id: int, detail: str) -> None:
        with self.repository.connection() as conn:
            changed = conn.execute("UPDATE dispatch_outbox SET status='cancelled', detail=?, completed_at_utc=? WHERE id=? AND status='pending'", (detail, utc_now(), outbox_id)).rowcount
        if changed:
            self.repository.record_log("WARNING", "dispatch_requeued", f"outbox={outbox_id} {detail}")

    def _refresh_or_reconcile_pending(self):
        """Wait for a fresh A state instead of dropping or sending stale dispatch."""
        client = self.client
        if client is None or not client.connected:
            return False
        row = self._peek_next_outbox()
        if row is None:
            return False
        runtime = self.repository.get_runtime_config()
        current = client.latest_state
        if current is None or current.received_age_s > float(runtime["max_state_age_s"]):
            if client._pending_state_request_seq is None and client._pending_ack_seq is None:
                client.request_state(full=client.needs_full_sync)
                self.repository.record_log("INFO", "state_refresh_for_dispatch", f"outbox={row['id']} waiting for fresh A state", session_id=row["session_id"], step=row["state_step"])
            return False
        if current.session_id != str(row["session_id"]) or current.step != int(row["state_step"]):
            self._cancel_pending_outbox(int(row["id"]), f"A state advanced to session={current.session_id} step={current.step}; recompute immediately")
            return False
        return True

    def _send_next_outbox(self):
        client = self.client
        if client is None or not client.connected or client._pending_state_request_seq is not None or client._pending_ack_seq is not None:
            return None
        if not self._refresh_or_reconcile_pending():
            return None
        row = self.repository.claim_next_outbox()
        if row is None:
            return None
        outbox_id = int(row["id"])
        state = self.repository.get_grid_state(str(row["session_id"]), int(row["state_step"]))
        runtime = self.repository.get_runtime_config()
        current = client.latest_state
        if state is None:
            self.repository.finish_outbox(outbox_id, status="cancelled", detail="referenced state is missing")
            return outbox_id
        if current is None or current.session_id != state.session_id or current.step != state.step:
            self.repository.finish_outbox(outbox_id, status="cancelled", detail="A state advanced or session changed before send")
            return outbox_id
        if state.received_age_s > float(runtime["max_state_age_s"]):
            self.repository.finish_outbox(outbox_id, status="cancelled", detail=f"state became stale before send: {state.received_age_s:.3f}s")
            return outbox_id
        decision = self._decision_from_row(state, row)
        try:
            seq = client.send_dispatch(decision)
            ack = client.get_ack(seq)
            if ack is None:
                raise DispatchDeliveryUnknown(seq, "dispatch returned without recorded ACK")
            status = "accepted" if ack.accepted else "rejected"
            command_id = self._record_network_result(row, seq=seq, status=status, ack=ack, reason=decision.result.reason)
            self.repository.finish_outbox(outbox_id, status=status, protocol_seq=seq, command_id=command_id, ack_accepted=ack.accepted, ack_reason=ack.reason)
            self.repository.record_log("INFO" if ack.accepted else "WARNING", "dispatch_result", f"outbox={outbox_id} seq={seq} accepted={ack.accepted} reason={ack.reason}", session_id=state.session_id, step=state.step)
        except DispatchDeliveryUnknown as error:
            command_id = self._record_network_result(row, seq=error.seq, status="delivery_unknown", ack=None, reason=str(error))
            self.repository.finish_outbox(outbox_id, status="delivery_unknown", protocol_seq=error.seq, command_id=command_id, detail=str(error))
            self._schedule_reconnect(error)
        except (ProtocolError, ValueError, RuntimeError) as error:
            self.repository.finish_outbox(outbox_id, status="local_error", detail=str(error))
            self.repository.record_log("ERROR", "dispatch_local_error", str(error), session_id=state.session_id, step=state.step)
        return outbox_id

    def run_cycle(self):
        if not self._ensure_connected():
            return None
        assert self.client is not None
        try:
            messages = self.client.receive_available()
            for message in messages:
                if isinstance(message, GridState):
                    fresh_state = self._freshly_received_state(message)
                    self.client.latest_state = fresh_state
                    self.repository.save_state(fresh_state)
            self._persist_wind_execution_for_new_states()
            endpoint = self._endpoint or ("?", 0)
            self.repository.heartbeat("B_IO", pid=os.getpid(), state="ONLINE", detail=f"{endpoint[0]}:{endpoint[1]}")
            now = self.clock()
            if self._last_command_check_at is None or now - self._last_command_check_at >= COMMAND_CHECK_PERIOD_S:
                self._last_command_check_at = now
                return self._send_next_outbox()
            return None
        except Exception as error:
            self._schedule_reconnect(error)
            return None

    def run(self, *, max_cycles=None, idle_sleep_s=0.1):
        self.initialize()
        cycles = 0
        try:
            while max_cycles is None or cycles < max_cycles:
                self.run_cycle()
                cycles += 1
                self.sleeper(idle_sleep_s)
        finally:
            self._disconnect()
            self.repository.heartbeat("B_IO", pid=os.getpid(), state="STOPPED", detail="process stopped")
