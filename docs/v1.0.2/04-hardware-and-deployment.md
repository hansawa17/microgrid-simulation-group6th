# v1.0.2 硬件工程与部署说明

## 基线与接线

- MCU：STM32G431RBT3，CubeIDE 工程位于 `C_controller/firmware/`。
- ESP8266-01S 使用 3.3 V 稳定供电，GND 与 STM32 共地；禁止 5 V 直接 IO。
- USART1（PA9/PA10）连 ESP8266；USART2（PA2/PA3）115200 8N1 连 C 上位机 USB-UART。TX/RX 交叉。
- 真实 Wi-Fi 配置写入本地 `wifi_config.h` 或本地配置副本，不提交凭据。A IP/端口可由 C GUI 经 UART 运行期修改，断电后恢复编译期默认。

## 编译与烧录

1. STM32CubeIDE 打开 `C_controller/firmware`，核对芯片和 Debug 配置。
2. Clean Project，再 Build Project；保存构建时间、IDE 版本和 `0 errors` 截图。
3. ST-LINK 连接后烧录，复位；在 C GUI 确认 `$WIND2`、网络状态和参数回读。
4. 如果构建失败，不应用旧 bin 冒充新版；先保留错误记录并恢复可构建工程。

## 网络自愈逻辑

启动时等待 ESP8266 完成上电，用 AT 同步后执行 `AT+RST`，再设置单连接、加入 AP、使用 `AT+CIFSR` 确认有效 STA IP，最后连 A。Wi-Fi/TCP/应用层连续 3 次失败或 USART1 异常会重置传输状态并再软复位模组。有效 state/ack 会清除失败计数。

当前工程未分配 ESP8266 硬件 RST 引脚，因此模组完全无响应或供电异常时仍需重新上电。USB 数据线短暂拔插后 C GUI 会重开原 COM 口；如 COM 号改变，在界面重选。

## 三机部署核对

- 三机与 ESP8266 在同一网段，禁用 `127.0.0.1`作为跨机 A 地址；检查热点客户端隔离。
- A 监听 `0.0.0.0:5000`，Windows 防火墙只放行该入站端口，不关闭整机防火墙。
- B 与 STM32 填 A WLAN IPv4；两端均观察 state/ACK 而不仅看 TCP connected。
- 实物验收必做：断 ESP8266 供电、断/恢复热点、拔/插 C USB 串口、重启 A、长时间运行和高风禁运。结果写入 `07-test-record.md`。
