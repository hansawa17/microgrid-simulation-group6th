"""Independent wind-speed and load scenario curve loading."""

from __future__ import annotations

from bisect import bisect_right
import csv
from dataclasses import dataclass
import math
from pathlib import Path


@dataclass(frozen=True)
class ScenarioPoint:
    sim_time_s: float
    wind_speed_mps: float
    load_power_kw: float


@dataclass(frozen=True)
class ScenarioCurve:
    points: tuple[ScenarioPoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("scenario must contain at least one point")
        previous = -math.inf
        for point in self.points:
            for name, value in (
                ("sim_time_s", point.sim_time_s),
                ("wind_speed_mps", point.wind_speed_mps),
                ("load_power_kw", point.load_power_kw),
            ):
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be finite and non-negative")
            if point.sim_time_s <= previous:
                raise ValueError("scenario times must be strictly increasing")
            previous = point.sim_time_s

    def at(self, sim_time_s: float) -> ScenarioPoint:
        """Linearly interpolate inside the curve and clamp at its endpoints."""
        if not math.isfinite(sim_time_s) or sim_time_s < 0:
            raise ValueError("sim_time_s must be finite and non-negative")
        if sim_time_s <= self.points[0].sim_time_s:
            first = self.points[0]
            return ScenarioPoint(sim_time_s, first.wind_speed_mps, first.load_power_kw)
        if sim_time_s >= self.points[-1].sim_time_s:
            last = self.points[-1]
            return ScenarioPoint(sim_time_s, last.wind_speed_mps, last.load_power_kw)
        times = [point.sim_time_s for point in self.points]
        right = bisect_right(times, sim_time_s)
        left_point, right_point = self.points[right - 1], self.points[right]
        fraction = (sim_time_s - left_point.sim_time_s) / (
            right_point.sim_time_s - left_point.sim_time_s
        )
        return ScenarioPoint(
            sim_time_s=sim_time_s,
            wind_speed_mps=left_point.wind_speed_mps
            + fraction * (right_point.wind_speed_mps - left_point.wind_speed_mps),
            load_power_kw=left_point.load_power_kw
            + fraction * (right_point.load_power_kw - left_point.load_power_kw),
        )


def load_scenario_csv(path: str | Path) -> ScenarioCurve:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"step", "sim_time_s", "wind_speed_mps", "load_power_kw"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"scenario CSV must contain columns: {sorted(required)}")
        points: list[ScenarioPoint] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                step_text = row["step"]
                if step_text is None or int(step_text) != len(points) or str(int(step_text)) != step_text.strip():
                    raise ValueError("step must start at 0 and increase by 1")
                points.append(
                    ScenarioPoint(
                        sim_time_s=float(row["sim_time_s"]),
                        wind_speed_mps=float(row["wind_speed_mps"]),
                        load_power_kw=float(row["load_power_kw"]),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid scenario value on line {line_number}") from exc
    return ScenarioCurve(tuple(points))

