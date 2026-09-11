"""Manufacture abrupt-close or application-idle failures against A TCP."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time


def close_abruptly(connection: socket.socket) -> None:
    """Close with TCP RST so the server exercises its connection-error path."""

    try:
        connection.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0)
        )
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise A connection-state handling")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--source", choices=("B", "C"), default="B")
    parser.add_argument("--mode", choices=("abrupt", "idle"), default="abrupt")
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=35.0,
        help="Use a value above A's configured server idle timeout",
    )
    args = parser.parse_args()
    request = {
        "version": 1,
        "type": "state_request",
        "source": args.source,
        "target": "A",
        "session_id": None,
        "seq": 1,
        "step": 0,
        "sim_time_s": 0.0,
        "payload": {"full": True},
    }
    connection = socket.create_connection((args.host, args.port), timeout=5.0)
    connection.settimeout(5.0)
    connection.sendall(
        (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    )
    response = connection.recv(8192)
    print("initial state received:", bool(response))
    if args.mode == "idle":
        print(f"holding connection idle for {args.idle_seconds:.1f}s")
        time.sleep(args.idle_seconds)
        connection.close()
    else:
        close_abruptly(connection)
    print("connection closed; A GUI must turn red and show a non-blocking warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
