# A 电网模拟器 · 基础实现

本目录负责场景加载、设备实际出力仿真、`grid.db` 和 TCP 服务端。当前版本已提供可运行的基础骨架，不代表完整作业已经完成。

## 已实现

- 从 CSV 分别读取风速和负荷曲线，并按仿真时刻线性插值。
- 默认提供 600 s、601 个输入点的 `scenarios/antarctic_10min.csv`；短场景 `demo.csv` 保留用于测试。
- 每个仿真步只读取一次系统 UTC，并为状态、历史和 SCADA 数据统一生成 `sampled_at_utc`。
- 纯函数单步模型：风机风速功率曲线、桨距降额、风/柴目标跟踪、上下限和爬坡约束。
- 明确区分 B 的功率目标、C 的启停/桨距动作与 A 计算的实际出力。
- SQLite `grid.db`：仿真状态、场景、设备参数、控制状态、当前状态、状态历史、SCADA 当前/历史、命令、连接状态和日志。
- 独立计算循环与线程化 TCP 服务；TCP 使用协议草案中的 UTF-8 JSON + LF，处理半帧/粘包、帧长、非法 JSON、NaN/Infinity、重复和乱序命令。
- 标准库 `unittest` 测试和本地 TCP 冒烟测试。

## 尚未实现或尚未确认

- Qt 人机界面尚未实现。
- `common/protocol.md` 仍是草案；完整四遥点表、增量变位格式和命令超时未冻结。
- `config.example.json` 中所有物理参数都是便于运行的示例，不是课程指定值。
- 基础桨距模型为线性降额；风机运行暂采用 B、C 双方都允许才启用的保守组合。柴发仅按 B 目标和设备约束跟踪，不擅自加入尚未确认的本地自动补偿规则。
- 未连接真实 STM32，当前结果均属于软件 mock。

## 快速运行

项目统一使用 Python 3.11.x，后续 Qt 界面使用 PyQt6。在仓库根目录先创建环境：

```powershell
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\activate
```

推荐通过固定使用 `.venv` 的入口执行：

```powershell
.\scripts\run_a.ps1 init
.\scripts\run_a.ps1 control start
.\scripts\run_a.ps1 run --steps 5
.\scripts\run_a.ps1 show
```

数据库默认生成在 `data/runtime/grid.db`，已由根目录 `.gitignore` 排除。`init` 不覆盖已有数据库。时间接口升级后 `grid.db` schema 为 v2；旧测试数据库不会自动迁移，开始新演示前请先备份并明确移走旧数据库，再重新执行 `init`。

通信进程需在另一个终端启动：

```powershell
.\scripts\run_a.ps1 serve
```

默认监听 `0.0.0.0:5000`。客户端应连接 A 电脑的实际 WLAN IPv4，而不是 `0.0.0.0`。也可用 `--bind`、`--port`、`--db`、`--config` 显式覆盖。

仿真状态控制：

```powershell
.\scripts\run_a.ps1 control pause
.\scripts\run_a.ps1 control resume
.\scripts\run_a.ps1 control stop
```

## 更新日志

### 2026-09-07 · Python 3.11 / PyQt6 环境

- A 的 PC 端开发环境统一为 Python 3.11.x。
- Qt 界面依赖固定为 `PyQt6==6.11.0`。
- 当前仅完成环境配置，A 的 Qt 界面仍待实现。

### 2026-09-07 · 授时时间接口与默认场景

- `SimulationState` 和 TCP `state.payload` 增加 `sampled_at_utc`。
- A 在每一步模型计算前读取一次系统 UTC，同一步的状态、SCADA 与历史记录复用该值。
- `grid.db` schema 升级为 v2，采样、接收、创建和更新时间采用含义明确的 `_utc` 字段名。
- CSV 增加显式连续 `step` 列；默认运行场景改为 10 分钟、601 点。
- 运行周期改由单调时钟调度，避免操作系统授时调整干扰 1 s 执行周期。
- 新增 `scripts/bootstrap.ps1` 和 `scripts/run_a.ps1`，分别负责环境初始化和固定使用项目 `.venv` 启动 A。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_a_simulator -v
```

测试覆盖无风、适宜风速、切出风速、负荷与出力不平衡、柴油机上下限/爬坡、场景插值、数据库历史、命令去重/越序、TCP 半帧与粘包。测试不等同于真实硬件联调。


