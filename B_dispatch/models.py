"""Data models shared by the B/EMS dispatch layer.

B models describe inputs received from A and power targets produced by B.
Actual values remain observations owned by A; targets are control requests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GridState:
    """Snapshot received from A.

    Power values are kW, wind speed is m/s, simulation time is seconds, and
    ``received_age_s`` is wall-clock age measured by B.
    """

    session_id: str
    step: int
    sim_time_s: float
    wind_speed_mps: float
    load_power_kw: float
    wind_actual_kw: float
    diesel_actual_kw: float
    wind_running: bool
    fault: bool = False
    received_age_s: float = 0.0


@dataclass(frozen=True)
class DispatchConfig:
    """EMS constraints.

    Physical ratings are required explicitly because the project has not
    frozen the wind/diesel nameplate values. Diesel reserve is fixed at 10 kW.
    """

    wind_max_kw: float
    diesel_max_kw: float
    wind_min_kw: float = 0.0
    reserve_kw: float = 10.0
    max_state_age_s: float = 2.0
    c_has_control_priority: bool = True

    @property
    def diesel_dispatch_max_kw(self) -> float:
        """Maximum diesel target available to normal B dispatch."""

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
