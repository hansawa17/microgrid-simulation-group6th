"""Inject reproducible malformed TCP frames into a running A server.

This script never edits grid.db directly.  A should reject each case, write a
WARNING log with occurrence time/session/step, keep serving, and let the GUI
show the new warning on its five-second health refresh.
"""

from __future__ import annotations

import argparse
import json
import socket


CASES = ("invalid-json", "non-finite", "missing-field", "oversize", "half-frame")


def frame_for(case: str, max_frame_bytes: int) -> bytes:
    base = {
        "version": 1,
        "type": "state_request",
        "source": "B",
        "target": "A",
        "session_id": None,
        "seq": 1,
        "step": 0,
        "sim_time_s": 0.0,
        "payload": {"full": True},
    }
    if case == "invalid-json":
        return b'{"version":1,"type":\n'
    if case == "non-finite":
        return json.dumps(base, separators=(",", ":")).replace(
            '"sim_time_s":0.0', '"sim_time_s":NaN'
        ).encode("utf-8") + b"\n"
    if case == "missing-field":
        del base["payload"]
        return json.dumps(base, separators=(",", ":")).encode("utf-8") + b"\n"
    if case == "oversize":
        return (b"x" * (max_frame_bytes + 32)) + b"\n"
    if case == "half-frame":
        return json.dumps(base, separators=(",", ":")).encode("utf-8")[:-8]
    raise ValueError(f"unknown case: {case}")


def inject(host: str, port: int, case: str, max_frame_bytes: int) -> None:
    with socket.create_connection((host, port), timeout=5.0) as connection:
        connection.settimeout(1.0)
        connection.sendall(frame_for(case, max_frame_bytes))
        if case == "half-frame":
            connection.shutdown(socket.SHUT_WR)
        try:
            response = connection.recv(max_frame_bytes + 1)
        except socket.timeout:
            response = b""
    summary = response.decode("utf-8", "replace").strip() or "(no ACK expected)"
    print(f"{case}: {summary}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject malformed frames into A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--max-frame-bytes", type=int, default=4096)
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    args = parser.parse_args()
    selected = CASES if args.case == "all" else (args.case,)
    for case in selected:
        inject(args.host, args.port, case, args.max_frame_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
