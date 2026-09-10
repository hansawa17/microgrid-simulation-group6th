# 风机指令执行评价状态扩展规范

状态：**已冻结的整改实现契约**。本文用于 B/C 工作流实施新版验收表中的“风机指令执行评价”和“参数经串口下发后验证 MCU 实际采用值”。在配套代码和测试完成前，不得宣称该扩展已经实现或通过硬件验收。

## 1. 目标与边界

本扩展只补充风机指令执行追踪，不改变既有计算职责：

- B 仍只产生 `wind_target_kw`、`diesel_target_kw`、`dispatch.wind_enable` 和 `dispatch.diesel_enable`。
- C 单片机仍产生 `wind_available_kw`、`wind_operating_limit_kw`、`controller_wind_enable` 和 `pitch_target_deg`。
- A 仍计算 `wind_actual_kw`、`diesel_actual_kw`、`pitch_actual_deg`、运行状态和功率不平衡。
- A 是 TCP 服务端和状态汇聚点；B、STM32/C 不共享 SQLite 文件。
- `ack.accepted` 只表示 A 接受命令，不表示风机已经达到目标；执行效果必须读取后续 `state`。

## 2. A → B/C `state.payload` 扩展字段

公共 TCP envelope 保持 `version=1`，原有字段不删除、不改名、不改单位。A 在 `state.payload` 增加以下字段：

| 字段 | JSON 类型 | 单位/范围 | 允许 `null` | 唯一语义和来源 |
|---|---|---|---|---|
| `controller_wind_enable` | boolean | `true/false` | 否 | A 最近一次接受的 C `wind_action.wind_enable`；不是 B 的 `dispatch.wind_enable`，也不是实际运行状态 |
| `pitch_target_deg` | number | deg，`0 <= value <= pitch_feather_deg` | 否 | A 最近一次接受的 C `wind_action.pitch_target_deg`；不得用 `pitch_actual_deg` 代替 |
| `last_wind_action_seq` | integer | `>= 0` | 是 | A 最近一次接受的、当前 `session_id` 下 source=C 的 `wind_action.seq`；新会话尚未收到动作时为 `null` |
| `last_wind_action_step` | integer | `>= 0` | 是 | 上述 C 动作 envelope 引用的 A `step`；尚未收到动作时为 `null` |
| `wind_action_applied_at_utc` | string | UTC RFC 3339，固定 `YYYY-MM-DDTHH:MM:SS.ffffffZ` | 是 | A 接收、校验并写入该 C 动作的本机 UTC 时间；不是 C 墙钟，也不是设备达到目标的时间 |

扩展后的示例：

```json
{
  "version": 1,
  "type": "state",
  "source": "A",
  "target": "B",
  "session_id": "session-001",
  "seq": 210,
  "step": 35,
  "sim_time_s": 35.0,
  "payload": {
    "sampled_at_utc": "2026-09-10T08:00:35.000000Z",
    "wind_speed_mps": 10.5,
    "wind_available_kw": 57.87,
    "wind_operating_limit_kw": 57.87,
    "load_power_kw": 80.0,
    "wind_target_kw": 50.0,
    "wind_actual_kw": 46.0,
    "diesel_target_kw": 30.0,
    "diesel_actual_kw": 28.0,
    "controller_wind_enable": true,
    "pitch_target_deg": 12.24,
    "pitch_actual_deg": 12.24,
    "wind_running": true,
    "diesel_running": true,
    "fault": false,
    "power_imbalance_kw": 6.0,
    "last_wind_action_seq": 108,
    "last_wind_action_step": 34,
    "wind_action_applied_at_utc": "2026-09-10T08:00:34.420000Z",
    "next_command_seq": 109
  }
}
```

### 2.1 明确禁止的错误映射

- 不得把 B 的 `dispatch.wind_enable` 写入 `controller_wind_enable`。
- 不得把 `wind_running` 当作控制指令；它是 A 计算的实际状态。
- 不得把 `pitch_target_deg` 和 `pitch_actual_deg` 合并成一个字段。
- 不得由 B 生成或修补 C 动作序号。
- 不得使用 `received_at_utc` 冒充 `wind_action_applied_at_utc`。
- 不得把字段缺失、`null` 或尚无反馈直接当作数值 0。

## 3. 兼容升级规则

1. TCP `version` 保持 1；本扩展是向后兼容的可选字段集合。
2. A 完成升级后必须始终发送五个键。新会话第一次 C 动作前：
   - `controller_wind_enable=false`；
   - `pitch_target_deg=pitch_feather_deg`；
   - 三个动作元数据字段为 `null`。
3. B 必须接受缺少全部或部分扩展字段的旧版 A 状态，保存为 SQL `NULL`，设置 `extension_status=legacy_or_incomplete`，继续通信。
4. C 固件必须忽略不认识的 `state.payload` 键，不得因为 A/B 增加字段而断线。
5. 所有接收端继续拒绝错误类型、NaN/Infinity、越界数值；“允许缺失”不等于“允许错误值”。
6. 现有 C→A `wind_action.payload` **仍精确保持四字段**：

```json
{
  "wind_enable": true,
  "pitch_target_deg": 12.24,
  "wind_available_kw": 57.87,
  "wind_operating_limit_kw": 57.87
}
```

不要向 `wind_action.payload` 增加 `control_mode`、`reason`、`last_wind_action_seq` 或时间字段，否则旧 A 会返回 `invalid_wind_action_fields`。动作序号和时间来自公共 envelope 及 A 的接收记录。

## 4. 数据库映射

### 4.1 A `grid.db`

在 `control_state` 或等价的当前控制状态中保存：

- `last_wind_action_seq INTEGER NULL`
- `last_wind_action_step INTEGER NULL`
- `wind_action_applied_at_utc TEXT NULL`

`controller_wind_enable` 和 `pitch_target_deg` 已存在，必须直接复用。只有通过 A 全部校验并返回 accepted 的新 C 动作才能原子更新这些字段。重复的同序号同报文不得产生新的应用时间；冲突或拒绝报文不得覆盖最近合法动作。

新建 A 会话时清空动作序号、动作步号和动作时间，并恢复安全停机/顺桨状态。数据库升级必须迁移旧库，禁止重建或覆盖用户运行数据。

### 4.2 B `ems.db`

`current_state` 与 `state_history` 增加：

- `controller_wind_enable INTEGER NULL CHECK (controller_wind_enable IS NULL OR controller_wind_enable IN (0,1))`
- `pitch_target_deg REAL NULL CHECK (pitch_target_deg IS NULL OR (pitch_target_deg >= 0 AND pitch_target_deg <= 90))`
- `last_wind_action_seq INTEGER NULL CHECK (last_wind_action_seq IS NULL OR last_wind_action_seq >= 0)`
- `last_wind_action_step INTEGER NULL CHECK (last_wind_action_step IS NULL OR last_wind_action_step >= 0)`
- `wind_action_applied_at_utc TEXT NULL`
- `extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete'`

新增 `wind_execution_evaluation` 表，至少保存：

- `session_id`、B dispatch/outbox 标识及 C `last_wind_action_seq`；
- 指令状态步、反馈状态步、指令时间、反馈时间和响应时延；
- B `wind_enable/wind_target_kw`；
- C `controller_wind_enable/pitch_target_deg/wind_available_kw/wind_operating_limit_kw`；
- A `wind_running/wind_actual_kw/pitch_actual_deg/fault`；
- 四个分项分数、总分、`verdict`、`reason` 和评价时间。

旧库迁移必须保留历史。扩展数据不足时不插入伪造的 0 值，评价结果使用 `insufficient_data`。

### 4.3 C `wind.db`

在遥测历史中保存 `last_wind_action_seq`；参数记录增加 `parameter_revision`、`parameter_verified`、`verified_at_utc` 和 `a_sync_status`。真实串口与本地 mock 必须明确标记，不能把 mock 写成硬件实测。

## 5. B 风机指令执行评价

一次评价以 B 的 dispatch 为起点，在同一 `session_id` 中关联第一条满足以下条件的后续状态：状态步前进、包含完整扩展字段、C 动作引用步不早于该 dispatch 使用的状态步，并且数据未超过配置的最大年龄。

默认小组评分建议如下；它不是课程指定公式，界面和文档必须标为“小组评价规则”：

| 分项 | 权重 | 核心判断 |
|---|---:|---|
| 启停执行一致性 | 30% | B 请求、C 许可/保护动作、A 实际运行状态是否在当前风况下合理一致 |
| 功率跟踪 | 35% | 后续 `wind_actual_kw` 对 B `wind_target_kw` 的误差，同时考虑 available、operating limit 和爬坡过程 |
| 桨距响应 | 20% | `pitch_actual_deg` 对 C `pitch_target_deg` 的偏差；没有真实桨距反馈时标记数据不足 |
| 能力与安全约束 | 15% | actual、available、operating limit、fault 和高风/无风保护是否符合约束 |

保护性停机不得简单判成执行失败：无风、高风切出、故障或 `wind_operating_limit_kw=0` 时，即使 B 请求启动，只要 C 安全停机、A 实际停止，也应按“保护动作合理”解释。目标突降后的爬坡阶段也不能因 actual 暂时高于新 target 直接判违规。

没有后续状态、动作元数据缺失、跨会话、状态过期或只有 ACK 时，评价为 `insufficient_data`，不计算总分。

## 6. C 串口兼容扩展

保留现有 `$WIND` 和 `$PARAM` 解析。新增版本化帧，禁止直接改变旧帧字段数量：

```text
MCU -> PC:
$WIND2,<cycle>,<wind_speed_mps>,<wind_available_kw>,<wind_operating_limit_kw>,<wind_target_kw>,<wind_actual_kw>,<wind_running>,<pitch_target_deg>,<control_mode>,<link_status>,<last_wind_action_seq>\r\n

PC -> MCU:
$PARAM2,<request_id>,<cut_in_speed_mps>,<rated_speed_mps>,<cut_out_speed_mps>,<wind_rated_power_kw>,<pitch_feather_deg>,<c_control_s>,<c_timeout_s>,<control_mode>\r\n

PC -> MCU:
$PARAMGET?,<request_id>\r\n

MCU -> PC:
$PARAMGET,<request_id>,<parameter_revision>,<cut_in_speed_mps>,<rated_speed_mps>,<cut_out_speed_mps>,<wind_rated_power_kw>,<pitch_feather_deg>,<c_control_s>,<c_timeout_s>,<control_mode>\r\n
```

- `request_id`、`parameter_revision` 为非负十进制整数。
- `$WIND2.last_wind_action_seq=-1` 表示当前会话尚无被 A ACK accepted 的 C 动作；数据库保存为 `NULL`。
- MCU 必须完整解析、校验所有 `$PARAM2` 字段后原子应用；失败时保持原参数并返回当前 `$PARAMGET`。
- C 上位机只有在 `$PARAMGET.request_id` 匹配、全部有效参数与界面设定一致时，才显示“MCU 已生效”。浮点比较使用统一容差，不比较格式化字符串。
- MCU 确认生效后，在现有单未决 TCP 状态机的安全空档排队发送 C→A `parameter_update`；不得与等待中的 `state_request`、`wind_action` ACK 并发。
- `control_mode` 不属于 A 的物理参数副本，不得放入 `parameter_update.parameters`。
- C 上位机分别显示“MCU 已生效”和“A 副本已同步”；前者不能冒充后者。

## 7. 实施顺序与验收条件

建议按以下顺序合入，任何阶段都必须保持旧节点可通信：

1. A 数据库迁移、C 动作元数据保存和扩展 state 输出；旧 B/C 应继续工作。
2. C 固件/上位机兼容解析、`$WIND2/$PARAM2/$PARAMGET`、参数回读及 A 参数同步。
3. B 模型、数据库迁移、TCP兼容解析、执行评价、历史关联和 GUI。
4. 运行完整 PC 测试，再进行 STM32、UART、Wi-Fi 和三机现场测试。

完成判据：

- 新旧状态都不会触发无意义断线；错误类型和越界值仍被拒绝。
- A 重启、会话切换、重复 C 报文和拒绝报文不会污染动作追踪字段。
- B 可以展示一条 B 指令、对应 C 动作及后续 A 实际反馈，并解释评分原因。
- C 参数修改有 MCU 有效值回读；A 参数副本同步状态可查。
- 无风、正常风、高风、启动、停止、功率变化、故障、超时和缺字段均有测试。
- 硬件未实测时明确写 mock，不得用自动测试结果替代硬件验收。
