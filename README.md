# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前采用三模块协作：A 电网模拟器、B EMS 主站、C 风电子站。数据计算职责与基础物理参数已于 2026-09-08 按课程原文统一；仍未冻结的通信和硬件细节不得由代码擅自假定。

## 分工与入口

| 成员 | 模块 | 代码位置 | 核心交付 |
|---|---|---|---|
| A 刘雨杭 | 电网模拟器 | `A_simulator/` | 场景、风柴实际出力仿真、TCP 服务端、grid.db、Qt 界面 |
| B 李佳霖 | EMS 主站 | `B_dispatch/` | 状态采集、调度、闭环、ems.db、Qt 界面 |
| C 陈信甫 | 风电子站 | `C_controller/` | STM32 控制、Wi-Fi TCP 客户端、串口上位机、wind.db |

运行关系：`B → A ← STM32`；A 是 TCP 服务端，B 和 STM32/C 是客户端。C 对风机保护/控制具有优先权。

## 参数计算职责（课程原文）

| 计算结果 | 责任模块 |
|---|---|
| 风速、负荷场景实时值 | A 从独立 CSV 曲线读取/插值 |
| 风机/柴发功率目标 | B EMS 调度计算 |
| 风机可用功率、运行许可、目标桨距角 | C 单片机计算 |
| 风机/柴发实际出力、功率不平衡 | A 仿真计算 |

统一风机基线：额定功率 **100 kW**，切入/额定/切出风速 **3/12/25 m/s**，切入到额定采用归一化三次功率曲线，桨距范围 **0-90 deg**。这些数值是小组统一配置，课程原文只规定职责、没有指定数值。完整字段写权限、公式和柴油机参数见 [`docs/parameter-ownership.md`](docs/parameter-ownership.md)。

## B EMS 当前结构

```text
B_dispatch/
├── models.py
├── dispatch.py
├── operator_core.py
├── runtime.py
├── repository.py
├── db_schema.sql
├── tcpB.py
├── serviceB.py
├── gui_b.py
├── __main__.py
└── README.md

tests/
├── test_b_dispatch.py
├── test_operator_core.py
├── test_repository.py
├── test_runtime.py
├── test_tcpB.py
└── test_serviceB.py
```

B 的 TCP/服务文件统一使用 `*B` 后缀，避免和其他成员冲突。

## 开发时间节点 / 功能增量

### 2026-09-07 · B EMS 调度、闭环与数据库基础

- 建立 `GridState / DispatchConfig / DispatchResult`、`EMSCore`、`runtime.py`。
- 风电优先、柴油补缺；柴油 reserve 固定 **10 kW**，允许完全 OFF，正常目标上限为 `diesel_max_kw - 10 kW`。
- C 保护/控制优先于 B 正常调度。
- B 只产生 target/enable，不写 actual，不发送桨距命令。
- `repository.py` / `db_schema.sql` 保存参数、运行配置、状态、历史、dispatch、评价和日志；A 的 `sampled_at_utc` 与 B 的 `received_at_utc` 分开保存。

### 2026-09-07 · B TCPB 基础与请求/ACK 风险处理

- `tcpB.py` 实现 B→A `state_request` / `dispatch` 与 A→B `state` / `ack`。
- UTF-8 JSON Lines、LF 分帧、最大 **4096 bytes（含 LF）**，处理半帧/粘包/多帧并拒绝非法 JSON、NaN/Infinity。
- 校验公共 envelope、source/target、seq/session/step；首次连接请求全量状态；step 回退/会话变化时重新同步。
- 同一连接避免同时存在未完成 state request 与 dispatch ACK；dispatch 后等待对应 ACK。
- ACK accepted/reason 被 B 保留；断线或 timeout 导致 ACK 未知时不得换新 seq 盲目重发。

### 2026-09-08 · 按最新公共协议完成 B 侧对齐

当前 `common/protocol.md` 已定义状态中的 `wind_available_kw`、`wind_operating_limit_kw`、`wind_target_kw` 和 `wind_actual_kw`。B 已更新模型、TCP、dispatch、repository、schema、service 及相关测试；dispatch 使用 `wind_operating_limit_kw` 约束风机目标，不再用 `wind_actual_kw` 反推能力。

### 2026-09-08 · B PyQt6 GUI 扩展与 C 风格统一

`B_dispatch/gui_b.py` 现为完整的本地 SCADA/操作工作台，保持 C GUI 的浅蓝灰卡片、蓝色主色、左侧导航和顶部状态布局，同时保留 A IP/端口功能。

当前页面：

1. **运行监控**：A 可达 IP、TCP 端口、本机 IP、连接/断开；负荷、available、operating limit、actual、target、功率不平衡、柴油余量、状态年龄等。
2. **实时曲线**：负荷、风电能力/限制、目标/实际及柴油实际趋势。
3. **本地场景 / 手动调度**：构造 mock `GridState`，验证风优先、operating limit、10 kW reserve、柴油 OFF、C fault 优先和缺供结果。
4. **参数设置**：编辑风电/柴油容量、状态年龄、采集周期、调度周期和闭环模式，并保存到 `ems.db`。
5. **EMS 调度**：手动计算/下发和可选自动闭环；显示 target unserved、target surplus、reason、ACK。
6. **历史数据**：读取 `ems.db/state_history`、session 过滤和 CSV 导出。
7. **通信诊断**：连接、pending request、full-sync、seq、ACK 和 GUI 事件日志。
8. **报警与评价**：C fault、状态过期、delivery unknown 以及数据库评价 KPI。

GUI 可以直接启动：

```bash
python B_dispatch/gui_b.py
```

也可以使用包入口：

```bash
python -m B_dispatch
python -m B_dispatch gui
```

### 当前边界

本阶段 GUI 已具备真实 TCP 接入能力，但 **不能把“GUI 能显示连接状态”当作 A/B/C 已完成联调**。自动闭环默认关闭，只有连接 A 后明确开启才发送 dispatch；B 不发送 pitch。

## 当前状态与下一步

### 已完成

- B 调度基础、C 优先级和 closed-loop runtime 框架。
- B SQLite 状态/历史/命令/评价/日志数据层。
- B-owned `tcpB.py` / `serviceB.py`。
- JSON Lines、4096 bytes、seq/session/step、双时间字段、ACK 风险处理。
- `wind_available_kw` / `wind_operating_limit_kw` 已进入 B 模型、TCP parser、dispatch、数据库和测试。
- B PyQt6 GUI 已进入 `B_dispatch/gui_b.py`，支持 A IP/端口、真实 TCP 接入、本地 mock、参数管理、历史导出、通信诊断和调度评价。

### 尚需完成

1. A/B/C 联调最新公共协议，验证 C 计算 → A 转发 → B 调度完整闭环。
2. A/B socket 联调，验证状态、dispatch、ACK、半帧/粘包、seq/session/step、timeout、断线恢复。
3. B 数据追溯验证，确认 state → dispatch → ACK → 后续 actual state 可从 `ems.db` 复原。
4. A/B/C 组合闭环，重点验证 C 保护优先、风机冷启动、operating limit 和柴油 10 kW reserve。
5. STM32 实机验证，由 C 负责硬件/Wi-Fi/串口，B 配合验证目标闭环。

## 测试说明

当前会话中没有在用户本机执行测试，也没有可用于证明最新 GUI 修改已经通过的 CI 结果，因此 **不宣称测试通过**。

建议在 Python 3.11 环境执行：

```bash
python -m unittest discover -s tests -v
```

尚未进行 STM32 实物、串口及三机联合验证。

## 环境

- Python **3.11.x**。
- Qt 绑定 **PyQt6**。
- STM32 固件使用独立交叉编译工具链。
- `data/runtime/ems.db` 为本地运行数据，不提交 GitHub。

## 协作规则

三人直接维护 `main`；B 使用 GitHub 文件级同步，不创建 feature branch/PR。详细规则见 `AGENTS.md` / `CONTRIBUTING.md`。

重要设计依据：

- `docs/requirements.md`
- `common/protocol.md`
- `docs/time-interface.md`
- `docs/network.md`
- `docs/tcp-interface-design.md`
- `docs/database.md`
- `docs/decisions.md`
- `docs/acceptance.md`
