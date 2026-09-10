"""Read-only evaluation of B wind commands against C actions and later A feedback.

The formulas here are explicitly a small-group evaluation rule, not a course
formula. No TCP or database writes are performed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite


WEIGHTS = {
    "start_stop": 0.30,
    "power_tracking": 0.35,
    "pitch_response": 0.20,
    "capability_safety": 0.15,
}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00") if value.endswith("Z") else datetime.fromisoformat(value)


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class WindExecutionResult:
    dispatch_command_id: int
    outbox_id: int | None
    session_id: str
    dispatch_step: int
    feedback_step: int | None
    c_wind_action_seq: int | None
    dispatch_created_at_utc: str
    feedback_sampled_at_utc: str | None
    wind_action_applied_at_utc: str | None
    response_latency_s: float | None
    b_wind_enable: bool
    b_wind_target_kw: float
    c_controller_wind_enable: bool | None
    c_pitch_target_deg: float | None
    c_wind_available_kw: float | None
    c_wind_operating_limit_kw: float | None
    a_wind_running: bool | None
    a_wind_actual_kw: float | None
    a_pitch_actual_deg: float | None
    a_fault: bool | None
    start_stop_score: float | None
    power_tracking_score: float | None
    pitch_response_score: float | None
    capability_safety_score: float | None
    total_score: float | None
    verdict: str
    reason: str


def _score_start_stop(b_enable: bool, c_enable: bool, a_running: bool, fault: bool, available: float, limit: float) -> float:
    if fault or available <= 0.0 or limit <= 0.0:
        return 100.0 if (not a_running and not c_enable) else 80.0
    return 100.0 if (b_enable == c_enable == a_running) else 50.0


def _score_power(target: float, actual: float, available: float, limit: float, *, fault: bool) -> float:
    if fault or available <= 0.0 or limit <= 0.0:
        return 100.0 if actual <= 1e-6 else 70.0
    effective_target = min(max(target, 0.0), limit, available)
    error = abs(actual - effective_target)
    scale = max(1.0, min(100.0, available))
    return _clamp_score(100.0 * (1.0 - error / scale))


def _score_pitch(target: float, actual: float) -> float:
    return _clamp_score(100.0 * (1.0 - abs(actual - target) / 90.0))


def _score_capability(actual: float, available: float, limit: float, target: float, fault: bool) -> float:
    violations = 0
    if available < -1e-6 or limit < -1e-6 or limit > available + 1e-6:
        violations += 1
    if actual < -1e-6 or actual > min(available, limit) + 1e-6:
        violations += 1
    if not fault and target > limit + 1e-6:
        # B target exceeding the C-reported limit is an input transition; do
        # not penalize the execution evaluator as an A actual constraint fault.
        violations += 0
    return max(0.0, 100.0 - 50.0 * violations)


def evaluate_pair(command, feedback) -> WindExecutionResult:
    """Evaluate one B dispatch against one later complete A state."""
    session_id = str(command["session_id"])
    if session_id != str(feedback["session_id"]):
        raise ValueError("cross-session wind execution pair")
    dispatch_step = int(command["step"])
    feedback_step = int(feedback["step"])
    if feedback_step <= dispatch_step:
        raise ValueError("feedback state must advance beyond dispatch step")
    if str(feedback["extension_status"]) != "complete":
        raise ValueError("feedback extension is incomplete")
    action_step = feedback["last_wind_action_step"]
    if action_step is None or int(action_step) < dispatch_step:
        raise ValueError("C action does not reference a step at or after dispatch input")
    age = float(feedback["received_age_s"])
    if not isfinite(age) or age < 0:
        raise ValueError("invalid feedback state age")

    b_enable = bool(command["wind_enable"])
    b_target = float(command["wind_target_kw"])
    c_enable = bool(feedback["controller_wind_enable"])
    c_pitch = float(feedback["pitch_target_deg"])
    available = float(feedback["wind_available_kw"])
    limit = float(feedback["wind_operating_limit_kw"])
    a_running = bool(feedback["wind_running"])
    a_actual = float(feedback["wind_actual_kw"])
    a_pitch = float(feedback["pitch_actual_deg"])
    fault = bool(feedback["fault"])

    if available < 0 or limit < 0 or limit > available + 1e-6 or a_actual < 0:
        raise ValueError("feedback capability values violate constraints")

    applied = feedback["wind_action_applied_at_utc"]
    response_latency = None
    if applied:
        response_latency = max(0.0, (_utc(str(applied)) - _utc(str(command["created_at_utc"]))).total_seconds())

    start_stop = _score_start_stop(b_enable, c_enable, a_running, fault, available, limit)
    power = _score_power(b_target, a_actual, available, limit, fault=fault)
    pitch = _score_pitch(c_pitch, a_pitch)
    capability = _score_capability(a_actual, available, limit, b_target, fault)
    total = (
        WEIGHTS["start_stop"] * start_stop
        + WEIGHTS["power_tracking"] * power
        + WEIGHTS["pitch_response"] * pitch
        + WEIGHTS["capability_safety"] * capability
    )
    return WindExecutionResult(
        dispatch_command_id=int(command["id"]),
        outbox_id=None,
        session_id=session_id,
        dispatch_step=dispatch_step,
        feedback_step=feedback_step,
        c_wind_action_seq=None if feedback["last_wind_action_seq"] is None else int(feedback["last_wind_action_seq"]),
        dispatch_created_at_utc=str(command["created_at_utc"]),
        feedback_sampled_at_utc=str(feedback["sampled_at_utc"]),
        wind_action_applied_at_utc=None if applied is None else str(applied),
        response_latency_s=response_latency,
        b_wind_enable=b_enable,
        b_wind_target_kw=b_target,
        c_controller_wind_enable=c_enable,
        c_pitch_target_deg=c_pitch,
        c_wind_available_kw=available,
        c_wind_operating_limit_kw=limit,
        a_wind_running=a_running,
        a_wind_actual_kw=a_actual,
        a_pitch_actual_deg=a_pitch,
        a_fault=fault,
        start_stop_score=start_stop,
        power_tracking_score=power,
        pitch_response_score=pitch,
        capability_safety_score=capability,
        total_score=total,
        verdict="pass" if total >= 70 else "needs_improvement",
        reason="complete B→C→A feedback chain evaluated by small-group rule",
    )


def insufficient(command, reason: str) -> WindExecutionResult:
    """Return a non-scored result; no fake zeros are inserted."""
    return WindExecutionResult(
        dispatch_command_id=int(command["id"]), outbox_id=None, session_id=str(command["session_id"]),
        dispatch_step=int(command["step"]), feedback_step=None, c_wind_action_seq=None,
        dispatch_created_at_utc=str(command["created_at_utc"]), feedback_sampled_at_utc=None,
        wind_action_applied_at_utc=None, response_latency_s=None,
        b_wind_enable=bool(command["wind_enable"]), b_wind_target_kw=float(command["wind_target_kw"]),
        c_controller_wind_enable=None, c_pitch_target_deg=None, c_wind_available_kw=None, c_wind_operating_limit_kw=None,
        a_wind_running=None, a_wind_actual_kw=None, a_pitch_actual_deg=None, a_fault=None,
        start_stop_score=None, power_tracking_score=None, pitch_response_score=None, capability_safety_score=None,
        total_score=None, verdict="insufficient_data", reason=reason,
    )


def find_feedback(conn, command, *, max_age_s: float) -> tuple[object | None, str | None]:
    """Find the first valid later state in the same session."""
    rows = conn.execute(
        """SELECT * FROM state_history
           WHERE session_id=? AND step>?
           ORDER BY step,id""",
        (command["session_id"], command["step"]),
    ).fetchall()
    for row in rows:
        if str(row["extension_status"]) != "complete":
            continue
        if float(row["received_age_s"]) > max_age_s:
            continue
        action_step = row["last_wind_action_step"]
        if action_step is None or int(action_step) < int(command["step"]):
            continue
        return row, None
    return None, "no complete post-dispatch feedback within session"
