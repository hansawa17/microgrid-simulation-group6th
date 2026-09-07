# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前项目采用三模块协作：A 电网模拟器、B EMS 主站、C 风电子站。未冻结的物理参数、通信细节和硬件行为不得被代码擅自假定。

## 分工与入口

| 成员 | 模块 | 代码位置 | 核心交付 |
|---|---|---|---|
| A 刘雨杭 | 电网模拟器 | `A_simulator/` | 场景、风柴实际出力仿真、TCP 服务端、grid.db、Qt 界面 |
| B 李佳霖 | EMS 主站 | `B_dispatch/` | 状态采集、调度、开闭环模式、ems.db、Qt 界面 |
| C 陈信甫 | 风电子站 | `C_controller/` | STM32 控制、Wi-Fi TCP 客户端、串口上位机、wind.db |

运行关系：`B ↔ A ↔ STM32`；`STM32 ↔ C 上位机（串口）`。A 是 TCP 服务端，B 和 STM32 主动连接 A。C 对风机保护/控制具有优先权。

## B EMS 当前结构

```text
B_dispatch/
├── __init__.py
├── models.py
├── dispatch.py
├── operator_core.py
├── runtime.py
├── repository.py
├── db_schema.sql
└── README.md

scripts/
└── init_ems_db.py

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
├── test_operator_core.py
└── test_runtime.py

data/runtime/ems.db   # 本地运行生成，*.db / data/runtime/ 不入 Git
```

## 开发时间节点 / 功能增量

### 2026-09-07 · 第一阶段：B EMS 调度基础

- 建立 `B_dispatch/` Python 包。
- 建立 `GridState`、`DispatchConfig`、`DispatchResult` 数据模型。
- 实现风电优先、柴油补偿调度。
- 柴油备用容量固定为 **10 kW**；柴油允许完全 OFF。
- 正常 B 调度柴油上限为 `diesel_max_kw - 10 kW`。
- C 故障/保护优先时，B 停止请求风机出力。
- 对过期状态执行拒绝，避免旧状态驱动闭环。
- 风机/柴油机额定功率必须显式配置，不在代码中隐藏硬编码。

### 2026-09-07 · 第一阶段补充：EMS 闭环核心

- 新增 `operator_core.py`。
- `EMSCore` 将 A 的 `GridState` 转换为 B 的调度目标。
- 明确 B 只产生 `target/enable`，不修改 A 的 `actual`。
- 保持 C 优先的控制边界。
- 增加闭环核心单元测试。

### 2026-09-07 · 第一阶段补充：本地 EMS 数据层

- `repository.py` 提供 SQLite 本地持久化。
- `db_schema.sql` 固化 B 数据层结构。
- 数据分为参数、运行配置、当前状态、状态历史、调度命令、调度评价和事件日志。
- 每次数据库操作使用独立连接和短事务，降低多进程 SQLite 冲突。
- `scripts/init_ems_db.py` 可生成本地 `data/runtime/ems.db`。
- 修复带 `pitch_actual_deg` 的状态写入字段错位问题。
- 修复 Windows 临时数据库清理问题：测试连接统一显式关闭。

### 2026-09-07 · 第二阶段：本地 EMS 周期运行框架

- 新增 `runtime.py`，实现无网络的 B 周期运行编排。
- 默认状态采集周期为 **1 s**，EMS 调度周期为 **5 s**。
- 首次获得状态后立即产生一次调度结果，之后每 5 s 使用最近一次采集状态决策。
- 通过 `state_provider` / `decision_sink` 注入外部数据源和结果接收端，当前不绑定 TCP、SQLite、Qt 或 STM32。
- 新增 `tests/test_runtime.py`，覆盖首次调度、轮询周期、调度周期和最新状态使用。
- 扩充 repository/operator_core 测试，覆盖参数校验、运行配置、命令/评价/日志、缺口和 C 优先等场景。

### 2026-09-07 · 第二阶段补充：运行时安全状态处理

- `runtime.py` 增加 `error_sink`，上层可记录状态源或调度异常，runtime 不绑定具体日志实现。
- 状态源读取失败时清空缓存状态，避免继续使用旧状态进行闭环调度。
- 调度层因状态过期等 `DispatchError` 拒绝时，默认生成**零风/零柴、两者均 OFF** 的安全兜底决策，并把当前负荷计入目标未供电量。
- `safe_fallback_on_dispatch_error=False` 可关闭自动兜底，由上层自行处置。
- `tests/test_runtime.py` 增加过期状态、关闭兜底、状态源异常和缓存清空测试。
- 该处理属于 B 软件层异常处理，不新增未确认的设备物理参数，也不改变 C 的保护优先权。

## 当前状态与下一步

B 已完成调度基础、闭环核心、本地数据层以及**带安全兜底的无网络周期运行框架**。本阶段只完成 B 单模块的代码骨架与测试覆盖，不能据此宣称 TCP 或 A/B/C 联调已经完成。

下一阶段继续完善 B 的调度评价指标、repository/runtime 服务层衔接和必要基础接口；待 B/A/C 各自基本完成后，再实现 B↔A TCP 客户端、ACK/超时/重连，并最终进行 A/B/C 组合调试和 STM32G431RBT6 实机验证。

## 本地运行与检查

Python 版本：**3.11**。

初始化 EMS 数据库：

```bash
python scripts/init_ems_db.py
```

运行 B 单元测试：

```bash
python -m unittest discover -s tests -v
```

数据库文件默认位于 `data/runtime/ems.db`，已由 `.gitignore` 排除，不上传运行数据。

## 协作规则

本项目三人直接在 `main` 中共同维护；本次 B 工作使用 GitHub 文件级同步，不依赖本地 `.git` 元数据，也不创建 feature branch/PR。提交前保留其他成员已有改动，不强制推送、不重置远端内容。

详细规则见 `CONTRIBUTING.md`，Agent 开始工作前阅读 `AGENTS.md`。

重要设计依据：

- [需求与边界](docs/requirements.md)
- [通信字段、方向和帧边界](common/protocol.md)
- [数据库职责](docs/database.md)
- [实施顺序与待确认项](docs/decisions.md)
- [协作说明](CONTRIBUTING.md)

不提交密码、令牌、数据库实测内容、构建输出及无授权课程附件。

---
原仓库备注（保留）：王鸡的彬巴
