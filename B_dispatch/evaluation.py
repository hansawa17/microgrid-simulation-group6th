"""Read-only EMS dispatch evaluation.

This module deliberately does not participate in TCP transport or dispatch control.
It evaluates data already persisted by B/A and is therefore safe to use from the GUI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# Main evaluation weights. These five items are the only components of the
# comprehensive EMS evaluation score; communication ACK performance is not scored.
EVALUATION_WEIGHTS = {
    "balance": 0.30,
    "wind": 0.25,
    "diesel": 0.15,
    "constraint": 0.15,
    "tracking": 0.15,
}

# The GUI exposes these rules through a read-only dropdown next to each score.
SCORING_RULES = {
    "balance": {
        "title": "供需平衡",
        "weight": 30,
        "metric": "平均绝对功率不平衡",
        "rules": [
            "≤ 1 kW：100 分",
            "≤ 3 kW：90 分",
            "≤ 5 kW：80 分",
            "≤ 10 kW：60 分",
            "> 10 kW：40 分",
        ],
    },
    "wind": {
        "title": "风能利用",
        "weight": 25,
        "metric": "实际风电 / 可利用风电",
        "rules": [
            "≥ 90%：100 分",
            "≥ 80%：90 分",
            "≥ 70%：80 分",
            "≥ 60%：70 分",
            "< 60%：按利用率计分，最低 40 分",
        ],
    },
    "diesel": {
        "title": "柴油经济性",
        "weight": 15,
        "metric": "柴油实际供电 / 负荷",
        "rules": [
            "≤ 20%：100 分",
            "≤ 30%：95 分",
            "≤ 40%：85 分",
            "≤ 50%：75 分",
            "> 50%：60 分",
        ],
    },
    "constraint": {
        "title": "运行约束",
        "weight": 15,
        "metric": "风电运行边界违反次数",
        "rules": [
            "0 次：100 分",
            "每增加 1 次：扣 10 分",
            "最低 0 分",
            "检查 actual ≤ operating limit ≤ available",
        ],
    },
    "tracking": {
        "title": "调度跟踪",
        "weight": 15,
        "metric": "已确认调度的目标与实际平均偏差",
        "rules": [
            "≤ 1 kW：100 分",
            "≤ 3 kW：95 分",
            "≤ 5 kW：85 分",
            "≤ 10 kW：70 分",
            "> 10 kW：50 分",
        ],
    },
}


@dataclass(frozen=True)
class EvaluationResult:
    period_label: str
    sample_count: int
    balance_score: float
    wind_score: float
    diesel_score: float
    constraint_score: float
    tracking_score: float
    ems_score: float
    system_score: float
    overall_score: float
    avg_balance_error_kw: float
    wind_utilization_pct: float
    diesel_share_pct: float
    constraint_violations: int
    tracking_error_kw: float
    dispatch_count: int
    unserved_kw: float
    surplus_kw: float

    @property
    def grade(self) -> str:
        if self.overall_score >= 90:
            return "优秀"
        if self.overall_score >= 80:
            return "良好"
        if self.overall_score >= 70:
            return "合格"
        return "需改进"


def _score_balance(error: float) -> float:
    if error <= 1:
        return 100.0
    if error <= 3:
        return 90.0
    if error <= 5:
        return 80.0
    if error <= 10:
        return 60.0
    return 40.0


def _score_wind(util: float) -> float:
    if util >= 90:
        return 100.0
    if util >= 80:
        return 90.0
    if util >= 70:
        return 80.0
    if util >= 60:
        return 70.0
    return max(40.0, util)


def _score_diesel(share: float) -> float:
    if share <= 20:
        return 100.0
    if share <= 30:
        return 95.0
    if share <= 40:
        return 85.0
    if share <= 50:
        return 75.0
    return 60.0


def _score_tracking(error: float) -> float:
    if error <= 1:
        return 100.0
    if error <= 3:
        return 95.0
    if error <= 5:
        return 85.0
    if error <= 10:
        return 70.0
    return 50.0


def evaluate_repository(repo, period: str = "5m") -> EvaluationResult:
    """Evaluate persisted state/dispatch data without changing any database rows."""
    minutes = {"1m": 1, "5m": 5}.get(period)
    with repo.connection() as conn:
        if period == "session":
            states = conn.execute(
                "SELECT * FROM state_history "
                "WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id"
            ).fetchall()
            commands = conn.execute(
                "SELECT * FROM dispatch_commands "
                "WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id"
            ).fetchall()
            tracking_rows = conn.execute(
                """SELECT c.wind_target_kw,c.diesel_target_kw,s.wind_actual_kw,s.diesel_actual_kw
                   FROM dispatch_commands c
                   JOIN state_history s ON s.session_id=c.session_id AND s.step=c.step
                  WHERE c.ack_accepted=1
                    AND c.session_id=(SELECT session_id FROM current_state WHERE id=1)"""
            ).fetchall()
            label = "本次 Session"
        elif minutes is None:
            states = conn.execute("SELECT * FROM state_history ORDER BY id").fetchall()
            commands = conn.execute("SELECT * FROM dispatch_commands ORDER BY id").fetchall()
            tracking_rows = conn.execute(
                """SELECT c.wind_target_kw,c.diesel_target_kw,s.wind_actual_kw,s.diesel_actual_kw
                   FROM dispatch_commands c
                   JOIN state_history s ON s.session_id=c.session_id AND s.step=c.step
                  WHERE c.ack_accepted=1"""
            ).fetchall()
            label = "全部历史"
        else:
            window = f"-{minutes} minutes"
            states = conn.execute(
                "SELECT * FROM state_history "
                "WHERE julianday(received_at_utc) >= julianday('now', ?) ORDER BY id",
                (window,),
            ).fetchall()
            commands = conn.execute(
                "SELECT * FROM dispatch_commands "
                "WHERE julianday(created_at_utc) >= julianday('now', ?) ORDER BY id",
                (window,),
            ).fetchall()
            tracking_rows = conn.execute(
                """SELECT c.wind_target_kw,c.diesel_target_kw,s.wind_actual_kw,s.diesel_actual_kw
                   FROM dispatch_commands c
                   JOIN state_history s ON s.session_id=c.session_id AND s.step=c.step
                  WHERE c.ack_accepted=1
                    AND julianday(c.created_at_utc) >= julianday('now', ?)""",
                (window,),
            ).fetchall()
            label = f"最近 {minutes} 分钟"

        sample_count = len(states)
        if states:
            balance_errors = [
                abs(float(r["load_power_kw"]) - float(r["wind_actual_kw"]) - float(r["diesel_actual_kw"]
                ))
                for r in states
            ]
            avg_balance = sum(balance_errors) / len(balance_errors)
            available_total = sum(float(r["wind_available_kw"]) for r in states)
            wind_util = (
                sum(float(r["wind_actual_kw"]) for r in states) / available_total * 100.0
                if available_total > 1e-9
                else 0.0
            )
            load_total = sum(float(r["load_power_kw"]) for r in states)
            diesel_share = (
                sum(float(r["diesel_actual_kw"]) for r in states) / load_total * 100.0
                if load_total > 1e-9
                else 0.0
            )
            unserved = sum(
                max(
                    float(r["load_power_kw"])
                    - float(r["wind_actual_kw"])
                    - float(r["diesel_actual_kw"]),
                    0.0,
                )
                for r in states
            ) / len(states)
            surplus = sum(
                max(
                    float(r["wind_actual_kw"]) + float(r["diesel_actual_kw"])
                    - float(r["load_power_kw"]),
                    0.0,
                )
                for r in states
            ) / len(states)
            violations = sum(
                1
                for r in states
                if float(r["wind_actual_kw"]) > float(r["wind_operating_limit_kw"]) + 1e-6
                or float(r["wind_operating_limit_kw"])
                > float(r["wind_available_kw"]) + 1e-6
            )
        else:
            avg_balance = wind_util = diesel_share = unserved = surplus = 0.0
            violations = 0

        tracking_error = (
            sum(
                (
                    abs(float(r["wind_target_kw"]) - float(r["wind_actual_kw"]))
                    + abs(float(r["diesel_target_kw"]) - float(r["diesel_actual_kw"]))
                )
                / 2
                for r in tracking_rows
            )
            / len(tracking_rows)
            if tracking_rows
            else 0.0
        )

    balance_score = _score_balance(avg_balance)
    wind_score = _score_wind(wind_util)
    diesel_score = _score_diesel(diesel_share)
    constraint_score = 100.0 if violations == 0 else max(0.0, 100.0 - 10.0 * violations)
    tracking_score = _score_tracking(tracking_error)

    # Comprehensive score: only the five requested evaluation dimensions.
    overall = (
        EVALUATION_WEIGHTS["balance"] * balance_score
        + EVALUATION_WEIGHTS["wind"] * wind_score
        + EVALUATION_WEIGHTS["diesel"] * diesel_score
        + EVALUATION_WEIGHTS["constraint"] * constraint_score
        + EVALUATION_WEIGHTS["tracking"] * tracking_score
    )

    # Keep separate EMS-strategy and wind-execution views required by the
    # acceptance sheet. They do not add a sixth component to the overall score.
    ems_score = (
        0.35 * balance_score
        + 0.25 * wind_score
        + 0.15 * diesel_score
        + 0.10 * constraint_score
        + 0.15 * tracking_score
    )
    system_score = (
        0.40 * tracking_score
        + 0.35 * constraint_score
        + 0.25 * wind_score
    )

    return EvaluationResult(
        label,
        sample_count,
        balance_score,
        wind_score,
        diesel_score,
        constraint_score,
        tracking_score,
        ems_score,
        system_score,
        overall,
        avg_balance,
        wind_util,
        diesel_share,
        violations,
        tracking_error,
        len(commands),
        unserved,
        surplus,
    )
