# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和 PyQt6 操作界面；B 是 A 的 TCP 客户端，不充当 TCP 中心。

> 本 README 按当前 `common/protocol.md` 更新。计算职责与基础物理参数已确认；传输细节和实机行为仍须联调验证。

## 当前目录

```text
B_dispatch/
├── __init__.py
├── __main__.py        # B 统一启动入口：python -m B_dispatch
├── models.py          # 协议状态、调度参数、调度结果模型
├── dispatch.py        # B 核心约束调度逻辑
├── operator_core.py   # 闭环决策核心：state -> dispatch target
├── runtime.py         # 1 s 采集 / 5 s 调度编排与 B 软件兜底
├── repository.py      # SQLite 本地数据访问层
├── db_schema.sql      # ems.db 结构
├── tcpB.py            # B-owned A-facing TCP JSON-line 客户端
├── serviceB.py        # B-owned TCP + SQLite + runtime 服务桥接
├── gui_b.py           # B / EMS PyQt6 GUI（仿照 C gui.py 风格）
└── README.md

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
├── test_operator_core.py
├── test_runtime.py
├── test_tcpB.py
└── test_serviceB.py
```

B 侧 TCP/服务相关文件统一使用 `*B` 后缀，避免与其他成员混淆。不要重新创建无后缀的 `tcp.py` / `service.py` / `test_tcp.py`。

## 已完成开发节点

### 2026-09-07 · 第一阶段：调度基础

- 建立 `GridState / DispatchConfig / DispatchResult` 和无状态 `EMSCore`。
- 实现风优先、柴发补缺。
- 柴发备用容量固定 **10 kW**；B 常规柴油目标上限为 `diesel_max_kw - reserve_kw`。
- 柴油允许完全 OFF，目标 0 kW 时 `diesel_enable=False`。
- C 对风机保护/控制具有优先权；故障状态下 B 不请求正常风机出力。
- B 只产生 target/enable，不修改 actual，不发送 `pitch_target_deg`。
- 未确认的设备额定功率和风速—功率曲线不硬编码。

### 2026-09-07 · 第二阶段：闭环 Runtime 与 SQLite

- `runtime.py` 默认 1 s 状态采集、5 s 调度决策。
- 状态异常清空缓存；调度异常可产生 B 软件层零风/零柴兜底。
- `repository.py` / `db_schema.sql` 保存参数、运行配置、当前状态、状态历史、dispatch、评价和事件日志。
- SQLite 每次操作独立连接、短事务、外键和 busy timeout。
- `sampled_at_utc`（A 采样时间）与 `received_at_utc`（B 接收时间）分开保存。

### 2026-09-07 · 第三阶段：TCPB 基础与 ACK 风险处理

- `tcpB.py` 实现 B→A `state_request` / `dispatch`，以及 A→B `state` / `ack`。
- UTF-8 JSON Lines，LF 分帧，最大 **4096 bytes（含 LF）**；处理半帧、粘包、多帧，拒绝非法 JSON、NaN/Infinity、错误 envelope。
- 校验 `version/type/source/target/session_id/seq/step/sim_time_s/payload`。
- 首次连接请求全量状态；step 回退、会话变化和旧 seq 会触发重新同步/停止使用旧状态。
- 同一 TCP 连接不允许同时保持未完成的应用层 state request 与 dispatch ACK；dispatch 发送后等待对应 ACK。
- ACK 的 `accepted` / `reason` 被 B 保存；`accepted=true` 只表示 A 接收/校验，不表示 actual 已达到目标。
- 如果 dispatch 已发送但 ACK 因 timeout/断线无法确认，B 标记为 **delivery_unknown**，不会换一个新 seq 静默重发同一命令。

### 2026-09-08 · 第四阶段：按最新公共协议完成 B 对齐

本阶段以当前 `common/protocol.md` 为接口真值；**不修改 A，也不假定 A 已完成实施**。

#### 1. 状态字段对齐

B `GridState` 和 TCP parser 现在明确区分：

| 字段 | B 的语义 |
|---|---|
| `wind_available_kw` | C 根据 A 风速和风机参数计算，经 A 校验、存储并转发 |
| `wind_operating_limit_kw` | C 考虑运行许可、保护和设备上限后计算，经 A 转发 |
| `wind_target_kw` | B 发给 A 的风机目标 |
| `wind_actual_kw` | A 仿真产生的实际风机出力 |

B **使用 `wind_operating_limit_kw` 约束目标，不再使用 `wind_actual_kw` 推断能力**。

这解决了冷启动语义问题：例如 `wind_actual_kw=0`、`wind_available_kw=70`、`wind_operating_limit_kw=70` 时，B 仍可以根据负荷下发风机启动目标，而不会因为 actual 为 0 永远得到 0 目标。

#### 2. 数据库升级

- `current_state` / `state_history` 增加 `wind_available_kw` 和 `wind_operating_limit_kw`。
- `dispatch_commands` 增加 `ack_accepted`、`ack_reason`、`ack_received_at_utc`，用于追踪 A 是否接受命令。
- schema version 升至 **3**。
- `repository.initialize()` 对已有旧数据库执行必要的 v3 字段迁移，避免旧 `ems.db` 因缺列直接失效。

#### 3. TCP 安全边界

- `sampled_at_utc` 按 RFC 3339 UTC 校验，并原样保存。
- `received_at_utc` 由 B 本机生成，不与 A 的采样时间混用。
- 发送 dispatch 后如果 ACK 未知，抛出 `DispatchDeliveryUnknown` 并阻止直接产生新的 dispatch，等待后续重新连接/状态同步处理。
- `serviceB.py` 将 ACK accepted/rejected 和 delivery_unknown 写入 `dispatch_commands`。

#### 4. 测试同步

已同步修改：

- `tests/test_b_dispatch.py`：冷启动、operating limit 上限、actual 不得作为能力值、C 优先、柴油 10 kW reserve。
- `tests/test_operator_core.py` / `tests/test_runtime.py`：使用新的状态模型。
- `tests/test_tcpB.py`：新状态字段、能力关系、RFC 3339、ACK accepted/rejected、未知投递结果、单未决请求、分包/粘包/seq/EOF/timeout。
- `tests/test_repository.py`：schema v3、功率字段、双时间字段和 ACK 追踪。
- `tests/test_serviceB.py`：ACK accepted/rejected 和 delivery_unknown 的数据库记录。

2026-09-08 已在 Python 3.11.14 环境执行仓库完整测试，共 76 项全部通过；尚未进行三机 TCP 和 STM32 实物联调。

### 2026-09-08 · 第五阶段：先做 B PyQt6 GUI，跳过通信联调

按照当前项目安排，本节点**暂时跳过 A/B/C socket 联调**，先完成 B 的 GUI，与 C 已提交的 `gui.py` 保持同一套视觉语言。

GUI 当前提供：

- **运行监控**：负荷、风电 available、operating limit、target、actual、柴油 actual、EMS 闭环状态、C 优先权、状态新鲜度。
- **实时曲线**：轻量级 Qt 绘图，展示负荷、风电能力、目标/实际、柴油和风速趋势，不增加对 pyqtgraph 的硬依赖。
- **调度参数**：风电/柴油额定与 reserve、状态年龄、1 s 采集周期、5 s 调度周期等本地参数入口。
- **手动调度**：可以直接在 GUI 中输入演示状态，调用现有 `calculate_dispatch()` 计算 B 的目标，验证风优先、operating limit、柴油 10 kW reserve、C fault 优先等约束。
- **历史数据**：提供与 `ems.db` 字段对应的演示表格，后续可接 `repository.py`。
- **报警与事件**：记录 GUI 本地调度、参数和当前集成边界事件。

GUI **不会**在当前阶段创建 TCP 连接，也不会假定 A 已经完成最新协议实施。预留了 `set_state_snapshot()` 和 `apply_dispatch_result()` 作为后续接入 `runtime.py / serviceB.py` 的统一入口。

### 2026-09-08 · 第六阶段：统一 B GUI 启动入口

- 新增 `B_dispatch/__main__.py`。
- 默认执行 `python -m B_dispatch` 即启动 `gui_b.py` 的 PyQt6 GUI。
- 同时支持显式命令 `python -m B_dispatch gui`。
- `__main__.py` 只负责启动入口，不重复 GUI 实现；实际窗口仍由 `gui_b.py` 的 `main()` 创建。

## 当前 B 的闭环路径

```text
A state
  │
  ├─ wind_available_kw
  ├─ wind_operating_limit_kw
  ├─ wind_actual_kw
  └─ load_power_kw
        │
        ▼
     tcpB.py
        │
        ▼
   serviceB.py / EMSCore
        │
        ├─ wind target <= operating limit
        ├─ diesel target <= diesel_max - 10 kW
        └─ C fault/priority suppresses normal wind request
        │
        ▼
   dispatch -> A
        │
        ▼
      ACK
        │
        └─ accepted/rejected/unknown -> ems.db
        │
        ▼
   later A state -> actual result
```

B 不负责把 target 变成 actual；A 根据双方启停条件、设备限制和爬坡过程计算 actual。B 不发送桨距命令，桨距/风机保护属于 C。

## 当前边界

1. **三方尚未 socket 联调**：A 已具备新状态字段和 C 能力字段校验，但仍需验证 C → A → B 的真实数据路径。
2. **参数已统一但需实机同步**：风机 100 kW、3/12/25 m/s、三次曲线、0-90 deg；B 不自行修改这些值。
3. **C 侧仍需实现 Wi-Fi TCP**：真实 STM32 需发送含 available/operating limit 的 `wind_action`。
4. **跨设备 ACK/重连行为仍需实机验证**：B 已实现“不盲目换 seq 重发”，但最终重连后的状态协调需要 A/B 联调确认。
5. **GUI 已建立但暂未接真实通信/实时数据库**：当前 `gui_b.py` 是本地演示与接入骨架，通信联调按阶段计划后置。

## 本地运行

Python 统一为 **3.11.x**。

### 一条命令启动 B GUI

在项目根目录执行：

```bash
python -m B_dispatch
```

也可以显式写成：

```bash
python -m B_dispatch gui
```

直接运行 GUI 文件仍然支持：

```bash
python B_dispatch/gui_b.py
```

初始化数据库：

```bash
python scripts/init_ems_db.py
```

运行 B 测试：

```bash
python -m unittest discover -s tests -v
```

`data/runtime/ems.db` 是本地运行数据，不提交 GitHub。

完整计算职责和小组统一参数见 `docs/parameter-ownership.md`。
