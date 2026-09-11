# STM32 固件 · 风电子站

STM32CubeIDE 工程，目标芯片 **STM32G431RBT3**（LQFP64）(目前claude code按照RBT3生成，后续会改为RBT6），主频 16 MHz（HSI，未开 PLL）。

- `Core/Src/wind_turbine.c` / `Core/Inc/wind_turbine.h`：风机控制计算 + 串口协议 + USART2 收发。
- `Core/Src/main.c`：主循环按控制周期调用 `WindTurbine_PeriodicTask()`，并驱动 `WifiClient_Task()`。
- `Core/Src/esp8266.c` / `Core/Inc/esp8266.h`：ESP8266-01S AT 驱动（USART1）。
- `Core/Src/wifi_client.c` / `Core/Inc/wifi_client.h`：Wi-Fi/TCP JSON Lines 客户端（`source=C`）。
- `Core/Inc/wifi_config.h`：SSID / 密码 / A 的 IP:端口 配置占位。
- USART1（PA9/PA10）：接 ESP8266-01S（Wi-Fi TCP 客户端，已实现）。
- USART2（PA2/PA3）：与上位机串口通信，115200 8N1。

> 联调时风速 / 目标 / 实际功率来自 A 的 `state`；**闭环**下 STM32 算启停 + 桨距并发
> `wind_action` 回 A，**开环**只读 A 数据、不向 A 输出闭环控制命令（计算结果仍经 `$WIND2`
> 上报串口，验收项 31）。Wi-Fi 未连 A 时不生成伪造数据（输出清零并停机）。
> `wind_actual_kw` 联网后由 A 计算。state/ACK 等待超时使用运行期 `c_timeout_s`（钳位
> 0.5～30 s）。不提交 Debug/Release 与二进制输出。

TCP 客户端已按单未决事务实现 `state_request → state → wind_action → ack`（开环下止于
`state`，只读轮询继续）。ESP8266 接收支持 `+IPD,<len>` 与 `+IPD,<id>,<len>`，发送端非阻塞
等待 `>` 和 `SEND OK`；在线同步期间保持上一状态，不会用本地输入覆盖 A 状态。真实模块的
AT 固件差异、缓冲余量和断线恢复仍需实机验证。
