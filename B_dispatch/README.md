# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和 PyQt6 操作界面；B 是 A 的 TCP 客户端，不充当 TCP 中心。

## 当前 GUI 功能

`B_dispatch/gui_b.py` 已扩展为 B 的 SCADA/操作工作台，并保持 C 主机的浅蓝灰卡片式视觉风格。

- **运行监控**：A IP、TCP 端口、本机 IP、连接/断开；负荷、风电 available、operating limit、target、actual、柴油 actual、功率不平衡、风速、柴油能力余量、状态年龄、桨距 actual、session/step、`sampled_at_utc`。
- **实时曲线**：负荷、风电 available/limit、wind target/actual、diesel actual 趋势；可清空并立即请求 A 状态。
- **本地场景 / 手动调度**：不连接 A 也能构造 mock `GridState`，验证风优先、operating limit、柴油 10 kW reserve、柴油 OFF、C fault 优先、缺电/过剩等结果；明确标记为 mock，不冒充 A 场景。
- **参数设置**：运行参数按统一基线展示；风机/柴油物理参数、B 调度限值和周期参数均由 `ems.db` 固定初始化，不允许 GUI 写成其他生产基线。
- **EMS 调度**：手动计算并发送 dispatch；可开启自动闭环；显示 target unserved、target surplus、reason 和 ACK；保留 delivery-unknown 禁止盲目重发的安全边界。
- **历史数据**：读取 `ems.db/state_history`，按 session 过滤并支持 CSV 导出。
- **通信诊断**：TCP 状态、pending state request、full-sync、最近入站 seq、最近 ACK 和 GUI 事件日志。
- **报警与评价**：状态过期、C fault、delivery unknown 等事件；从 `dispatch_evaluation` 和 `dispatch_commands` 汇总目标缺电、目标过剩和 ACK 接受率。

## 参数与计算职责基线

依据 `docs/parameter-ownership.md`、`common/protocol.md`、`docs/decisions.md`：

- B 只计算 `wind_target_kw`、`diesel_target_kw`，并产生 `wind_enable` / `diesel_enable`。
- B 使用 A 转发、由 C 计算的 `wind_operating_limit_kw` 约束风机目标；不得使用 `wind_actual_kw` 反推风电能力。
- B 不计算 `wind_available_kw`、风速、桨距或任何 `actual` 字段，不发送 `pitch_target_deg`。
- 柴油调度保留 **10 kW** 备用容量；柴油目标为 0 时允许完全 OFF。
- 统一物理/周期基线：风机 100 kW、3/12/25 m/s、0/90 deg、40/60 kW/s；柴油 20/120 kW、30/40 kW/s；A/B/C 周期分别按 1 s / 1 s、1 s / 5 s、1 s，C 超时 3 s。以上均为**小组统一配置**，不是课程原文指定数值。

## ems.db 结构与初始化

`EMSRepository.initialize()` 会创建/迁移 `ems.db`。当前 schema version 为 **4**，主要分为四类：

1. `physical_parameters`：A/B 保留的只读物理参数副本；记录风机 100 kW、3/12/25 m/s、0/90 deg、40/60 kW/s，以及柴油 20/120 kW、30/40 kW/s。
2. `dispatch_parameters`：B 自己使用的调度约束，固定为风电 0-100 kW、柴油最大 120 kW、reserve 10 kW。
3. `ems_runtime_config`：B 采集 1 s、B 调度 5 s、closed loop、命令超时 3 s 的运行基线。
4. `current_state / state_history / dispatch_commands / dispatch_evaluation / event_log`：状态、历史、dispatch、评价、通信/运行日志；A 的 `sampled_at_utc` 与 B 的 `received_at_utc` 分开保存。

初始化时会把上述统一基线写入/重新对齐已有数据库，不需要连接 A；A 连接只负责后续实时状态、dispatch ACK 等运行数据。不会删除已有状态、历史、dispatch 或日志记录。

**不要把真实 `data/runtime/ems.db` 提交到 GitHub。** 仓库只保存 schema 和初始化逻辑。

## 本地运行

Python 统一为 **3.11.x**，Qt 使用 **PyQt6**。

```bash
python -m B_dispatch
python -m B_dispatch gui
python B_dispatch/gui_b.py
```

初始化/重新对齐数据库：

```bash
python scripts/init_ems_db.py
```

测试命令：

```bash
python -m unittest discover -s tests -v
```

> 当前会话通过 GitHub 文件级检查完成静态对齐；没有在你的本机执行测试，因此不在此宣称测试通过。

`data/runtime/ems.db` 为本地运行数据，不提交 GitHub。

## 当前边界

- GUI 的本地场景输入只用于 B 算法演示，不能替代 A 的 CSV 场景。
- A/B/C socket、STM32/Wi-Fi/串口实机联调仍需单独验收；GUI 能显示通信状态不等于实机联调完成。
- 自动闭环默认关闭，只有连接 A 并明确开启后才会发送 dispatch。
- B 只发送 `wind_target_kw / diesel_target_kw / wind_enable / diesel_enable`，不发送 `pitch_target_deg`。
- 生产数据库基线不可由 GUI 随意改写；测试场景应在独立临时数据库中完成。
