"""Closed-loop EMS core for B.

This module deliberately contains no socket, GUI, or database code. It turns
an A state snapshot into a dispatch decision so the transport/UI layers can
remain independent and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dispatch import calculate_dispatch
from .models import DispatchConfig, DispatchResult, GridState


@dataclass(frozen=True)
class EMSDecision:
    """One deterministic EMS decision for an input state."""

    state: GridState
    result: DispatchResult


class EMSCore:
    """Stateless decision engine used by the B closed loop."""

    def __init__(self, config: DispatchConfig) -> None:
        self.config = config

    def decide(self, state: GridState) -> EMSDecision:
        """Validate the state and calculate the next power targets."""

        return EMSDecision(state=state, result=calculate_dispatch(state, self.config))
