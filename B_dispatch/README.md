# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和后续 Qt6 操作界面；B 连接 A，但不充当项目 TCP 中心。联网与 A/B/C 组合调试在各模块基本完成后再进行。

## 当前目录

```text
B_dispatch/
├── __init__.py        # B 包公开接口
├── models.py          # 状态、调度参数、调度结果数据模型
├── dispatch.py        # B 核心约束调度逻辑
├── operator_core.py   # 闭环决策核心：状态 -> 调度目标
├── runtime.py         # 无网络运行框架：1 s 采集 / 5 s 调度编排与安全兜底
├── repository.py      # SQLite 本地数据访问层
├── db_schema.sql      # ems.db 数据库结构
└── README.md

scripts/
└── init_ems_db.py     # 本地数据库初始化

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
├── test_operator_core.py
└── test_runtime.py
```

## 已完成开发节点

### 2026-09-07 · 第一阶段：调度基础

- 建立 `models.py`，统一 `GridState / DispatchConfig / DispatchResult`。
- 实现风优先、柴发补缺的基础调度。
- 柴发保留容量固定为 **10 kW**，B 的常规柴发调度上限为 `diesel_max_kw - reserve_kw`。
- 柴发允许完全停机，目标功率为 `0 kW` 时发送 `diesel_enable=False`。
- C 具有控制优先权；发生 C 优先故障状态时，B 不发送风机正常调度目标。
- 不在 B 中虚构风速—功率曲线；风机/柴油机额定参数必须由配置提供。
- 对过期状态进行拒绝，避免旧测量静默进入新一轮闭环决策。

### 2026-09-07 · 第一阶段：闭环核心

- 增加 `operator_core.py`。
- `EMSCore` 保持无状态：输入 A 的状态，输出 B 的风/柴目标及 enable 标志。
- B 只产生目标值和启停标志，不修改 A 的实际功率，也不发送桨距角控制。
- 为正常调度、柴油备用、缺口、C 优先和过期状态补充单元测试。

### 2026-09-07 · 第一阶段：本地数据库

- 建立 `repository.py` 与 `db_schema.sql`。
- `ems.db` 分离保存参数、运行配置、当前状态、状态历史、调度命令、调度评价和事件日志。
- 物理额定功率不在初始化时偷偷写入默认值。
- SQLite 每次操作使用独立连接、短事务，并设置外键与 busy timeout。
- 增加 `scripts/init_ems_db.py` 作为本地数据库初始化入口。
- 修复状态写入中带 `pitch_actual_deg` 时的字段/参数错位问题。
- 修复 Windows 测试临时数据库清理问题：测试连接统一使用 `try/finally` 显式关闭。

### 2026-09-07 · 第二阶段：本地 EMS 周期运行框架

- 新增 `runtime.py`，建立与网络无关的 EMS 周期编排层。
- 默认采集周期为 **1 s**，调度周期为 **5 s**，与课程要求的 B 周期保持一致。
- 第一次获得有效状态后立即产生一次调度结果，后续使用最近一次采集状态按 5 s 周期决策。
- `state_provider` 和 `decision_sink` 采用依赖注入，当前不绑定 TCP、SQLite、Qt 或 STM32，便于先完成 B 单模块测试。
- 新增 `tests/test_runtime.py`，覆盖首次调度、1 s 轮询、5 s 调度和最新状态使用。
- 扩充 `tests/test_repository.py`，覆盖运行配置、参数校验、命令/评价/日志和状态字段完整性。
- 扩充 `tests/test_operator_core.py`，覆盖风机独立供电、柴油备用、缺口和 C 优先场景。

### 2026-09-07 · 第二阶段补充：运行时安全状态处理

- `runtime.py` 增加 `error_sink`，让上层可以记录状态源或调度异常，不在 runtime 内绑定日志实现。
- 状态源读取失败时清空缓存状态，避免继续使用旧状态进行闭环调度。
- 调度层因状态过期等 `DispatchError` 拒绝时，默认生成**零风/零柴、两者均 OFF** 的安全兜底决策，并保留当前负荷作为目标未供电量。
- `safe_fallback_on_dispatch_error=False` 可关闭自动兜底，由上层自行决定错误处置。
- 新增测试覆盖：过期状态安全兜底、关闭兜底、状态源异常和缓存清空。
- 该安全兜底只属于 B 软件层错误处理，不新增任何未确认的设备物理参数，也不改变 C 的保护优先权。

## 当前运行方式

项目约定 Python 3.11。初始化本地数据库：

```bash
python scripts/init_ems_db.py
```

运行 B 单元测试：

```bash
python -m unittest discover -s tests -v
```

当前 runtime 是**无网络测试框架**，尚未连接 A；因此本阶段不代表 TCP 收发或 A/B/C 联调已经完成。`data/runtime/ems.db` 属于本地运行数据，不应提交到 GitHub。

## 当前边界

- `runtime.py` 只负责周期调度编排和本地错误兜底，不实现 TCP、ACK、重连或串口。
- B 不控制桨距角；桨距动作属于 C。
- B 不修改 A 的 actual 功率；actual 由 A 的仿真状态反馈产生。
- C 的保护/控制优先于 B 的正常调度。
- 未确定的设备额定功率、功率曲线、爬坡和启停规则保持为配置/待确认项。
- 安全兜底输出 0 kW 是针对 B 决策异常的保守软件状态，不等同于对 C/STM32 下发保护动作。

## 后续阶段

1. 完成 B 剩余基础功能和调度评价指标。
2. 将 runtime 与 repository 的本地状态/命令/事件记录做清晰的服务层衔接，并继续补测试。
3. 再实现 B↔A TCP 客户端、状态轮询、调度周期、ACK/超时/重连。
4. 再进行 A/B/C 组合调试；C 的 STM32G431RBT6 串口协议不由 B 负责。
5. 最后接入 PyQt6 操作界面。
