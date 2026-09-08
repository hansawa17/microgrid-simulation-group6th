"""Data models shared by the B/EMS dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GridState:
    """Snapshot received from A according to the current common TCP protocol.

    C computes ``wind_available_kw`` and ``wind_operating_limit_kw``; A
    validates, stores and relays them. ``wind_actual_kw`` is A's simulated
    result and must never be used as the capability input for B dispatch.
    """

    session_id: str
    step: int
    sim_time_s: float
    wind_speed_mps: float
    wind_available_kw: float
    wind_operating_limit_kw: float
    load_power_kw: float
    wind_actual_kw: float
    diesel_actual_kw: float
    wind_running: bool
    fault: bool = False
    received_age_s: float = 0.0
    sampled_at_utc: str = ""
    received_at_utc: str = ""
    wind_target_kw: float = 0.0
    pitch_actual_deg: float | None = None


@dataclass(frozen=True)
class DispatchConfig:
    """EMS constraints.

    Ratings remain explicit inputs even though the team baseline is now
    wind=100 kW and diesel=120 kW. Diesel reserve is fixed at 10 kW.
    """

    wind_max_kw: float
    diesel_max_kw: float
    wind_min_kw: float = 0.0
    reserve_kw: float = 10.0
    max_state_age_s: float = 2.0
    c_has_control_priority: bool = True

    @property
    def diesel_dispatch_max_kw(self) -> float:
        return self.diesel_max_kw - self.reserve_kw


@dataclass(frozen=True)
class DispatchResult:
    """Power targets and evaluation values produced by B."""

    wind_target_kw: float
    diesel_target_kw: float
    wind_enable: bool
    diesel_enable: bool
    target_unserved_kw: float
    target_surplus_kw: float
    reason: str
