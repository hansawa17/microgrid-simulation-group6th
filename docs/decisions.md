# 决策与实施计划

## 已确定的小组方向

- A 刘雨杭、B 李佳霖、C 陈信甫；一个共享 Git 仓库。
- 单片机通过 Wi-Fi 模块联网，拟使用手机热点。
- 原文明确 A 为 TCP 服务端，B 与单片机为客户端；C 上位机通过串口连接单片机。
- 已对照新上传作业原文核对各模块职责、周期、提交要求和验收标准，见 requirements.md。

## 2026-09-07：统一时间轴与授时接口

- A 作为唯一仿真时间和状态采样时间源；B/C 不自行推进或推算 A 的时间。
- CSV 保留可重复的 `step`、`sim_time_s`、风速和负荷曲线，不写本次运行日期。
- A 每生成一步状态只读取一次系统 UTC 授时，并为该断面统一生成 RFC 3339 格式的 `sampled_at_utc`。
- A 的 `state.payload` 增加必填 `sampled_at_utc`；B/C 原样保存，并分别记录本机 `received_at_utc`。
- 日志的实际发生时间使用 `created_at_utc`；控制顺序使用 `session_id + step + seq`，不依赖电脑墙钟排序。
- 当前线协议仍为草案 `version=1`，三方分别开发时按 `docs/time-interface.md` 实现，组合联调前完成字段一致性检查。
- A 已完成状态模型、`grid.db` schema v2、TCP `state` 和默认 10 分钟场景适配；B/C 对应模型与数据库字段仍由各模块分别完成。

## 2026-09-07：Python 与 Qt 运行环境

- A/B/C 的 PC 端 Python 代码统一使用 Python 3.11.x；本机验证环境为 Python 3.11.14。
- Qt 界面统一使用 PyQt6，当前共享依赖固定为 `PyQt6==6.11.0`。
- 根目录 `.python-version` 声明 Python 3.11，`requirements.txt` 声明共享 PC 端依赖。
- 每台电脑使用各自的 `.venv`，虚拟环境不提交；STM32 固件工具链不受此决定影响。

## 2026-09-07：公网 TCP 穿透通信方案（草案）

- 正式拓扑保持课程原文职责：A 为 TCP 服务端，B 与 STM32/Wi-Fi 为 TCP 客户端，C 上位机仅通过 UART 连接 STM32。
- A 电脑运行 raw TCP 内网穿透客户端，把公网域名/IP及端口映射到 A 本机 `127.0.0.1:5000`；B 与 STM32 使用普通 TCP 连接公网端点。
- 公网端点由 B 界面和 STM32/Wi-Fi 的本地配置输入，不硬编码；Radmin 地址不作为正式通信端点。
- 当前 v1 协议没有认证和加密。正式公网运行前必须确认穿透访问控制、来源限制、TLS 或应用层认证方案，凭据不得提交仓库。
- 完整拓扑、报文、ACK、重连与联调顺序见 `docs/tcp-interface-design.md`。

## 2026-09-07：状态增加风机能力字段（历史草案，已废止）

> 本节只保留决策轨迹，不得据此实现。计算方以紧随其后的 2026-09-08 决策和 `docs/parameter-ownership.md` 为准。

- `state.payload` 增加必填有限非负数 `wind_available_kw` 和 `wind_operating_limit_kw`，单位均为 kW。
- `wind_available_kw` 由 A 根据当前风速和已配置风机功率曲线计算并受额定功率限制；不考虑目标、启停、桨距或实际爬坡。
- `wind_operating_limit_kw` 由 A 在资源可用功率基础上考虑 C/STM32 启停许可、保护/故障、当前桨距和设备运行上限计算；不考虑 B 当前目标、B 当前启停请求或实际爬坡。
- B 使用 `wind_operating_limit_kw` 约束风机目标，解决以 `wind_actual_kw` 估算能力造成的冷启动和限功率误判。
- `rated`、`available`、`operating_limit`、`target`、`actual` 分别表示额定值、风资源能力、运行约束后的稳态上限、B 指令和 A 实际输出，不得互相替代。
- 线协议仍处于未冻结的 version 1 草案阶段，本次不升级版本；A/B/STM32 必须在组合联调前同步实现并补字段一致性测试。

> 以上能力字段的计算方已被 2026-09-08 决策修正：课程原文明确当前可用功率由 C 单片机计算，A 只校验、存储和转发公共能力值。

## 必须共同确认（不得擅自当成已定要求）

- [ ] 三人确认 requirements.md 任务分解并认领，保留后续接口修改记录。
- [ ] STM32 型号、Wi-Fi 模块型号、AT 固件、UART 分配及电气接线。
- [x] 风机额定值、切入/额定/切出风速、风速功率曲线、桨距模型（见 2026-09-08 决策）。
- [x] 柴发最小/最大功率、爬坡与备用容量基线（启停/补偿的实机动态仍需联调）。
- [ ] B 调度启停与 C 保护动作的优先级；各类开闭环组合。
- [ ] TCP JSON 草案能否在实际 Wi-Fi 模块/MCU 内存预算内实现。
- [ ] C 串口帧头、长度、消息号、校验、编码、字节序和最大帧长。
- [ ] raw TCP 穿透服务、固定公网端点、访问控制以及 STM32 DNS/TLS 能力。
- [ ] C 控制周期、超时、安全状态、恢复/重连行为和命令有效期。
- [ ] 数据库表结构；Python/Qt 运行环境已在上文确定。

## 实施顺序

1. 复核要求和协议，确定最小测试场景。
2. A 实现场景/仿真；B 使用样例状态测试调度；C 先验证 Wi-Fi 和串口。
3. 跑通三方一轮收发，再接入数据库/界面和完整周期任务。
4. 逐条执行 acceptance.md，记录硬件结果、异常和复现步骤。

每次决策追加日期、参与成员、结论与受影响文件，不仅口头通知某一个 Agent。

## 2026-09-09：修复 A/C TCP 收包、周期事务与重启序号恢复

- 参与：A/C 公共通信联调；本次由共享仓库直接跨模块修复。
- 运行数据库显示 A 能收到 C 的首次合法报文，但随后没有任何 C `wind_action`，并反复触发 10 s 空闲超时。根因是 ESP8266 单连接格式 `+IPD,<len>` 的长度解析从第二位数字开始，A 的 495 字节 state 被误读为 95 字节。
- C 的 USART1 接收改为环形缓冲，`+IPD` 载荷可跨多次 Poll 搬运，同时支持单连接与多连接格式；裸 `>` 在普通行之前识别。
- C 的应用层固定为单未决 `state_request → state → wind_action → ack`，`CIPSEND` 等待 prompt/SEND OK，不再阻塞主循环或在未获 prompt 时发送正文。在线等待响应时保持上一状态，不以本地随机值冒充 A 状态。
- TCP 重连不清零 C 发送序号；为覆盖 MCU 整机重启，A 在 state 中按 `session_id + source` 返回 `next_command_seq`。C 只提高本地新命令序号，不改变 ACK 未知命令用原报文/原 seq 重发的幂等规则。
- 未更改 A/B/C 计算职责、功率字段单位或控制权；受影响文件为 A server/repository、C ESP/Wi-Fi/风机任务、公共协议、通信设计与相关 README/测试。
- 当前仍为软件级修复，真实 ESP8266 AT 固件、STM32 RAM 余量、热点断线和上电重连必须按 acceptance.md 留下实机记录。

## 2026-09-08：按课程原文冻结计算职责与参数基线

- 参与：A/B/C 共享仓库；依据为课程 PDF 第 2 节、3.1、3.2、3.3。
- A 从独立曲线读取/插值风速与负荷，计算风机/柴发实际出力及功率不平衡；A 不生成 B 调度目标，也不替代 C 发布公共可用功率。
- B 读取 A 状态，按风电优先、柴发补偿和备用约束计算风机/柴发目标；B 不写 actual 或桨距。
- C 单片机接收 A 风速与 B 风机目标，计算可用功率、运行许可和目标桨距角；C 上位机维护参数并通过 UART 写回 MCU。
- `wind_available_kw` 与 `wind_operating_limit_kw` 由 C 经 `wind_action` 发送，A 校验/存储并在 state 中转发；A 可内部复算物理上限，仅用于 actual 安全裁剪。
- 小组数值基线：风机 100 kW；切入/额定/切出 3/12/25 m/s；切入到额定为归一化三次曲线；0 deg 满功率、90 deg 完全顺桨；风机爬坡 40/60 kW/s。
- 柴发基线：20-120 kW，爬坡 30/40 kW/s；B 保留 10 kW 备用。C 控制周期 1 s、通信超时 3 s。
- 课程原文规定职责但未指定上述数值；文档必须称其为“小组统一配置”。
- 受影响：`docs/parameter-ownership.md`、`common/protocol.md`、A/B/C README、A 配置/协议校验、C 固件与 host mock。

## 2026-09-08：A 综合监控 UI、UTC 显示与参数副本同步

- 参与：A/B/C 共享仓库；本次实现由 A 主导，公共参数同步接口需 B/C 后续接入并联调。
- A 的“场景曲线”和“运行监控”合并为综合运行页：通信与仿真控制、场景编辑、实时 KPI、连接/设备状态、风速曲线和功率曲线在同一工作区完成。
- A 新增参数设置、历史数据、报警与通信视图。历史表和曲线读取 A 自己的 `grid.db`；不通过共享 SQLite 文件跨电脑同步。
- 参数行记录 `name/value/unit/owner/source/updated_at_utc/editable`。A UI 只允许编辑 owner=A 且 editable 的条目；B/C 所有权参数在 A 侧只读，通过 TCP `parameter_update` 更新副本。
- `parameter_update` 保持协议 version 1 与公共 envelope，不改变计算职责。B 只能同步 `reserve_kw/b_poll_s/b_dispatch_s`；C 只能同步风机额定/风速/桨距参数及 C 周期/超时。A 校验整条更新并写参数历史，越权或部分非法时不落库。
- C 的物理参数变化后，A 立即作废缓存的 C action，设置为停机、完全顺桨、available/operating limit 为 0；必须等待 C 基于新参数重新发送合法 `wind_action`。A 不代替 C 计算新动作。
- 顶栏和曲线“实时”语义统一为操作系统 UTC 授时：运行/历史横轴直接使用 `sampled_at_utc`；CSV 预览可使用打开时 UTC 加 `sim_time_s` 的派生标签，但不写回 CSV、不冒充真实采样时间。
- 模型仍用 CSV 的 `sim_time_s` 插值；协议与控制顺序仍严格使用 `session_id + step + seq`。系统校时、不同电脑墙钟偏差或 UI 刷新不会改变命令排序。
- A 通信设置显示本机 IPv4，并允许在 TCP 子进程停止时修改 bind/port；运行中锁定，修改仅在下一次启动生效。客户端连接 A 的实际 IP/域名，不能连接 `0.0.0.0`。
- FRP 联调可将公网 `frp-box.com:38243` 映射到 A 本机自定义端口（例如 `127.0.0.1:5005`）。B/C 的 host 与 port 分开填写；A UI 只配置本地监听，不管理 FRP 隧道。
- 新增 `scripts/start_a.ps1` 与根目录 `启动A程序.bat`。入口只在数据库缺失时初始化，已有 `grid.db` 不覆盖；本地数据库、`config.local.json` 和穿透凭据仍不提交。
- 安全边界：协议 v1 的 source/type 白名单不是身份认证；公网联调仍需受控隧道或来源限制。当前未完成 STM32 实机、UART、三机公网闭环及授时异常联调，不得据本次 PC 软件结果宣称硬件已验证。
- B 的新 TCP 客户端不再从 0 重置发送序号，而以 epoch 毫秒作为进程启动基线并在进程内维护高水位；这是避免同一 A session 内 GUI 断开重连后被判为旧序号的兼容措施，最终跨设备序号持久化仍需在协议冻结前联调确认。
- A 在 TCP 服务启动、停止或 GUI 子进程结束时统一将 B/C 标记离线；已识别连接连续 10 s 没有报文即按空闲超时关闭并记日志。10 s 仅是当前 PC 端连接探活实现值，不等同于尚未冻结的控制指令有效期。
- 停止或完成的仿真可创建新会话：生成新的 `session_id`，从场景起点恢复为 ready，清零目标与实际出力、停止设备并完全顺桨；旧会话状态历史、场景和已确认参数保留，A 的服务端响应序号不倒退。
- 同值 `parameter_update` 视为幂等同步并返回成功，但不改写参数来源/更新时间、不追加 `parameter_history`。损坏或不可迁移的 `grid.db` 不自动覆盖，A GUI 显示异常状态，由操作员备份后另建数据库。
- 状态历史以数据库写入顺序读取，实时曲线只画当前 `session_id`，历史页通过会话选择器查看旧会话，禁止跨会话连线。计算子进程意外退出且数据库仍为 running 时，A GUI 自动转为 paused 并记录告警，允许人工检查后继续。
- A UI 固定按 UTC+8 北京时间显示顶部时钟、实时/历史横轴和各类时间列；`sampled_at_utc/received_at_utc/created_at_utc` 的名称、UTC 存储与 TCP 内容不变，时区换算不参与控制排序。
- 历史页的 SQLite 查询由单独工作线程和独立短连接执行，快速切换筛选时只保留最新请求；曲线读取用户选择的记录窗口，状态和日志表各最多创建最近 1000 行控件，避免主线程因大批量 `QTableWidgetItem` 和 `ResizeToContents` 阻塞。
- 受影响：A GUI/数据库/TCP、`common/protocol.md`、`docs/time-interface.md`、A 与根 README、启动脚本。

## 2026-09-09：A/B 合法过渡状态与长连接恢复

- 现场 `grid.db` 显示 A 的启动状态为 `wind_operating_limit_kw=0`、`wind_target_kw=100`；A 日志同时显示 B 在短时间内反复连接后由客户端关闭，且没有 `frame_rejected` 或 `peer_timeout`。根因是 B 错把 `wind_target_kw <= wind_operating_limit_kw` 当作 state 不变量。
- `wind_target_kw` 是 B 上次目标，`wind_operating_limit_kw` 是 C 当前限制。启动、C 故障或限制突变后两者允许暂时不一致，B 应接收该状态并根据当前限制产生下一条调度；本次不修改协议字段或计算所有权。
- B 的 TCP 建连使用独立 5 s 超时，状态请求 6 s 无响应视为半开连接；GUI 非阻塞收包，异常后按 1/2/4/8/15 s 上限退避自动重连并先请求全量状态。地址输入兼容 `host:port`，但不接受 URL。
- A/B 已连接 socket 启用 TCP keepalive/TCP_NODELAY；A 已识别客户端空闲窗口由 10 s 调整为 30 s，监听积压队列为 16。B 的周期 `state_request` 仍是主要应用层心跳。
- 后续现场日志显示 A 仿真在 15:37-15:42 运行，但本机 5000/5005 均无监听且 A 无同期 TCP 连接记录；公网 FRP 入口能握手、却在首个 state 前关闭，说明隧道后端没有可用 A server。A GUI 因此将“启动/继续仿真”与“确保 TCP 子进程启动”联动，失败时不进入 running。
- B 对回环地址显示同机限定提示；公网端点在首个合法 state 前 EOF 时显示“检查 A TCP 服务和隧道后端端口”的针对性诊断。该提示不改变协议和重连安全边界。
- 本次只修改 A、B、测试和说明，没有修改 C。软件回归覆盖真实 loopback A server + B client 连续轮询超过 3 s；公网隧道、跨电脑防火墙和硬件链路必须另行实测。

## 2026-09-10：beta0.2 评价与 C 运行期配置边界

- B 的综合评价固定为五项只读指标：供需平衡 30%、风能利用 25%、柴油经济性 15%、运行约束 15%、调度跟踪 15%。评价只读取 `ems.db` 既有数据，不发送命令、不修改调度；ACK 继续用于通信诊断，但不进入综合评分。
- C 上位机通过 UART 草案帧 `$WIFI,<host>,<port>` 修改 STM32 连接 A 的运行期地址并请求重连，通过 `$WIFI?` / `$WIFIGET,<host>,<port>` 查询当前值。地址仅保存在 MCU RAM，重新上电后恢复 `wifi_config.h` 的占位默认值。
- Wi-Fi SSID 与密码仍是固件编译期本地配置；真实凭据不得写入 GUI、日志或 Git。此次功能不改变 `B → A ← STM32` 拓扑，也不让 C 上位机直接连接 A。
- C 风机参数修改当前写入本机 `wind.db`，并作用于本地仿真或通过 `$PARAM` 下发 STM32；C 经 TCP 向 A 发送 `parameter_update` 仍未完成，因此不得宣称 A 的 C 参数副本会随上位机修改自动更新。
- 公共 TCP 协议仍为 version 1，字段所有权、单位和控制权不变；新增 `$WIFI` 系列属于尚未冻结的 C 上位机↔STM32 串口草案。
- C README 记录 STM32 与三机主链条数据传递已跑通，但异常场景尚未完成；beta0.2 发布未重新执行固件编译或硬件测试，不把 PC 自动测试当作硬件验收。

## 2026-09-10：B 正式运行拆分为通信、计算、GUI 三进程

- 参与：B 模块整改；依据为作业原文“通信、计算、界面独立进程”和本仓库不得通过共享 SQLite 跨电脑通信的约束。
- `B_IO` 是 B 内唯一 TCP 所有者：向 A 周期请求完整 state、落入 B 本机 `ems.db`，从 `dispatch_outbox` 原子领取闭环命令并记录 ACK。`B_COMPUTE` 不导入 TCP，只从数据库读取状态/参数并写决策；GUI 不持有套接字、不运行自动调度，只操作本机数据库。
- SQLite 仅用于同一台 B 电脑上的进程交接，不用于 A/B/C 跨电脑通信。每次仓储操作使用独立短连接，outbox 通过 `BEGIN IMMEDIATE` 领取，避免计算和通信进程重复发送同一条命令。
- 开环决策写为 `open_loop`，不进入发送队列；闭环决策写为 `pending`。通信进程发送前再次核对 state 的 `session_id + step` 和动态年龄，旧状态、跨会话状态或已前进状态一律取消。
- 已领取但进程异常退出的 `sending` 命令在重启时标记为 `delivery_unknown`，不得换 seq 盲目重发。该规则延续现有 ACK 未知安全边界。
- `ems.db` schema 升至 v5，补齐 state 的 `diesel_target_kw`、`diesel_running`、`power_imbalance_kw`，新增 outbox、进程心跳、通信配置和 `max_state_age_s`。初始化只补缺省行，不再覆盖 B 用户已保存参数。
- 柴油机一旦启动必须满足小组配置的最小出力；为避免可消除的目标过剩，B 会在允许范围内同步下调风电目标。无法消除的过剩仍显式记录，不伪装为平衡。
- 本次没有改变公共 TCP version、字段单位或 A/B/C 控制权；B 仍只发送风机/柴油 target 与 enable，C 动作和 A actual 职责不变。受影响文件为 B 调度、仓储、TCP、GUI、启动入口、数据库 schema、测试和 B 文档。
- 当前验证仅覆盖 PC 端自动测试与语法检查；三机 TCP、公网穿透、STM32/Wi-Fi/UART 和真实设备动作尚未在本次整改中验证。

## 2026-09-10：beta0.3 发布范围

- beta0.3 以 B 验收整改为发布主题，包含三进程运行边界、ems.db schema v5、dispatch outbox、参数持久化、动态状态年龄、四遥点表、历史反馈追溯、分项评价和一键启动。
- 发布前在 Python 3.11 与 PyQt6 6.11.0 环境执行完整仓库测试，122 项全部通过；同时完成 B 源码语法与 Git 差异检查。
- 本版本没有更改公共 TCP version、字段单位或 A/B/C 控制权，也没有把 C 物理参数开放给 B 修改。
- beta0.3 是 PC 软件验收候选版本，不代表三机公网、STM32/Wi-Fi/UART、真实断线恢复或完整硬件闭环已经在本版本重新实测。

## 2026-09-10：新版风机指令执行评价扩展契约

- 新版评分表在 B 第24项 EMS策略评价后新增“风机指令执行评价”，要求关联启停/功率指令、实际运行状态、实际出力、目标桨距和可用功率；评分表仍写 B/24分且与 C 的25号重复，计分方式待教师确认，但功能先按必做整改。
- 公共 TCP `version` 保持1。A 的 state 计划增加 `controller_wind_enable`、`pitch_target_deg`、`last_wind_action_seq`、`last_wind_action_step`、`wind_action_applied_at_utc`；精确定义以 `docs/wind-execution-status-extension.md` 为准。
- C→A `wind_action.payload` 继续严格保持 `wind_enable/pitch_target_deg/wind_available_kw/wind_operating_limit_kw` 四字段。动作序号和引用step来自公共 envelope，应用时间由 A 在合法动作落库时生成，禁止重复塞入payload造成严格校验错误。
- 采用滚动兼容：A升级后发送全部扩展键；B接收旧A缺字段时保存NULL并标记数据不足，不断线、不把缺失当0；C忽略不认识的state附加键。错误类型、NaN、越界、乱序和冲突仍必须拒绝。
- B新增独立风机执行评价，不覆盖现有EMS策略评分。默认小组权重为启停30%、功率跟踪35%、桨距响应20%、能力与安全15%；数据不足不评分，保护性停机和A爬坡必须结合上下文解释。
- C串口新增版本化 `$WIND2/$PARAM2/$PARAMGET`，保留旧 `$WIND/$PARAM` 兼容。MCU参数通过同request_id有效值回读证明生效，再由STM32在TCP单未决事务空档向A发送C白名单parameter_update；GUI分别展示“MCU已生效”和“A副本已同步”。
- 本次仅冻结规范并生成 B/C 工作流提示词，尚未实现代码、数据库迁移或硬件验证，不得据此宣称新版验收项已完成。
