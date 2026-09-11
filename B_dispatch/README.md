# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和 PyQt6 操作界面；B 是 A 的 TCP 客户端，不充当 TCP 中心。正式运行由单一 GUI 进程直接持有 B→A TCP，并由 Qt 定时器完成采集、计算和闭环调度。

## 当前 GUI 功能

`B_dispatch/gui_b.py` 已扩展为 B 的 SCADA/操作工作台，并保持 C 主机的浅蓝灰卡片式视觉风格。

- **运行监控**：配置 A IP/TCP 端口并由 GUI 直接连接/断开；显示负荷、风电 available/operating limit/target/actual、柴油 target/actual、功率不平衡、风速、柴油能力余量、动态状态年龄、桨距 actual、session/step、`sampled_at_utc`。
- **实时曲线**：负荷、风电 available/limit、wind target/actual、diesel actual 趋势；可清空并立即请求 A 状态。
- **本地场景 / 手动调度**：不连接 A 也能构造 mock `GridState`，验证风优先、operating limit、柴油 10 kW reserve、柴油 OFF、C fault 优先、缺电/过剩等结果；明确标记为 mock，不冒充 A 场景。
- **参数设置**：风机/柴油物理参数为只读副本；B 自有调度限值、reserve、采集/调度周期、最大状态年龄和开闭环模式可写入 `ems.db`，重启与重复初始化不会覆盖用户保存值。
- **EMS 调度**：GUI 按周期生成决策；开环只记录/展示，闭环核对 session/step/状态年龄后直接发送并记录 ACK；accepted/rejected/delivery-unknown 均写入 `dispatch_commands`，delivery-unknown 会停止自动调度并禁止盲目重发。
- **历史数据**：读取 `ems.db/state_history`，按 session 过滤并支持 CSV 导出。
- **通信诊断**：显示 GUI TCP 状态、待处理 state/ACK、最近协议 seq、ACK 和事件日志。
- **报警与评价**：保留原有状态过期、C fault、delivery unknown 等报警；评价部分为只读分析，当前综合评价只包含五项调度/运行指标，并在每项评分右侧提供下拉评分标准。
- **风机执行追溯**：已确认的 B 调度会与后续完整 C 动作/A actual 状态配对，并写入 `wind_execution_evaluation`；缺少完整反馈时保持“数据不足”，不伪造评分。

主监控页的“RTU / SCADA 四遥实时点表”按验收口径明确分类：

| 类别 | B 中的代表点 |
|---|---|
| YC 遥测 | 风速、负荷、available、operating limit、风柴 actual、功率不平衡 |
| YX 遥信 | 风机/柴发运行状态、fault |
| YT 遥调 | `wind_target_kw`、`diesel_target_kw` 连续功率设定 |
| YK 遥控 | `wind_enable`、`diesel_enable` 启停命令及 outbox 状态 |

这里的 RTU 是数据采集/执行端的逻辑角色，不表示 B 用软件替代 STM32。点表同时给出值、单位、数据时刻和来源说明，target 与 actual 不混用。

## EMS 调度评价

`B_dispatch/evaluation.py` 是独立的**只读评价模块**。它只读取已经存在的运行数据，不发送 TCP、不修改 dispatch、不改变 A/B/C 控制链路。**ACK/通信响应不再作为评价评分项。**通信诊断页面仍保留 ACK 状态，用于排查通信问题，但不会影响综合评价分数。

评价范围支持：

- 最近 1 分钟
- 最近 5 分钟
- 本次 Session
- 全部历史

### 五项评分及权重

综合评分固定由以下五项组成，总权重 100%：

| 评价项 | 权重 | 评分依据 |
|---|---:|---|
| **供需平衡** | **30%** | 平均绝对功率不平衡，越小越好 |
| **风能利用** | **25%** | 实际风电 / 可利用风电 |
| **柴油经济性** | **15%** | 柴油实际供电 / 负荷，比例越低通常越好 |
| **运行约束** | **15%** | 风电实际、运行上限、可利用功率之间的边界约束 |
| **调度跟踪** | **15%** | 已确认 dispatch 的目标与实际平均偏差 |

综合评分计算：

```text
综合评分 =
  供需平衡 × 30%
+ 风能利用 × 25%
+ 柴油经济性 × 15%
+ 运行约束 × 15%
+ 调度跟踪 × 15%
```

评价等级保持：

- `≥ 90`：优秀
- `≥ 80`：良好
- `≥ 70`：合格
- `< 70`：需改进

### 评分标准下拉查看

“报警与评价”页面的五项评分标题右侧各有一个**查看评分标准**下拉框。下拉框只用于查看规则，不会改变评分参数，也不会向 A/C/TCP 发送任何消息。

当前规则：

**供需平衡（30%）**

- ≤ 1 kW：100 分
- ≤ 3 kW：90 分
- ≤ 5 kW：80 分
- ≤ 10 kW：60 分
- > 10 kW：40 分

**风能利用（25%）**

- ≥ 90%：100 分
- ≥ 80%：90 分
- ≥ 70%：80 分
- ≥ 60%：70 分
- < 60%：按利用率计分，最低 40 分

**柴油经济性（15%）**

- ≤ 20%：100 分
- ≤ 30%：95 分
- ≤ 40%：85 分
- ≤ 50%：75 分
- > 50%：60 分

**运行约束（15%）**

- 0 次违反：100 分
- 每增加 1 次违反：扣 10 分
- 最低 0 分
- 主要检查 `wind_actual_kw ≤ wind_operating_limit_kw ≤ wind_available_kw`

**调度跟踪（15%）**

- ≤ 1 kW：100 分
- ≤ 3 kW：95 分
- ≤ 5 kW：85 分
- ≤ 10 kW：70 分
- > 10 kW：50 分

### 页面说明

顶部保留“综合评价 / EMS 调度结果 / 风机执行效果 / 评价等级”四张卡片。其中综合评价严格按上述五项权重计算；EMS 调度结果和风机执行效果是分别面向策略与执行反馈的辅助视图，不额外参与综合评价。

页面明细同时显示：

- 评价样本
- 平均功率不平衡
- 风能利用率
- 柴油供电占比
- 平均未供电功率
- 平均过剩功率
- 运行约束违反次数
- 平均目标跟踪误差
- 调度次数

通信 ACK 仍可在“通信诊断”和“EMS 调度”页面查看，但**不会进入上述五项评分，也不会拉低综合评价**。

> 注意：评价模块不会把 C 的保护限制造成的风电下降简单判定为 B 调度错误；`wind_operating_limit_kw` 仍被视为 A/C 提供给 B 的运行约束。

## 参数与计算职责基线

依据 `docs/parameter-ownership.md`、`common/protocol.md`、`docs/decisions.md`：

- B 只计算 `wind_target_kw`、`diesel_target_kw`，并产生 `wind_enable` / `diesel_enable`。
- B 使用 A 转发、由 C 计算的 `wind_operating_limit_kw` 约束风机目标；不得使用 `wind_actual_kw` 反推风电能力。
- B 不计算 `wind_available_kw`、风速、桨距或任何 `actual` 字段，不发送 `pitch_target_deg`。
- 柴油调度保留 **10 kW** 备用容量；柴油目标为 0 时允许完全 OFF。
- 统一物理/周期基线：风机 100 kW、3/12/25 m/s、0/90 deg、40/60 kW/s；柴油 20/120 kW、30/40 kW/s；A/B/C 周期分别按 1 s / 1 s、1 s / 5 s、1 s，C 超时 3 s。以上均为**小组统一配置**，不是课程原文指定数值。

## ems.db 结构与初始化

`EMSRepository.initialize()` 会创建/迁移 `ems.db`。当前 schema version 为 **6**，主要分为五类：

1. `physical_parameters`：A/B 保留的只读物理参数副本；记录风机 100 kW、3/12/25 m/s、0/90 deg、40/60 kW/s，以及柴油 20/120 kW、30/40 kW/s。
2. `dispatch_parameters`：B 自己使用、可持久化配置的风电上下限、柴油上限和 reserve。
3. `ems_runtime_config / communication_config`：采集周期、调度周期、开闭环、命令超时、最大状态年龄及 A 端点。
4. `current_state / state_history / dispatch_commands / dispatch_evaluation / event_log`：完整 SCADA 状态、历史、已发送 dispatch、评价、通信/运行日志；A 的 `sampled_at_utc` 与 B 的 `received_at_utc` 分开保存。
5. `dispatch_outbox / process_status`：兼容旧服务的本机队列，以及 GUI/兼容服务的状态记录；默认 GUI 直连路径不依赖 B_IO/B_COMPUTE 心跳。

初始化只在配置行不存在时写入统一默认值；已有参数、状态、历史、dispatch 和日志不会被重置。状态年龄在读库时叠加本机经过时间，不能通过反复读旧行伪装成新数据。

**不要把真实 `data/runtime/ems.db` 提交到 GitHub。** 仓库只保存 schema 和初始化逻辑。

## A 连接与运行边界

- `gui_b.py` 是正式运行时唯一的 B→A TCP 所有者；直接请求 state、计算并发送 dispatch，收到的数据继续写入 `ems.db` 追溯。
- `communication_service.py` 与 `compute_service.py` 仅保留兼容/诊断入口，`python -m B_dispatch` 和 `scripts/start_b.ps1` 不会启动它们。
- 建连在线程中完成；已连接 socket 由 GUI 事件循环非阻塞轮询。dispatch 发送不等待 ACK，ACK 到达或 10 s 超时均由后续轮询处理。
- 新建 B 客户端以 UTC epoch 毫秒为发送序号基线，并在进程内保持严格递增，避免 A 会话未变化时重连后从 0 开始而被拒绝为旧序号。
- A 已实现接收 B 所有权参数 `reserve_kw/b_poll_s/b_dispatch_s` 的 `parameter_update`；B GUI 的参数发送入口尚未接入，当前不能宣称 B 已完成参数发布。
- 本机软件测试覆盖两个独立 B 客户端连续连接与 dispatch ACK；公网三机、系统时钟异常及 STM32 仍需联调。

## Dispatch ACK 超时处理（2026-09-09）

- B GUI 的 TCP polling timeout 可以保持较短，但 `dispatch` 不再在 GUI 线程同步等待 ACK。
- GUI 每 50 ms 非阻塞检查 socket；底层兼容同步接口仍有独立 10 s ACK 等待窗口。
- 原先 GUI 使用 `timeout_s=0.25`，A 虽然已经收到并执行 dispatch，但 SQLite/仿真处理稍慢时可能超过 250 ms，B 会误判为 ACK unknown、主动关闭 socket；这正是“**A 已更新、B 弹 ACK 错误并断连，但主页仍显示已连接**”的主要原因。
- 本次修复保留 delivery-unknown 的安全原则：真正超过 ACK 等待窗口仍不得自动换新 seq 重发。

## A/B 长连接稳定性处理（2026-09-09）

- A 的 `wind_target_kw` 是 B 上次下发目标，`wind_operating_limit_kw` 是 C 当前限制。启动、C 故障或限制切换时前者可暂时高于后者；B 只校验各字段自身范围及 `operating limit <= available`，收到这种合法过渡状态时不再误断 TCP。
- GUI 建连在后台线程执行，公网/域名建连使用独立 5 s 窗口；已连接 socket 由 Qt 定时器每 50 ms 做一次非阻塞检查。
- 首个或后续 `state_request` 连续 6 s 无响应时判定半开连接；意外 EOF、socket 错误或响应超时后按 1/2/4/8/15 s 上限退避重连，重连后自动发送 `full=true`。
- 地址框既可填写局域网 IP/域名并在端口框填端口，也可直接填写 `host:port`；不要填写 `tcp://`、路径或 `0.0.0.0`。
- `127.0.0.1/localhost` 只表示 B 自己，只有 A/B 同机时可用。公网端点若完成握手但在首个 state 前关闭，B 会明确提示检查 A TCP 服务和隧道后端端口，而不再只显示笼统 EOF。
- A/B 两端启用 TCP keepalive；A 的应用层空闲断线窗口为 30 s。公网隧道仍必须是 raw TCP 长连接映射，且需另行检查端点有效性和防火墙放行。

## 本地运行

Python 统一为 **3.11.x**，Qt 使用 **PyQt6**。

```bash
python -m B_dispatch
python -m B_dispatch run
python -m B_dispatch gui
python -m B_dispatch.gui_b
```

前三条包命令均启动同一个 GUI TCP 运行路径；`gui_b` 是等价的模块入口。Windows 也可运行 `scripts/start_b.ps1`。

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

- v1.0.2 的供需平衡评分阈值为：平均绝对不平衡 `<=3 kW` 记 100 分，`<=10 kW` 记 80 分，`<=50 kW` 记 60 分，其他记 40 分。

- GUI 的本地场景输入只用于 B 算法演示，不能替代 A 的 CSV 场景。
- 评价模块是只读分析，不参与 B dispatch 计算，不修改 A 状态，不发送 C 控制指令。
- A/B/C socket、STM32/Wi-Fi/串口实机联调仍需单独验收；GUI 能显示通信状态不等于实机联调完成。
- 默认闭环配置由 `ems_runtime_config` 持久化；开环模式只计算展示，不发送 TCP dispatch。
- B 只发送 `wind_target_kw / diesel_target_kw / wind_enable / diesel_enable`，不发送 `pitch_target_deg`。
- B 自有参数可由 GUI 修改并持久化；物理参数仍为只读副本，测试场景应在独立临时数据库中完成。
