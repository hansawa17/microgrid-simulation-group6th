# v1.0.0 硬件工程与部署说明

## 1. 发布工程基线

| 项目 | v1.0.0 基线 |
|---|---|
| MCU 工程 | `C_controller/firmware/` |
| 工程类型 | STM32CubeIDE |
| `.ioc` 目标 | STM32G431R 系列，LQFP64 |
| 当前工程说明中的具体料号 | STM32G431RBT3 |
| 时钟 | HSI 16 MHz，未启用 PLL |
| Wi-Fi | ESP8266-01S，AT 指令固件 |
| ESP8266 UART | USART1，PA9 TX / PA10 RX |
| PC 上位机 UART | USART2，PA2 TX / PA3 RX，115200 8N1 |
| TCP 角色 | STM32/ESP8266 是连接 A `:5000` 的 `source=C` 客户端 |

仓库说明曾计划改用 RBT6，但 `v1.0.0` 工程当前按 RBT3 组织。若验收板实际是 RBT6，必须先在 CubeIDE 中核对器件、Flash/RAM、链接脚本和引脚，再重新生成/编译；不得直接把 RBT3 工程写成已适配 RBT6。

## 2. 所需硬件与工具

- STM32G431 对应开发板或最小系统板；
- ST-LINK 下载器及 SWD 连接；
- ESP8266-01S；
- 能提供足够峰值电流的稳定 3.3 V 电源；
- 3.3 V TTL USB-UART；
- A/B/C 电脑和同一局域网或手机热点；
- STM32CubeIDE（工程 `.ioc` 由 STM32CubeMX 6.5.0 生成）。

ESP8266 不能接 5 V 逻辑电平；模块发射电流会瞬时升高，供电不足常表现为随机复位、联网失败或高延迟。所有模块必须共地。

## 3. 参考接线

| STM32 | 对端 | 方向/说明 |
|---|---|---|
| PA9 / USART1_TX | ESP8266 RX | MCU 向 Wi-Fi 模块发送 AT/数据 |
| PA10 / USART1_RX | ESP8266 TX | MCU 接收 Wi-Fi 模块数据 |
| PA2 / USART2_TX | USB-UART RX | MCU 向 C 上位机发送遥测 |
| PA3 / USART2_RX | USB-UART TX | C 上位机向 MCU 下发参数/命令 |
| 3.3 V | ESP8266 VCC、EN | 按模块资料供电和上拉 |
| GND | ESP8266、USB-UART、STM32 GND | 必须共地 |
| SWDIO/SWCLK/GND | ST-LINK | 编程与调试 |

具体开发板跳线、BOOT0、复位和供电方式以所用板卡原理图为准；不要在未核对板卡的情况下照表带电连接。

## 4. 本地 Wi-Fi 配置

`C_controller/firmware/Core/Inc/wifi_config.h` 提交的是占位符：

```c
#define WIFI_SSID          "YOUR_WIFI_SSID"
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"
#define WIFI_SERVER_IP     "192.168.1.100"
#define WIFI_SERVER_PORT   5000
```

烧录前在本地填写热点 SSID/密码和合理的 A 默认地址。真实密码只能存在本地工作副本，禁止提交。A IP/端口也可在设备运行后由 C GUI 通过 `$WIFI` 修改并立即重连，因此 A 地址变化通常不需要重新编译；该运行期值掉电不保存。

## 5. 编译与烧录

1. 用 STM32CubeIDE 导入 `C_controller/firmware` 现有工程。
2. 核对目标 MCU、链接脚本、LQFP64 引脚、USART1/USART2 和 16 MHz 时钟与实物一致。
3. 填写本地 Wi-Fi 配置，但不要提交凭据。
4. 直接构建工程。若重新从 `.ioc` 生成代码，先确认用户代码区及 `wind_turbine`、`esp8266`、`wifi_client` 文件不会被覆盖。
5. 保存构建日志：IDE 版本、工具链版本、警告和错误数量、生成物大小。
6. 用 ST-LINK 连接 SWD，确认目标电压和芯片 ID，再烧录并复位。
7. 构建生成的 Debug/Release、`.elf/.hex/.bin/.map` 只作为本地验收证据，不提交 Git。

如果发布机器没有 ARM 交叉工具链，只能记录“固件源码检查/PC 测试完成，固件未在本机重新编译”，不能记录为编译通过。

## 6. 部署步骤

1. A、B 和 ESP8266 加入同一网络；查出 A 的 WLAN IPv4。
2. A 运行 `start_a.ps1` 并监听 `0.0.0.0:5000`，仅对所需网络配置防火墙入站规则。
3. B 连接 A 的 WLAN IPv4，确认全量状态同步和 ACK。
4. STM32/ESP8266 上电；通过 USART2 日志确认 AT 初始化、加入热点、连接 A 和全量状态请求。
5. C 电脑运行上位机、选择正确 COM 口；查询 `$WIFI?`，必要时下发 A 地址。
6. 开环核对遥测，再切闭环；观察 A 的 C 在线状态、B 调度、STM32 动作 seq、A actual 和三端历史。

当前 TCP 协议无认证和加密，不应把 A 端口长期无访问控制地暴露到公网。

## 7. 现场验收检查表

- [ ] 固件对实际目标板零错误编译，记录 IDE/工具链/提交 SHA。
- [ ] ST-LINK 烧录和复位成功，串口启动信息稳定。
- [ ] ESP8266 可加入热点，A 显示 C 在线并持续更新。
- [ ] `$WIFI?` 回读正确，运行期修改 IP/端口后能重连。
- [ ] `$PARAM2` 参数原子应用，`$PARAMGET` 回读一致，版本递增。
- [ ] C→A `parameter_update` 收到 accepted ACK，C GUI 显示 A 副本同步完成。
- [ ] 开环持续读取 A 状态但不发送 `wind_action`。
- [ ] 闭环发送动作，`last_wind_action_seq` 与 A 接受记录一致。
- [ ] 无风时停机；3–12 m/s 按三次曲线；12–25 m/s 额定；`>=25 m/s` 高风禁运并顺桨。
- [ ] 断开热点、拔串口、A 重启和 MCU 重启后状态可见且按设计恢复。
- [ ] 超时、半包/粘包、重复/乱序、非法参数不导致 GUI 卡死或危险动作。
- [ ] 记录至少一轮 `state → dispatch → wind_action → actual state` 的三端截图和日志。

## 8. 安全和验证边界

本工程输出是课程仿真和控制信号，不直接承担真实风机的最终安全保护。首次上电应采用限流电源、隔离负载和可随时断电的台架。高风禁运、通信失联、STOP、冷启动、保护优先级和上电恢复必须在无危险负载条件下逐项验证。

PC 本地 mock、SQLite 回放和自动化测试不能替代 STM32/ESP8266/UART 实物测试；实测未完成的项目应在验收记录中保持“待验证”。
