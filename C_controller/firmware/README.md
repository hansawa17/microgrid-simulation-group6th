# STM32 固件 · 风电子站

STM32CubeIDE 工程，目标芯片 **STM32G431RBT3**（LQFP64）(目前claude code按照RBT3生成，后续会改为RBT6），主频 16 MHz（HSI，未开 PLL）。

- `Core/Src/wind_turbine.c` / `Core/Inc/wind_turbine.h`：风机控制计算 + 串口协议 + USART2 收发。
- `Core/Src/main.c`：主循环按控制周期调用 `WindTurbine_PeriodicTask()`，并驱动 `WifiClient_Task()`。
- `Core/Src/esp8266.c` / `Core/Inc/esp8266.h`：ESP8266-01S AT 驱动（USART1）。
- `Core/Src/wifi_client.c` / `Core/Inc/wifi_client.h`：Wi-Fi/TCP JSON Lines 客户端（`source=C`）。
- `Core/Inc/wifi_config.h`：SSID / 密码 / A 的 IP:端口 配置占位。
- USART1（PA9/PA10）：接 ESP8266-01S（Wi-Fi TCP 客户端，已实现）。
- USART2（PA2/PA3）：与上位机串口通信，115200 8N1。

> 联调时风速 / 目标 / 实际功率来自 A 的 `state`，STM32 算启停 + 桨距并发 `wind_action`；
> Wi-Fi 未连 A 时回退本地随机模拟。`wind_actual_kw` 联网后由 A 计算。
> 不提交 Debug/Release 与二进制输出。

TCP 客户端已按单未决事务实现 `state_request → state → wind_action → ack`。ESP8266 接收支持
`+IPD,<len>` 与 `+IPD,<id>,<len>`，发送端非阻塞等待 `>` 和 `SEND OK`；在线同步期间保持上一状态，
不会用本地随机输入覆盖 A 状态。真实模块的 AT 固件差异、缓冲余量和断线恢复仍需实机验证。
