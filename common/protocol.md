# 通信协议 v1（职责与参数基线已确认）

TCP：A 服务端，B 与 C 单片机客户端；C 上位机串口帧另行确认。2026-09-08 已按课程原文冻结数据计算职责和物理参数基线；增量变位、命令 TTL、串口校验等传输细节仍待实机确认。

完整职责矩阵、统一参数和公式见 `docs/parameter-ownership.md`。任何代码或 README 与该文件冲突时，以课程原文和该矩阵为准。

时间字段的完整语义、A 授时规则、CSV 格式、数据库映射及暂停/重放规则见 `docs/time-interface.md`。

## TCP 帧与公共字段

建议 UTF-8 JSON，一行一帧，以 LF 字节结束；帧内字符串换行必须 JSON 转义。最大帧建议 4096 字节（含 LF），需按 MCU 内存复核。
接收端缓存到 LF 再解析；一次 recv 不等于一条消息，必须处理多帧和半帧。超长帧、非法 JSON/类型、NaN/Infinity 拒绝并记录。

公共字段：version=1，type，source，target，session_id，seq，step，sim_time_s，payload。
source/target 为 A、B、C（C 指单片机）；session_id 为 A 仿真会话标识；seq 为发送方会话内递增整数；step 为所引用状态步号；sim_time_s 单位秒。现实接收时间由接收端另外记录。
连接建立先请求全量状态，后续可请求变化量；增量基线缺失或会话变化必须重新全量同步。旧会话命令拒绝。

| type | 方向 | payload 关键字段 |
|---|---|---|
| state_request | B/C → A | full: bool；首次会话字段可为 null |
| state | A → B/C | sampled_at_utc、wind_speed_mps、load_power_kw、wind_available_kw、wind_operating_limit_kw、wind_target_kw、wind_actual_kw、diesel_target_kw、diesel_actual_kw、pitch_actual_deg、wind_running、diesel_running、fault、power_imbalance_kw、next_command_seq |
| dispatch | B → A | wind_target_kw、diesel_target_kw、wind_enable、diesel_enable |
| wind_action | C → A | wind_enable、pitch_target_deg、wind_available_kw、wind_operating_limit_kw |
| parameter_update | B/C → A | parameters: 非空参数名到有限非负数的对象；按 source 白名单校验 |
| ack | A → B/C | ack_seq、accepted、reason |

功率统一 kW，风速 m/s，桨距 deg；bool 使用 JSON true/false，不用字符串。字段名中的 actual/target 不能省略。统一数值范围从 `docs/parameter-ownership.md` 读取。

`wind_speed_mps` 由 A 从独立风速场景曲线读取或插值。B 不生成风速；C 接收 A 的风速用于控制计算。

`wind_available_kw` 由 C 单片机根据 A 的 `wind_speed_mps`、C 保存的切入/额定/切出风速和额定功率，按统一三次功率曲线计算。C 经 `wind_action` 发送给 A；A 校验、存储并在 `state` 中转发给 B/C。它不考虑 B 目标、启停、桨距或实际爬坡。

`wind_operating_limit_kw` 由 C 单片机在 `wind_available_kw` 基础上考虑运行许可、保护/故障和设备上限计算。它满足 `0 <= operating_limit <= available <= 100 kW`，不扣 B 目标、桨距目标或实际爬坡，避免能力值因上一轮限功率形成自锁。A 只存储、校验和转发该字段；B 使用它约束 `wind_target_kw`。

`wind_target_kw`、`diesel_target_kw` 由 B 计算；`pitch_target_deg` 与 C 风机启停动作由 C 计算；`wind_actual_kw`、`diesel_actual_kw` 和 `power_imbalance_kw` 由 A 计算。A 可独立复算风速物理上限用于 actual 安全裁剪，但不得以该内部值替代 C 发布的公共 `wind_available_kw`。

五个功率层次不得混用：`rated` 是静态设备额定值，`available` 是风资源能力，`operating_limit` 是当前运行约束后的稳态上限，`target` 是 B 指令，`actual` 是 A 仿真结果。

`sampled_at_utc` 为 A 读取系统授时并生成状态断面时的 UTC RFC 3339 时间；B/C 应原样保存，并另外记录本机 `received_at_utc`。控制顺序仍以 `session_id + step + seq` 为准，不使用三台电脑的墙钟时间排序。

`next_command_seq` 是 A 按当前 `session_id` 和请求方 `source` 查询命令历史后返回的非负整数，表示该客户端新命令可使用的最小安全序号。STM32 在重启或重连后的全量同步中将本地发送序号提升到不小于该值；已发送但 ACK 未知的命令仍保留原报文和原序号重发，由 A 的幂等规则判定。该提示不放宽 A 的重复、冲突或乱序校验。

A 综合监控界面使用 `sampled_at_utc` 作为实时和历史曲线横轴；这不改变公共信封中的 `sim_time_s`。CSV 仍用相对 `sim_time_s` 插值和重放，界面显示的 UTC 不能代替 `session_id + step + seq` 参与控制排序。

## 参数副本同步 `parameter_update`

`parameter_update` 沿用 version 1 公共信封，只允许 `B -> A` 或 `C -> A`。payload 必须**精确**包含一个非空 `parameters` 对象；值必须是有限非负数字，不能使用 bool、NaN 或 Infinity。示例：

```json
{
  "version": 1,
  "type": "parameter_update",
  "source": "C",
  "target": "A",
  "session_id": "a-session-id",
  "seq": 10,
  "step": 5,
  "sim_time_s": 5.0,
  "payload": {
    "parameters": {
      "wind_rated_power_kw": 100.0,
      "cut_in_speed_mps": 3.0,
      "rated_speed_mps": 12.0,
      "cut_out_speed_mps": 25.0,
      "pitch_full_output_deg": 0.0,
      "pitch_feather_deg": 90.0
    }
  }
}
```

参数写权限：

| source/owner | 可同步到 A 的参数名 | 单位 |
|---|---|---|
| B | `reserve_kw` | kW |
| B | `b_poll_s`、`b_dispatch_s` | s |
| C | `wind_rated_power_kw` | kW |
| C | `cut_in_speed_mps`、`rated_speed_mps`、`cut_out_speed_mps` | m/s |
| C | `pitch_full_output_deg`、`pitch_feather_deg` | deg |
| C | `c_control_s`、`c_timeout_s` | s |

A 自身的仿真步长/执行周期、风柴爬坡和柴油机上下限只允许 A 本地参数接口修改，不接受 TCP 客户端写入。A 对所有更新执行单参数类型、所有权、数值范围及跨参数关系校验；任一字段失败时整条更新不生效。

通过校验后，A 更新 `parameter_state` 的 `source` 和 `updated_at_utc`，逐字段写入 `parameter_history` 并返回通用 ACK。若参数值与已保存副本完全相同，A 仍正常 ACK，但不改写来源/时间且不新增虚假的变更历史。C 的风机物理参数改变时，A 不能代替 C 重算控制动作，因此立即将缓存的 C action 安全失效为停机、完全顺桨、available/operating limit 为 0，直到 C 基于新参数发送下一条合法 `wind_action`。

成功 ACK 使用 `reason=accepted`；完全相同的同序号重发使用 `duplicate_accepted`，同一 `session_id + source + seq` 携带不同 type 或 payload 时拒绝为 `seq_conflict`。字段或权限错误使用明确 reason，例如 `invalid_parameter_update_fields`、`invalid_parameters`、`unauthorized_parameter`、`invalid_parameter_value:<name>` 或跨参数关系错误。ACK 只确认 A 已保存参数副本，不证明 B 调度或 C/STM32 已经采用新值。

本协议目前给出最小业务报文；完整四遥点表仍须加入 RTU/设备归属、变位版本标识。风机统一为 100 kW、3/12/25 m/s、0-90 deg；B 查询周期 1 s、决策周期 5 s；C 控制周期 1 s。设备 ID 使用 WT01、DG01、LOAD01。

## 命令处理约定

单 TCP 连接双向收发；由 source/type 验证写入权限。B 只能写调度目标/启停请求及 B 所有权参数；C 只能写风机可用能力、运行许可、桨距目标及 C 所有权参数。A 计算实际状态，客户端不可写 actual 字段。
按 session_id + source + seq 识别重复，重复命令不重复执行；同序号但消息类型或规范化 payload 不同必须拒绝为冲突。拒绝过期、乱序和越权命令。命令有效期及安全动作优先级见 decisions.md。
ack.accepted 只说明指令通过校验并接收，不代表设备达到目标；实际效果看后续 state。
超时或断线需标记离线、写日志并按确认的安全策略处理，不得静默把旧值标为新状态。A 当前软件服务的 TCP 空闲读超时为 10 s；服务启动、停止及 GUI 子进程异常退出都会复位 B/C 在线标志。该值是连接状态检测实现值，不替代仍待三方确认的控制命令有效期。

样例见 messages.example.json：这是 JSON 数组形式的文档样例；实际发送要逐对象序列化并各加一个 LF，不发送整个数组。
串口不直接套用本草案；帧头、长度与校验将在硬件确认后另立协议。
