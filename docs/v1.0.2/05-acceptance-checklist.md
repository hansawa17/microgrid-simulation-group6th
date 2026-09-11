# v1.0.2 最新评分表逐项核对

状态含义：“已具备”表示代码/自动化证据可查；“现场实测”表示必须使用目标电脑、网络或硬件录屏签字；“设计偏差”是已明确的评分风险，不用文档冒充完成。

| # | 验收点 | 状态 | 代码/演示证据 |
|---:|---|---|---|
| 01 | A 通信、计算、GUI 分工 | 已具备 | GUI 以 QProcess 启动独立 runner/server；计算通过 `grid.db` 交换 |
| 02 | `grid.db` 四遥、参数、场景、历史、日志 | 已具备 | A “数据库分类”8 页签 + repository 测试 |
| 03 | 开始/结束/步长可配，启停暂停继续 | 已具备 | A 场景页三个时间控件与运行控制，GUI 回归 |
| 04 | 风速/负荷曲线可编辑且同时间轴 | 已具备 | CurveEditor + CSV 往返/插值/重采样测试 |
| 05 | 变化召唤/客户端更新 | 已具备 | B/C 周期 `state_request`，A 返回最新完整 state；曲线热更新下一步生效 |
| 06 | A 接收解析 YK/YT 并入库 | 已具备 | `dispatch`/`wind_action`/`parameter_update` 原子事务与 ACK 测试 |
| 07 | 功率不平衡计算入库 | 已具备 | `load - wind_actual - diesel_actual`，current/history/SCADA |
| 08 | 柴发 actual 受目标、上下限和爬坡约束 | 已具备 | A 单步模型；不把 B target 当 actual |
| 09 | 每步历史 | 已具备 | `state_history/scada_history`，sampled UTC 统一 |
| 10 | 连接状态和异常日志 | 已具备 | B/C 心跳龄、WARNING 弹窗、断线/超时故障注入 |
| 11 | A 实时界面、四遥、参数 | 已具备 | A 综合监控、参数所有权及只读副本 |
| 12 | A 历史/日志查询、单位和时间 | 已具备 | UTC+8 筛选、session、变量/单位、JSONL 导出 |
| 13 | B 启动、`ems.db`、通信/计算/GUI | 设计偏差 | 当前项目决策为 B GUI 直接持有 TCP 并用定时器计算；兼容 I/O/compute 进程保留但不是默认入口 |
| 14 | 1 s 采集 YC/YX | 已具备 | `poll_period_s=1`，可配，GUI TCP 非阻塞 |
| 15 | 1 s 检查 YK/YT，只在改变时发送 | 已具备 | 指纹去重 + `e46f8a1` 及回归 |
| 16 | 采集1 s/调度5 s/默认闭环且可配 | 已具备 | B 参数页与 `ems_runtime_config` 持久化 |
| 17 | 风电优先、柴发补偿 | 已具备 | EMSCore/dispatch 测试 |
| 18 | 柴发上下限/缺电 | 已具备 | 20–120 kW 小组基线、reserve、unserved/surplus |
| 19 | B 开/闭环语义 | 已具备 | 开环只记录，闭环才发 dispatch |
| 20 | B 调度历史/日志 | 已具备 | `dispatch_commands/event_log` 和 GUI 页签 |
| 21 | B 实时监视与 A IP | 已具备 | 运行监控、四遥点表、IP/端口和自动重连 |
| 22 | B 历史和目标/actual 追踪 | 已具备 | session 筛选、CSV 导出、调度与后续反馈 |
| 23 | EMS 调度评价 | 已具备 | 五项只读评分；v1.0.2 平衡阈值边界测试 |
| 24 | 风机指令执行评价 | 已具备 | B dispatch–C action–A feedback 关联，数据不足不伪造得分 |
| 25 | 真 STM32 + Wi-Fi，C 为 A 客户端 | 现场实测 | 固件源码已具备；必须当场构建/烧录/连网，mock 不计 |
| 26 | C 接收存储风速/目标/桨距/actual/单位 | 代码已具备+实测 | state 解析、`$WIND2`、`wind.db` |
| 27 | Wi-Fi/控制/UART 并发 | 代码已具备+实测 | 非阻塞主循环状态机；需示波/长跑证据 |
| 28 | 风速启停策略 | 代码已具备+实测 | 3/25 m/s 保护基线，现场演示无风/正常/高风 |
| 29 | 可用功率 | 代码已具备+实测 | C 三次曲线和 available/limit 约束 |
| 30 | 桨距目标 | 代码已具备+实测 | C 控制计算，A actual 应用效果 |
| 31 | C 开/闭环和控制返回 | 代码已具备+实测 | 开环不发 action，闭环发 action/ACK |
| 32 | C 串口实时数据 | 现场实测 | C GUI 显示 `$WIND2`，记录 COM 口与频率 |
| 33 | `wind.db` | 已具备 | 8 类表、迁移、参数/遥测/日志回归 |
| 34 | C 实时 GUI + A IP | 已具备+实测 | UART 下发 `$WIFI`，固件持有 A TCP |
| 35 | C 参数修改/回读/一致 | 代码已具备+实测 | `$PARAM2/$PARAMGET/$SYNC`，分开 MCU 已生效与 A 已同步 |
| 36 | C 历史 | 已具备 | `wind.db` 遥测/控制/通信/系统日志查询 |
| 37 | 三台 PC + MCU 真实网络 | 现场实测 | 按 `06-onsite-demo-runbook.md` 录屏 |
| 38 | A/B/C 独立数据库 | 已具备+实测 | 三机展示各自 DB 路径，不共享文件 |
| 39 | 接口/时间语义 | 已具备 | `common/protocol.md` + UTC/sim time 区分 |
| 40 | 测量–决策–动作–反馈全链 | 现场实测 | 用 session/step/seq 从三库展示一条完整记录 |
| 41 | 无风/正常/高风 | 软件已覆盖+实测 | CSV 含 >=25 m/s；高风应禁运，必须现场硬件复核 |
| 42 | 低/高负荷与柴发边界 | 软件已覆盖+实测 | A/B 测试 + 现场曲线切换 |
| 43 | MCU 无线断线识别/恢复 | 现场实测 | v1.0.2 自动 `AT+RST`/重连，现场记录恢复时间 |
| 44 | EMS 断线识别/恢复 | 软件已覆盖+实测 | B 指数退避，A 告警弹窗，故障注入 |
| 45 | 完整源码与运行说明 | 已具备 | `01-source-and-run.md` |
| 46 | Git 仓库与版本 | 已具备 | `02-version-management.md` + `v1.0.2` 标签/Release |
| 47 | 系统与接口文档 | 已具备 | `03-system-and-interfaces.md` + `common/protocol.md` |
| 48 | 硬件工程与部署文档 | 已具备 | `04-hardware-and-deployment.md` + CubeIDE 工程 |
| 49 | 规定工况与一条全闭环记录 | 现场实测 | `07-test-record.md` 模板，需当场填实际值/截图 |
| 50 | 个人分工与接口问题说明 | 现场口试 | `08-personal-briefing.md`，三人分别补全姓名/贡献 |

## 验收前仍必须完成

1. STM32CubeIDE 现场 Clean/Build 且 0 error，烧录该标签对应固件。
2. 三机与 STM32 实物联调，记录 37、40–44、49 的实际时间和截图。
3. 向老师主动说明第 13 项的设计差异：按项目最终决策，B GUI 直接持有 TCP；不应伪称默认是三进程。
