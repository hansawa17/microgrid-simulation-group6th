# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和后续 Qt6 操作界面；B 连接 A，但不充当项目 TCP 中心。联网与 A/B/C 组合调试在各模块基本完成后再进行。

## 当前目录

```text
B_dispatch/
├── __init__.py
├── models.py          # 状态、调度参数、调度结果数据模型
├── dispatch.py        # B 核心约束调度逻辑
├── operator_core.py   # 闭环决策核心：状态 -> 调度目标
├── repository.py      # SQLite 本地数据访问层
├── db_schema.sql      # ems.db 数据库结构
└── README.md
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
- 为调度核心补充边界与 C 优先级测试。

### 2026-09-07 · 第一阶段：本地数据库

- 建立 `repository.py` 与 `db_schema.sql`。
- `ems.db` 分离保存参数、运行配置、当前状态、状态历史、调度命令、调度评价和事件日志。
- 物理额定功率不在初始化时偷偷写入默认值。
- SQLite 每次操作使用独立连接、短事务，并设置外键与 busy timeout。
- 增加 `scripts/init_ems_db.py` 作为本地数据库初始化入口。
- 修复状态写入中带 `pitch_actual_deg` 时的字段/参数错位问题。
- 修复 Windows 测试临时数据库清理问题：测试连接统一使用 `try/finally` 显式关闭。

## 当前运行方式

项目约定 Python 3.11。初始化本地数据库：

```bash
python scripts/init_ems_db.py
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

`data/runtime/ems.db` 属于本地运行数据，不应提交到 GitHub。

## 后续阶段

1. 完成 B 模块剩余基础功能与测试。
2. 再实现 B↔A TCP 客户端、状态轮询、调度周期、ACK/超时/重连。
3. 再进行 A/B/C 组合调试；C 的 STM32G431RBT6 串口协议不由 B 负责。
4. 最后接入 PyQt6 操作界面。
