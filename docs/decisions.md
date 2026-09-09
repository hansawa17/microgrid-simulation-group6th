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
- 本次只修改 A、B、测试和说明，没有修改 C。软件回归覆盖真实 loopback A server + B client 连续轮询超过 3 s；公网隧道、跨电脑防火墙和硬件链路必须另行实测。
