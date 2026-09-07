"""B-owned service bridge connecting TCP, SQLite and the EMS runtime."""
from __future__ import annotations

from typing import Callable

from .operator_core import EMSCore, EMSDecision
from .repository import EMSRepository
from .runtime import EMSRuntime, RuntimeConfig
from .tcpB import EMSTcpClient


class EMSServiceB:
    """Compose B's transport, persistence and closed-loop decision layers."""

    def __init__(
        self,
        client: EMSTcpClient,
        repository: EMSRepository,
        core: EMSCore,
        *,
        runtime_config: RuntimeConfig | None = None,
        error_sink: Callable[[Exception], None] | None = None,
    ) -> None:
        self.client = client
        self.repository = repository
        self.core = core
        self.runtime = EMSRuntime(
            core=core,
            state_provider=self._poll_state,
            decision_sink=self._send_decision,
            config=runtime_config,
            error_sink=error_sink or self._record_error,
        )

    def initialize(self) -> None:
        """Initialize the B database before network operation starts."""
        self.repository.initialize()
        self.repository.record_log("INFO", "service_start", "EMSServiceB initialized")

    def connect(self) -> None:
        self.client.connect()
        self.repository.record_log("INFO", "tcp_connect", f"connected to A at {self.client.host}:{self.client.port}")

    def close(self) -> None:
        self.client.close()
        self.repository.record_log("INFO", "tcp_disconnect", "B TCP client closed")

    def run(self, *, max_cycles: int | None = None) -> None:
        """Run the periodic closed loop; exceptions are handled by EMSRuntime."""
        self.runtime.run(max_cycles=max_cycles)

    def run_cycle(self) -> EMSDecision | None:
        """Run one runtime cycle and return the decision emitted by EMSRuntime."""
        return self.runtime.run_cycle()

    def _poll_state(self):
        state = self.client.poll_state()
        if state is None:
            return None
        self.repository.save_state(state)
        return state

    def _send_decision(self, decision: EMSDecision) -> None:
        seq = self.client.send_dispatch(decision)
        command_id = self.repository.record_command({
            "session_id": decision.state.session_id,
            "step": decision.state.step,
            "sim_time_s": decision.state.sim_time_s,
            "source": "B",
            "seq": seq,
            "wind_target_kw": decision.result.wind_target_kw,
            "diesel_target_kw": decision.result.diesel_target_kw,
            "wind_enable": decision.result.wind_enable,
            "diesel_enable": decision.result.diesel_enable,
            "status": "sent",
            "reason": decision.result.reason,
        })
        self.repository.record_evaluation(
            command_id=command_id,
            target_unserved_kw=decision.result.target_unserved_kw,
            target_surplus_kw=decision.result.target_surplus_kw,
        )

    def _record_error(self, error: Exception) -> None:
        state = self.runtime.latest_state
        self.repository.record_log(
            "ERROR",
            "runtime_error",
            str(error),
            session_id=state.session_id if state else None,
            step=state.step if state else None,
        )
