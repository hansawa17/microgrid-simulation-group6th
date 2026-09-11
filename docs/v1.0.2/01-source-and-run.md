# v1.0.2 完整源码与运行说明

## 源码范围

- `A_simulator/`：场景、仿真计算、`grid.db`、TCP 服务和 A GUI。
- `B_dispatch/`：EMS 调度、GUI 直持 TCP、`ems.db`、历史和评价。
- `C_controller/firmware/`：STM32G431 + ESP8266 固件源码和 CubeIDE 工程。
- `C_controller/host/`：仅经 UART 连接 STM32 的 C GUI、`wind.db` 和明确标记的 mock。
- `common/`、`scripts/`、`tests/`、`docs/`：协议、启动/故障注入、回归和设计记录。

`.venv`、`*.db`、日志、真实 Wi-Fi 密码、`config.local.json`、MCU 编译产物不进 Git。

## 环境初始化

三台 Windows 电脑都在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
.\.venv\Scripts\python.exe -c "import sys; from PyQt6.QtCore import PYQT_VERSION_STR; print(sys.version); print(PYQT_VERSION_STR)"
```

验收固定使用 CPython 3.11.x，不复制或上传虚拟环境。

## 启动

1. A 电脑：`.\scripts\start_a.ps1 -BindAddress 0.0.0.0 -Port 5000`，点击“启动（含 TCP）”。
2. B 电脑：`.\scripts\start_b.ps1`，填 A 的 WLAN IPv4 和 5000，点击连接。B GUI 是唯一正式 TCP 所有者。
3. STM32/ESP8266 上电；C 电脑执行 `Set-Location C_controller\host` 后运行 `..\..\.venv\Scripts\python.exe main.py`，选择 STM32 USART2 对应 COM 口。

C 上位机不直连 A。“本地仿真”只供无硬件界面演示，必须口头说明为 mock。

## 全量测试

```powershell
$modules = Get-ChildItem tests -File -Filter 'test_*.py' |
  Sort-Object Name | ForEach-Object { 'tests.' + $_.BaseName }
.\.venv\Scripts\python.exe -m unittest $modules -v
```

固件另在 STM32CubeIDE 选择 `Debug` 配置执行 Clean/Build，确认 0 error 再烧录。PC 测试不替代此步。

## 故障注入

```powershell
.\.venv\Scripts\python.exe scripts\fault_injection\a_tcp_protocol_faults.py --port 5000 --case all
.\.venv\Scripts\python.exe scripts\fault_injection\a_connection_faults.py --port 5000 --source B --mode abrupt
.\.venv\Scripts\python.exe scripts\fault_injection\a_connection_faults.py --port 5000 --source C --mode idle --idle-seconds 35
.\.venv\Scripts\python.exe scripts\fault_injection\a_input_faults.py --output scripts\fault_injection\runtime\fault_inputs
```

A 应变红并弹出非阻塞告警，仿真/界面不退出。损坏输入只在独立 runtime 目录使用。
