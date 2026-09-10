# B 风机指令执行评价

本页对应 `docs/wind-execution-status-extension.md` 的 B 侧落地。它是独立于五项 EMS 综合评分的“小组评价规则”，不是课程原公式。

## 数据链

`B dispatch → A accepted C wind_action → A 后续 state → B ems.db`

B 只发送原有四个 dispatch 字段：`wind_target_kw`、`diesel_target_kw`、`wind_enable`、`diesel_enable`。B 不写 `pitch_target_deg` 或任何 actual 字段。A→B state 中的 `controller_wind_enable`、`pitch_target_deg`、动作 seq/step 和 `wind_action_applied_at_utc` 用于记录 C 动作和 A 的真实应用时间。

## 关联

只允许同一 `session_id`。从 B dispatch 使用的 `step` 开始寻找第一条 `step` 严格递增、扩展完整、`last_wind_action_step >= dispatch step` 且 `received_age_s` 不超过运行配置的后续 state。跨 session、旧 step、只有 ACK、状态过期或扩展字段不足都返回 `insufficient_data`，不伪造 0 分。

## 评分

| 项目 | 权重 |
|---|---:|
| 启停执行一致性 | 30% |
| 功率跟踪 | 35% |
| 桨距响应 | 20% |
| 能力与安全约束 | 15% |

无风、切出、高风、fault、`wind_operating_limit_kw=0` 时，C 的保护性停机可以是合理执行；目标突降后的实际功率爬坡阶段不会因为 actual 暂时高于新 target 直接判安全约束失败。

## GUI

使用：

```powershell
python -m B_dispatch wind-evaluation
```

页面支持 Session 与 UTC 时间段查询，展示 B 指令、C 动作、A 实际反馈、响应时延、四项分数、总分、结论和原因。评价页面只读，不发送 TCP，也不修改 dispatch。

## 迁移

`EMSRepository.initialize()` 将旧 `ems.db` 原位升级到 schema 6；新增扩展列和 `wind_execution_evaluation` 表，已有参数、state history、outbox 和日志按 `ON CONFLICT DO NOTHING` 保留，不重复初始化覆盖用户参数。真实 `data/runtime/ems.db` 不进入 Git。
