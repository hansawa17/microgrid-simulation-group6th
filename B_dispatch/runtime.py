"""Deterministic, network-free runtime loop for the B EMS core.

This layer only orchestrates polling and dispatch timing. TCP, SQLite I/O,
Qt, and hardware control are intentionally injected or kept outside the
runtime so the loop can be tested before A/B/C integration.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional

from .dispatch import DispatchError
from .models import DispatchResult, GridState
from .operator_core import EMSCore, EMSDecision


@dataclass(frozen=True)
class RuntimeConfig:
    """Timing and local safety configuration for the EMS loop."""

    poll_period_s: float = 1.0
    dispatch_period_s: float = 5.0
    safe_fallback_on_dispatch_error: bool = True

    def __post_init__(self) -> None:
        if self.poll_period_s <= 0 or self.dispatch_period_s <= 0:
            raise ValueError("runtime periods must be positive")


class EMSRuntime:
    """Poll state periodically and dispatch at the configured interval.

    The first valid state is dispatched immediately. Subsequent dispatches
    use the most recently polled state. If dispatch validation fails, the
    default behavior is to emit a zero-output safe fallback. A state-provider
    failure clears the cached state so an old state is never dispatched again.
    """

    def __init__(
        self,
        core: EMSCore,
        state_provider: Callable[[], GridState],
        decision_sink: Callable[[EMSDecision], None],
        config: RuntimeConfig | None = None,
        *,
        error_sink: Callable[[Exception], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.core = core
        self.state_provider = state_provider
        self.decision_sink = decision_sink
        self.config = config or RuntimeConfig()
        self.error_sink = error_sink
        self.clock = clock
        self.sleeper = sleeper
        self.latest_state: Optional[GridState] = None
        self._last_poll_at: Optional[float] = None
        self._last_dispatch_at: Optional[float] = None

    def _report_error(self, error: Exception) -> None:
        if self.error_sink is not None:
            self.error_sink(error)

    @staticmethod
    def _safe_decision(state: GridState, error: Exception) -> EMSDecision:
        """Return a zero-output decision for a rejected state/dispatch."""
        result = DispatchResult(
            wind_target_kw=0.0,
            diesel_target_kw=0.0,
            wind_enable=False,
            diesel_enable=False,
            target_unserved_kw=state.load_power_kw,
            target_surplus_kw=0.0,
            reason=f"safe fallback: {error}",
        )
        return EMSDecision(state=state, result=result)

    def run_cycle(self, now: float | None = None) -> Optional[EMSDecision]:
        """Perform work due at ``now`` and return a new decision if emitted."""
        current = self.clock() if now is None else now

        poll_due = self._last_poll_at is None or current - self._last_poll_at >= self.config.poll_period_s
        if poll_due:
            try:
                self.latest_state = self.state_provider()
            except Exception as error:
                self.latest_state = None
                self._report_error(error)
                self._last_poll_at = current
                return None
            self._last_poll_at = current

        dispatch_due = (
            self.latest_state is not None
            and (self._last_dispatch_at is None or current - self._last_dispatch_at >= self.config.dispatch_period_s)
        )
        if not dispatch_due:
            return None

        try:
            decision = self.core.decide(self.latest_state)
        except DispatchError as error:
            self._report_error(error)
            if not self.config.safe_fallback_on_dispatch_error:
                self._last_dispatch_at = current
                return None
            decision = self._safe_decision(self.latest_state, error)

        self.decision_sink(decision)
        self._last_dispatch_at = current
        return decision

    def run(self, *, max_cycles: int | None = None) -> None:
        """Run continuously until interrupted or ``max_cycles`` is reached."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            started = self.clock()
            self.run_cycle(started)
            cycles += 1
            elapsed = self.clock() - started
            self.sleeper(max(0.0, self.config.poll_period_s - elapsed))
