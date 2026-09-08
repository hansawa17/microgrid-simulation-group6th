# C 串口上位机 · 风电监控

PyQt6 + pyserial + pyqtgraph 桌面端，维护 `wind.db`。入口 `main.py`。

- 串口后台收发 `serial_comm.py`、帧编解码 `protocol.py`、本地仿真 `simulator.py`。
- 遥测字段名已对齐仓库 `common/protocol.md`，并含 `wind_operating_limit_kw`。
- 详见上一级 `README.md`。

> PC-C 上位机只通过 UART 连接 STM32，不直连 A、不代替 STM32 控制算法。
