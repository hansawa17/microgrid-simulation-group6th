# B 工作流 Prompt：接入风机指令执行评价

你正在仓库 `https://github.com/hansawa17/microgrid-simulation-group6th` 中整改 B EMS。先完整阅读 `AGENTS.md`、`README.md`、`docs/requirements.md`、`common/protocol.md`、`docs/decisions.md`、`docs/parameter-ownership.md`、`docs/wind-execution-status-extension.md` 和 `B_dispatch/README.md`。先检查 `git status`，保留用户已有文件和改动，不重置、不覆盖、不自行合并或发布版本。

## 任务目标

修复新版评分表新增的“风机指令执行评价”：扩展 B 模型和 `ems.db`，兼容接收 A 的新增状态字段，把 B 调度指令、C 风机动作和 A 后续实际反馈关联起来，新增独立的风机执行评分、历史表和 GUI 展示。不得改变 A/B/C 计算职责，不得让 B 写桨距或 actual 字段。

## 必须逐字采用的 TCP 扩展字段

以下字段位于 A→B `state.payload`。字段名、大小写、JSON类型、单位和语义不得自行修改：

| 字段 | JSON 类型 | 约束 | 语义 |
|---|---|---|---|
| `controller_wind_enable` | boolean | 必须为 JSON `true/false` | A 最近一次接受的 C `wind_action.wind_enable`；不是 B `dispatch.wind_enable`，不是 `wind_running` |
| `pitch_target_deg` | number | 有限数，`0..pitch_feather_deg`，单位 deg | C 目标桨距；不得使用 `pitch_actual_deg` 冒充 |
| `last_wind_action_seq` | integer 或 null | 非负整数 | 当前 session 最近一次被 A 接受的 C `wind_action.seq` |
| `last_wind_action_step` | integer 或 null | 非负整数 | 上述 C 动作 envelope 引用的 A 状态 step |
| `wind_action_applied_at_utc` | string 或 null | UTC RFC3339：`YYYY-MM-DDTHH:MM:SS.ffffffZ` | A 校验并应用 C 动作的时间，不是 C 或 B 本机接收时间 |

现有关键字段仍是：`wind_target_kw`、`wind_actual_kw`、`wind_available_kw`、`wind_operating_limit_kw`、`pitch_actual_deg`、`wind_running`、`fault`、`sampled_at_utc`。不要重命名，不要改变 kW/m/s/deg 单位。

兼容规则是强制要求：

- TCP `version` 保持 1。
- 旧 A 可能完全不发送上述五个扩展字段。B 必须继续保存原状态和保持连接，扩展列写 SQL NULL，标记 `legacy_or_incomplete`，评分显示“数据不足”。
- 字段存在但类型错误、NaN/Infinity、越界时仍应拒绝该帧并记录明确错误。
- 解析器必须忽略将来未知的额外 state 字段。
- 不得把缺失、null、false、0 混为一谈。
- 不修改 B→A `dispatch` 四字段；B 绝对不能发送 `pitch_target_deg`。

跨模块检查时必须坚持：C→A `wind_action.payload` 仍然只能包含 `wind_enable`、`pitch_target_deg`、`wind_available_kw`、`wind_operating_limit_kw` 四字段。不得建议把动作 seq、step 或时间塞进 payload，否则 A 会返回 `invalid_wind_action_fields`；这些元数据分别来自公共 envelope 和 A 的接收记录。

## 必须实施

1. 扩展 `GridState`、TCP state 解析、repository 映射和 schema 迁移。
2. `current_state/state_history` 增加：
   - `controller_wind_enable`
   - `pitch_target_deg`
   - `last_wind_action_seq`
   - `last_wind_action_step`
   - `wind_action_applied_at_utc`
   - `extension_status`
3. 旧 `ems.db` 必须原位迁移，保留参数、历史、outbox、日志；初始化不得覆盖用户保存值。
4. 新增 `wind_execution_evaluation`，记录同一 session 下的 B dispatch、C action 和后续 A feedback。至少保存规范文档第4.2节列出的字段。
5. 关联规则：从 B dispatch 使用的状态步开始，寻找同 session 的第一条更新状态；要求状态步前进、扩展字段完整、C 动作引用步不早于 dispatch 输入步、状态年龄合格。禁止跨 session 关联。
6. 独立实现风机执行评价，不要复用或覆盖现有 EMS 第24项评分：
   - 启停一致性 30%；
   - 功率跟踪 35%；
   - 桨距响应 20%；
   - 能力与安全约束 15%。
   界面标记为“小组评价规则”，不得宣称是课程原公式。
7. 无风、高风切出、fault 或 operating limit=0 时，C 的保护性停机是合理动作，不能简单判失败。目标突降后的 A 爬坡阶段也不能立即按 actual 高于新 target 扣成约束失败。
8. 没有完整反馈、只有 ACK、扩展缺失、状态过期或跨会话时返回 `insufficient_data`，不生成伪造总分。
9. GUI 增加独立“风机指令执行评价”视图，至少显示指令、C动作、实际反馈、时延、四项分数、总分、结论和原因；历史可按 UTC 时间段/session 查询。
10. 保持正式运行三进程边界：GUI 不创建 socket，B_COMPUTE 不创建 socket，只有 B_IO 访问 TCP；所有 SQLite 操作用短事务和独立连接。

## 测试要求

至少新增并运行以下测试：

- 新版完整 state 正确解析和持久化；
- 旧版缺五字段仍保持连接、存 NULL、评价数据不足；
- 新字段类型错误、越界、NaN/Infinity 被拒绝；
- 未知额外字段被忽略；
- A 新会话尚无 C 动作时三个元数据 null；
- dispatch → C action → 后续 state 正确关联；
- 跨 session、旧 step、状态过期不关联；
- START、STOP、正常功率跟踪、无风、高风、fault、爬坡和没有反馈；
- 评价只读，不修改 dispatch、不发送 TCP；
- GUI 无 socket 边界测试继续通过；
- 完整仓库 `python -m unittest discover -s tests -v` 通过。

## 禁止事项

- 不要修改扩展字段名或添加别名。
- 不要把 B target 当 actual，不要把 `controller_wind_enable` 当 `wind_running`。
- 不要为了评分向 A/C 写入虚假状态。
- 不要修改 `wind_action` payload。
- 不要覆盖未跟踪的 `C_controller/host/tcp_client.py` 或用户场景文件。
- 没有实机测试时明确写 mock，不得宣称硬件已验证。

## 交付

完成代码、数据库迁移、GUI、测试和 B 文档更新；公共字段或跨模块影响必须写入 `docs/decisions.md`。最后报告修改文件、迁移方式、测试结果、兼容性验证、硬件未验证范围和仍未完成事项。不要自行提交、打标签或发布，除非用户另行明确要求。
