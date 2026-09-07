# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前项目采用三模块协作：A 电网模拟器、B EMS 主站、C 风电子站。未冻结的物理参数、通信细节和硬件行为不得被代码擅自假定。

## 分工与入口

| 成员 | 模块 | 代码位置 | 核心交付 |
|---|---|---|---|
| A 刘雨杭 | 电网模拟器 | `A_simulator/` | 场景、风柴实际出力仿真、TCP 服务端、grid.db、Qt 界面 |
| B 李佳霖 | EMS 主站 | `B_dispatch/` | 状态采集、调度、开闭环模式、ems.db、Qt 界面 |
| C 陈信甫 | 风电子站 | `C_controller/` | STM32 控制、Wi-Fi TCP 客户端、串口上位机、wind.db |

运行关系：`B → A ← STM32`；A 是 TCP 服务端，B 和 STM32 主动连接 A。C 对风机保护/控制具有优先权。

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
├── tcpB.py              # B-owned A-facing TCP JSON-line client
├── serviceB.py          # B-owned TCP + SQLite + runtime service bridge
└── README.md

tests/
├── test_b_dispatch.py
├── test_operator_core.py
├── test_repository.py
├── test_runtime.py
├── test_tcpB.py
└── test_serviceB.py
```

B 侧 TCP/服务文件统一使用 `*B` 后缀，避免与其他合作成员的同类文件混淆。

## 开发时间节点 / 功能增量

### 2026-09-07 · 第一阶段：B EMS 调度与闭环基础

- 建立 B 数据模型、风电优先/柴油补偿调度和无状态 `EMSCore`。
- 柴油备用容量固定 **10 kW**；柴油允许完全 OFF；B 正常调度上限为 `diesel_max_kw - 10 kW`。
- C 保护/控制优先于 B 正常调度。
- B 只生成 target/enable，不写 A 的 actual，不发送桨距命令。
- 未确认的风机/柴油机物理额定值保持显式配置，不硬编码。

### 2026-09-07 · 第二阶段：B 本地数据库与周期运行

- 建立 `repository.py` / `db_schema.sql`，本地 `ems.db` 保存参数、当前状态、历史、调度命令、评价和事件日志。
- SQLite 每次操作独立连接、短事务，并设置外键与 busy timeout。
- `runtime.py` 默认 1 s 采集、5 s 调度；状态异常清缓存，调度异常默认产生 B 软件层零风/零柴安全兜底。
- 补充 repository、operator_core、runtime 单元测试。

### 2026-09-07 · 第三阶段：TCPB 协议基础对齐

根据 `docs/requirements.md`、`common/protocol.md`、`docs/time-interface.md`、`docs/network.md` 和 `docs/acceptance.md` 排查 B 目录及 B 测试文件：

- 新增 `B_dispatch/tcpB.py`，实现 B→A `state_request` / `dispatch` 与 A→B `state` / `ack` 的基础解析/发送。
- 严格使用 UTF-8 JSON + LF 一行一帧，最大 **4096 bytes（含 LF）**；处理 TCP 分包/粘包/多帧并拒绝非法 JSON、NaN/Infinity 和错误 envelope。
- 校验 `version/type/source/target/session_id/seq/step/sim_time_s/payload`，B 的 dispatch 不含桨距控制字段。
- 首次连接自动请求全量状态；step 回退或会话变化时停止使用旧状态并请求全量同步。
- `sampled_at_utc` 与 B 的 `received_at_utc` 分开保存；数据库 schema version 为 2。
- 增加 A→B `seq` 重复/旧序号抑制，以及 EOF/timeout 行为测试。
- 原 `tcp.py` / `test_tcp.py` 已替换为 B 专属 `tcpB.py` / `test_tcpB.py`，避免与 A/C 同类 TCP 文件混淆。

### 2026-09-07 · 第四阶段：TCP + SQLite + Runtime 服务层

- 新增 `B_dispatch/serviceB.py`，把 TCP、SQLite、runtime 和 EMSCore 接成一个 B 运行服务。
- 状态路径：A state → `tcpB` → `serviceB` → `repository.current_state/state_history` → `runtime`。
- 调度路径：`EMSCore` → `serviceB` → `tcpB.dispatch` → `repository.dispatch_commands/dispatch_evaluation`。
- runtime/network 异常进入 `event_log`；不会把断线后的旧状态静默当作新状态。
- `test_serviceB.py` 覆盖状态落库、命令/评价落库和首次闭环调用。

### 2026-09-07 · 统一 PC 运行环境与时间接口

- PC 端 Python 统一为 **3.11.x**，Qt 绑定统一为 **PyQt6 6.11.0**。
- 根目录使用 `.python-version` 和 `requirements.txt` 声明环境；每台电脑创建自己的 `.venv`。
- `docs/time-interface.md` 约定 A 产生 `sampled_at_utc`，B/C 原样保存并另记接收时间。

### 2026-09-07 · A 授时状态与 10 分钟默认场景

- A 每个仿真步只读取一次系统 UTC；同一步状态、状态历史和 SCADA 历史共用 `sampled_at_utc`。
- TCP `state.payload` 已加入 `sampled_at_utc`；控制顺序仍使用 `session_id + step + seq`。
- `grid.db` schema 升级为 v2，时间字段使用明确的 `_utc` 后缀。
- 默认场景改为 600 s、601 点的 `antarctic_10min.csv`；`demo.csv` 保留用于测试。
- A 运行循环使用单调时钟安排 1 s 周期，系统 UTC 仅用于状态授时。
- 新增 `scripts/bootstrap.ps1` 和 `scripts/run_a.ps1`，用于初始化环境和固定从项目 `.venv` 启动 A。

## 当前状态与下一步

B 已具备调度基础、闭环核心、本地数据库、周期 runtime、**B-owned TCPB** 和服务层桥接。代码层已完成 TCP/SQLite/runtime 的组合，但尚未声称 A/B 实机 TCP 联调通过。

下一步按验收清单推进：

1. **A/B 实际 socket 联调**：A server ↔ B `tcpB.py`，验证全量状态、分包/粘包、seq/session/step、ACK、timeout、断线重连。
2. **B 数据追溯验证**：确认 state → dispatch → ACK → 后续 actual state 能在 `ems.db` 复原，且 sim time / sampled time / received time 不混用。
3. **A/B/C 组合调试**：确认 C 保护优先于 B 正常调度，不让 B 越权发送桨距命令。
4. **STM32G431RBT6 实机验证**：由 C 完成硬件/Wi-Fi/串口部分，B 配合验证 A state 与调度目标闭环。
5. **PyQt6 UI**：在通信和数据库追溯稳定后再接界面，避免 UI 掩盖底层联调问题。

## 系统运行环境初始化

PC 端统一要求：

- 64 位 CPython 3.11.x，不使用 3.12/3.13 运行本项目。
- 安装 `uv`，首次初始化时允许下载 Python 3.11 和 `requirements.txt` 中的依赖。
- 每台电脑在自己的仓库根目录创建 `.venv`；不得复制或提交虚拟环境。
- STM32 固件仍使用其独立交叉编译工具链，不受 Python 环境约束。

推荐自动初始化：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

手动等价步骤：

```powershell
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\activate
python --version
```

输出必须为 Python 3.11.x；自动脚本还会导入 PyQt6 并打印版本。依赖发生变化后，重新运行 `bootstrap.ps1` 即可同步本机环境。

## 本地运行与检查

Python 版本：**3.11.x**；Qt 绑定：**PyQt6 6.11.0**。

```powershell
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\activate
```

初始化 EMS 数据库：

```bash
python scripts/init_ems_db.py
```

运行全部测试：

```bash
python -m unittest discover -s tests -v
```

数据库文件位于 `data/runtime/ems.db`，已由 `.gitignore` 排除，不上传运行数据。

## TCP 联调边界

- A 监听 `0.0.0.0:5000`；B 必须连接 A 的实际 WLAN IPv4，不能把 `0.0.0.0` 当客户端地址。
- B/C 与 A 在同一局域网/手机热点中；真实 SSID、密码不进入 Git。
- TCP 控制顺序使用 `session_id + step + seq`，不能用三台电脑墙钟时间排序。
- ACK `accepted=true` 只表示 A 接收并通过校验，不代表实际功率已经达到目标；实际效果以后续 state 为准。
- B 不发送 `pitch_target_deg`；C 的桨距/保护动作仍由 C 负责。
- 超时、断线、重复/乱序、非法值等验收项已进入 B TCP 基础测试范围；跨设备实际行为仍需联调验证。

## 协作规则

本项目三人直接在 `main` 中共同维护；B 工作使用 GitHub 文件级同步，不创建 feature branch/PR。提交前保留其他成员已有改动，不强制推送、不重置远端内容。

详细规则见 `CONTRIBUTING.md`，Agent 开始工作前阅读 `AGENTS.md`。

重要设计依据：

- [需求与边界](docs/requirements.md)
- [通信字段、方向和帧边界](common/protocol.md)
- [统一时间接口](docs/time-interface.md)
- [网络联调](docs/network.md)
- [数据库职责](docs/database.md)
- [实施顺序与待确认项](docs/decisions.md)
- [验收清单](docs/acceptance.md)

不提交密码、令牌、数据库实测内容、构建输出及无授权课程附件。

---
原仓库备注（保留）：王鸡的彬巴


