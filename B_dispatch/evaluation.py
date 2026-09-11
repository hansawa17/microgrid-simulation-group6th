"""Read-only EMS and wind-execution evaluation."""
from __future__ import annotations

from dataclasses import dataclass

from .wind_execution import evaluate_pair, find_feedback, insufficient

# 新版 EMS 评价：两个维度，各占 50%
EVALUATION_WEIGHTS = {"diesel_bounds": 0.50, "green_ratio": 0.50}

SCORING_RULES = {
    "diesel_bounds": {
        "title": "柴发出力上下限",
        "weight": 50,
        "metric": "柴发实际出力是否处于 [下限, 上限]",
        "rules": [
            "停机（出力≈0）或出力在 [下限, 上限] 内：100 分",
            "出力超过上限，或运行中低于下限：40 分（不合格）",
        ],
    },
    "green_ratio": {
        "title": "绿电比例",
        "weight": 50,
        "metric": "风电实际出力 / (风电 + 柴发实际出力)",
        "rules": [
            "绿电比例按 0% ~ 100% 线性映射为 0 ~ 100 分",
            "全风电 = 100 分，全柴发 = 0 分",
        ],
    },
}


@dataclass(frozen=True)
class EvaluationResult:
    period_label: str
    sample_count: int
    diesel_bounds_score: float
    green_ratio_score: float
    ems_score: float
    system_score: float
    overall_score: float
    diesel_violations: int
    green_ratio_pct: float
    wind_actual_kw: float
    diesel_actual_kw: float
    dispatch_count: int
    wind_execution_count: int
    wind_execution_score: float | None
    wind_execution_verdict: str
    wind_execution_reason: str

    @property
    def grade(self) -> str:
        return "优秀" if self.overall_score >= 90 else "良好" if self.overall_score >= 80 else "合格" if self.overall_score >= 70 else "需改进"


def _score_diesel_bounds(violations: int) -> float:
    return 100.0 if violations == 0 else 40.0


def _score_green_ratio(ratio: float) -> float:
    return max(0.0, min(100.0, ratio * 100.0))


def _window_sql(period: str):
    m = {"1m": 1, "5m": 5}.get(period)
    return m


def evaluate_repository(repo, period: str = "5m") -> EvaluationResult:
    m = _window_sql(period)
    with repo.connection() as conn:
        if period == "session":
            states = conn.execute(
                "SELECT * FROM state_history WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id"
            ).fetchall()
            commands = conn.execute(
                "SELECT * FROM dispatch_commands WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id"
            ).fetchall()
            label = "本次 Session"
        elif m is None:
            states = conn.execute("SELECT * FROM state_history ORDER BY id").fetchall()
            commands = conn.execute("SELECT * FROM dispatch_commands ORDER BY id").fetchall()
            label = "全部历史"
        else:
            window = f"-{m} minutes"
            states = conn.execute(
                "SELECT * FROM state_history WHERE julianday(received_at_utc)>=julianday('now',?) ORDER BY id",
                (window,),
            ).fetchall()
            commands = conn.execute(
                "SELECT * FROM dispatch_commands WHERE julianday(created_at_utc)>=julianday('now',?) ORDER BY id",
                (window,),
            ).fetchall()
            label = f"最近 {m} 分钟"

        # 维度一：柴发实际出力是否符合上下限
        ph = conn.execute(
            "SELECT diesel_min_kw, diesel_max_kw FROM physical_parameters WHERE id=1"
        ).fetchone()
        diesel_min = float(ph["diesel_min_kw"]) if ph is not None else 0.0
        diesel_max = float(ph["diesel_max_kw"]) if ph is not None else float("inf")

        diesel_violations = 0
        wind_sum = 0.0
        diesel_sum = 0.0
        for row in states:
            d = float(row["diesel_actual_kw"])
            w = float(row["wind_actual_kw"])
            wind_sum += w
            diesel_sum += d
            if d > diesel_max + 1e-6:
                diesel_violations += 1
            elif d > 1e-6 and d < diesel_min - 1e-6:
                diesel_violations += 1

        diesel_bounds_score = _score_diesel_bounds(diesel_violations)

        # 维度二：绿电比例
        total_generation = wind_sum + diesel_sum
        green_ratio = (wind_sum / total_generation) if total_generation > 1e-9 else 0.0
        green_ratio_score = _score_green_ratio(green_ratio)

        ems_score = (
            EVALUATION_WEIGHTS["diesel_bounds"] * diesel_bounds_score
            + EVALUATION_WEIGHTS["green_ratio"] * green_ratio_score
        )
        overall_score = ems_score

        # 风机执行评价（独立维度，保留原逻辑）
        rt = conn.execute(
            "SELECT max_state_age_s FROM ems_runtime_config WHERE id=1"
        ).fetchone()
        age = float(rt["max_state_age_s"]) if rt is not None else 2.0

        exec_rows = []
        exec_cmds = [r for r in commands if r["status"] == "accepted" and (r["ack_accepted"] in (1, True))]
        for cmd in exec_cmds:
            fb, reason = find_feedback(conn, cmd, max_age_s=age)
            result = insufficient(cmd, reason) if fb is None else evaluate_pair(cmd, fb)
            if fb is not None:
                exec_rows.append(result)

        if exec_rows:
            exec_score = sum(float(x.total_score) for x in exec_rows if x.total_score is not None) / len(exec_rows)
            exec_verdict = "pass" if exec_score >= 70 else "needs_improvement"
            exec_reason = f"{len(exec_rows)} 条完整 B→C→A 反馈，按小组评价规则汇总"
        else:
            exec_score = None
            exec_verdict = "insufficient_data"
            exec_reason = "无完整 B dispatch→C action→A 后续 state 反馈"
        system_score = exec_score if exec_score is not None else 0.0

        return EvaluationResult(
            period_label=label,
            sample_count=len(states),
            diesel_bounds_score=diesel_bounds_score,
            green_ratio_score=green_ratio_score,
            ems_score=ems_score,
            system_score=system_score,
            overall_score=overall_score,
            diesel_violations=diesel_violations,
            green_ratio_pct=green_ratio * 100.0,
            wind_actual_kw=(wind_sum / len(states)) if states else 0.0,
            diesel_actual_kw=(diesel_sum / len(states)) if states else 0.0,
            dispatch_count=len(commands),
            wind_execution_count=len(exec_rows),
            wind_execution_score=exec_score,
            wind_execution_verdict=exec_verdict,
            wind_execution_reason=exec_reason,
        )
