# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和后续 PyQt6 操作界面；B 是 A 的 TCP 客户端，不充当项目 TCP 中心。

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
├── tcp.py             # A-facing TCP JSON-line 客户端与协议校验
└── README.md

scripts/
└── init_ems_db.py

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
├── test_operator_core.py
├── test_runtime.py
└── test_tcp.py
```

## 已完成开发节点

### 2026-09-07 · 第一阶段：调度基础

- 建立 `GridState / DispatchConfig / DispatchResult`。
- 实现风优先、柴发补缺。
- 柴发保留容量固定为 **10 kW**；B 常规柴发上限为 `diesel_max_kw - reserve_kw`。
- 柴发允许完全停机，目标 0 kW 时 `diesel_enable=False`。
- C 具有控制优先权；C 优先故障时 B 不请求正常风机出力。
- 不虚构风速—功率曲线；额定参数必须由配置提供。

### 2026-09-07 · 第一阶段：闭环核心与本地数据库

- `EMSCore` 保持无状态：A 状态 -> B 调度目标。
- B 只产生 target/enable，不修改 actual，不发送桨距角。
- `repository.py` / `db_schema.sql` 保存参数、运行配置、状态、历史、命令、评价和日志。
- SQLite 使用独立连接、短事务、外键和 busy timeout。
- 状态写入保留 `pitch_actual_deg`；物理额定功率不偷偷写默认值。

### 2026-09-07 · 第二阶段：本地 EMS 周期运行与安全处理

- `runtime.py` 默认 1 s 采集、5 s 调度。
- 状态源异常时清空缓存；`DispatchError` 默认产生零风/零柴、两者 OFF 的 B 软件兜底。
- `error_sink` 可供上层记录异常；该兜底不等同于 C/STM32 保护动作。
- 相关单元测试覆盖首次调度、周期、最新状态、过期状态、异常和缓存清空。

### 2026-09-07 · 第三阶段：TCP 协议基础对齐

依据 `common/protocol.md`、`docs/time-interface.md`、`docs/network.md` 与 `docs/acceptance.md`，排查并补齐 B 侧 TCP 基础接口：

- 新增 `tcp.py`：B -> A 的 `state_request` / `dispatch`，A -> B 的 `state` / `ack` 解析。
- UTF-8 JSON 一行一帧，按 LF 分帧；严格限制最大帧 **4096 bytes（含 LF）**。
- 正确处理 TCP 半帧、粘包、多帧；拒绝非法 JSON、非有限数和错误 envelope。
- 校验公共字段 `version/type/source/target/session_id/seq/step/sim_time_s/payload`。
- B 的 `dispatch` 只包含 `wind_target_kw/diesel_target_kw/wind_enable/diesel_enable`，明确不发送 `pitch_target_deg`。
- 首次连接自动发送 `state_request(full=true)`；客户端地址禁止使用服务端监听地址 `0.0.0.0`。
- 状态必须包含 A 的 `sampled_at_utc`；B 另外生成并保存 `received_at_utc`，不混用两种时间。
- 检测会话变化或 step 回退后清空旧状态并请求全量同步；发送 socket 错误时关闭连接，支持上层调用 `connect()` 重连。
- `tests/test_tcp.py` 覆盖半帧/粘包、4096 字节限制、NaN、方向校验、首次全量请求、时间字段、step 回退重同步以及 B 写权限边界。
- `repository.py` / `db_schema.sql` 同步增加 `sampled_at_utc`，schema version 更新为 2。

## 当前状态

B 已具备**单模块调度 + 本地数据库 + 无网络周期 runtime + A-facing TCP 协议基础层**。TCP 客户端目前是独立阻塞式传输组件，尚未声称已经完成 A/B 实机联调，也没有把 TCP 直接耦合进 runtime。

TCP 关键约束：

- A：TCP server；B：TCP client；默认端口按项目草案为 `5000`。
- B 每 1 s 请求/检查状态，EMS 默认每 5 s 决策。
- 控制关联依赖 `session_id + step + seq`，不依赖三台电脑墙钟时间。
- `sampled_at_utc` 是 A 的采样时间；`received_at_utc` 是 B 的接收时间；数据库均按字段分别保存。
- ACK 的 `accepted` 只表示 A 接收并通过校验，不表示实际设备已经达到目标；实际结果仍以后续 `state` 为准。
- 超时、断线、非法报文等最终安全动作仍需结合共同确认的 A/C 行为进行联调；B 不擅自定义 C 的保护动作。

## 本地运行

Python 版本统一为 3.11.x。

初始化数据库：

```bash
python scripts/init_ems_db.py
```

运行 B 测试：

```bash
python -m unittest discover -s tests -v
```

`data/runtime/ems.db` 是本地运行数据，不提交 GitHub。

## 当前边界与后续

- `runtime.py` 暂不直接绑定 TCP/SQLite/Qt，便于单元测试。
- `tcp.py` 已完成协议基础层，但还需要下一阶段把 TCP 收发、状态落库、runtime 调度和 ACK/超时事件记录组成 B 的正式运行服务。
- B 不控制桨距角；桨距动作属于 C。
- B 不修改 A 的 actual 功率。
- 未确定的设备额定功率、功率曲线、爬坡、启停和 C 安全策略不得硬编码。
- 下一步：TCP + repository/runtime 服务层衔接 -> A/B 联调 -> 再做 A/B/C 组合调试 -> PyQt6 UI。
