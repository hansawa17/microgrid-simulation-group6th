"""Constraint-based first-stage EMS dispatch logic.

B produces power targets only. It never writes A's actual values and never
controls wind pitch. C has priority for wind protection/control.
"""

from __future__ import annotations

import math

from .models import DispatchConfig, DispatchResult, GridState


class DispatchError(ValueError):
    """Raised when a dispatch input violates the EMS contract."""


def _finite_nonnegative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise DispatchError(f"{name} must be a finite non-negative number")


def _validate(state: GridState, config: DispatchConfig) -> None:
    if not state.session_id:
        raise DispatchError("session_id is required")
    if state.step < 0:
        raise DispatchError("step must be non-negative")
    for name in (
        "sim_time_s",
        "wind_speed_mps",
        "wind_available_kw",
        "wind_operating_limit_kw",
        "load_power_kw",
        "wind_actual_kw",
        "diesel_actual_kw",
        "received_age_s",
    ):
        _finite_nonnegative(name, getattr(state, name))
    if state.wind_operating_limit_kw > state.wind_available_kw:
        raise DispatchError("wind_operating_limit_kw must be <= wind_available_kw")
    for name in (
        "wind_min_kw",
        "wind_max_kw",
        "diesel_max_kw",
        "reserve_kw",
        "diesel_min_kw",
        "max_state_age_s",
    ):
        _finite_nonnegative(name, getattr(config, name))
    if config.wind_max_kw < config.wind_min_kw:
        raise DispatchError("wind_max_kw must be >= wind_min_kw")
    if config.diesel_max_kw < config.reserve_kw:
        raise DispatchError("diesel_max_kw must be >= reserve_kw")
    if config.diesel_min_kw > config.diesel_dispatch_max_kw:
        raise DispatchError("diesel_min_kw must be <= diesel_dispatch_max_kw")
    if state.received_age_s > config.max_state_age_s:
        raise DispatchError(
            f"state is stale: age={state.received_age_s:.3f}s > "
            f"limit={config.max_state_age_s:.3f}s"
        )


def calculate_dispatch(state: GridState, config: DispatchConfig) -> DispatchResult:
    """Calculate wind-first, diesel-compensating targets.

    ``wind_operating_limit_kw`` is the protocol-defined capability boundary for
    B. ``wind_available_kw`` remains an informational resource-capability field,
    while ``wind_actual_kw`` is deliberately never used to decide the target.
    """
    _validate(state, config)

    if state.fault and config.c_has_control_priority:
        wind_target = 0.0
        diesel_target = min(state.load_power_kw, config.diesel_dispatch_max_kw)
        reason = "C-priority fault: wind request suppressed"
    else:
        operating_limit = min(state.wind_operating_limit_kw, config.wind_max_kw)
        wind_target = min(state.load_power_kw, operating_limit)
        remaining_load = max(state.load_power_kw - wind_target, 0.0)
        diesel_target = min(remaining_load, config.diesel_dispatch_max_kw)
        reason = "wind-first dispatch within operating limit"

    if 0.0 < diesel_target < config.diesel_min_kw:
        diesel_target = min(config.diesel_min_kw, config.diesel_dispatch_max_kw)

    target_generation = wind_target + diesel_target
    target_unserved = max(state.load_power_kw - target_generation, 0.0)
    target_surplus = max(target_generation - state.load_power_kw, 0.0)
    return DispatchResult(
        wind_target_kw=wind_target,
        diesel_target_kw=diesel_target,
        wind_enable=wind_target > 0.0,
        diesel_enable=diesel_target > 0.0,
        target_unserved_kw=target_unserved,
        target_surplus_kw=target_surplus,
        reason=reason,
    )
