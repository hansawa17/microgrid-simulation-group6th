# C 工作流 Prompt：动作追踪、参数回读与 A 状态转发

你正在仓库 `https://github.com/hansawa17/microgrid-simulation-group6th` 中整改 C 风电子站及必要的 A 汇聚接口。先完整阅读 `AGENTS.md`、`README.md`、`docs/requirements.md`、`common/protocol.md`、`docs/decisions.md`、`docs/parameter-ownership.md`、`docs/wind-execution-status-extension.md`、`A_simulator/README.md`、`C_controller/README.md`、`C_controller/firmware/README.md` 和 `C_controller/host/README.md`。先检查 `git status`；保留用户已有文件和改动，尤其不要覆盖未跟踪的 `C_controller/host/tcp_client.py`。

## 任务目标

完成新版验收要求的生产端数据链：STM32/C 继续产生风机运行许可、可用功率、运行上限和目标桨距；A 保存最近一次合法 C 动作的追踪信息并在 state 中转发；C 上位机扩展数据库和界面，使用参数回读证明 MCU 真正采用了修改值，并把已生效的 C 参数通过 STM32/Wi-Fi同步到 A。不得让 C 上位机直接替代 STM32 连接 A。

## 必须逐字采用的 A→B/C 状态扩展字段

以下字段位于 A→B/C `state.payload`。字段名、大小写、JSON类型、单位和语义不得自行修改：

| 字段 | JSON 类型 | 约束 | 语义 |
|---|---|---|---|
| `controller_wind_enable` | boolean | JSON `true/false` | A 最近一次接受的 C `wind_action.wind_enable`；不是 B `dispatch.wind_enable`，不是 A `wind_running` |
| `pitch_target_deg` | number | 有限数，`0..pitch_feather_deg`，单位 deg | C 计算并通过 wind_action 发送的目标桨距；区别于 A `pitch_actual_deg` |
| `last_wind_action_seq` | integer 或 null | 非负整数 | 当前 session 最近一次被 A 接受的 source=C `wind_action.seq` |
| `last_wind_action_step` | integer 或 null | 非负整数 | 上述 C 动作 envelope 引用的 A 状态 step |
| `wind_action_applied_at_utc` | string 或 null | UTC RFC3339：`YYYY-MM-DDTHH:MM:SS.ffffffZ` | A 接收、校验并原子应用该动作的时间；不是 STM32 墙钟 |

现有 C→A `wind_action.payload` **必须继续精确包含且只包含以下四字段**：

```json
{
  "wind_enable": true,
  "pitch_target_deg": 12.24,
  "wind_available_kw": 57.87,
  "wind_operating_limit_kw": 57.87
}
```

严禁把 `control_mode`、`reason`、`last_wind_action_seq`、`last_wind_action_step` 或时间字段塞进 wind_action payload，否则 A 的严格校验会产生 `invalid_wind_action_fields`。序号和 step 使用公共 envelope；应用时间由 A 生成。

兼容规则是强制要求：

- TCP `version` 保持 1。
- A 升级后始终输出五个键；新 session 尚无合法 C 动作时，enable=false、pitch target=安全顺桨值，其余三个字段=null。
- B/C 旧接收端必须可以忽略新增字段；不得要求接收端 payload key 集完全相等。
- 滚动升级期间，消费端遇到缺字段应标记 `legacy_or_incomplete`；需要执行评价而字段不足时必须返回 `insufficient_data`，不能断线或把缺失当0分。
- 重复的同序号同 payload 返回幂等 ACK，但不得刷新动作应用时间；seq 冲突、乱序、旧 session 或非法 payload 不得覆盖最近合法动作。
- 不能使用 ACK 代表实际运行效果；实际效果看后续 A state。

## A 汇聚端必须实施

虽然任务主题是 C，但 A 是唯一 TCP 汇聚点，本工作流负责完成最小必要的 A 改动：

1. `grid.db` 迁移增加 `last_wind_action_seq`、`last_wind_action_step`、`wind_action_applied_at_utc`，复用已有 `controller_wind_enable`、`pitch_target_deg`。
2. A 只有在 C wind_action 通过所有字段、范围、session 和 seq 校验后，才在同一事务更新动作值及追踪字段。
3. 拒绝、乱序、冲突报文不更新；重复相同报文不刷新时间。
4. 新 session 清空动作追踪元数据并恢复安全停机/顺桨状态。
5. A `state.payload` 输出上述五字段；旧库必须迁移，不能重建用户数据库。
6. 更新 A state/model/repository/TCP测试，并证明现有旧 B 客户端能忽略新增字段继续工作。

## C 串口协议必须实施

保留现有 `$WIND`、`$PARAM`、`$CMD` 和 `$WIFI` 兼容解析。不要直接改变旧帧字段数量；新增：

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

规则：

- `request_id` 和 `parameter_revision` 为非负整数；`last_wind_action_seq=-1` 表示当前会话尚无被 A accepted 的 C 动作，PC入库转换为 NULL。
- MCU 完整解析并校验所有 `$PARAM2` 字段后原子应用，不能逐字段半更新；失败保持旧参数。
- MCU 每次 `$PARAM2` 后返回同 request_id 的 `$PARAMGET`；收到 `$PARAMGET?` 也返回当前有效值。
- C 上位机比较数值值和统一容差，不比较两位小数字符串。只有 request_id 匹配且全部参数一致时显示“MCU 已生效”。
- 参数页同时显示 `parameter_revision`、验证时间和验证失败原因。
- Host解析器同时接受旧 `$WIND` 10字段和新 `$WIND2`；旧帧没有动作序号时存 NULL，不能断开串口。
- 固件必须处理半帧、多帧、超长帧和非法数字；UART回调内不执行阻塞网络操作。

## C 参数同步到 A

MCU确认 `$PARAM2` 生效后，在现有单未决 TCP事务的安全空档排队发送 source=C 的 `parameter_update`。不得与未完成的 `state_request`、`wind_action` 或 ACK等待并发，也不得因此停止周期状态采集。

允许同步到 A 的参数仅为：

- `wind_rated_power_kw`
- `cut_in_speed_mps`
- `rated_speed_mps`
- `cut_out_speed_mps`
- `pitch_feather_deg`
- `c_control_s`
- `c_timeout_s`

如 C 实际支持并维护 `pitch_full_output_deg`，可按公共白名单发送；否则不得凭空加入示例默认值。`control_mode` 不属于 A 参数副本，禁止放进 `parameter_update.parameters`。

保存并展示 parameter_update 的 seq、ACK、失败原因和同步时间。C 上位机必须分开显示：

- “MCU 已生效”：来自匹配的 `$PARAMGET` 回读；
- “A 副本已同步”：来自 STM32经Wi-Fi发送 parameter_update 后的 A accepted ACK。

两者不能互相冒充；离线、本地 mock 或 ACK未知必须如实显示。

## C 数据库与 GUI

1. `wind.db` 遥测历史增加 `last_wind_action_seq`，旧帧存 NULL。
2. 参数/同步记录增加 `parameter_revision`、`parameter_verified`、`verified_at_utc`、`a_sync_status`、`a_sync_seq`、`a_sync_reason`。
3. 数据库原位迁移，保留已有参数和历史；每次操作使用短事务。
4. GUI实时显示目标功率、实际功率、可用功率、运行上限、目标桨距、控制模式、运行状态、A链路和最近动作seq。
5. 参数界面显示设定值、MCU回读有效值、是否一致、版本和A同步状态。
6. 本地仿真和真实串口记录必须标注数据源，不能把 mock 记录写成 MCU 实测。

## 测试要求

至少新增并运行：

- A 接受合法 wind_action 后五字段正确输出；
- 重复报文不刷新应用时间；冲突、乱序、旧session和非法字段不污染追踪值；
- 新session安全清空元数据；
- 旧 B/C 接收端忽略新增 state 字段；
- `$WIND` 与 `$WIND2` 均可解析，字段不足、过多、非法数字不造成串口线程崩溃；
- `$PARAM2` 原子校验、request_id 匹配、参数版本递增和 `$PARAMGET` 回读；
- 回读不一致时 GUI/数据库显示失败，不能宣称生效；
- parameter_update 只含白名单字段，control_mode 不发送；
- parameter_update 与 state/wind_action 单未决事务不并发，ACK/超时/重连状态可查；
- 旧数据库迁移保留数据；
- PC完整测试通过；若存在 STM32工具链则编译固件并报告结果，否则明确未编译。

## 禁止事项

- 不改变五个扩展字段名、类型或单位。
- 不扩充 wind_action payload。
- 不让 PC-C 上位机直接替代 STM32连接A。
- 不把 C 本地估算功率冒充 A 的 `wind_actual_kw`。
- 不把 B target 冒充 actual，不把 C 动作直接写成负荷变化。
- 不提交 Wi-Fi密码、令牌、真实数据库或固件编译输出。
- 不覆盖用户未跟踪文件；没有硬件测试时明确写 mock。

## 交付

完成 C 固件、C host、C数据库迁移、GUI、最小A汇聚改动、测试及文档；公共协议和跨模块影响写入 `docs/decisions.md`。最后报告字段映射、迁移方式、兼容性、PC测试、固件编译、硬件验证范围和未完成事项。不要自行提交、打标签或发布，除非用户另行明确要求。
