# v1.0.0 系统与接口说明

## 1. 系统架构

```text
                         JSON Lines / TCP 5000
 B EMS GUI（TCP 客户端） --------------------------+
                                                   |
                                                   v
                                            A 电网模拟器
                                                   ^
                                                   |
 STM32 风机控制器 + ESP8266（TCP 客户端，source=C）-+
              ^
              | USART2，115200 8N1
              v
 C 风电上位机（串口、展示、参数、历史）
```

A 是唯一 TCP 服务端；B 和 STM32/ESP8266 分别建立到 A 的独立长连接。C 上位机只持有 UART，不直接连接 A。A、B、C 各自使用本机 SQLite，网络通信不能用共享数据库替代。

## 2. 计算和控制职责

| 数据/动作 | 唯一产生方 | 说明 |
|---|---|---|
| `step`、`sim_time_s`、`sampled_at_utc` | A | 仿真时钟和采样时间 |
| `wind_speed_mps`、`load_power_kw` | A | 分别从独立场景曲线读取/插值 |
| `wind_target_kw`、`diesel_target_kw`、调度启停 | B | 功率目标，不是实际出力 |
| `wind_available_kw`、`wind_operating_limit_kw` | STM32 C | 根据风速、参数、许可和保护计算 |
| `pitch_target_deg`、控制器风机启停 | STM32 C | 风机动作，不改变负荷 |
| `wind_actual_kw`、`diesel_actual_kw` | A | 应用目标、动作、设备约束和爬坡后的实际值 |
| `power_imbalance_kw` | A | `load - wind_actual - diesel_actual` |

不得用 B target、C 本地估算或 mock 冒充 A actual。开环只计算展示，不发送控制；闭环才发送 `dispatch` 或 `wind_action`。

## 3. TCP 传输层

- UTF-8 JSON Lines：每帧一个 JSON 对象，以 LF (`\n`) 结束。
- 最大帧 4096 bytes，包含 LF。
- 接收端缓存到 LF 后解析，必须处理半包、粘包和一次接收多帧。
- 拒绝超长、非法 JSON、NaN/Infinity、字段缺失和错误类型并记录日志。
- 建连后先请求全量状态；会话变化或增量基线丢失时重新全量同步。
- A 对 B/C 分连接处理，启用 TCP_NODELAY、keepalive 和应用层空闲检测。

公共 envelope：

```json
{
  "version": 1,
  "type": "state_request",
  "source": "B",
  "target": "A",
  "session_id": null,
  "seq": 0,
  "step": null,
  "sim_time_s": null,
  "payload": {"full": true}
}
```

`source/target` 取 `A`、`B`、`C`；`session_id` 是 A 仿真会话；`seq` 是发送方会话内递增序号；`step/sim_time_s` 引用 A 状态。现实接收时间另存 `received_at_utc`，不得与仿真时间混用。

## 4. TCP 消息

| type | 方向 | payload 关键字段 | 处理结果 |
|---|---|---|---|
| `state_request` | B/C → A | `full` | A 返回 `state` |
| `state` | A → B/C | 风速、负荷、能力、目标、actual、桨距、启停、故障、不平衡、`next_command_seq` | 客户端更新本地副本 |
| `dispatch` | B → A | `wind_target_kw`、`diesel_target_kw`、`wind_enable`、`diesel_enable` | A 校验、原子写库并 ACK |
| `wind_action` | C → A | `wind_enable`、`pitch_target_deg`、`wind_available_kw`、`wind_operating_limit_kw` | A 校验、原子写库并 ACK |
| `parameter_update` | B/C → A | `parameters` 白名单对象 | A 按 source 白名单校验并 ACK |
| `ack` | A → B/C | `ack_seq`、`accepted`、`reason` | 客户端匹配未决事务 |

B 参数白名单：`reserve_kw`、`b_poll_s`、`b_dispatch_s`。C 参数白名单：`wind_rated_power_kw`、切入/额定/切出风速、满功率/顺桨角、`c_control_s`、`c_timeout_s`。

ACK 超时或连接断开会形成“投递结果未知”；客户端不得换新 seq 盲目重发。重连后按 A `state.next_command_seq` 恢复序号。B 在 GUI 中非阻塞轮询网络，并只在 YK/YT 组合发生变化时发送新的 `dispatch`。

## 5. 单未决事务时序

B 正常闭环：

```text
B -> A  state_request
A -> B  state
B       计算调度（默认每 5 s；每 1 s 检查状态/变化）
B -> A  dispatch（仅 YK/YT 改变时）
A -> B  ack
```

C 闭环：

```text
STM32 -> A  state_request
A     -> C  state
STM32       计算能力、启停和桨距
STM32 -> A  wind_action
A     -> C  ack
```

C 开环在收到 `state` 后只通过 UART 上报计算结果，不发送 `wind_action`。参数更新排队到 TCP 安全空档发送，不与状态/动作 ACK 竞争同一事务。

## 6. 字段单位与基线

- 功率：kW；风速：m/s；桨距：deg；时间：s；布尔值使用 JSON `true/false`。
- 项目配置基线：风机额定 100 kW；切入/额定/切出 3/12/25 m/s；桨距 0–90 deg。
- A 默认执行周期 1 s；B 采集/检查 1 s、调度 5 s；C 控制 1 s、超时 3 s（允许 0.5–30 s）。
- 上述物理数值是小组统一配置，不是课程原文指定常数。

一致性约束：

```text
0 <= wind_operating_limit_kw <= wind_available_kw <= wind_rated_power_kw
0 <= wind_target_kw <= wind_operating_limit_kw
0 <= pitch_target_deg <= 90
power_imbalance_kw = load_power_kw - wind_actual_kw - diesel_actual_kw
```

actual 受爬坡约束，目标突然下降后可短时高于新 target；应观察后续状态，不能把命令 ACK 解释成实际值已经到位。

## 7. C 上位机串口协议

USART2 为 115200、8 数据位、无校验、1 停止位。文本帧以 `\r\n` 结束，主要帧如下：

| 帧 | 方向 | 用途 |
|---|---|---|
| `$WIND2,...` | MCU → PC | 遥测、链路状态和最近一次 A 接受的动作 seq |
| `$PARAM2,<request_id>,...` | PC → MCU | 全字段校验后原子应用参数并递增版本 |
| `$PARAMGET?,<request_id>` / `$PARAMGET,...` | PC ↔ MCU | 参数回读和 request_id 对应验证 |
| `$SYNC,<status>,<seq>,<reason>` | MCU → PC | C 参数副本同步到 A 的状态 |
| `$WIFI,<ip>,<port>` / `$WIFI?` / `$WIFIGET,...` | PC ↔ MCU | 查询或运行期修改 A 端点 |
| `$CMD,<START|STOP|RESET|AUTO|MANUAL>` | PC → MCU | 运行和模式命令 |
| `$ACK,<kind>,<0|1>` | MCU → PC | 串口命令处理结果 |

旧 `$WIND/$PARAM/$CMD/$WIFI` 解析仍保留兼容。串口传输细节仍需根据最终硬件噪声和缓冲测试决定是否增加校验码；当前不能宣称协议已通过全部电磁环境测试。

## 8. 数据库与追溯

- A：`data/runtime/grid.db`
- B：`data/runtime/ems.db`
- C：`C_controller/host/database/wind.db`

各库保存本模块参数、当前状态、历史、通信和运行日志。A 的状态时间、B/C 的接收时间分别保存；验收时应能由记录还原 `state → dispatch → wind_action → actual state`。数据库连接采用短事务，不跨电脑共享 SQLite 文件。

更完整字段定义以 `common/protocol.md`、`docs/time-interface.md`、`docs/database.md` 和 `docs/parameter-ownership.md` 为准。
