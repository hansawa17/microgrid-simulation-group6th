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

## 启动 A 综合监控界面

Windows 在仓库根目录双击 `启动A程序.bat`，或执行：

```powershell
.\scripts\start_a.ps1
```

一键脚本会从脚本位置定位仓库，在缺少环境时调用 `scripts/bootstrap.ps1`，并且只在目标数据库不存在时初始化。已有 `grid.db` 不会被覆盖，本地数据库和 `config.local.json` 不提交 Git。

可从命令行指定数据库、配置、场景及 GUI 初始监听设置：

```powershell
.\scripts\start_a.ps1 -BindAddress 0.0.0.0 -Port 5005 `
  -Db .\data\runtime\grid.db `
  -Config .\A_simulator\config.example.json `
  -Scenario .\A_simulator\scenarios\antarctic_10min.csv
```

A UI 当前分为“运行监控、参数设置、历史数据、报警与通信”：

- 运行监控将场景编辑、仿真控制、通信设置、实时 KPI、状态卡片、风速和功率曲线集中在同一页。
- 参数按 `owner/source/updated_at_utc` 展示；A 只能编辑自身所有权参数，B/C 参数经 `parameter_update` 同步为只读副本。
- 历史页在后台线程读取 `state_history` 和日志，合并快速切换产生的重复刷新请求，并限制表格一次渲染的行数，避免大数据库打开页面时阻塞 GUI。
- 顶部时钟及运行/历史曲线读取原始 `sampled_at_utc`，统一换算为 UTC+8 北京时间显示；数据库和 TCP 仍保存 UTC，CSV 的 `sim_time_s` 继续用于场景插值和确定性重放，协议排序仍以 `session_id + step + seq` 为准。
- 通信页显示本机 IPv4；监听地址和端口仅能在 TCP 停止后修改，并作用于下一次启动的 TCP 子进程。

FRP 示例：公网 `frp-box.com:38243` 可映射到 A 本机 `127.0.0.1:5005`。A 设置本地端口 `5005`；B/C 客户端的主机与端口应分开填写为 `frp-box.com` 和 `38243`。公网端点、令牌和密码不能硬编码或提交；协议 v1 尚无认证和加密。

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

### 2026-09-09 · B Dispatch ACK 超时与连接状态修复

- 定位到 B GUI 使用 `timeout_s=0.25`，而 `EMSTcpClient.send_dispatch()` 原先直接复用这个短 socket timeout 等待 A 的 ACK；A 已经收到 dispatch 后，只要处理超过 250 ms，B 就会错误进入 `DispatchDeliveryUnknown` 并主动关闭 TCP。
- `B_dispatch/tcpB.py` 现在为 dispatch ACK 使用独立的至少 **2 s** 等待窗口，普通 state polling 仍可保持短 timeout，ACK 成功后恢复普通 polling timeout。
- 保留 delivery-unknown 安全边界：真正超时仍禁止自动换新 seq 盲目重发。
- 这同时解释并修复了“**A 端 dispatch 已更新、B 弹 ACK 错误并断连，但主页仍显示已连接**”这一现象的主要来源。

### 2026-09-09 · A/C TCP 收包与重连闭环修复

- 修复 ESP8266 单连接 `+IPD,<len>` 长度漏读首位导致 A 的 state 被截断、C 只连接不发 `wind_action` 的问题。
- C 改为环形接收缓冲和非阻塞 `CIPSEND`，按 `state_request → state → wind_action → ack` 单未决事务运行。
- A 在 state 中返回当前会话、当前 source 的 `next_command_seq`，供 STM32 重启后恢复发送序号；普通 TCP 重连不再清零 seq。
- A/B Python 逻辑与 GUI 回归已通过；ESP8266/STM32 实机、热点断线和上电恢复仍需硬件验证。

### 2026-09-08 · B GUI 手动调度等待最新状态后自动下发

- 修复 `B_dispatch/gui_b.py` 点击“下发当前调度”时遇到未完成 `state_request` 直接失败的问题。
- 手动下发现在先排队并获取 A 的最新 `state`；若已有请求正在等待，则复用该请求，不重复发送。
- 收到最新 `state` 后重新计算 EMS decision，再自动发送 `dispatch` 并处理 ACK；不再使用点击瞬间可能已经过期的旧 decision。
- 自动闭环调度不会与手动排队请求、state request 或 dispatch ACK 争抢同一 TCP 事务。
- ACK 未知仍保持原有安全策略：不自动换新 seq 盲目重发。

### 2026-09-08 · A 综合监控 UI 与一键启动

- 合并场景曲线和运行监控，新增参数、历史、日志/通信页面。
- 实时/历史横轴将 A 的 UTC 采样时间换算为 UTC+8 北京时间显示，同时保留原始 UTC 字段和 CSV 相对仿真时间语义。
- 增加 B/C 参数副本同步、来源与所有权显示；不改变 A/B/C 原有计算职责。
- 增加本机 IPv4、可配置监听地址/端口和一键启动入口。
- 当前为 PC 端软件实现，STM32、UART、三机公网闭环和授时异常仍需实机验证。

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
4. **参数设置**：统一基线从 `ems.db` 读取并按职责展示；生产基线不允许由 GUI 任意改写。
5. **EMS 调度**：手动计算/下发和可选自动闭环；显示 target unserved、target surplus、reason、ACK；手动下发会等待最新 A state 后自动重算并发送。
6. **历史数据**：读取 `ems.db/state_history`、session 过滤和 CSV 导出。
7. **通信诊断**：连接、pending request、full-sync、seq、ACK 和 GUI 事件日志。
8. **报警与评价**：C fault、状态过期、delivery unknown 以及数据库评价 KPI。

`EMSRepository.initialize()` 会创建/迁移本地 `ems.db` 到 schema v4，并把统一数据库基线写入三个参数层：`physical_parameters` 保存 A/B 只读物理参数副本；`dispatch_parameters` 保存 B 调度限值；`ems_runtime_config` 保存 B/C 周期与闭环基线。真实 `data/runtime/ems.db` 不提交仓库。

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
- `ems.db` schema v4 已增加完整物理参数只读副本，并固定 B dispatch / runtime baseline。
- B PyQt6 GUI 已进入 `B_dispatch/gui_b.py`，支持 A IP/端口、真实 TCP 接入、本地 mock、参数展示、历史导出、通信诊断和调度评价。
- 手动 GUI dispatch 已支持等待最新 A state 后自动重算和下发。

### 尚需完成

1. A/B/C 联调最新公共协议，验证 C 计算 → A 转发 → B 调度完整闭环。
2. A/B socket 联调，验证状态、dispatch、ACK、半帧/粘包、seq/session/step、timeout、断线恢复。
3. B 数据追溯验证，确认 state → dispatch → ACK → 后续 actual state 可从 `ems.db` 复原。
4. A/B/C 组合闭环，重点验证 C 保护优先、风机冷启动、operating limit 和柴油 10 kW reserve。
5. STM32 实机验证，由 C 负责硬件/Wi-Fi/串口，B 配合验证目标闭环。

## 测试说明

本次 A 综合监控改造在变基前已于 Python 3.11.14 环境完成 94 项测试并全部通过。提交前又合入了队友最新的 B `ems.db` schema v4 更新；按用户要求未重复执行合并后工作树的完整测试，因此这里不把变基前结果冒充为最终三模块联合验证。

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
