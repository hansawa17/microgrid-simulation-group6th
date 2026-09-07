# A 电网模拟器 · 基础实现

本目录负责场景加载、设备实际出力仿真、`grid.db` 和 TCP 服务端。当前版本已提供可运行的基础骨架，不代表完整作业已经完成。

## 已实现

- 从 CSV 分别读取风速和负荷曲线，并按仿真时刻线性插值。
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
uv pip install --python .venv\\Scripts\\python.exe -r requirements.txt
.\\.venv\\Scripts\\activate
```

然后执行：

```powershell
python -m A_simulator init
python -m A_simulator control start
python -m A_simulator run --steps 5
python -m A_simulator show
```

数据库默认生成在 `data/runtime/grid.db`，已由根目录 `.gitignore` 排除。`init` 不覆盖已有数据库；开始新的演示前请先备份或明确移走旧数据库。

通信进程需在另一个终端启动：

```powershell
python -m A_simulator serve
```

默认监听 `0.0.0.0:5000`。客户端应连接 A 电脑的实际 WLAN IPv4，而不是 `0.0.0.0`。也可用 `--bind`、`--port`、`--db`、`--config` 显式覆盖。

仿真状态控制：

```powershell
python -m A_simulator control pause
python -m A_simulator control resume
python -m A_simulator control stop
```

## 更新日志

### 2026-09-07 · Python 3.11 / PyQt6 环境

- A 的 PC 端开发环境统一为 Python 3.11.x。
- Qt 界面依赖固定为 `PyQt6==6.11.0`。
- 当前仅完成环境配置，A 的 Qt 界面仍待实现。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖无风、适宜风速、切出风速、负荷与出力不平衡、柴油机上下限/爬坡、场景插值、数据库历史、命令去重/越序、TCP 半帧与粘包。测试不等同于真实硬件联调。



