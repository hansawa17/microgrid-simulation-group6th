# B EMS 主站 · 李佳霖

B 负责 EMS/operator 侧的调度决策、本地 `ems.db` 数据层和 PyQt6 操作界面；B 是 A 的 TCP 客户端，不充当 TCP 中心。

## 当前 GUI 功能

`B_dispatch/gui_b.py` 已扩展为 B 的 SCADA/操作工作台，并保持 C 主机的浅蓝灰卡片式视觉风格。

- **运行监控**：A IP、TCP 端口、本机 IP、连接/断开；负荷、风电 available、operating limit、target、actual、柴油 actual、功率不平衡、风速、柴油能力余量、状态年龄、桨距 actual、session/step、`sampled_at_utc`。
- **实时曲线**：负荷、风电 available/limit、wind target/actual、diesel actual 趋势；可清空并立即请求 A 状态。
- **本地场景 / 手动调度**：不连接 A 也能构造 mock `GridState`，验证风优先、operating limit、柴油 10 kW reserve、柴油 OFF、C fault 优先、缺电/过剩等结果；明确标记为 mock，不冒充 A 场景。
- **参数设置**：编辑风电上下限、柴油容量、状态最大年龄、采集周期、调度周期和 closed-loop 模式；柴油 reserve 固定 10 kW，并写入 `ems.db`。
- **EMS 调度**：手动计算并发送 dispatch；可开启自动闭环；显示 target unserved、target surplus、reason 和 ACK；保留 delivery-unknown 禁止盲目重发的安全边界。
- **历史数据**：读取 `ems.db/state_history`，按 session 过滤并支持 CSV 导出。
- **通信诊断**：TCP 状态、pending state request、full-sync、最近入站 seq、最近 ACK 和 GUI 事件日志。
- **报警与评价**：状态过期、C fault、delivery unknown 等事件；从 `dispatch_evaluation` 和 `dispatch_commands` 汇总目标缺电、目标过剩和 ACK 接受率。

## 与工程要求的对应关系

依据 `docs/requirements.md`、`common/protocol.md`、`docs/time-interface.md`、`docs/acceptance.md`：

1. B 默认闭环；采集/调度周期可配置，默认基线为 1 s / 5 s。
2. B 使用 C 发布的 `wind_operating_limit_kw` 约束 wind target，不使用 `wind_actual_kw` 反推可用能力。
3. 柴油 reserve 固定 10 kW，正常调度上限为 `diesel_max_kw - 10 kW`，目标为 0 时允许柴油完全 OFF。
4. C 保护/控制优先；B 不发送 pitch 指令，不写任何 actual。
5. A 的 `sampled_at_utc` 与 B 的接收时间语义分开；GUI 展示 session/step/sim time 和状态 age。
6. ACK `accepted` 仅表示 A 接收/校验，不代表 actual 已达到目标；actual 需通过后续 A state 判断。
7. 历史、评价和日志以 `ems.db` 为数据源；GUI 当前可读取状态历史、命令评价和事件日志。

## 本地运行

Python 统一为 **3.11.x**，Qt 使用 **PyQt6**。

```bash
python -m B_dispatch
python -m B_dispatch gui
python B_dispatch/gui_b.py
```

初始化数据库：

```bash
python scripts/init_ems_db.py
```

测试命令：

```bash
python -m unittest discover -s tests -v
```

> 当前会话中没有执行本机测试，因此不在此宣称测试通过。

`data/runtime/ems.db` 为本地运行数据，不提交 GitHub。

## 当前边界

- GUI 的本地场景输入只用于 B 算法演示，不能替代 A 的 CSV 场景。
- A/B/C socket、STM32/Wi-Fi/串口实机联调仍需单独验收；GUI 能显示通信状态不等于实机联调完成。
- 自动闭环默认关闭，只有连接 A 并明确开启后才会发送 dispatch。
- B 只发送 `wind_target_kw / diesel_target_kw / wind_enable / diesel_enable`，不发送 `pitch_target_deg`。
