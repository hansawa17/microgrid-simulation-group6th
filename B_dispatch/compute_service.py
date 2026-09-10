"""Database-only EMS calculation process for B.

This process never imports or opens a socket. It reads the latest A state and
B-owned parameters from ``ems.db``, calculates one decision per dispatch
period, and writes that decision to the database outbox. The independent I/O
process is the only component allowed to transmit executable commands.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from .dispatch import DispatchError
from .models import DispatchConfig
from .operator_core import EMSCore
from .repository import EMSRepository


class EMSComputeService:
    """Run B's dispatch calculation using SQLite as the sole integration bus."""

    def __init__(self, repository: EMSRepository, *, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.repository = repository
        self.clock = clock
        self.sleeper = sleeper
        self._last_decision_at: Optional[float] = None
        self._last_state_key: Optional[tuple[str, int, str]] = None
        self._waiting_for_fresh_state = False
        self._last_problem: Optional[str] = None

    def initialize(self) -> None:
        self.repository.initialize()
        self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="STARTING", detail="database-only EMS")

    def _configuration(self) -> tuple[DispatchConfig, float, bool]:
        dispatch = self.repository.get_parameters()
        runtime = self.repository.get_runtime_config()
        physical = self.repository.get_physical_parameters()
        config = DispatchConfig(
            wind_min_kw=0.0,
            wind_max_kw=float(physical["wind_rated_kw"]),
            diesel_max_kw=float(physical["diesel_max_kw"]),
            reserve_kw=float(dispatch["reserve_kw"]),
            diesel_min_kw=float(physical["diesel_min_kw"]),
            max_state_age_s=float(runtime["max_state_age_s"]),
            c_has_control_priority=True,
        )
        return config, float(runtime["dispatch_period_s"]), bool(runtime["closed_loop"])

    def _report_problem(self, event_type: str, message: str) -> None:
        key = f"{event_type}:{message}"
        if key != self._last_problem:
            self.repository.record_log("WARNING", event_type, message)
            self._last_problem = key

    def run_cycle(self, now: Optional[float] = None) -> Optional[int]:
        current = self.clock() if now is None else now
        config, dispatch_period_s, closed_loop = self._configuration()
        state = self.repository.get_current_grid_state()
        if state is None:
            self._report_problem("state_unavailable", "ems.db has no A state yet")
            self._waiting_for_fresh_state = True
            self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="WAITING", detail="no A state")
            self._last_decision_at = current
            return None

        state_key = (state.session_id, state.step, state.sampled_at_utc)
        age_ok = state.received_age_s <= config.max_state_age_s
        if not age_ok:
            self._report_problem("dispatch_input_rejected", f"A state is stale: {state.received_age_s:.3f}s > {config.max_state_age_s:.3f}s")
            self._waiting_for_fresh_state = True
            self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="WAITING", detail=f"stale A state age={state.received_age_s:.3f}s")
            self._last_decision_at = current
            return None

        # Normally respect the configured dispatch period. If the process was
        # blocked on missing/stale state, however, calculate immediately when
        # the first fresh state arrives so reconnection does not add another
        # full dispatch-period delay.
        recovering = self._waiting_for_fresh_state
        if (
            not recovering
            and self._last_decision_at is not None
            and current - self._last_decision_at < dispatch_period_s
        ):
            self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="RUNNING", detail="waiting for dispatch period")
            return None

        try:
            decision = EMSCore(config).decide(state)
        except DispatchError as error:
            self._report_problem("dispatch_input_rejected", str(error))
            self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="WAITING", detail=str(error))
            self._waiting_for_fresh_state = True
            self._last_decision_at = current
            return None

        outbox_id, created = self.repository.queue_decision(decision.state, decision.result, executable=closed_loop)
        if created:
            mode = "closed_loop" if closed_loop else "open_loop"
            self.repository.record_log(
                "INFO",
                "decision_created",
                f"outbox={outbox_id} mode={mode} {decision.result.reason}",
                session_id=decision.state.session_id,
                step=decision.state.step,
            )
        self._last_problem = None
        self._waiting_for_fresh_state = False
        self._last_state_key = state_key
        self._last_decision_at = current
        self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="RUNNING", detail=f"last outbox={outbox_id}; executable={closed_loop}")
        return outbox_id if created else None

    def run(self, *, max_cycles: Optional[int] = None, idle_sleep_s: float = 0.2) -> None:
        self.initialize()
        cycles = 0
        try:
            while max_cycles is None or cycles < max_cycles:
                self.run_cycle()
                cycles += 1
                self.sleeper(idle_sleep_s)
        finally:
            self.repository.heartbeat("B_COMPUTE", pid=os.getpid(), state="STOPPED", detail="process stopped")
