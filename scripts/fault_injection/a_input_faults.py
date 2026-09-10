"""Generate isolated invalid A input fixtures without damaging live data."""

from __future__ import annotations

import argparse
from pathlib import Path


FIXTURES = {
    "missing_column.csv": "sim_time_s,wind_speed_mps\n0,8\n10,9\n",
    "negative_value.csv": (
        "sim_time_s,wind_speed_mps,load_power_kw\n0,-1,50\n10,8,60\n"
    ),
    "unsorted_time.csv": (
        "sim_time_s,wind_speed_mps,load_power_kw\n10,8,60\n0,7,50\n"
    ),
    "duplicate_time.csv": (
        "sim_time_s,wind_speed_mps,load_power_kw\n0,7,50\n0,8,60\n"
    ),
    "invalid_config.json": (
        '{"schema_version":1,"simulation":{"start_s":0,"end_s":0,'
        '"step_s":-1,"poll_interval_s":0}}\n'
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create invalid A CSV/config/database files")
    parser.add_argument("--output", type=Path, default=Path("runtime/fault_inputs"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in FIXTURES.items():
        path = args.output / name
        path.write_text(content, encoding="utf-8", newline="\n")
        print(path.resolve())
    corrupt = args.output / "corrupt_grid.db"
    corrupt.write_bytes(b"not a sqlite database\n")
    print(corrupt.resolve())
    print("Load these copies through A's UI; do not replace the live grid.db.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
