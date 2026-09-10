# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前采用三模块协作：A 电网模拟器、B EMS 主站、C 风电子站。数据计算职责与基础物理参数已于 2026-09-08 按课程原文统一；仍未冻结的通信和硬件细节不得由代码擅自假定。

## 版本说明

### beta0.3（2026-09-10）

- B：正式拆分为 `B_IO` 通信、`B_COMPUTE` 调度计算和 GUI 三个独立进程；SQLite 仅用于 B 本机进程交接，A/B 跨电脑仍通过 TCP 通信。
- B：`ems.db` 当前为 schema v6，在 v5 的可靠 dispatch outbox、进程心跳、通信配置和完整 SCADA 状态字段基础上，新增风机指令执行评价；参数重复初始化不覆盖用户保存值，状态年龄随本机时间持续增长。
- B：开环决策只记录展示，闭环命令才由通信进程领取；发送前复核 session、step 和状态年龄，进程异常退出后的在途命令标记为 `delivery_unknown`，禁止盲目重发。
- B：补齐 YC/YX/YT/YK 四遥实时点表、UTC 时间段历史曲线、目标/实际区分、调度与后续反馈对应，以及 EMS 调度结果和风机执行效果两类评价展示。
- B：柴油机投运遵守 20 kW 小组最小出力配置，并在可行时协调下调风电目标；新增三进程一键启动入口和失效虚拟环境恢复流程。
- 验证范围：Python 3.11/PyQt6 环境下完整仓库 122 项 PC 自动测试全部通过；本次未重新执行三机公网、STM32/Wi-Fi/UART 或真实设备闭环验收。

### beta0.2（2026-09-10）

- B：新增独立只读的五项调度评价，按供需平衡 30%、风能利用 25%、柴油经济性 15%、运行约束 15%、调度跟踪 15% 计算综合分；支持时间范围筛选与评分标准查看，ACK 仅用于通信诊断、不参与评分。
- C：上位机可经 UART 查询和修改 STM32 连接 A 的地址/端口，并触发 Wi-Fi/TCP 重连；SSID/密码仍为固件编译期本地配置，不进入界面或仓库。
- C：修复参数应用死锁，本地仿真可在运行期采用当前风机参数，并在参数页按 A/B/C 来源展示实时只读数据；C 参数经 TCP 同步到 A 的 `parameter_update` 转发仍未完成。
- 联调：C 记录 STM32 与 A/B/C 主数据链路已跑通；断线重连、超时、分包/粘包、故障保护和上电恢复等异常场景仍待硬件验收。
- 验证范围：PC 自动测试覆盖 A/B、B 评价及 C 上位机的 Wi-Fi 串口帧和参数数据库更新；本次发布未重新编译 STM32 固件，也未重复执行三机硬件测试。

### beta0.1（2026-09-09）

- A：提供场景驱动的电网仿真、统一参数管理、TCP 服务端、`grid.db` 持久化和综合监控界面。
- B：提供状态轮询、风柴调度、ACK/超时处理、运行数据库和 Qt 操作界面。
- C：提供 STM32 风机控制、ESP8266 TCP 客户端与串口上位机；修复 `+IPD` 分包、非阻塞发送、断线重连和命令序号恢复。
- 协议：明确 A/B/C 字段所有权，采用 JSON Lines、`session_id + step + seq` 幂等规则和 `next_command_seq` 恢复机制。
- 验证范围：PC 端自动测试已通过；STM32、ESP8266、三机网络及真实风机硬件仍需实机验证。

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

A UI 当前分为“运行监控、参数设置、历史数据、数据库分类、报警与通信”：

- 运行监控将场景编辑、仿真控制、通信设置、实时 KPI、状态卡片、风速和功率曲线集中在同一页。
- 参数按 `owner/source/updated_at_utc` 展示；A 只能编辑自身所有权参数，B/C 参数经 `parameter_update` 同步为只读副本。
- 历史页在后台线程按 UTC+8 起止时段和 session 读取 SCADA 状态、运行日志与控制追溯，日志可导出并在资源管理器定位；数据库分类页明确分开 YC/YX/YT/YK、设备参数、环境/场景、仿真历史和日志。
- 场景编辑可修改仿真总时长；B/C 连接每 5 s 显示应用层心跳龄并对陈旧记录强制显示超时，协议/数据库错误以非阻塞弹窗提示。
- 顶部时钟及运行/历史曲线读取原始 `sampled_at_utc`，统一换算为 UTC+8 北京时间显示；数据库和 TCP 仍保存 UTC，CSV 的 `sim_time_s` 继续用于场景插值和确定性重放，协议排序仍以 `session_id + step + seq` 为准。
- 通信页显示本机 IPv4；监听地址和端口始终可编辑，TCP 运行中修改会标记为待应用，并作用于下一次启动的 TCP 子进程。

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
├── communication_service.py
├── compute_service.py
├── gui_b.py
├── __main__.py
└── README.md

tests/
├── test_b_dispatch.py
├── test_operator_core.py
├── test_repository.py
├── test_runtime.py
├── test_tcpB.py
├── test_serviceB.py
└── test_b_process_services.py
```

B 正式运行由 `communication_service.py`、`compute_service.py` 和 `gui_b.py` 三个独立进程组成，进程间只通过 B 本机 `ems.db` 短事务交接；A/B 跨电脑仍只走 TCP。

## 开发时间节点 / 功能增量

### 2026-09-10 · B 通信/计算/界面三进程整改

- B_IO 独占 TCP，B_COMPUTE 只读写 `ems.db` 并计算调度，GUI 只监视、配置和把人工闭环请求写入 outbox。
- 开环决策仅记录为 `open_loop`，通信进程永不领取；闭环发送前再次核对 session、step 与动态状态年龄。
- `ems.db` 当前为 schema v6，补齐完整 SCADA 字段、outbox、进程心跳、通信配置及风机指令执行评价；重复初始化不覆盖已保存参数。
- `python -m B_dispatch` 或 `scripts/start_b.ps1` 启动完整三进程，`python -m B_dispatch gui` 仅调试界面。

### 2026-09-10 · C 参数设置运行期可调 + 只读实时展示

- 修复「应用参数」后界面卡死：`database.py` 的 `Lock` 改为可重入 `RLock`，消除
  `update_params` 内再调 `get_params` 的同线程死锁（连接 A 与本地仿真均受影响）。
- 本地仿真参数运行期可调：`simulator.py` 的 `set_params` 增加数值转换与越界钳位，
  切入/额定/切出风速、额定功率、最大顺桨角、控制周期、通信超时、控制模式均可在运行中
  修改并即时生效；风速→功率的三次方曲线框架不变。
- 新建本地仿真时继承数据库当前参数，不再退回默认值。
- 「参数设置」页由静态文字卡改为实时只读网格，展示风速/可用功率/运行上限/目标功率/
  实际功率/桨距角/运行状态/A 链路/周期，均标注来源（A/B/C 计算）且不可修改。

### 2026-09-10 · C 风机指令追踪 + 参数回读证明 + A 参数副本同步

- 固件新增版本化串口帧：`$WIND2`（在 `$WIND` 后追加 `last_wind_action_seq`，-1→NULL）、
  `$PARAM2,<request_id>,<8字段>`（全字段校验通过后原子应用并递增 `parameter_revision`）、
  `$PARAMGET?,<request_id>` 与 `$PARAMGET,<request_id>,<revision>,<8字段>`；保留旧
  `$WIND/$PARAM/$CMD/$WIFI` 兼容解析。新增 `$SYNC,<a_sync_status>,<a_sync_seq>,<reason>`
  上报 STM32→A `parameter_update` 结果。
- 固件在 `$PARAM2` 生效后，于 TCP 单未决事务安全空档（不打断 state/wind_action/ACK）排队
  向 A 发送 source=C 的 `parameter_update`，payload 只含 7 个白名单物理参数，`control_mode`
  不发送；断线/超时 ACK 标记 unknown，不盲目重发。
- 上位机 `wind.db` 原位迁移补齐 `telemetry.last_wind_action_seq` 与参数表
  `parameter_revision/parameter_verified/verified_at_utc/verify_reason/a_sync_*`；回读验证用
  统一 0.01 容差，`$PARAMGET.request_id` 匹配且数值一致才显示「MCU 已生效」，`$SYNC`
  accepted 才显示「A 副本已同步」，两者互不冒充；历史查询增加「参数修改」与同步状态可查。
- 配套 A 汇聚端最小改动（见 `docs/wind-execution-status-extension.md`）：`grid.db` 升至
  schema v6，`state.payload` 恒输出 `controller_wind_enable/pitch_target_deg/
  last_wind_action_seq/last_wind_action_step/wind_action_applied_at_utc` 五字段，`wind_action`
  payload 仍严格四字段。
- 验证范围：PC 自动测试覆盖 A 五字段/幂等/新会话清空、`$WIND`/`$WIND2`/`$PARAM2`/
  `$PARAMGET`/`$SYNC` 解析、parameter_update 白名单与旧库迁移；未重新编译 STM32 固件，未
  执行三机/串口/硬件实测。

### 2026-09-09 · C Wi-Fi 地址/端口运行期可配

- C 的 A 服务器地址/端口不再只写死在固件 `wifi_config.h`，改为「上电默认值 + 运行期可改」：
  - 固件新增 `WifiClient_SetServer()`，USART2 的 `$WIFI,<ip>,<port>` 下发后自动断开并按新配置重连；`$WIFI?` 查询回 `$WIFIGET,<ip>,<port>`。
  - 上位机「运行监控」连接卡片新增「A 服务器 (Wi-Fi)」一行：IP 输入框 + 端口 + 「应用并连接」按钮；连上串口自动查询回填，点击后下发并触发 MCU 重连。
- SSID/密码仍为固件编译期常量，真实值仅本地烧录、不提交；IP/端口默认值使用占位符。
- 验证范围：MCU↔PC 串口与 Wi-Fi↔A 链路已跑通；运行期改地址/端口的重连路径为 PC 端代码检查，实机重连仍需硬件验证。

### 2026-09-09 · A/B 长连接稳定性修复

- 修复 B 把“上一条风机目标高于 C 当前 operating limit”误判为非法 state 并主动断开的问题；这两个字段属于不同控制环节，启动、故障或限值切换期间允许暂时不一致。
- A 的“启动（含 TCP）/继续”现在先确保 TCP 子进程按界面 bind/port 启动，再运行仿真，避免仿真显示 running 但 5000/5005 没有监听、FRP 前端握手后立即断开的假连接。
- B 的公网建连超时从 GUI 的 250 ms 收包窗口中分离为 5 s；收包改为非阻塞轮询，断线后按 1/2/4/8/15 s 上限退避自动重连并重新请求全量状态。
- B 地址框同时接受分开的 host/port 和 `host:port`；A/B socket 启用 TCP keepalive，A 的已识别客户端空闲窗口调整为 30 s。
- 新增真实 A server + B client 连续轮询超过 3 s 的回归测试。本次为本机软件联调，公网隧道、Windows 防火墙和跨电脑链路仍需在目标网络验证。

### 2026-09-09 · A 端口、数据库恢复与曲线写回

- A 的监听地址与端口在 TCP 运行期间仍可编辑，界面会显示当前端点和下次启动待应用端点。
- `grid.db` 缺失或损坏时可直接初始化；重建已有数据库前自动生成带 UTC 时间戳的备份，失败时恢复原库。
- 场景曲线编辑完成后原子同步到源 CSV，并保留“保存当前 CSV”和“另存 CSV”；数据库快照不会擅自覆盖未知文件。

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

> 本节页面说明已按 2026-09-10 三进程整改更新；GUI 的 IP/端口操作现在写入本地数据库，由 B_IO 实际连接。

当前页面：

1. **运行监控**：A 可达 IP、TCP 端口、本机 IP、B_IO 启停；负荷、available、operating limit、actual、target、功率不平衡、柴油余量、动态状态年龄及 YC/YX/YT/YK 点表。
2. **实时曲线**：负荷、风电能力/限制、目标/实际及柴油实际趋势。
3. **本地场景 / 手动调度**：构造 mock `GridState`，验证风优先、operating limit、10 kW reserve、柴油 OFF、C fault 优先和缺供结果。
4. **参数设置**：物理参数按职责只读；B 自有调度、周期、状态年龄和开闭环参数可修改并持久化。
5. **EMS 调度**：手动请求只写 outbox，自动决策由 B_COMPUTE 完成，B_IO 核对最新状态后发送；显示 target unserved、target surplus、reason 和 ACK。
6. **历史数据**：按 UTC 时间段/session 查询 SCADA 曲线、目标/实际、调度及后续反馈、运行日志，并导出筛选结果。
7. **通信诊断**：B_IO/B_COMPUTE 心跳、outbox、seq、ACK 和 GUI 事件日志。
8. **报警与评价**：C fault、状态过期、delivery unknown 以及数据库评价 KPI。

`EMSRepository.initialize()` 会创建/迁移本地 `ems.db` 到 schema v6；已有参数不再被重复初始化覆盖，并包含完整 SCADA state、outbox、通信配置、进程心跳和风机指令执行评价。真实 `data/runtime/ems.db` 不提交仓库。

GUI 可以单独调试（不会自行连接 A 或计算自动调度）：

```bash
python B_dispatch/gui_b.py
```

完整运行使用包入口：

```bash
python -m B_dispatch
python -m B_dispatch run
python -m B_dispatch gui
```

### 当前边界

本阶段 B_IO 具备真实 TCP 接入能力，GUI 只通过本地数据库观察通信状态，**不能把“GUI 能显示 ONLINE”当作 A/B/C 已完成本次联调**。开环不下发，闭环由持久化配置控制；B 不发送 pitch。

## 当前状态与下一步

### 已完成

- B 调度基础、C 优先级和 closed-loop runtime 框架。
- B SQLite 状态/历史/命令/评价/日志数据层，以及计算到通信的可靠 outbox。
- B-owned `tcpB.py` / `serviceB.py`。
- JSON Lines、4096 bytes、seq/session/step、双时间字段、ACK 风险处理。
- `wind_available_kw` / `wind_operating_limit_kw` 已进入 B 模型、TCP parser、dispatch、数据库和测试。
- `ems.db` schema v6 已包含完整物理参数只读副本、完整 SCADA state、进程心跳、通信配置、outbox 和风机指令执行评价；B 自有参数可持久化且初始化不覆盖。
- B PyQt6 GUI 支持 A 端点配置、本地 mock、参数设置、历史导出、通信诊断和调度评价，但不直接接触 TCP。
- B_IO / B_COMPUTE / GUI 已拆为三个独立进程；手动 GUI dispatch 只写入队列，由 B_IO 安全核对后下发。
- STM32 硬件接入调试与三机（A/B/C）主链条联调已完成，状态/调度/动作数据传递暂时正常。

### 尚需完成

> STM32 硬件接入调试与三机（A/B/C）主链条联调已完成，状态/调度/动作数据传递暂时正常；
> 仍未完成异常情况排查（断线重连、超时、半帧/粘包、故障与保护、上电恢复等边界场景）。

1. 异常情况排查：断线重连、通信超时、半帧/粘包、风机冷启动/切出保护、上电恢复等边界与故障场景。
2. B 数据追溯验证，确认 state → dispatch → ACK → 后续 actual state 可从 `ems.db` 复原。
3. A/B/C 组合闭环边界验证：C 保护优先、风机冷启动、operating limit 和柴油 10 kW reserve 的异常分支。

## 测试说明

本次 B 三进程整改完成后，在修复 Qt 环境并补齐四遥界面测试后，完整仓库 **122 项测试全部通过**。公网三机、STM32/Wi-Fi/UART 和真实设备动作没有在本次 PC 整改中重新验证。

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
