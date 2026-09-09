"""Read-only EMS dispatch evaluation.

This module deliberately does not participate in TCP transport or dispatch control.
It evaluates data already persisted by B/A and is therefore safe to use from the GUI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class EvaluationResult:
    period_label: str
    sample_count: int
    balance_score: float
    wind_score: float
    diesel_score: float
    constraint_score: float
    tracking_score: float
    response_score: float
    ems_score: float
    system_score: float
    overall_score: float
    avg_balance_error_kw: float
    wind_utilization_pct: float
    diesel_share_pct: float
    constraint_violations: int
    tracking_error_kw: float
    dispatch_count: int
    ack_success_pct: float
    avg_ack_ms: Optional[float]
    max_ack_ms: Optional[float]
    unserved_kw: float
    surplus_kw: float

    @property
    def grade(self) -> str:
        if self.overall_score >= 90: return "优秀"
        if self.overall_score >= 80: return "良好"
        if self.overall_score >= 70: return "合格"
        return "需改进"


def _score_balance(error: float) -> float:
    if error <= 1: return 100.0
    if error <= 3: return 90.0
    if error <= 5: return 80.0
    if error <= 10: return 60.0
    return 40.0


def _score_wind(util: float) -> float:
    if util >= 90: return 100.0
    if util >= 80: return 90.0
    if util >= 70: return 80.0
    if util >= 60: return 70.0
    return max(40.0, util)


def _score_diesel(share: float) -> float:
    if share <= 20: return 100.0
    if share <= 30: return 95.0
    if share <= 40: return 85.0
    if share <= 50: return 75.0
    return 60.0


def _score_tracking(error: float) -> float:
    if error <= 1: return 100.0
    if error <= 3: return 95.0
    if error <= 5: return 85.0
    if error <= 10: return 70.0
    return 50.0


def _score_response(success: float, avg_ms: Optional[float]) -> float:
    if avg_ms is None: return success
    latency = 100.0 if avg_ms <= 100 else 90.0 if avg_ms <= 300 else 75.0 if avg_ms <= 1000 else 50.0
    return 0.7 * success + 0.3 * latency


def evaluate_repository(repo, period: str = "5m") -> EvaluationResult:
    """Evaluate persisted state/dispatch data without changing any database rows."""
    minutes = {"1m": 1, "5m": 5}.get(period)
    with repo.connection() as conn:
        if period == "session":
            states = conn.execute("SELECT * FROM state_history WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id").fetchall()
            commands = conn.execute("SELECT * FROM dispatch_commands WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id").fetchall()
            label = "本次 Session"
        elif minutes is None:
            states = conn.execute("SELECT * FROM state_history ORDER BY id").fetchall()
            commands = conn.execute("SELECT * FROM dispatch_commands ORDER BY id").fetchall()
            label = "全部历史"
        else:
            states = conn.execute("SELECT * FROM state_history WHERE julianday(received_at_utc) >= julianday('now', ?) ORDER BY id", (f"-{minutes} minutes",)).fetchall()
            commands = conn.execute("SELECT * FROM dispatch_commands WHERE julianday(created_at_utc) >= julianday('now', ?) ORDER BY id", (f"-{minutes} minutes",)).fetchall()
            label = f"最近 {minutes} 分钟"

        sample_count = len(states)
        if states:
            balance_errors = [abs(float(r['load_power_kw']) - float(r['wind_actual_kw']) - float(r['diesel_actual_kw'])) for r in states]
            avg_balance = sum(balance_errors) / len(balance_errors)
            available_total = sum(float(r['wind_available_kw']) for r in states)
            wind_util = sum(float(r['wind_actual_kw']) for r in states) / available_total * 100.0 if available_total > 1e-9 else 0.0
            load_total = sum(float(r['load_power_kw']) for r in states)
            diesel_share = sum(float(r['diesel_actual_kw']) for r in states) / load_total * 100.0 if load_total > 1e-9 else 0.0
            unserved = sum(max(float(r['load_power_kw']) - float(r['wind_actual_kw']) - float(r['diesel_actual_kw']), 0.0) for r in states) / len(states)
            surplus = sum(max(float(r['wind_actual_kw']) + float(r['diesel_actual_kw']) - float(r['load_power_kw']), 0.0) for r in states) / len(states)
            violations = sum(1 for r in states if float(r['wind_actual_kw']) > float(r['wind_operating_limit_kw']) + 1e-6 or float(r['wind_operating_limit_kw']) > float(r['wind_available_kw']) + 1e-6)
        else:
            avg_balance = wind_util = diesel_share = unserved = surplus = 0.0
            violations = 0

        ack_rows = [r for r in commands if r['ack_accepted'] is not None]
        accepted = sum(1 for r in ack_rows if int(r['ack_accepted']) == 1)
        success = accepted * 100.0 / len(ack_rows) if ack_rows else 0.0
        latencies = []
        for r in ack_rows:
            if r['ack_received_at_utc'] and r['created_at_utc']:
                try:
                    a = datetime.fromisoformat(str(r['created_at_utc']).replace('Z', '+00:00'))
                    b = datetime.fromisoformat(str(r['ack_received_at_utc']).replace('Z', '+00:00'))
                    latencies.append(max(0.0, (b - a).total_seconds() * 1000.0))
                except ValueError:
                    pass
        avg_ms = sum(latencies) / len(latencies) if latencies else None
        max_ms = max(latencies) if latencies else None

        tracking_rows = conn.execute("""SELECT c.wind_target_kw,c.diesel_target_kw,s.wind_actual_kw,s.diesel_actual_kw
            FROM dispatch_commands c JOIN state_history s ON s.session_id=c.session_id AND s.step=c.step
            WHERE c.ack_accepted=1""").fetchall()
        tracking_error = (sum((abs(float(r['wind_target_kw']) - float(r['wind_actual_kw'])) + abs(float(r['diesel_target_kw']) - float(r['diesel_actual_kw']))) / 2 for r in tracking_rows) / len(tracking_rows)) if tracking_rows else 0.0

    balance_score = _score_balance(avg_balance)
    wind_score = _score_wind(wind_util)
    diesel_score = _score_diesel(diesel_share)
    constraint_score = 100.0 if violations == 0 else max(0.0, 100.0 - 10.0 * violations)
    tracking_score = _score_tracking(tracking_error)
    response_score = _score_response(success, avg_ms)
    ems_score = 0.15 * diesel_score + 0.15 * constraint_score + 0.10 * tracking_score + 0.10 * response_score + 0.50 * (0.5 * balance_score + 0.5 * wind_score)
    system_score = 0.30 * balance_score + 0.20 * wind_score + 0.15 * diesel_score + 0.15 * constraint_score + 0.10 * tracking_score + 0.10 * response_score
    overall = 0.5 * ems_score + 0.5 * system_score
    return EvaluationResult(label, sample_count, balance_score, wind_score, diesel_score, constraint_score, tracking_score, response_score, ems_score, system_score, overall, avg_balance, wind_util, diesel_share, violations, tracking_error, len(commands), success, avg_ms, max_ms, unserved, surplus)
