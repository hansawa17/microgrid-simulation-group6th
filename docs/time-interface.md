# ABC 统一时间轴与授时接口规范 v0.1

状态：三方基础开发接口基线。A 已完成状态、数据库和 TCP 输出适配；B/C 按各自开发进度适配。本文补充 `common/protocol.md`，不改变 A/B/C 的控制权划分。

## 1. 目标与数据流

A 同时读取预设 CSV 曲线和操作系统 UTC 授时。每生成一个仿真状态断面，A 只读取一次 UTC 时间，为该断面的风速、负荷、风柴实际出力统一标记 `sampled_at_utc`，写入 `grid.db` 后发送给 B/C。这模拟了气象站和负荷测量装置产生带采样时间遥测的过程。

```text
CSV 风速/负荷曲线 + A 仿真时刻 + 系统 UTC 授时
                         |
                         v
          A 生成统一时间戳的状态断面
             |                       |
             v                       v
          grid.db              TCP state -> B/C
```

A 是唯一的仿真时间和状态采样时间权威。B/C 不自行推算 A 的采样时间，只保存和引用 A 发布的 `session_id`、`step`、`sim_time_s` 与 `sampled_at_utc`。

## 2. 四类时间不得混用

| 字段 | 类型与格式 | 产生者 | 含义 | 主要用途 |
|---|---|---|---|---|
| `sim_time_s` | 非负数，单位 s | A | 当前会话从 CSV 起点经过的仿真秒数 | 曲线读取、周期判断、模型计算 |
| `sampled_at_utc` | UTC RFC 3339 字符串 | A | A 生成这一状态断面时的授时时刻 | 模拟实时遥测采样时间、ABC 历史对齐 |
| `received_at_utc` | UTC RFC 3339 字符串 | 报文接收方 | B/C 实际收到该状态的本机时间 | 接收延迟、断线和陈旧状态排查 |
| `created_at_utc` | UTC RFC 3339 字符串 | 各数据库写入方 | 日志或命令实际创建的本机时间 | 日志与操作审计 |

统一采用固定毫秒格式：`YYYY-MM-DDTHH:MM:SS.mmmZ`，例如 `2026-09-07T08:03:25.417Z`。数据库不得使用无时区字符串，也不得使用含义不明的 `time` 或 `timestamp` 字段名。

控制顺序只依据 `session_id + step + seq`，不得依据不同电脑的墙钟时间排序。`sampled_at_utc` 和 `received_at_utc` 的差值可辅助分析通信延迟，但只有三台电脑均已可靠授时时才可视作近似单向延迟。

## 3. A 的授时规则

- A 使用带时区的系统 UTC 时钟；由 Windows/Linux 系统时间服务负责 NTP 校时，仿真循环不直接逐秒访问公网 NTP 服务器。
- 每个 `step` 只读取一次 UTC 时间；同一步的风速、负荷、设备状态、SCADA 点和历史记录共用同一个 `sampled_at_utc`。
- A 同时使用单调时钟控制“每隔 1 秒执行”，避免系统校时导致循环等待异常；UTC 时钟只用于生成时间戳。
- 如果检测到 UTC 时间相对上一状态倒退，A 记录 `clock_adjusted_backwards` 告警，但仍使用 `session_id + step + seq` 保证顺序。
- 时间戳生成失败时不得伪造旧时间；应记录错误并停止发布该步状态。

## 4. 10 分钟场景 CSV

正式演示场景建议持续 600 s，1 s 一个输入点，包含首尾共 601 行。CSV 是可重复的场景输入，不写本次运行的真实年月日；真实采样时间由 A 运行时授时生成。

CSV 使用 UTF-8，表头固定为：

```csv
step,sim_time_s,wind_speed_mps,load_power_kw
0,0,0.0,45.0
1,1,0.1,45.2
2,2,0.2,45.5
```

字段约束：

- `step` 从 0 开始且逐行加 1。
- `sim_time_s` 从 0 开始且严格递增；基础演示每行增加 1 s。
- 风速单位为 m/s，负荷功率单位为 kW，均为有限非负数。
- A 初始化场景时校验全部字段并将曲线写入 `grid.db`；运行中不得临时随机生成另一条未落盘曲线。
- 同一份 CSV 每次运行生成新的 `session_id` 和新的 `sampled_at_utc`，但风速/负荷序列保持可复现。

若后续导入真实气象历史数据，可以另加 `source_time_utc` 表示原始数据集时间；它不能替代本次仿真的 `sampled_at_utc`。

## 5. TCP 状态时间接口

`state` 报文继续使用公共信封中的 `session_id`、`step` 和 `sim_time_s`，并在 `payload` 中增加必填的 `sampled_at_utc`。当前协议仍处于草案阶段，因此线协议 `version` 暂保持为 1；三方应在组合联调前完成该字段，不保留缺字段的正式联调模式。

```json
{
  "version": 1,
  "type": "state",
  "source": "A",
  "target": "B",
  "session_id": "7afba3c0-2d02-4e50-bddd-32a798dfdb37",
  "seq": 18,
  "step": 205,
  "sim_time_s": 205.0,
  "payload": {
    "sampled_at_utc": "2026-09-07T08:03:25.417Z",
    "wind_speed_mps": 8.2,
    "wind_available_kw": 68.0,
    "wind_operating_limit_kw": 64.0,
    "load_power_kw": 76.0,
    "wind_actual_kw": 42.0,
    "diesel_actual_kw": 34.0,
    "wind_target_kw": 45.0,
    "pitch_actual_deg": 0.0,
    "wind_running": true,
    "fault": false
  }
}
```

B 和 C 接收状态后：

1. 原样保存 A 给出的 `session_id`、`step`、`sim_time_s` 和 `sampled_at_utc`。
2. 本机立即生成并另存 `received_at_utc`，不得用它覆盖 `sampled_at_utc`。
3. 后续 `dispatch` 或 `wind_action` 使用同一个 `session_id` 和所引用状态的 `step`、`sim_time_s`；状态关联不依赖本机日期。
4. 会话变化、步号回退或同一步内容冲突时停止使用旧状态并请求全量同步。

## 6. 数据库存储要求

### A：`grid.db`

- 场景曲线表：保存 `step`、`sim_time_s`、风速和负荷，不保存运行时 UTC。
- 当前状态与状态历史：保存 `session_id`、`step`、`sim_time_s`、`sampled_at_utc`。
- 同一步的环境值、设备结果和 SCADA 历史使用同一个 `sampled_at_utc`。
- 命令历史：保存命令引用的会话和仿真时刻，同时保存实际 `received_at_utc`。
- 运行日志：保存 `created_at_utc`；若日志关联某一步，再同时保存会话和仿真时间字段。

### B：`ems.db`

- A 状态副本和调度历史原样保存 `sampled_at_utc`。
- 状态接收时另存 `received_at_utc`。
- 调度命令保存所依据状态的 `session_id`、`step`、`sim_time_s`、`sampled_at_utc` 和本地 `created_at_utc`。

### C：单片机与 `wind.db`

- 单片机至少保留 `session_id` 的可实现标识、`step` 和 `sim_time_s`，返回结果时引用同一步状态。
- 若 MCU 不便解析日期字符串，可将 `sampled_at_utc` 作为不参与控制计算的文本透传，或由 C 上位机按 `session_id + step` 与收到的状态关联；采用哪种方式需在串口协议冻结时记录。
- C 上位机数据库原样保存 A 的 `sampled_at_utc`，并另存本地 `received_at_utc`。

## 7. 暂停、恢复、重放和调速

- 暂停：A 不推进 `step` 和 `sim_time_s`，也不生成新的状态采样时间。
- 恢复：从下一步继续推进；新状态使用恢复后的真实 UTC，因此 `sampled_at_utc` 会体现暂停造成的时间间隔。
- 停止后重新开始：生成新的 `session_id`，从 CSV 第 0 步重新运行并重新授时。
- 同一场景重放：复用 CSV，但每次运行的 `sampled_at_utc` 不同；依靠 `session_id` 区分。
- 调速仿真：墙钟执行间隔可以改变，`sim_time_s` 按场景步长推进，`sampled_at_utc` 始终记录真实生成时刻。

这使 CSV 保持确定性，同时使状态表现为真实时间产生的遥测。若需要完全可重复的离线回放，可额外提供“固定回放时钟”模式，但必须在会话元数据中标记，不能冒充实时授时。

## 8. 基础运行周期

| 任务 | 周期 |
|---|---:|
| A 读取参数、CSV 和授时并推进仿真 | 墙钟 1 s；基础模式每次推进仿真 1 s |
| B 请求状态与检查待发命令 | 1 s |
| B 读取调度配置与采集断面 | 1 s |
| B 生成调度决策 | 默认 5 s |
| C 获取状态和执行控制计算 | 作业原文未指定；基础联调建议 1 s，最终由三方确认 |

GUI 刷新周期不属于控制周期，可以独立设置，但不得阻塞计算、TCP 或串口处理。

## 9. 各模块实现清单

- A：`grid.db` 和状态模型已有 `wind_available_kw`；仍需增加 `wind_operating_limit_kw`，并在 TCP `state.payload` 发布两个能力字段。授时、`sampled_at_utc` 和 CSV 相对时间曲线已完成。
- B：扩展本地状态模型、`ems.db` 状态/调度历史字段与 RFC 3339 时间格式校验。
- C：确认 MCU 时间戳透传方式，扩展 `wind.db`，并在串口协议中记录映射。
- 三方：组合联调前验证同一状态的四个关联字段完全一致，并测试暂停、恢复、系统校时、断线重连和场景重放。

本规范不要求三台电脑共享 SQLite 文件；每个模块只操作自己的数据库，通过协议同步业务状态。
