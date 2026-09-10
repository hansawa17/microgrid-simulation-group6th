"""Data models shared by the B/EMS dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GridState:
    """Snapshot received from A according to the common TCP protocol.

    The wind-execution fields are nullable for backward compatibility with old A
    nodes. Missing extension data must never be silently converted to zero.
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
    diesel_target_kw: float = 0.0
    pitch_actual_deg: float | None = None
    diesel_running: bool = False
    power_imbalance_kw: float = 0.0
    controller_wind_enable: bool | None = None
    pitch_target_deg: float | None = None
    last_wind_action_seq: int | None = None
    last_wind_action_step: int | None = None
    wind_action_applied_at_utc: str | None = None
    extension_status: str = "legacy_or_incomplete"
    parameters: dict[str, float] | None = None


@dataclass(frozen=True)
class DispatchConfig:
    """EMS constraints."""

    wind_max_kw: float
    diesel_max_kw: float
    wind_min_kw: float = 0.0
    reserve_kw: float = 10.0
    diesel_min_kw: float = 20.0
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
