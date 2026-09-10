# A 验收故障注入脚本

这些脚本只用于验收环境，默认不修改正在运行的 `grid.db`：

- `a_tcp_protocol_faults.py`：制造非法 JSON、NaN、缺字段、超长帧和半帧；A 应拒绝、记 WARNING，GUI 最迟在下一次 5 秒健康刷新时弹出非阻塞告警，主界面继续运行。
- `a_connection_faults.py`：制造 B/C 主动断开或超过服务端阈值的空闲连接；顶栏和主界面必须转为离线/超时，不得永久保留“正常”。
- `a_input_faults.py`：在独立目录生成缺列、负值、时间乱序/重复 CSV、非法配置和损坏数据库副本；从 GUI 加载时应出现错误提示，不得导致主界面退出。

示例（先在 A 界面启动 TCP 服务，并按实际端口替换 `5000`）：

```powershell
python scripts/fault_injection/a_tcp_protocol_faults.py --port 5000 --case all
python scripts/fault_injection/a_connection_faults.py --port 5000 --source B --mode abrupt
python scripts/fault_injection/a_connection_faults.py --port 5000 --source C --mode idle --idle-seconds 35
python scripts/fault_injection/a_input_faults.py --output runtime/fault_inputs
```

每次执行应记录日期、提交 SHA、输入案例、预期、实际、截图以及从 A 历史页导出的 JSONL 路径。脚本验证的是 PC 软件错误处理；STM32/Wi-Fi/UART 断线仍需实机验收。
