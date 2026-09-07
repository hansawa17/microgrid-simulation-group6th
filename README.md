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
├── tcp.py              # A-facing TCP JSON-line client
└── README.md

tests/
├── test_b_dispatch.py
├── test_operator_core.py
├── test_repository.py
├── test_runtime.py
└── test_tcp.py
```

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

### 2026-09-07 · 第三阶段：TCP 协议基础对齐

根据 `docs/requirements.md`、`common/protocol.md`、`docs/time-interface.md`、`docs/network.md` 和 `docs/acceptance.md` 排查 B 目录及 B 测试文件：

- 新增 `B_dispatch/tcp.py`，实现 B→A `state_request` / `dispatch` 与 A→B `state` / `ack` 的基础解析/发送。
- 严格使用 UTF-8 JSON + LF 一行一帧，最大 **4096 bytes（含 LF）**；处理 TCP 分包/粘包/多帧并拒绝非法 JSON、NaN/Infinity 和错误 envelope。
- 校验 `version/type/source/target/session_id/seq/step/sim_time_s/payload`，B 的 dispatch 不含桨距控制字段。
- 首次连接自动请求全量状态；step 回退或会话变化时停止使用旧状态并请求全量同步。
- `state.payload.sampled_at_utc` 与 B 的 `received_at_utc` 分开保存；数据库 schema version 更新为 2。
- 新增 `tests/test_tcp.py`，覆盖帧边界、非法值、方向、首次全量请求、时间字段、重同步和 B 控制权限。
- B README 已同步记录本次 TCP 对齐范围与剩余联调边界。

## 当前状态与下一步

B 已具备调度基础、闭环核心、本地数据库、周期 runtime 和**独立的 A-facing TCP 协议基础层**。这不等同于 A/B 实机 TCP 联调已经完成。

下一阶段将把 `tcp.py` 与 `runtime.py`、`repository.py` 组合成正式 B 运行服务，补齐状态落库、调度命令落库、ACK/超时/断线事件记录和重连策略；随后再进行 A/B 联调，最后进行 A/B/C 与 STM32G431RBT6 实机验证。

## 本地运行与检查

Python 版本：**3.11.x**；Qt 绑定：**PyQt6 6.11.0**。

```powershell
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv\\Scripts\\python.exe -r requirements.txt
.\\.venv\\Scripts\\activate
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
