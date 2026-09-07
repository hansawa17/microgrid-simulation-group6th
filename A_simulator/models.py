"""Pure, deterministic device models for one simulation step.

All numerical values are supplied by configuration.  The repository deliberately
does not claim that the example parameter values are course requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re


def _finite_non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")


def _bounded_move(current: float, target: float, up: float, down: float, dt: float) -> float:
    if target >= current:
        return min(target, current + up * dt)
    return max(target, current - down * dt)


def _validate_utc_timestamp(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value
    ) is None:
        raise ValueError("sampled_at_utc must use YYYY-MM-DDTHH:MM:SS.mmmZ")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("sampled_at_utc must use YYYY-MM-DDTHH:MM:SS.mmmZ") from exc


@dataclass(frozen=True)
class WindTurbineParameters:
    rated_power_kw: float
    cut_in_speed_mps: float
    rated_speed_mps: float
    cut_out_speed_mps: float
    pitch_full_output_deg: float
    pitch_feather_deg: float
    ramp_up_kw_per_s: float
    ramp_down_kw_per_s: float

    def __post_init__(self) -> None:
        for name in (
            "rated_power_kw",
            "cut_in_speed_mps",
            "rated_speed_mps",
            "cut_out_speed_mps",
            "pitch_full_output_deg",
            "pitch_feather_deg",
            "ramp_up_kw_per_s",
            "ramp_down_kw_per_s",
        ):
            _finite_non_negative(name, getattr(self, name))
        if not self.cut_in_speed_mps < self.rated_speed_mps < self.cut_out_speed_mps:
            raise ValueError("wind speeds must satisfy cut_in < rated < cut_out")
        if not self.pitch_full_output_deg < self.pitch_feather_deg:
            raise ValueError("pitch_full_output_deg must be below pitch_feather_deg")
        if self.rated_power_kw == 0 or self.ramp_up_kw_per_s == 0 or self.ramp_down_kw_per_s == 0:
            raise ValueError("wind power and ramp rates must be greater than zero")


@dataclass(frozen=True)
class DieselGeneratorParameters:
    min_power_kw: float
    max_power_kw: float
    ramp_up_kw_per_s: float
    ramp_down_kw_per_s: float

    def __post_init__(self) -> None:
        for name in (
            "min_power_kw",
            "max_power_kw",
            "ramp_up_kw_per_s",
            "ramp_down_kw_per_s",
        ):
            _finite_non_negative(name, getattr(self, name))
        if self.max_power_kw <= self.min_power_kw:
            raise ValueError("diesel max_power_kw must exceed min_power_kw")
        if self.ramp_up_kw_per_s == 0 or self.ramp_down_kw_per_s == 0:
            raise ValueError("diesel ramp rates must be greater than zero")


@dataclass(frozen=True)
class ControlInputs:
    wind_target_kw: float
    diesel_target_kw: float
    dispatch_wind_enable: bool
    controller_wind_enable: bool
    diesel_enable: bool
    pitch_target_deg: float


@dataclass(frozen=True)
class SimulationState:
    session_id: str
    step: int
    sim_time_s: float
    sampled_at_utc: str
    wind_speed_mps: float
    load_power_kw: float
    wind_available_kw: float
    wind_target_kw: float
    wind_actual_kw: float
    diesel_target_kw: float
    diesel_actual_kw: float
    pitch_actual_deg: float
    wind_running: bool
    diesel_running: bool
    fault: bool
    power_imbalance_kw: float

    def __post_init__(self) -> None:
        _validate_utc_timestamp(self.sampled_at_utc)

    def protocol_payload(self) -> dict[str, object]:
        """Return fields currently listed in common/protocol.md."""
        return {
            "sampled_at_utc": self.sampled_at_utc,
            "wind_speed_mps": self.wind_speed_mps,
            "load_power_kw": self.load_power_kw,
            "wind_actual_kw": self.wind_actual_kw,
            "diesel_actual_kw": self.diesel_actual_kw,
            "wind_target_kw": self.wind_target_kw,
            "pitch_actual_deg": self.pitch_actual_deg,
            "wind_running": self.wind_running,
            "fault": self.fault,
        }


def wind_available_power(wind_speed_mps: float, params: WindTurbineParameters) -> float:
    """Use a configurable cubic ramp followed by a rated-power plateau."""
    _finite_non_negative("wind_speed_mps", wind_speed_mps)
    if wind_speed_mps < params.cut_in_speed_mps or wind_speed_mps >= params.cut_out_speed_mps:
        return 0.0
    if wind_speed_mps >= params.rated_speed_mps:
        return params.rated_power_kw
    span = params.rated_speed_mps - params.cut_in_speed_mps
    fraction = (wind_speed_mps - params.cut_in_speed_mps) / span
    return params.rated_power_kw * fraction**3


def pitch_power_factor(pitch_deg: float, params: WindTurbineParameters) -> float:
    """Return the configured linear derating factor used by the basic mock model."""
    if not math.isfinite(pitch_deg):
        raise ValueError("pitch_target_deg must be finite")
    if pitch_deg <= params.pitch_full_output_deg:
        return 1.0
    if pitch_deg >= params.pitch_feather_deg:
        return 0.0
    span = params.pitch_feather_deg - params.pitch_full_output_deg
    return 1.0 - (pitch_deg - params.pitch_full_output_deg) / span


def simulate_step(
    *,
    previous: SimulationState,
    next_step: int,
    next_sim_time_s: float,
    sampled_at_utc: str,
    step_s: float,
    wind_speed_mps: float,
    load_power_kw: float,
    controls: ControlInputs,
    wind: WindTurbineParameters,
    diesel: DieselGeneratorParameters,
) -> SimulationState:
    """Calculate actual output from environment, targets, actions and constraints."""
    _finite_non_negative("step_s", step_s)
    _validate_utc_timestamp(sampled_at_utc)
    if step_s == 0:
        raise ValueError("step_s must be greater than zero")
    _finite_non_negative("wind_speed_mps", wind_speed_mps)
    _finite_non_negative("load_power_kw", load_power_kw)
    _finite_non_negative("wind_target_kw", controls.wind_target_kw)
    _finite_non_negative("diesel_target_kw", controls.diesel_target_kw)

    available = wind_available_power(wind_speed_mps, wind)
    wind_enabled = controls.dispatch_wind_enable and controls.controller_wind_enable
    wind_demand = 0.0
    if wind_enabled:
        wind_demand = min(
            controls.wind_target_kw,
            available * pitch_power_factor(controls.pitch_target_deg, wind),
            wind.rated_power_kw,
        )
    wind_actual = _bounded_move(
        previous.wind_actual_kw,
        wind_demand,
        wind.ramp_up_kw_per_s,
        wind.ramp_down_kw_per_s,
        step_s,
    )

    if controls.diesel_enable:
        diesel_demand = min(
            max(controls.diesel_target_kw, diesel.min_power_kw),
            diesel.max_power_kw,
        )
    else:
        diesel_demand = 0.0
    diesel_actual = _bounded_move(
        previous.diesel_actual_kw,
        diesel_demand,
        diesel.ramp_up_kw_per_s,
        diesel.ramp_down_kw_per_s,
        step_s,
    )

    imbalance = load_power_kw - wind_actual - diesel_actual
    return SimulationState(
        session_id=previous.session_id,
        step=next_step,
        sim_time_s=next_sim_time_s,
        sampled_at_utc=sampled_at_utc,
        wind_speed_mps=wind_speed_mps,
        load_power_kw=load_power_kw,
        wind_available_kw=available,
        wind_target_kw=controls.wind_target_kw,
        wind_actual_kw=wind_actual,
        diesel_target_kw=controls.diesel_target_kw,
        diesel_actual_kw=diesel_actual,
        pitch_actual_deg=controls.pitch_target_deg,
        wind_running=wind_enabled and wind_actual > 0,
        diesel_running=controls.diesel_enable and diesel_actual > 0,
        fault=False,
        power_imbalance_kw=imbalance,
    )

