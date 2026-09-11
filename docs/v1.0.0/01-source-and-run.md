# v1.0.0 完整源码与运行说明

## 1. 交付范围

发布标签 `v1.0.0` 固定以下源码和材料：

| 目录 | 内容 |
|---|---|
| `A_simulator/` | 电网仿真、场景曲线、实际功率计算、TCP 服务、SQLite 和综合 GUI |
| `B_dispatch/` | EMS 调度、B 自持 TCP、SQLite、历史评价和 GUI |
| `C_controller/firmware/` | STM32G431 + ESP8266 固件工程源码 |
| `C_controller/host/` | C 串口上位机、SQLite、图表和无硬件 mock |
| `common/` | A/B/C 公共 TCP 协议与配置示例 |
| `scripts/` | 环境初始化、A/B 启动和 A 故障注入脚本 |
| `tests/` | Python 自动化回归测试 |
| `docs/` | 需求、职责、网络、时间、数据库、接口、决策和验收说明 |

运行数据库、日志、虚拟环境、真实 Wi-Fi 密码及 MCU 编译产物不随 Git 发布。

## 2. 软件环境

- Windows 10/11、PowerShell。
- `uv`，用于安装 CPython 3.11 和创建项目虚拟环境。
- CPython 3.11.x。不要用系统中的其他 Python 版本替代验收环境。
- Python 依赖由根目录 `requirements.txt` 固定：PyQt6、pyqtgraph、pyserial。
- 真硬件部署另需 STM32CubeIDE、ST-LINK、ESP8266-01S 和 3.3 V USB-UART；见硬件说明。

## 3. 获取并校验正式版

```powershell
git clone https://github.com/hansawa17/microgrid-simulation-group6th.git
cd microgrid-simulation-group6th
git fetch --tags origin
git checkout v1.0.0
git show --no-patch --decorate v1.0.0
git status --short
```

验收运行应基于标签而不是后续变化的 `main`。正常情况下最后一条命令没有输出。

## 4. 初始化 Python 3.11 环境

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
.\.venv\Scripts\python.exe -c "import sys; print(sys.version)"
```

脚本会安装/选择 Python 3.11、创建 `.venv` 并安装完整依赖。若旧 `.venv` 已损坏，脚本会将其保留为 `.venv.stale-时间戳` 后重建；这些目录不会提交到 Git。

三台电脑分别克隆源码并执行一次初始化。不要复制正在使用的 SQLite 文件在电脑间通信。

## 5. 启动 A 电网模拟器

在 A 电脑仓库根目录运行：

```powershell
.\scripts\start_a.ps1 -BindAddress 0.0.0.0 -Port 5000
```

默认使用：

- 配置：`A_simulator/config.example.json`
- 场景：`A_simulator/scenarios/antarctic_10min.csv`
- 本地数据库：`data/runtime/grid.db`

数据库不存在时只初始化一次；已存在时保留参数和历史。GUI 中点击启动会同时启动 TCP 服务和仿真。局域网客户端必须连接 A 电脑实际 WLAN IPv4，不能连接 `0.0.0.0`。

A 的曲线编辑有两个平行出口：原有保存/另存 CSV，以及“应用曲线到仿真”。运行或暂停期间点击后不重启进程、不清空历史，新曲线从下一执行时刻开始生效；时间轴、点数或总时长变化仍需重新初始化。

## 6. 启动 B EMS 调度端

在 B 电脑仓库根目录运行：

```powershell
.\scripts\start_b.ps1
```

在“运行监控”填写 A 的 WLAN IPv4 和端口 `5000` 后连接。B GUI 直接持有 TCP，不依赖另一个后台启动器。正式版行为为：

- 默认闭环状态从本地持久化配置读取；新库默认闭环。
- 每 1 s 获取/检查状态；默认每 5 s 重新计算调度。
- 只有风/柴启停或功率目标发生变化时才发送 `dispatch`，避免无变化命令占用链路。
- GUI 定时轮询 ACK，不在界面线程阻塞等待网络。
- 本地数据库为 `data/runtime/ems.db`。

开环模式只计算和展示，不发送调度控制。手动下发会先获取最新 A 状态，再计算和发送。

## 7. 启动 C 风电端

### 7.1 真硬件模式

先按[硬件工程与部署说明](04-hardware-and-deployment.md)编译、烧录和接线。在 C 电脑仓库根目录运行：

```powershell
Set-Location C_controller\host
..\..\.venv\Scripts\python.exe main.py
```

选择连接 STM32 的 COM 口并点击连接。串口连接后可在运行监控页查询或设置 STM32 要连接的 A IP/端口；地址经 `$WIFI` 立即应用并触发重连，断电后恢复固件编译期默认值。C 上位机只通过 UART 连接 STM32，不直接建立到 A 的 TCP。

本地数据库为 `C_controller/host/database/wind.db`。

### 7.2 无硬件演示

C GUI 的“本地仿真”可验证界面、数据库和算法展示，但属于 mock，不代表 STM32、ESP8266、UART 或真实风机已通过验收。

## 8. 推荐启动顺序

1. 三台电脑加入同一局域网或手机热点，确认地址互不冲突。
2. A 启动并监听 `0.0.0.0:5000`。
3. B 用 A 的 WLAN IPv4 连接，确认状态由离线变为在线。
4. STM32/ESP8266 上电并连接 A；再启动 C 上位机并连接 USART2 对应 COM 口。
5. 先开环核对状态和字段，再切闭环观察 `state → dispatch → wind_action → actual state`。
6. 保存 A/B/C 截图、通信日志、历史查询和参数回读结果。

B 电脑可先验证端口：

```powershell
Test-NetConnection <A的WLAN-IPv4> -Port 5000
```

若失败，检查 A 服务状态、监听地址、Windows 防火墙入站规则和热点客户端隔离；不要关闭整机防火墙。

## 9. 自动化测试

在根目录使用项目 Python 3.11 执行全部测试：

```powershell
$modules = Get-ChildItem tests -File -Filter 'test_*.py' |
  Sort-Object Name |
  ForEach-Object { 'tests.' + $_.BaseName }
.\.venv\Scripts\python.exe -m unittest $modules -v
```

发布记录应写明 Python 小版本、测试数量、失败数和提交 SHA。自动化测试不替代固件编译及实物联调。

## 10. 故障注入与验收场景

A 的测试入口在 `scripts/fault_injection/`：

```powershell
.\.venv\Scripts\python.exe scripts\fault_injection\a_tcp_protocol_faults.py --port 5000 --case all
.\.venv\Scripts\python.exe scripts\fault_injection\a_connection_faults.py --port 5000 --source B --mode abrupt
.\.venv\Scripts\python.exe scripts\fault_injection\a_connection_faults.py --port 5000 --source C --mode idle --idle-seconds 35
.\.venv\Scripts\python.exe scripts\fault_injection\a_input_faults.py --output scripts\fault_injection\runtime\fault_inputs
```

只对测试数据库和独立输入目录执行，不得用损坏数据库覆盖正式运行数据。至少记录以下场景：无风、适宜风速、高风禁运（`wind_speed_mps >= 25`）、低/高负荷、柴发上下限、开/闭环、断线重连、超时、重复/乱序命令、非法值及 TCP 半包/粘包。

## 11. 正常退出与数据保存

先停止闭环，再停止仿真并关闭 GUI。数据库和导出文件保留在各自电脑本地。提交或打包前运行 `git status --short`，确认没有 `.db`、密码、本地配置、日志、虚拟环境或固件二进制被纳入版本管理。
