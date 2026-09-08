# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前采用三模块协作：A 电网模拟器、B EMS 主站、C 风电子站。数据计算职责与基础物理参数已于 2026-09-08 按课程原文统一；仍未冻结的通信和硬件细节不得由代码擅自假定。

## 分工与入口

| 成员 | 模块 | 代码位置 | 核心交付 |
|---|---|---|---|
| A 刘雨杭 | 电网模拟器 | `A_simulator/` | 场景、风柴实际出力仿真、TCP 服务端、grid.db、Qt 界面 |
| B 李佳霖 | EMS 主站 | `B_dispatch/` + `gui_b.py` | 状态采集、调度、闭环、ems.db、Qt 界面 |
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
└── README.md

gui_b.py                 # B / EMS PyQt6 GUI（仿照 C gui.py 风格）

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

当前 `common/protocol.md` 已定义状态中的：

- `wind_available_kw`：C 根据 A 风速和统一功率曲线得到的资源可用功率，经 A 存储转发。
- `wind_operating_limit_kw`：C 考虑运行许可、保护和设备限制后的稳态上限，经 A 存储转发。
- `wind_target_kw`：B 的调度目标。
- `wind_actual_kw`：A 的实际仿真出力。

B 已更新 `models.py`、`tcpB.py`、`dispatch.py`、`repository.py`、`db_schema.sql`、`serviceB.py` 及相关测试：

- B dispatch 改为使用 `wind_operating_limit_kw` 约束风机目标，**不再把 `wind_actual_kw` 当作可用能力**。
- 支持冷启动语义：`actual=0` 但 `operating_limit>0` 时仍可下发风机目标。
- `current_state/state_history` 增加 available/operating 两个功率字段。
- `dispatch_commands` 增加 ACK accepted/reason/received time；schema version 升为 **3**。
- `sampled_at_utc` 增加 RFC 3339 UTC 校验。
- 更新 TCP、dispatch、repository、service 回归测试。

### 2026-09-08 · 先完成 B PyQt6 GUI，暂时跳过通信联调

本阶段按开发安排，**暂时不进行 A/B/C socket 联调**，先把 B 的图形界面做出来，并参照 C 的 `gui.py` 使用统一的浅色卡片、蓝色主色、左侧导航、顶部状态胶囊和大数据卡片风格。

新增：`gui_b.py`。

GUI 当前覆盖：

- EMS 运行监控：负荷、风电 available、operating limit、target、actual、柴油 actual、闭环状态和 C 优先权。
- 实时趋势：功率与风速趋势。
- 调度参数：风电/柴油容量、10 kW reserve、状态年龄、1 s / 5 s 周期等。
- 本地调度演示：调用现有 B `calculate_dispatch()`，在不建立 TCP 的情况下验证风优先、operating limit、柴油 reserve、C fault 优先和安全兜底。
- 历史数据与事件区：按 `ems.db` 的状态/dispatch/事件语义提供界面骨架。
- 预留 `set_state_snapshot()` / `apply_dispatch_result()`，后续可接 `runtime.py`、`serviceB.py` 和 `repository.py`。

> `gui_b.py` 当前是 **本地 GUI / 调度演示层**，不会假定 A 已完成最新协议，也不会在本阶段主动建立 TCP。

> **重要状态说明：A 目前只是制定了最新协议，A 的 TCP/state 代码尚未完成对应更新。** 因此本阶段完成的是 B 侧 GUI 与调度展示，不代表 A/B 联调已经完成。

## 当前状态与下一步

### 已完成

- B 调度基础与 C 优先级语义。
- B closed-loop runtime 框架。
- B SQLite 状态/历史/命令/评价/日志数据层。
- B-owned `tcpB.py` / `serviceB.py`。
- JSON Lines、4096 bytes、seq/session/step、双时间字段、ACK 处理等协议基础。
- 最新协议中的 `wind_available_kw` / `wind_operating_limit_kw` 已进入 B 模型、TCP parser、dispatch、数据库和测试。
- B PyQt6 GUI `gui_b.py` 已建立，并与 C GUI 保持相同的整体视觉语言。

### 尚需完成

1. **A/B/C 联调最新公共协议**：A 已补齐能力字段并校验 C 报文，下一步验证 C 计算 → A 转发 → B 调度的完整闭环。
2. **A/B socket 联调**：验证状态、dispatch、ACK、半帧/粘包、seq/session/step、timeout、断线重连。
3. **B GUI 接真实数据**：把 `gui_b.py` 的状态、趋势、历史和事件接到 `serviceB.py` / `runtime.py` / `repository.py`。
4. **B 数据追溯验证**：验证 state → dispatch → ACK → 后续 actual state 能从 `ems.db` 复原。
5. **A/B/C 组合闭环**：重点验证 C 保护优先、风机冷启动、operating limit 限制和柴油 10 kW reserve。
6. **STM32 实机验证**：由 C 负责硬件/Wi-Fi/串口，B 配合验证目标闭环。

## 测试说明

2026-09-08 已在 Python 3.11.14 环境执行完整测试：

```bash
python -m unittest discover -s tests -v
```

结果为 76 项全部通过。A 的 PyQt6 主窗口已完成离屏构造检查，C 的主机侧 mock 已完成默认参数和功率曲线检查；尚未进行 STM32 实物、串口及三机联合验证。

预览 B GUI：

```bash
python gui_b.py
```

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
