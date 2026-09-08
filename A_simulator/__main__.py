"""Command-line entry points for independently running A's processes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from .config import load_config
from .repository import Repository
from .scenario import load_scenario_csv
from .server import serve


MODULE_DIR = Path(__file__).resolve().parent
ROOT_DIR = MODULE_DIR.parent
DEFAULT_CONFIG = MODULE_DIR / "config.example.json"
DEFAULT_SCENARIO = MODULE_DIR / "scenarios" / "antarctic_10min.csv"
DEFAULT_DB = ROOT_DIR / "data" / "runtime" / "grid.db"


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Student A microgrid simulator foundation")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create grid.db from explicit config and scenario files")
    init.add_argument("--db", type=Path, default=DEFAULT_DB)
    init.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    init.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)

    control = sub.add_parser("control", help="start, pause, resume or stop simulation")
    control.add_argument("action", choices=("start", "pause", "resume", "stop"))
    control.add_argument("--db", type=Path, default=DEFAULT_DB)

    step = sub.add_parser("step", help="advance one simulation step")
    step.add_argument("--db", type=Path, default=DEFAULT_DB)

    run = sub.add_parser("run", help="run the database-only calculation loop")
    run.add_argument("--db", type=Path, default=DEFAULT_DB)
    run.add_argument("--steps", type=int, help="stop this process after N successful steps")

    server = sub.add_parser("serve", help="run the independent TCP communication process")
    server.add_argument("--db", type=Path, default=DEFAULT_DB)
    server.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    server.add_argument("--bind")
    server.add_argument("--port", type=int)

    show = sub.add_parser("show", help="print current runtime and state")
    show.add_argument("--db", type=Path, default=DEFAULT_DB)

    gui = sub.add_parser("gui", help="run the PyQt6 CSV curve and simulator console")
    gui.add_argument("--db", type=Path, default=DEFAULT_DB)
    gui.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    gui.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Repository(args.db)
    if args.command == "init":
        config = load_config(args.config)
        session_id = repository.initialize(config, load_scenario_csv(args.scenario))
        print(f"initialized {args.db} (session_id={session_id})")
        if config.parameter_status != "confirmed":
            print(f"WARNING: parameter_status={config.parameter_status}; results are mock/example only")
        return 0
    if args.command == "control":
        print(repository.set_status(args.action))
        return 0
    if args.command == "step":
        state = repository.step_once()
        _print_json(None if state is None else asdict(state))
        return 0
    if args.command == "show":
        _print_json({"runtime": repository.runtime(), "state": asdict(repository.get_state())})
        return 0
    if args.command == "gui":
        from .gui import run_gui

        return run_gui(db_path=args.db, config_path=args.config, scenario_path=args.scenario)
    if args.command == "serve":
        config = load_config(args.config)
        serve(
            repository,
            args.bind if args.bind is not None else config.server_bind,
            args.port if args.port is not None else config.server_port,
            config.max_frame_bytes,
        )
        return 0
    if args.command == "run":
        if args.steps is not None and args.steps <= 0:
            raise ValueError("--steps must be greater than zero")
        completed_steps = 0
        next_poll = time.monotonic()
        while True:
            wait_s = next_poll - time.monotonic()
            if wait_s > 0:
                time.sleep(wait_s)
            runtime = repository.runtime()
            if runtime["status"] in {"completed", "stopped"}:
                break
            poll_interval_s = float(runtime["poll_interval_s"])
            next_poll = max(next_poll + poll_interval_s, time.monotonic())
            if runtime["status"] == "running":
                state = repository.step_once()
                if state is not None:
                    _print_json(asdict(state))
                    completed_steps += 1
                    if args.steps is not None and completed_steps >= args.steps:
                        break
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
