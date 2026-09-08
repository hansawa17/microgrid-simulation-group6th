# 参数基线与计算职责

本文是 A/B/C 的统一实现依据。职责来自课程原文第 2 节、第 3.1-3.3 节；原文未给出的数值由小组于 2026-09-08 统一选定，不能表述为课程指定值。

## 1. 不可混淆的职责

| 数据或动作 | 唯一产生/计算方 | 其他模块职责 | 原文依据 |
|---|---|---|---|
| `step`、`sim_time_s`、`sampled_at_utc` | A 电网模拟器 | B/C 原样保存，另记本机接收时间 | 3.1 仿真参数与场景 |
| `wind_speed_mps` | A 从风速场景曲线读取/插值 | B 用于调度；C 用于风机控制计算 | 2(1)、3.1(3)、3.3(1) |
| `load_power_kw` | A 从负荷场景曲线读取/插值 | B 用于调度；C 只读或不使用 | 2(1)、3.1(3) |
| `wind_target_kw`、`diesel_target_kw` | B EMS 调度进程 | A 执行并仿真 actual；C 使用风机目标计算桨距 | 2(2)、3.2(3) |
| `dispatch.wind_enable`、`dispatch.diesel_enable` | B EMS | A 作为调度启停请求，不等同实际状态 | 3.2(3) |
| `wind_available_kw` | C 单片机根据 A 风速和风机参数计算 | A 存储并转发；B 用于能力约束；C 上位机展示/存储 | 2(3)、3.3(1) |
| `wind_operating_limit_kw` | C 单片机根据可用功率、运行许可和保护状态计算 | A 存储并转发；B 约束风机目标 | 对原文“允许运行 + 当前可用功率”的协议化表达 |
| `pitch_target_deg`、`controller wind_enable` | C 单片机 | A 执行动作并仿真 actual；B 不得写桨距 | 2(3)、3.3(1) |
| `wind_actual_kw` | A 电网模拟器 | B/C 只读；不得用 target 或 C 本地计算值冒充 | 2(1)、3.1(3) |
| `diesel_actual_kw` | A 电网模拟器 | B/C 只读 | 2(1)、3.1(3) |
| `power_imbalance_kw` | A，定义为 `load - wind_actual - diesel_actual` | B 用于评价或下一轮决策 | 3.1(3) |
| 风机参数维护 | C 上位机编辑，写回 C 单片机 | A/B 保留一致的只读副本用于仿真和限值校验 | 2(3)、3.3(2) |
| `received_at_utc`、连接状态、通信日志 | 各接收模块本地生成 | 不跨机器冒充 A 的采样时间 | 3.1-3.3 通信与日志要求 |

关键结论：风速不是 B 计算量。A 产生风速与负荷场景，B 计算功率目标，C 计算风机可用功率、启停和桨距，A 计算最终实际出力。

## 2. 小组统一数值基线

| 参数 | 统一值 | 单位 | 维护/使用 |
|---|---:|---|---|
| 风机额定功率 | 100 | kW | C 上位机维护并写回 MCU；A/B 同步副本 |
| 切入风速 | 3 | m/s | C 参数 |
| 额定风速 | 12 | m/s | C 参数 |
| 切出风速 | 25 | m/s | C 参数；`wind_speed_mps >= 25` 停机 |
| 满功率桨距角 | 0 | deg | C 控制基准 |
| 完全顺桨角 | 90 | deg | C 控制上限与停机安全位置 |
| 风机升/降功率爬坡 | 40 / 60 | kW/s | A 实际出力仿真 |
| 柴发最小/最大功率 | 20 / 120 | kW | A 实际出力仿真，B 调度限值 |
| 柴发升/降功率爬坡 | 30 / 40 | kW/s | A 实际出力仿真 |
| 柴发备用容量 | 10 | kW | B 调度约束，不是 A actual |
| A 仿真步长/执行周期 | 1 / 1 | s | A |
| B 采集/调度周期 | 1 / 5 | s | B，来自原文默认值 |
| C 控制周期/通信超时 | 1 / 3 | s | 小组统一配置 |

## 3. 统一风机模型

令 `v` 为 A 发布的风速，`P_rated = 100 kW`。C 计算：

```text
P_available(v) = 0                                      , v < 3
P_available(v) = 100 * ((v - 3) / (12 - 3))^3          , 3 <= v < 12
P_available(v) = 100                                    , 12 <= v < 25
P_available(v) = 0                                      , v >= 25
```

C 的运行许可为真且无保护/故障时：

```text
wind_operating_limit_kw = wind_available_kw
```

否则为 0。该上限不扣除 B 目标和桨距目标，防止 B 因上一轮限功率而无法重新升功率。

闭环模式下 C 根据 B 的 `wind_target_kw` 计算桨距目标：

```text
pitch_target_deg = clamp(90 * (1 - wind_target_kw / wind_available_kw), 0, 90)
```

无可用功率、停机或保护时目标为 90 deg；开环运行时为 0 deg。A 使用 B 目标、B/C 启停许可、C 桨距、A 内部物理上限和爬坡约束计算 `wind_actual_kw`。C 本地 mock 可显示估算实际功率，但不得作为联网后的 `wind_actual_kw` 真值。

## 4. 数据流与写权限

```text
A 场景风速/负荷 -> B 调度目标 -> A
       |                         |
       +-> C 可用功率/启停/桨距 ->+
                                  |
                         A actual 与不平衡 -> B/C
```

- B 不能写 `pitch_target_deg` 或任何 `actual` 字段。
- C 不能写柴发目标、负荷或任何 `actual` 字段。
- A 不替代 B 生成调度目标，也不替代 C 发布可用功率/桨距控制结果。
- A 可以在内部重复计算物理功率上限用于 actual 安全裁剪，但公共 `wind_available_kw` 必须来自最近一次通过校验的 C 报文。
- C 上位机只经 UART 管理单片机参数；最终 TCP 客户端是 STM32/Wi-Fi，不是 PC-C 占位客户端。

## 5. 一致性约束

```text
0 <= wind_operating_limit_kw <= wind_available_kw <= 100
0 <= wind_target_kw <= wind_operating_limit_kw
0 <= wind_actual_kw <= min(wind_target_kw, wind_operating_limit_kw)
0 <= pitch_target_deg <= 90
power_imbalance_kw = load_power_kw - wind_actual_kw - diesel_actual_kw
```

爬坡过程中 actual 可以暂时高于刚下降的新 target；此时以 A 的爬坡模型和后续状态为准，不能把 ACK 当成已达到目标。
