# v1.0.2 系统与接口说明

```text
B EMS GUI -- JSON Lines/TCP --> A 仿真服务器 <-- JSON Lines/TCP -- STM32 + ESP8266
                                                                      |
                                                          USART2 115200 8N1
                                                                      |
                                                                 C 上位机
```

A 产生仿真时间、风速、负荷、actual 与功率不平衡；B 产生风/柴 target 和启停；STM32 C 产生风电 available/operating limit、启停动作和桨距目标。不得用 target 冒充 actual，不得把桨距效果写成负荷变化。

## TCP

- A 是唯一服务端，默认 `0.0.0.0:5000`；B GUI 和 STM32 各自维护一条长连接。
- UTF-8 JSON 每行一帧，LF 结尾，最大 4096 bytes；处理分包、粘包、超时、重复和乱序。
- 包络含 `version/type/source/target/session_id/seq/step/sim_time_s/payload`，目前 `version=1`。
- 消息：`state_request`、`state`、`dispatch`、`wind_action`、`parameter_update`、`ack`。
- ACK 未知不换 seq 盲目重发；重连使用 A 返回的 `next_command_seq` 恢复序号。

## 主时序

`state_request -> state -> B dispatch -> ack -> C wind_action -> ack -> next state(actual)`。B 默认每 1 s 检查，每 5 s 调度，仅 YK/YT 组合变化才发送；C 闭环发 `wind_action`，开环只读取和串口上报。

## 串口与数据库

C 的 USART2 帧包括 `$WIND2`、`$PARAM2`、`$PARAMGET`、`$SYNC`、`$WIFI/$WIFI?/$WIFIGET`、`$CMD`、`$ACK`，均以 CRLF 结尾。A/B/C 分别使用本机 `grid.db/ems.db/wind.db`，短事务、独立连接，不通过共享 SQLite 跨机通信。完整字段、单位、白名单和错误规则见 `common/protocol.md`、`docs/time-interface.md`、`docs/database.md`和 `docs/parameter-ownership.md`。
