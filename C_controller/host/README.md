# C 串口上位机 · 风电监控

PyQt6 + pyserial + pyqtgraph 桌面端，维护 `wind.db`。入口 `main.py`。

- 串口后台收发 `serial_comm.py`、帧编解码 `protocol.py`、本地仿真 `simulator.py`。
- 遥测字段名已对齐仓库 `common/protocol.md`，并含 `wind_operating_limit_kw`。
- 运行监控页可通过 `$WIFI/$WIFI?` 查询或修改 STM32 连接 A 的地址/端口；该运行期值断电后恢复固件默认值，SSID/密码不由界面管理。
- 风机参数可写入 `wind.db` 并下发 MCU 或本地仿真；固件已实现 C→A 的
  `parameter_update` 排队转发及 ACK 状态回传。该固件变更仍需重新编译、烧录并实机复测。
- 参数页新增「读回 MCU 参数」按键（仅串口连接时可用）：`$PARAMGET?` 查询后用 MCU
  实际生效值覆盖表单与 `wind.db`（`remote_adjust.source="MCU"` 留审计痕迹）；
  串口未连接时「应用参数」只写本地库并显式标记 `parameter_verified=0`（未下发）。
- 详见上一级 `README.md`。

> PC-C 上位机只通过 UART 连接 STM32，不直连 A、不代替 STM32 控制算法。
