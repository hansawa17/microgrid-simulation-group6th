# 通信协议 v0.1 草案

本文件是协作建议，尚未冻结或实现。TCP：A 服务端，B 与 C 单片机客户端；C 上位机串口帧另行确认。

## TCP 帧与公共字段

建议 UTF-8 JSON，一行一帧，以 LF 字节结束；帧内字符串换行必须 JSON 转义。最大帧建议 4096 字节（含 LF），需按 MCU 内存复核。
接收端缓存到 LF 再解析；一次 recv 不等于一条消息，必须处理多帧和半帧。超长帧、非法 JSON/类型、NaN/Infinity 拒绝并记录。

公共字段：version=1，type，source，target，session_id，seq，step，sim_time_s，payload。
source/target 为 A、B、C（C 指单片机）；session_id 为 A 仿真会话标识；seq 为发送方会话内递增整数；step 为所引用状态步号；sim_time_s 单位秒。现实接收时间由接收端另外记录。
连接建立先请求全量状态，后续可请求变化量；增量基线缺失或会话变化必须重新全量同步。旧会话命令拒绝。

| type | 方向 | payload 关键字段 |
|---|---|---|
| state_request | B/C → A | full: bool；首次会话字段可为 null |
| state | A → B/C | wind_speed_mps、load_power_kw、wind_actual_kw、diesel_actual_kw、wind_target_kw、pitch_actual_deg、wind_running、fault |
| dispatch | B → A | wind_target_kw、diesel_target_kw、wind_enable、diesel_enable |
| wind_action | C → A | wind_enable、pitch_target_deg |
| ack | A → B/C | ack_seq、accepted、reason |

功率统一 kW，风速 m/s，桨距 deg；bool 使用 JSON true/false，不用字符串。字段名中的 actual/target 不能省略。模型相关合法区间从设备配置读取，不猜测额定值。

本草案目前只给最小业务报文，完整四遥点表还须加入 RTU/设备归属、数据范围、更新周期与变位版本标识，才能冻结供三端实现。物理量范围：风速与功率为非负且受设备/场景上限约束；桨距上下限待统一模型；状态为布尔量。B 查询/命令检查周期为 1 s，决策默认 5 s；C 周期待约定。建议设备 ID WT01、DG01、LOAD01（仅小组提案）。

## 命令处理约定（待确认）

单 TCP 连接双向收发；由 source/type 验证写入权限。B 不能写桨距，C 不能写柴油机目标。A 计算实际状态，客户端不可写 actual 字段。
按 session_id + source + seq 识别重复，重复命令不重复执行；拒绝过期、乱序和越权命令。命令有效期及安全动作优先级见 decisions.md。
ack.accepted 只说明指令通过校验并接收，不代表设备达到目标；实际效果看后续 state。
超时或断线需标记离线、写日志并按确认的安全策略处理，不得静默把旧值标为新状态。

样例见 messages.example.json：这是 JSON 数组形式的文档样例；实际发送要逐对象序列化并各加一个 LF，不发送整个数组。
串口不直接套用本草案；帧头、长度与校验将在硬件确认后另立协议。
