"""Configuration loading for the basic simulator."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .models import DieselGeneratorParameters, WindTurbineParameters


@dataclass(frozen=True)
class SimulationConfig:
    parameter_status: str
    start_s: float
    end_s: float
    step_s: float
    poll_interval_s: float
    server_bind: str
    server_port: int
    max_frame_bytes: int
    wind: WindTurbineParameters
    diesel: DieselGeneratorParameters
    initial_control: dict[str, object]


def _require_number(mapping: dict[str, object], name: str) -> float:
    value = mapping.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def load_config(path: str | Path) -> SimulationConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("only config schema_version 1 is supported")
    simulation = data["simulation"]
    network = data["network"]
    wind_data = data["wind_turbine"]
    diesel_data = data["diesel_generator"]
    initial = data["initial_control"]
    start_s = _require_number(simulation, "start_s")
    end_s = _require_number(simulation, "end_s")
    step_s = _require_number(simulation, "step_s")
    poll = _require_number(simulation, "poll_interval_s")
    if start_s < 0 or end_s <= start_s or step_s <= 0 or poll <= 0:
        raise ValueError("simulation times require 0 <= start < end and positive intervals")
    port = network.get("server_port")
    frame_bytes = network.get("max_frame_bytes")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("server_port must be an integer from 1 to 65535")
    if isinstance(frame_bytes, bool) or not isinstance(frame_bytes, int) or frame_bytes < 128:
        raise ValueError("max_frame_bytes must be an integer of at least 128")

    bool_names = ("dispatch_wind_enable", "controller_wind_enable", "diesel_enable")
    for name in bool_names:
        if type(initial.get(name)) is not bool:
            raise ValueError(f"initial_control.{name} must be boolean")
    for name in (
        "wind_target_kw",
        "diesel_target_kw",
        "pitch_target_deg",
        "controller_wind_available_kw",
        "controller_wind_operating_limit_kw",
    ):
        _require_number(initial, name)

    wind = WindTurbineParameters(**{key: _require_number(wind_data, key) for key in (
        "rated_power_kw", "cut_in_speed_mps", "rated_speed_mps", "cut_out_speed_mps",
        "pitch_full_output_deg", "pitch_feather_deg", "ramp_up_kw_per_s", "ramp_down_kw_per_s",
    )})
    diesel = DieselGeneratorParameters(**{key: _require_number(diesel_data, key) for key in (
        "min_power_kw", "max_power_kw", "ramp_up_kw_per_s", "ramp_down_kw_per_s",
    )})
    wind_target = _require_number(initial, "wind_target_kw")
    diesel_target = _require_number(initial, "diesel_target_kw")
    pitch_target = _require_number(initial, "pitch_target_deg")
    controller_available = _require_number(initial, "controller_wind_available_kw")
    controller_limit = _require_number(initial, "controller_wind_operating_limit_kw")
    if not 0 <= wind_target <= wind.rated_power_kw:
        raise ValueError("initial wind_target_kw is outside the configured device range")
    if not 0 <= diesel_target <= diesel.max_power_kw:
        raise ValueError("initial diesel_target_kw is outside the configured device range")
    if not wind.pitch_full_output_deg <= pitch_target <= wind.pitch_feather_deg:
        raise ValueError("initial pitch_target_deg is outside the configured device range")
    if not 0 <= controller_limit <= controller_available <= wind.rated_power_kw:
        raise ValueError("initial C wind capability values are outside the configured device range")
    bind = network.get("server_bind")
    if not isinstance(bind, str) or not bind:
        raise ValueError("server_bind must be a non-empty string")
    return SimulationConfig(
        parameter_status=str(data.get("parameter_status", "unspecified")),
        start_s=start_s,
        end_s=end_s,
        step_s=step_s,
        poll_interval_s=poll,
        server_bind=bind,
        server_port=port,
        max_frame_bytes=frame_bytes,
        wind=wind,
        diesel=diesel,
        initial_control=dict(initial),
    )
