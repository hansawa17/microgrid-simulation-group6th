"""Lifecycle helpers for A's GUI-independent worker processes.

The GUI may be closed and reopened without owning either worker.  A small
file lock prevents duplicate simulation/TCP processes for the same grid.db;
JSON status files provide PID and heartbeat information for reattachment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Mapping, Sequence
import uuid


SERVICE_ROLES = frozenset({"simulation", "tcp"})
SERVICE_HEARTBEAT_STALE_S = 5.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _check_role(role: str) -> str:
    if role not in SERVICE_ROLES:
        raise ValueError(f"unsupported A service role: {role}")
    return role


def _service_path(db_path: str | Path, role: str, suffix: str) -> Path:
    database = Path(db_path).resolve()
    return database.parent / f".{database.name}.a-{_check_role(role)}.{suffix}"


def service_log_path(db_path: str | Path) -> Path:
    database = Path(db_path).resolve()
    return database.parent / f".{database.name}.a-services.log"


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            encoding="utf-8",
        )
        # Windows can briefly deny replacement while a GUI/antivirus reader
        # has the old JSON open.  Keep the update atomic and retry that narrow
        # sharing race instead of letting a monitoring write kill the worker.
        for attempt in range(50):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.01)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class ServiceStatus:
    role: str
    state: str
    pid: int | None
    instance_id: str | None
    started_at_utc: str | None
    heartbeat_at_utc: str | None
    heartbeat_age_s: float | None
    detail: str
    metadata: dict[str, object]
    active: bool


def read_service_status(
    db_path: str | Path,
    role: str,
    *,
    stale_after_s: float = SERVICE_HEARTBEAT_STALE_S,
) -> ServiceStatus:
    status_path = _service_path(db_path, role, "status.json")
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("service status must be an object")
        modified_age = max(0.0, time.time() - status_path.stat().st_mtime)
        state = str(payload.get("state", "unknown"))
        active = state in {"starting", "running", "stopping"} and modified_age <= stale_after_s
        raw_pid = payload.get("pid")
        pid = raw_pid if isinstance(raw_pid, int) and raw_pid > 0 else None
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return ServiceStatus(
            role=role,
            state=state,
            pid=pid,
            instance_id=str(payload["instance_id"]) if payload.get("instance_id") else None,
            started_at_utc=str(payload["started_at_utc"]) if payload.get("started_at_utc") else None,
            heartbeat_at_utc=str(payload["heartbeat_at_utc"]) if payload.get("heartbeat_at_utc") else None,
            heartbeat_age_s=modified_age,
            detail=str(payload.get("detail", "")),
            metadata={str(key): value for key, value in metadata.items()},
            active=active,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ServiceStatus(role, "stopped", None, None, None, None, None, "no active heartbeat", {}, False)


class ServiceAlreadyRunning(RuntimeError):
    pass


class ServiceLease:
    """Hold a process-lifetime lock and publish a renewable heartbeat."""

    def __init__(
        self,
        db_path: str | Path,
        role: str,
        *,
        detail: str = "",
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.role = _check_role(role)
        self.detail = detail
        self.metadata = dict(metadata or {})
        self.instance_id = uuid.uuid4().hex
        self.started_at_utc = _utc_now()
        self._created_at_epoch = time.time()
        self._lock_handle = None
        self._last_heartbeat = 0.0

    @property
    def status_path(self) -> Path:
        return _service_path(self.db_path, self.role, "status.json")

    @property
    def stop_path(self) -> Path:
        return _service_path(self.db_path, self.role, "stop")

    def _lock(self) -> None:
        path = _service_path(self.db_path, self.role, "lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            handle.close()
            raise ServiceAlreadyRunning(
                f"A {self.role} service is already running for {self.db_path}"
            ) from error
        self._lock_handle = handle

    def _unlock(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._lock_handle = None

    def _publish(self, state: str, detail: str | None = None) -> None:
        _atomic_json(
            self.status_path,
            {
                "role": self.role,
                "state": state,
                "pid": os.getpid(),
                "instance_id": self.instance_id,
                "started_at_utc": self.started_at_utc,
                "heartbeat_at_utc": _utc_now(),
                "detail": self.detail if detail is None else detail,
                "metadata": self.metadata,
            },
        )
        self._last_heartbeat = time.monotonic()

    def __enter__(self) -> "ServiceLease":
        self._lock()
        try:
            # Discard only a stop request left by an older service instance.
            # A request written after this process was created must win, even
            # when it lands during the short launch-to-lock window.
            try:
                if self.stop_path.stat().st_mtime < self._created_at_epoch:
                    self.stop_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            self._publish("starting")
        except Exception:
            self._unlock()
            raise
        return self

    def heartbeat(self, detail: str | None = None, *, force: bool = False) -> None:
        if force or time.monotonic() - self._last_heartbeat >= 0.5:
            try:
                self._publish("running", detail)
            except OSError:
                # Status reporting is observability, not the simulation itself.
                # Keep the worker alive and retry on the next heartbeat.
                pass

    def stop_requested(self) -> bool:
        return self.stop_path.is_file()

    def __exit__(self, error_type, error, _traceback) -> None:
        try:
            detail = "clean exit" if error is None else f"{type(error).__name__}: {error}"
            self._publish("stopped", detail)
            self.stop_path.unlink(missing_ok=True)
        finally:
            self._unlock()


def request_service_stop(db_path: str | Path, role: str) -> ServiceStatus:
    stop_path = _service_path(db_path, role, "stop")
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text(_utc_now(), encoding="utf-8")
    return read_service_status(db_path, role)


def launch_background_process(
    db_path: str | Path,
    role: str,
    command: Sequence[str],
    *,
    working_directory: str | Path,
) -> int | None:
    """Launch one detached service unless a fresh heartbeat already exists."""

    status = read_service_status(db_path, role)
    if status.active:
        return status.pid
    log_path = service_log_path(db_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    popen_options: dict[str, object] = {}
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        popen_options["start_new_session"] = True
    with log_path.open("ab", buffering=0) as output:
        process = subprocess.Popen(
            list(command),
            cwd=str(Path(working_directory).resolve()),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
            **popen_options,
        )
    # Keep a waiter alive while the launcher itself remains open.  This avoids
    # leaking a POSIX zombie and suppresses misleading Popen ResourceWarnings;
    # the daemon waiter never owns or terminates the detached worker.
    threading.Thread(target=process.wait, name=f"a-{role}-reaper", daemon=True).start()
    return int(process.pid)
