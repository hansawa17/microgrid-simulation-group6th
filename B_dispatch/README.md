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
- **报警与评价**：保留原有状态过期、C fault、delivery unknown 等报警，同时增加只读的 EMS 调度评价：综合评分、EMS 调度评分、系统运行评分、六项分项评分、评价时间范围和明细指标。

## EMS 调度评价

`B_dispatch/evaluation.py` 是独立的**只读评价模块**。它只读取 `state_history`、`dispatch_commands` 等已经存在的运行数据，不发送 TCP、不修改 dispatch、不改变 A/B/C 控制链路。

评价范围支持：

- 最近 1 分钟
- 最近 5 分钟
- 本次 Session
- 全部历史

六项指标及评分：

1. **供需平衡**：平均功率不平衡，误差越小得分越高。
2. **风能利用**：实际风电相对可利用风电的利用率。
3. **柴油经济性**：柴油实际功率占负荷的比例，柴油使用越少通常得分越高。
4. **运行约束**：检查风电实际功率与 `wind_operating_limit_kw / wind_available_kw` 的边界关系。
5. **调度跟踪**：已确认 dispatch 的目标与同 `session_id + step` 状态实际值的平均偏差。
6. **响应性能**：结合 ACK 成功率与 ACK 平均响应时间评价。

界面同时显示两个辅助结果：

- **EMS 调度评分**：更关注柴油调度、约束、目标跟踪和通信响应。
- **系统运行评分**：更关注供需平衡、风能利用、柴油经济性和约束满足。

最终综合评价为两者的平均值，并给出“优秀 / 良好 / 合格 / 需改进”等级。评价中的原始指标也会同时展示，避免只显示一个无法解释的总分。

> 注意：评价模块不会把 C 的保护限制造成的风电下降简单判定为 B 调度错误；`wind_operating_limit_kw` 仍被视为 A/C 提供给 B 的运行约束。

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

## A 连接兼容与当前参数同步边界

- `gui_b.py` 已直接使用 `EMSTcpClient` 连接 A，主机/域名和端口分开填写。
- 新建 B 客户端以 UTC epoch 毫秒为发送序号基线，并在进程内保持严格递增，避免 A 会话未变化时重连后从 0 开始而被拒绝为旧序号。
- A 已实现接收 B 所有权参数 `reserve_kw/b_poll_s/b_dispatch_s` 的 `parameter_update`；B GUI 的参数发送入口尚未接入，当前不能宣称 B 已完成参数发布。
- 本机软件测试覆盖两个独立 B 客户端连续连接与 dispatch ACK；公网三机、系统时钟异常及 STM32 仍需联调。

## Dispatch ACK 超时处理（2026-09-09）

- B GUI 的 TCP polling timeout 可以保持较短，但 `dispatch` 不再复用该短 timeout 等待 ACK。
- `B_dispatch/tcpB.py` 为 dispatch ACK 单独设置至少 **2 s** 的等待窗口；收到 ACK 后恢复普通 state polling timeout。
- 原先 GUI 使用 `timeout_s=0.25`，A 虽然已经收到并执行 dispatch，但 SQLite/仿真处理稍慢时可能超过 250 ms，B 会误判为 ACK unknown、主动关闭 socket；这正是“**A 已更新、B 弹 ACK 错误并断连，但主页仍显示已连接**”的主要原因。
- 本次修复保留 delivery-unknown 的安全原则：真正超过 ACK 等待窗口仍不得自动换新 seq 重发。

## A/B 长连接稳定性处理（2026-09-09）

- A 的 `wind_target_kw` 是 B 上次下发目标，`wind_operating_limit_kw` 是 C 当前限制。启动、C 故障或限制切换时前者可暂时高于后者；B 只校验各字段自身范围及 `operating limit <= available`，收到这种合法过渡状态时不再误断 TCP。
- GUI 建连在后台线程执行，公网/域名建连使用独立 5 s 窗口；已连接 socket 由 Qt 定时器做非阻塞读取，不再每 250 ms 阻塞主线程。
- 首个或后续 `state_request` 连续 6 s 无响应时判定半开连接；意外 EOF、socket 错误或响应超时后按 1/2/4/8/15 s 上限退避重连，重连后自动发送 `full=true`。
- 地址框既可填写局域网 IP/域名并在端口框填端口，也可直接填写 `host:port`；不要填写 `tcp://`、路径或 `0.0.0.0`。
- `127.0.0.1/localhost` 只表示 B 自己，只有 A/B 同机时可用。公网端点若完成握手但在首个 state 前关闭，B 会明确提示检查 A TCP 服务和隧道后端端口，而不再只显示笼统 EOF。
- A/B 两端启用 TCP keepalive；A 的应用层空闲断线窗口为 30 s。公网隧道仍必须是 raw TCP 长连接映射，且需另行检查端点有效性和防火墙放行。

## 本地运行

Python 统一为 **3.11.x**，Qt 使用 **PyQt6**。

```bash
python -m B_dispatch
python -m B_dispatch gui
python -m B_dispatch.gui_b
```

初始化/重新对齐数据库：

```bash
python scripts/init_ems_db.py
```

测试命令：

```bash
python -m unittest discover -s tests -v
```

`data/runtime/ems.db` 为本地运行数据，不提交 GitHub。

## 当前边界

- GUI 的本地场景输入只用于 B 算法演示，不能替代 A 的 CSV 场景。
- 评价模块是只读分析，不参与 B dispatch 计算，不修改 A 状态，不发送 C 控制指令。
- A/B/C socket、STM32/Wi-Fi/串口实机联调仍需单独验收；GUI 能显示通信状态不等于实机联调完成。
- 自动闭环默认关闭，只有连接 A 并明确开启后才会发送 dispatch。
- B 只发送 `wind_target_kw / diesel_target_kw / wind_enable / diesel_enable`，不发送 `pitch_target_deg`。
- 生产数据库基线不可由 GUI 随意改写；测试场景应在独立临时数据库中完成。
