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
- 联调时 STM32 读 A 的 `state`（风速 + B 目标），算可用功率/稳态上限/启停/桨距并发
  `wind_action{wind_enable,pitch_target_deg,wind_available_kw,wind_operating_limit_kw}`；串口 `$WIND` 扩为 10 字段（含 cycle 与链路状态）。
- 已修复 ESP8266 单连接 `+IPD,<len>` 把三位长度漏掉首位的问题；收包改为 ISR/主循环环形缓冲，支持跨多次串口接收的 `+IPD` 载荷。
- TCP 应用层按 `state_request → state → wind_action → ack` 单未决事务运行；`CIPSEND` 非阻塞等待提示符与 `SEND OK`，ACK 未知时保留同序号命令，重连先全量同步。
- A 在 state 中返回 `next_command_seq`，STM32 据此恢复当前 A 会话的发送序号，避免 MCU 重启后被判为 `out_of_order`。
- 配置项在 `Core/Inc/wifi_config.h`（SSID / 密码 / A 的 IP:端口）。

### 2026-09-09 · Wi-Fi 地址/端口运行期可配（上位机下发）

- A 服务器地址/端口从固件 `wifi_config.h` 写死改为「上电默认值 + 运行期可改」：
  - `wifi_client.c` 新增 `WifiClient_SetServer()` / `GetServerIp/Port()`，`AT+CIPSTART` 改用运行期变量；
  - USART2 新增 `$WIFI,<ip>,<port>`（改地址并自动重连）与 `$WIFI?`（查询，回 `$WIFIGET,<ip>,<port>`）。
- 上位机「运行监控」连接卡片新增「A 服务器 (Wi-Fi)」：IP 输入框 + 端口 + 「应用并连接」按钮；
  连上串口自动 `$WIFI?` 回填当前值，点击后经 `$WIFI` 下发并触发 MCU 重连。
- SSID / 密码仍为固件编译期常量，真实值仅本地烧录、不提交；IP/端口默认值用占位符。
- 验证范围：MCU↔PC 串口与 Wi-Fi↔A 链路已跑通；运行期改地址/端口重连为 PC 端代码检查，实机重连仍需验证。

### 2026-09-10 · 参数设置运行期可调 + 只读实时展示

- 修复「应用参数」后界面卡死：`database.py` 的 `Lock` 改为可重入 `RLock`，消除
  `update_params` 内再调 `get_params` 的同线程死锁（连接 A 与本地仿真均受影响）。
- 本地仿真参数运行期可调：`simulator.py` 的 `set_params` 增加数值转换与越界钳位，
  切入/额定/切出风速、额定功率、最大顺桨角、控制周期、通信超时、控制模式均可在运行中
  修改并即时生效；风速→功率的三次方曲线框架不变。
- 新建本地仿真时继承数据库当前参数，不再退回默认值。
- 「参数设置」页改为实时只读网格，展示风速/可用功率/运行上限/目标功率/实际功率/桨距角/
  运行状态/A 链路/周期，均标注来源（A/B/C 计算）且不可修改。

### 2026-09-10 · 风机指令追踪 + 参数回读证明 + A 参数副本同步

- **固件**（`wind_turbine.{c,h}` / `wifi_client.{c,h}`）：
  - 遥测 `$WIND` 升级为 `$WIND2`，追加 `last_wind_action_seq`（-1 表示当前会话尚无被
    A accepted 的 C 动作，PC 入库转 NULL）；保留旧 `$WIND/$PARAM/$CMD/$WIFI` 兼容。
  - 参数下发改用 `$PARAM2,<request_id>,<8字段>`：MCU 完整解析并校验全部字段后原子应用、
    递增 `parameter_revision`，失败保持旧参数并返回当前 `$PARAMGET`；`$PARAMGET?,<request_id>`
    读回当前有效值。
  - `$PARAM2` 生效后，在 TCP 单未决事务安全空档排队发送 source=C 的 `parameter_update`，
    payload 只含 7 个白名单物理参数（`control_mode` 不发送）；`wind_action` 被 A accepted 时
    记录 `last_wind_action_seq`；新增 `$SYNC` 上报同步结果（0未同步/1排队/2已发送/3accepted/
    4rejected/5ack未知）。
- **上位机**（`config/protocol/database/controller/gui`）：
  - `wind.db` 原位迁移补齐 `telemetry.last_wind_action_seq` 与参数表
    `parameter_revision/parameter_verified/verified_at_utc/verify_reason/a_sync_*`，保留历史。
  - 回读验证用统一 0.01 数值容差：`$PARAMGET.request_id` 匹配且全部参数一致才显示
    「MCU 已生效」，否则显示失败原因；`$SYNC` 的 accepted 才显示「A 副本已同步」，两者
    互不冒充。参数页新增参数版本/验证状态/失败原因/A 同步状态显示，监控页显示最近动作 seq。
- 配套 A 汇聚端最小改动（`grid.db` schema v6 + `state.payload` 五字段 + `wind_action` 原子
  追踪）见 `docs/wind-execution-status-extension.md` 与 `docs/decisions.md`。
- 验证：新增 `tests/test_c_wind_extension.py`（`$WIND/$WIND2/$PARAM2/$PARAMGET/$SYNC` 解析、
  parameter_update 白名单、旧库迁移）与 A 侧 `test_a_simulator.py` 五字段/幂等/新会话用例，
  PC 全部通过；**未重新编译 STM32 固件**，未执行三机/串口/硬件实测。

### 2026-09-11 · 开环不回 wind_action（验收项31整改）+ Wi-Fi 超时接入 c_timeout_s

- **开环整改**（`wind_turbine.{c,h}` / `wifi_client.{c,h}`）：开环（`control_mode=0`）不再向
  A 发送 `wind_action`，只按周期 `state_request` 读取 A 数据；计算结果仍经 `$WIND2` 上报串口。
  - `WindTurbine_PeriodicTask` 按模式分流：闭环发 `wind_action`，开环调用新增的
    `WifiClient_ReleaseState()` 只读消费 state，维持轮询继续。
  - `WifiClient_SendWindAction` 入口对开环双重拦截；切到开环后丢弃未获 ACK 的挂起
    `wind_action`（不再重发闭环控制命令）。
  - A 侧无需改动：开环缺 `wind_action` 不触发故障（A 沿用上次控制值），C 持续轮询不会
    触发 A 的 TCP 空闲超时。
- **Wi-Fi 超时**（同步 55b9781 并补修）：state/ACK 等待超时由固定 3 s 改为运行期
  `c_timeout_s`（钳位 0.5～30 s，`WindTurbine_GetTimeoutMs()`）；补上 `wifi_client.c` 缺失的
  `#include "wind_turbine.h"`（隐式声明修复）。
- 验证：本地上位机测试 40/40 通过；**固件未编译**（本机无交叉工具链），
  「开环 + A 在线不回 wind_action」待烧录后实机复测。

## 尚未完成 / 待确认

- **C 已对齐冻结参数**：额定 100 kW、三次曲线、桨距 0-90°；C 计算 `wind_available_kw`/
  `wind_operating_limit_kw` 并随 `wind_action` 上报（联调待 A 端解析验证）。
- **实际功率联调后由 A 计算**：当前联网时读 A、离线时本地兜底估算。
- **串口协议未冻结**：`$WIND` 现为 9 个功率字段，仍需扩展 `session_id/step/sim_time_s/sampled_at_utc`。
- 物理参数已统一（见 `docs/parameter-ownership.md`）：额定 100 kW、切入/额定/切出风速
  3/12/25 m/s、三次功率曲线、桨距 0-90 deg；这些是小组配置，不是课程原文指定数值。
- **STM32 硬件接入调试与三机（A/B/C）主链条联调已完成**：状态/调度/动作数据传递暂时正常；
  **异常情况排查（断线重连、超时、半帧/粘包、故障与保护、上电恢复）仍未完成**，尚不能以
  主链条跑通替代异常场景验证。

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
- STM32 硬件接入调试与三机主链条联调已完成，数据传递正常；异常情况排查仍未完成，不以
  单元/冒烟测试替代异常场景的硬件验证。

## 下一步（与 A/B 联调）

1. **端口已统一到 5000**（C 固件已改，与 A/common 一致）；C 参数名已对齐 A 的 canonical 名
   （`cut_in_speed_mps/rated_speed_mps/cut_out_speed_mps/wind_rated_power_kw/pitch_feather_deg/c_control_s/c_timeout_s`）。
   Wi-Fi TCP 的 state/ACK 等待现已实际使用运行期 `c_timeout_s`（固件限制 0.5～30 s），不再固定为 3 s。
2. （已完成）STM32→A 实机联调：A 已解析 `wind_action` 四字段并据此算 `wind_actual_kw`，
   C 侧按冻结职责计算并上报；state_request/state/wind_action/ack 全链路已跑通。
   **下一步转入异常情况排查**（断线重连、超时、半帧/粘包、故障与保护、上电恢复）。
3. C 补 `parameter_update` 转发：A 已支持 `parameter_update`（C 参数回写），C 固件/上位机需把
   C 上位机编辑的风机参数经 STM32 转发给 A。
4. 冻结并扩展 C 串口协议（补 `session_id/step/sim_time_s/sampled_at_utc`）。
5. 联调顺序：A/B 先跑通 socket → STM32→A → C 上位机 UART。

完整计算职责和统一公式见 `docs/parameter-ownership.md`。
