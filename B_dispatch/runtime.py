"""Deterministic, network-free runtime loop for the B EMS core.

This layer only orchestrates polling and dispatch timing. TCP, SQLite I/O,
Qt, and hardware control are intentionally injected or kept outside the
runtime so the loop can be tested before A/B/C integration.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional

from .models import GridState
from .operator_core import EMSCore, EMSDecision


@dataclass(frozen=True)
class RuntimeConfig:
    """Timing configuration for the local EMS loop."""

    poll_period_s: float = 1.0
    dispatch_period_s: float = 5.0

    def __post_init__(self) -> None:
        if self.poll_period_s <= 0 or self.dispatch_period_s <= 0:
            raise ValueError("runtime periods must be positive")


class EMSRuntime:
    """Poll state periodically and dispatch at the configured interval.

    The first valid state is dispatched immediately. Subsequent dispatches
    use the most recently polled state. ``state_provider`` and
    ``decision_sink`` are dependency-injected so this class does not depend
    on TCP, SQLite, Qt, or real hardware.
    """

    def __init__(
        self,
        core: EMSCore,
        state_provider: Callable[[], GridState],
        decision_sink: Callable[[EMSDecision], None],
        config: RuntimeConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.core = core
        self.state_provider = state_provider
        self.decision_sink = decision_sink
        self.config = config or RuntimeConfig()
        self.clock = clock
        self.sleeper = sleeper
        self.latest_state: Optional[GridState] = None
        self._last_poll_at: Optional[float] = None
        self._last_dispatch_at: Optional[float] = None

    def run_cycle(self, now: float | None = None) -> Optional[EMSDecision]:
        """Perform work due at ``now`` and return a new decision if emitted."""
        current = self.clock() if now is None else now

        poll_due = self._last_poll_at is None or current - self._last_poll_at >= self.config.poll_period_s
        if poll_due:
            self.latest_state = self.state_provider()
            self._last_poll_at = current

        dispatch_due = (
            self.latest_state is not None
            and (self._last_dispatch_at is None or current - self._last_dispatch_at >= self.config.dispatch_period_s)
        )
        if not dispatch_due:
            return None

        decision = self.core.decide(self.latest_state)
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
