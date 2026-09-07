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
├── repository.py
├── db_schema.sql
└── README.md

scripts/
└── init_ems_db.py

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
└── test_operator_core.py

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
- 增加当前状态读取、调度评价写入和运行配置读取接口。

### 下一阶段：尚未完成

- B ↔ A TCP 客户端接入当前协议草案。
- 周期性状态采集与 5 s 调度周期。
- 命令 ACK、超时和重连策略。
- Qt6 EMS 操作界面。
- A/B/C 实机联调。
- STM32G431RBT6 与真实 C 子站联调。

以上未完成项不应被描述为已经验证的系统功能。

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

提交前同步最新 `main`；不要强制推送、不要覆盖他人修改。详细规则见 `CONTRIBUTING.md`，Agent 开始工作前阅读 `AGENTS.md`。

重要设计依据：

- [需求与边界](docs/requirements.md)
- [通信字段、方向和帧边界](common/protocol.md)
- [数据库职责](docs/database.md)
- [实施顺序与待确认项](docs/decisions.md)
- [协作说明](CONTRIBUTING.md)

不提交密码、令牌、数据库实测内容、构建输出及无授权课程附件。

---
原仓库备注（保留）：王鸡的彬巴
