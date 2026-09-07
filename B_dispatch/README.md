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
├── tcpB.py            # B-owned A-facing TCP JSON-line 客户端与协议校验
├── serviceB.py        # B-owned TCP + SQLite + runtime 运行服务桥接
└── README.md

tests/
├── test_scaffold.py
├── test_b_dispatch.py
├── test_repository.py
├── test_operator_core.py
├── test_runtime.py
├── test_tcpB.py
└── test_serviceB.py
```

> B 侧与 TCP/服务相关的文件统一使用 `*B` 后缀，避免和其他成员的 TCP 文件混淆。`tcp.py` / `test_tcp.py` 已替换为 `tcpB.py` / `test_tcpB.py`。

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

### 2026-09-07 · 第三阶段：TCPB 协议基础对齐

依据 `common/protocol.md`、`docs/time-interface.md`、`docs/network.md` 与 `docs/acceptance.md`，排查并补齐 B 侧 TCP 基础接口：

- `tcpB.py` 实现 B -> A 的 `state_request` / `dispatch`，A -> B 的 `state` / `ack` 解析。
- UTF-8 JSON 一行一帧，按 LF 分帧；严格限制最大帧 **4096 bytes（含 LF）**。
- 正确处理 TCP 半帧、粘包、多帧；拒绝非法 JSON、非有限数和错误 envelope。
- 校验公共字段 `version/type/source/target/session_id/seq/step/sim_time_s/payload`。
- B 的 `dispatch` 只包含 `wind_target_kw/diesel_target_kw/wind_enable/diesel_enable`，明确不发送 `pitch_target_deg`。
- 首次连接自动发送 `state_request(full=true)`；客户端地址禁止使用服务端监听地址 `0.0.0.0`。
- 状态必须包含 A 的 `sampled_at_utc`；B 另外生成并保存 `received_at_utc`，不混用两种时间。
- 检测会话变化或 step 回退后清空旧状态并请求全量同步；发送 socket 错误时关闭连接，支持上层调用 `connect()` 重连。
- 增加 A -> B `seq` 的重复/旧序号抑制，避免重复状态被当成新状态处理。
- `test_tcpB.py` 覆盖半帧/粘包、4096 字节限制、NaN、方向校验、首次全量请求、时间字段、seq 重复、step 回退、EOF/timeout 以及 B 写权限边界。

### 2026-09-07 · 第四阶段：TCP + SQLite + Runtime 服务层

- 新增 `serviceB.py`，把 `tcpB.py`、`repository.py`、`runtime.py`、`EMSCore` 组合成 B 正式运行服务。
- 状态轮询路径：A state -> `tcpB` -> `serviceB` -> `repository.current_state/state_history` -> `runtime`。
- 调度路径：`EMSCore` -> `serviceB` -> `tcpB.dispatch` -> `repository.dispatch_commands/dispatch_evaluation`。
- 运行异常写入 `event_log`；网络断线/超时不把旧状态静默当作新状态。
- `test_serviceB.py` 覆盖状态落库、命令/评价落库和首次 runtime 闭环调用。

### 2026-09-07 · 第五阶段：A/B 联调风险预处理（仅修改 B）

A 已按共同协议完成第一轮 TCP server 对齐；本阶段只修改 B，不修改 A：

- **同一时刻只允许一个未完成的 `state_request`**：B 记录 `_pending_state_request_seq`，重复轮询不会连续发送新的状态请求。
- `poll_state()` 不再只读取一次 TCP 响应，而是持续消费 A 返回的帧，直到获得对应的新 `state`；期间到达的 ACK 不会被静默丢弃。
- **dispatch 发送后立即等待并消费对应 ACK**，通过 `_pending_ack_seq` 约束同一时刻只有一个待确认 dispatch。
- ACK 保存在 B 客户端内存中，`accepted/reason` 可被后续上层联调检查；ACK 只表示 A 接收/校验，不代表 actual 已达到目标。
- TCP 断线/超时仍向上层抛出，不把旧状态冒充新状态。
- 新增回归测试覆盖“未决 state request 不重复发送”和“dispatch 必须消费匹配 ACK”。

**风功率启动风险暂不通过虚构字段解决。** 当前共同协议没有 `wind_available_kw`，B 也没有获得已确认的风速—功率曲线，因此不能把 `wind_speed_mps` 擅自换算成可用功率，也不能把 `wind_max_kw` 当成当前可用功率。B 当前仍以 A 实际状态中的 `wind_actual_kw` 作为保守可用出力依据；若后续验收要求“风机停机且 actual=0 时可主动启动”，必须先在共同设计中确认启动语义或可用功率来源，再修改 B。

## 当前状态

B 已具备**调度 + 本地数据库 + 周期 runtime + B-owned TCPB + 服务层桥接 + 单未决请求/ACK 消费机制**。当前代码层已经把 TCP、SQLite 和 EMS runtime 接通，但这仍不等于 A/B 实机 TCP 联调已经完成。

TCP 关键约束：

- A：TCP server；B：TCP client；默认端口按项目草案为 `5000`。
- B 每 1 s 检查/请求状态，但不会在已有 `state_request` 未完成时重复发送请求；EMS 默认每 5 s 决策。
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

- `runtime.py` 仍保持可独立测试；正式运行组合由 `serviceB.py` 完成。
- `tcpB.py` 是 B 专属 TCP 文件；不要再新建无后缀的 `tcp.py`，避免和其他成员冲突。
- B 不控制桨距角；桨距动作属于 C。
- B 不修改 A 的 actual 功率。
- 未确定的设备额定功率、功率曲线、爬坡、启停和 C 安全策略不得硬编码。
- 下一步：A/B 实际 socket 联调 -> 根据 ACK/state 验证数据库追溯 -> 再做 A/B/C 组合调试 -> 最后接 PyQt6 UI。
