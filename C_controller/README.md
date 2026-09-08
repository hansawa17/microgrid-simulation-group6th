# C 风电子站 · 陈信甫

南极考察站微电网智能调控 —— 风电子站。C 由两部分组成：**风机控制器（STM32 固件）** 与
**风电上位机（PyQt6 桌面端）**。STM32 通过 Wi-Fi 作为 A 的 TCP 客户端（`source=C`），
计算风机启停与桨距；上位机只通过 UART 与 STM32 交换状态 / 参数 / 模式，维护 `wind.db`。

> 拓扑：A 是唯一 TCP 服务端，B 与 STM32/Wi-Fi 是 TCP 客户端，C 上位机只走 UART。
> TCP 中的 `C` 指 STM32 风机控制逻辑节点；C 上位机不代替 STM32 执行控制算法。

## 目录结构

```text
C_controller/
├── README.md              # 本文件：进度与联调说明
├── firmware/              # 真实 STM32 控制程序（STM32CubeIDE，STM32G431RBT3）
│   ├── README.md
│   ├── firmware.ioc / STM32G431RBTX_FLASH.ld / .cproject / .project / .mxproject
│   ├── Core/{Inc,Src}/wind_turbine.{h,c}   # 控制计算 + 串口协议 + USART2 收发
│   ├── Core/Src/main.c                     # 主循环按控制周期调用控制任务
│   └── Drivers/                            # STM32G4 HAL 驱动（已生成）
└── host/                  # 风电上位机（PyQt6 + pyserial + pyqtgraph，wind.db）
    ├── README.md
    ├── main.py / gui.py / controller.py
    ├── config.py / protocol.py
    ├── database.py / serial_comm.py / tcp_client.py
    ├── simulator.py / plot_widget.py
    └── 数据库目标.txt / 图形界面目标.txt
```

## 已完成

### 2026-09-08 · MCU ↔ PC 串口数据流闭环（第一阶段）

- STM32 固件：`wind_turbine.{h,c}` + `main.c` 集成；USART1(PA9/PA10) 预留 Wi-Fi、
  USART2(PA2/PA3) 与上位机串口通信（115200 8N1）。
- 上位机模块化：`config / protocol / database / serial_comm / tcp_client / simulator /
  controller / gui / main`。
- 串口帧：`$WIND / $PARAM / $CMD(START|STOP|RESET|AUTO|MANUAL) / $ACK`，字段序在
  `config.WIND_FIELDS / PARAM_FIELDS` 与固件对齐。
- `wind.db` 8 张表（device / parameter / telemetry / control_history / remote_command /
  remote_adjust / communication_log / system_log）。
- 无硬件演示：`simulator.py` 本地仿真（与 MCU 同逻辑）。

### 2026-09-08 · 字段名对齐 A/B 协议 + 功率五层语义（联调准备）

- 遥测字段名对齐仓库 `common/protocol.md`：`wind_speed_mps / wind_available_kw /
  wind_target_kw / wind_actual_kw / wind_running(bool) / pitch_target_deg`。
- 新增 `wind_operating_limit_kw`：在 `wind_available_kw` 基础上考虑启停许可、保护和
  设备上限，不扣 B 目标、桨距目标或实际爬坡，避免能力反馈自锁。
- 约束 `0 <= wind_operating_limit_kw <= wind_available_kw <= wind_rated_kw` 已落地。
- `wind.db` 相应列改名并新增 `wind_operating_limit_kw`，旧库首次启动自动迁移。

### 2026-09-08 · STM32 Wi-Fi/TCP 客户端（ESP8266-01S，联调）

- 新增 `esp8266.c`（AT 驱动，USART1）与 `wifi_client.c`（JSON Lines 客户端，`source=C`）。
- 连接流程：`AT → ATE0 → CWMODE=1 → CWJAP → CIPSTART → state_request(full=true) → ONLINE`，断线自动重连。
- 联调时 STM32 读 A 的 `state`（风速/目标/实际），算启停 + 桨距并发 `wind_action`；串口 `$WIND` 扩为 9 字段（新增 `wind_operating_limit_kw`）。
- 配置项在 `Core/Inc/wifi_config.h`（SSID / 密码 / A 的 IP:端口）。

## 尚未完成 / 待确认

- **C 代码参数尚未对齐冻结值**：固件/上位机默认仍为额定 1000 kW、线性曲线、桨距 0-20°，
  需对齐 `docs/parameter-ownership.md`（100 kW、三次曲线、桨距 0-90°）；且 `wind_action`
  尚未按文档带上 `wind_available_kw / wind_operating_limit_kw`（需由 C 计算后上报）。
- **实际功率联调后由 A 计算**：当前联网时读 A、离线时本地兜底估算。
- **串口协议未冻结**：`$WIND` 现为 9 个功率字段，仍需扩展 `session_id/step/sim_time_s/sampled_at_utc`。
- 物理参数已统一（见 `docs/parameter-ownership.md`）：额定 100 kW、切入/额定/切出风速
  3/12/25 m/s、三次功率曲线、桨距 0-90 deg；这些是小组配置，不是课程原文指定数值。
- 未接真实 STM32；当前结果均属软件 mock，不等同于真实硬件联调。

## 快速运行（上位机）

```powershell
# Python 3.11.x；依赖见根目录 requirements.txt（PyQt6==6.11.0）
cd C_controller/host
python main.py
```

数据流二选一：连接真实 STM32（USART2 串口，选 COM 口 → 「连接 STM32」），或「启动本地
仿真」做无硬件演示。

## 测试 / 验证

- 无硬件冒烟（本阶段已通过）：串口帧解析、本地仿真（含 `wind_operating_limit_kw` 约束）、
  `wind.db` 建表/插入/查询、旧库自动迁移。
- 场景覆盖：无风 / 适宜风速 / 额定区间 / 高风，STOP / START，开环 / 闭环。
- 未做：A/B/C 实机 TCP/UART 联调；不以单元/冒烟测试替代真实硬件联调。

## 下一步（与 A/B 联调）

1. 对齐冻结参数（`docs/parameter-ownership.md`）：C 改为 100 kW / 三次曲线 / 桨距 0-90°，
   并由 C 计算 `wind_available_kw`、`wind_operating_limit_kw` 后随 `wind_action` 上报。
2. 冻结并扩展 C 串口协议（补 `session_id/step/sim_time_s/sampled_at_utc`）。
3. 联调顺序：A/B 先跑通 socket → STM32→A → C 上位机 UART。
4. 环境统一 Python 3.11 + `PyQt6==6.11.0`；连 A 端口（当前 5005，与仓库草案 5000 需统一）。

完整计算职责和统一公式见 `docs/parameter-ownership.md`。
