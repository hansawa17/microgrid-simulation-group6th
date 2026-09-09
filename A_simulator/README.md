# A 电网模拟器 · 基础实现

本目录负责场景加载、设备实际出力仿真、`grid.db` 和 TCP 服务端。当前版本已提供可运行的基础骨架，不代表完整作业已经完成。

## 已实现

- 从 CSV 分别读取风速和负荷曲线，并按仿真时刻线性插值。
- 默认提供 600 s、601 个输入点的 `scenarios/antarctic_10min.csv`；短场景 `demo.csv` 保留用于测试。
- 每个仿真步只读取一次系统 UTC，并为状态、历史和 SCADA 数据统一生成 `sampled_at_utc`。
- 纯函数单步模型：A 独立复算风速物理上限用于 actual 安全裁剪，并执行桨距降额、风/柴目标跟踪、上下限和爬坡约束。
- 明确区分 B 的功率目标、C 的启停/桨距动作与 A 计算的实际出力。
- SQLite `grid.db`：仿真状态、场景、设备参数、控制状态、当前状态、状态历史、SCADA 当前/历史、命令、连接状态和日志。
- 独立计算循环与线程化 TCP 服务；TCP 使用协议草案中的 UTF-8 JSON + LF，处理半帧/粘包、帧长、非法 JSON、NaN/Infinity、重复和乱序命令。
- PyQt6 综合监控界面：在“运行监控”同页展示通信配置、仿真/CSV 控制、关键指标、设备与连接状态、实时风速和实时功率曲线，并可切换到场景编辑模式。
- 参数设置页按 `owner/source/updated_at_utc` 展示当前参数；只允许在 A 界面编辑 A 负责的可编辑项，B/C 参数通过协议同步后以只读副本显示，避免跨模块越权修改。
- 历史数据页查询 `state_history` 与日志，将采样时间、风速、负荷、能力值、目标、实际出力和功率不平衡以表格及可选指标曲线展示；会话选择器避免重放后把不同 `session_id` 的曲线错误相连。
- 实时与历史曲线横轴使用 `sampled_at_utc` 并换算为 UTC+8 北京时间显示；场景预览也以打开/初始化时刻加 `sim_time_s` 后显示为北京时间，但模型、数据库和协议仍保留 UTC 与可复现的相对仿真时间。
- 报警与通信页展示本机 IPv4、监听地址和端口；监听配置始终可编辑，TCP 运行期间修改会明确标注为待应用，并在下一次启动 TCP 子进程时生效。
- TCP 服务启动、停止或子进程异常退出时会将 B/C 连接状态复位；连接连续 10 s 无报文时按空闲超时断开并标记离线，连接/断开变化写入日志，避免界面保留“假在线”。
- 停止或完成后可从界面“新建会话”：生成新的 `session_id`，安全清零目标/实际出力并完全顺桨，同时保留场景、参数与旧会话历史。
- 旧数据库损坏或版本不兼容时，GUI 显示 `grid.db 异常`并启用修复按钮；确认后先把数据库及 WAL 文件备份为带 UTC 时间戳的 `.bak`，再用当前曲线初始化新库，失败时自动恢复原文件。
- GUI 通过独立 `QProcess` 启动计算循环和 TCP 服务，不在界面线程内执行阻塞收发或仿真循环。
- 标准库 `unittest` 测试和本地 TCP 冒烟测试。

## 尚未实现或尚未确认

- `common/protocol.md` 的职责与物理参数基线已确认；完整四遥点表、增量变位格式和命令超时仍未冻结。
- `config.example.json` 使用小组确认的统一值；课程原文没有指定这些数值。
- 基础桨距模型为线性降额；风机运行暂采用 B、C 双方都允许才启用的保守组合。柴发仅按 B 目标和设备约束跟踪，不擅自加入尚未确认的本地自动补偿规则。
- 未连接真实 STM32，当前结果均属于软件 mock。

## 快速运行

项目统一使用 Python 3.11.x 和 PyQt6。Windows 推荐在仓库根目录双击 `启动A程序.bat`，或从 PowerShell 执行：

```powershell
.\scripts\start_a.ps1
```

一键脚本从自身位置定位仓库；缺少 `.venv` 时调用 `scripts/bootstrap.ps1`，缺少数据库时按指定配置和场景初始化一次，然后启动 GUI。启动脚本会原样复用已有 `grid.db`；如需重建，应在 GUI 中确认操作，程序会先生成可恢复备份。脚本不会创建或覆盖 `config.local.json`，本地配置与运行数据库仍由 `.gitignore` 排除。

需要指定运行文件或预设监听端点时：

```powershell
.\scripts\start_a.ps1 `
  -BindAddress 0.0.0.0 `
  -Port 5005 `
  -Db .\data\runtime\grid-demo.db `
  -Config .\A_simulator\config.example.json `
  -Scenario .\A_simulator\scenarios\demo.csv
```

`-BindAddress/-Port` 只有显式提供时才覆盖配置文件值，并作为 GUI 的初始监听设置；界面运行期间也可修改，当前 TCP 服务保持原端点，重启服务后应用新值。`-Db/-Config/-Scenario` 可使用仓库相对路径或绝对路径。

如需手工创建环境：

```powershell
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\activate
```

推荐通过固定使用 `.venv` 的入口执行：

```powershell
.\scripts\run_a.ps1 init
.\scripts\run_a.ps1 control start
.\scripts\run_a.ps1 run --steps 5
.\scripts\run_a.ps1 show
```

不使用一键脚本时，也可直接启动综合监控界面：

```powershell
.\.venv\Scripts\python.exe -m A_simulator gui
```

界面默认加载 `scenarios/antarctic_10min.csv`。也可显式指定数据库、配置、场景与初始监听地址：

```powershell
.\.venv\Scripts\python.exe -m A_simulator gui `
  --scenario .\A_simulator\scenarios\demo.csv `
  --db .\data\runtime\grid-demo.db `
  --bind 0.0.0.0 `
  --port 5005
```

CSV 表头固定为 `step,sim_time_s,wind_speed_mps,load_power_kw`。从 CSV 加载曲线后，在界面拖动曲线并松开鼠标会原子写回当前 CSV；也可点击“保存当前 CSV”或“另存 CSV”。数据库快照没有源 CSV，编辑后必须另存。曲线修改不会直接改写已有 `grid.db`；重新初始化数据库时会先备份旧库。

数据库默认生成在 `data/runtime/grid.db`，已由根目录 `.gitignore` 排除。`init` 不覆盖已有数据库。当前 `grid.db` schema 为 v5；完整的 v4 数据库会在首次访问时原地迁移并保留状态历史，v4 以下或结构损坏的旧库不会猜测迁移，应先备份再初始化新库。

GUI 可启动独立 TCP 子进程；命令行调试时也可在另一个终端启动：

```powershell
.\scripts\run_a.ps1 serve
```

默认监听 `0.0.0.0:5000`。客户端应连接 A 电脑的实际 WLAN IPv4，而不是 `0.0.0.0`。也可用 `--bind`、`--port`、`--db`、`--config` 显式覆盖。

FRP/raw TCP 穿透时，要区分 A 的本地监听端点和客户端访问的公网端点。例如将公网 `frp-box.com:38243` 映射到 A 本机 `127.0.0.1:5005` 后，A 界面监听端口填写 `5005`；B/C 客户端则将主机与端口**分开**填写为 `frp-box.com` 和 `38243`。界面中的 bind/port 只影响下一次启动的 A TCP 子进程，不会修改 FRP 隧道配置。当前协议没有认证和加密，公网联调结束后应停止隧道，且不得把令牌写入仓库。

## UTC 时间轴与参数同步

- 顶部时钟把操作系统提供的当前 UTC 换算为固定 UTC+8 北京时间；A 不在 GUI 主线程直接轮询公网 NTP，系统时间服务负责授时。
- `sim_time_s` 仍是 CSV 场景内的相对时间，用于插值和模型计算；真实运行/历史图横轴使用每一步唯一的 `sampled_at_utc`，仅在显示时转换为 UTC+8。
- UTC 校时造成墙钟跳变时，控制报文顺序仍只依据 `session_id + step + seq`，不以图表时间或不同电脑墙钟排序。
- B/C 的参数变更以 `parameter_update` 同步到 A。A 只校验、存储并展示符合来源写权限的参数；同步 ACK 仅表示接收成功，参数是否作用于 B 调度或 C/STM32 控制应查看后续状态。
- 重连后重复发送值完全相同的参数快照仍会正常 ACK，但不会伪造新的参数变更历史。
- A 的 `state.payload.next_command_seq` 按当前会话和请求方返回下一条新命令的安全序号下界，供无可靠墙钟的 STM32 在重启后恢复发送高水位；ACK 未知命令仍按原 seq 幂等重发。
- 风机参数由 C 上位机维护并写回 STM32；B 维护调度参数；A 维护仿真、场景和实际出力模型参数。A 参数页不会让本地操作员越权改写 B/C 所有权参数。

仿真状态控制：

```powershell
.\scripts\run_a.ps1 control pause
.\scripts\run_a.ps1 control resume
.\scripts\run_a.ps1 control stop
```

## 更新日志

### 2026-09-07 · Python 3.11 / PyQt6 环境

- A 的 PC 端开发环境统一为 Python 3.11.x。
- Qt 界面依赖固定为 `PyQt6==6.11.0`。
- 当前仅完成环境配置，A 的 Qt 界面仍待实现。

### 2026-09-07 · 授时时间接口与默认场景

- `SimulationState` 和 TCP `state.payload` 增加 `sampled_at_utc`。
- A 在每一步模型计算前读取一次系统 UTC，同一步的状态、SCADA 与历史记录复用该值。
- `grid.db` schema 升级为 v2，采样、接收、创建和更新时间采用含义明确的 `_utc` 字段名。
- CSV 增加显式连续 `step` 列；默认运行场景改为 10 分钟、601 点。
- 运行周期改由单调时钟调度，避免操作系统授时调整干扰 1 s 执行周期。
- 新增 `scripts/bootstrap.ps1` 和 `scripts/run_a.ps1`，分别负责环境初始化和固定使用项目 `.venv` 启动 A。

### 2026-09-08 · CSV 曲线 GUI 与 TCP 状态字段对齐

- 将助教参考控件适配为 A 的 PyQt6 曲线组件，横轴改用 CSV 的显式 `sim_time_s`，不再硬编码 24 小时。
- GUI 使用协议字段名 `wind_speed_mps`、`load_power_kw`，支持加载、编辑、另存和仿真时刻游标。
- A 的状态模型、数据库、SCADA 和 TCP payload 增加 `wind_operating_limit_kw`；同时发布 `wind_available_kw`。
- B 调度输入改为使用 `wind_operating_limit_kw`，不再用 `wind_actual_kw` 冒充可用能力。
- 本次为软件 mock 验证，尚未完成 A/B/C 跨电脑或 STM32 实机联调。

### 2026-09-08 · A/C 界面风格统一（旧版布局，已由综合监控取代）

- 参考 GitHub `main` 分支的 `C_controller/host/gui.py`，统一浅蓝灰 SCADA 背景、白色卡片、深蓝主色、顶栏状态胶囊、左侧导航和微软雅黑字体层级。
- 该阶段 A 界面曾按自身职责拆分为“场景曲线、运行监控、TCP 协议状态”三页；后续综合监控重构已将前两页合并并增加参数、历史和通信页面，仍不复制 C 的串口或风机控制功能。
- 顶栏和监控页实时显示 `grid.db`、仿真进程、TCP 服务及 B/C 客户端连接状态。

### 2026-09-08 · 按课程原文重申计算职责

- A 从 CSV 产生实时风速和负荷，并计算 `wind_actual_kw`、`diesel_actual_kw` 与功率不平衡。
- B 只产生风机/柴发 target；C 产生 `wind_available_kw`、运行许可和 `pitch_target_deg`。
- `wind_action` 增加 C 计算的 available/operating limit；A 校验、存储并转发，不把自身内部物理上限冒充为 C 的公共可用功率。
- 风机统一为 100 kW、3/12/25 m/s、三次功率曲线、0-90 deg；完整说明见 `docs/parameter-ownership.md`。

### 2026-09-08 · A 综合监控 UI、UTC 历史与通信配置

- 将场景曲线和运行监控合并为一页，同屏展示场景/仿真控制、实时 KPI、状态卡片、风速与功率曲线。
- 新增参数设置、历史数据、报警与通信页面；历史表和曲线直接读取 `grid.db`，B/C 同步参数按所有权只读展示。
- 顶部时钟与实时/历史横轴对齐 UTC 授时并以 UTC+8 北京时间展示；保留 UTC 存储和 `sim_time_s` 模型时间，控制顺序保持 `session_id + step + seq`。
- 历史数据库查询移入单独工作线程，重复筛选请求合并处理；曲线可读取所选窗口，表格最多渲染最近 1000 行，避免主线程因 SQLite 查询和大量单元格自适应计算进入未响应状态。
- 新增可视化本机 IPv4、bind/port 修改和 TCP 子进程重启约束，并提供 `scripts/start_a.ps1` 与根目录 `启动A程序.bat` 一键入口。
- 增加安全新会话与按会话历史曲线、损坏数据库容错、计算子进程异常退出自动暂停、TCP 启停复位及 10 s 空闲断线判定，避免进程/连接状态与数据库脱节。
- 当前完成范围为 PC 端软件测试；尚未完成 STM32 实机、UART、三机公网闭环与系统授时异常的实机联调。

### 2026-09-09 · A 端口、数据库恢复与曲线写回

- TCP 运行时不再禁用 bind/port 控件；界面区分当前监听端点和下次启动待应用端点。
- 缺失、损坏或需要重建的 `grid.db` 均可从界面初始化；已有数据库先备份，创建失败会自动恢复。
- 场景曲线编辑完成后自动原子写回源 CSV，同时提供“保存当前 CSV”和“另存 CSV”；数据库快照仍要求另存，避免来源不明时覆盖文件。

### 2026-09-09 · A/B 长连接稳定性

- A TCP server 对已连接 socket 启用 keepalive/TCP_NODELAY，将已识别客户端的应用层空闲窗口由 10 s 调整为 30 s，并将监听积压队列设为 16。
- B 仍以 1 s 状态请求作为主要应用层心跳；30 s 只用于清理真正无报文的连接，不是控制指令有效期。
- 真实跨电脑局域网、公网 raw TCP 隧道与 Windows 防火墙仍需在目标环境验证。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_a_simulator tests.test_a_gui -v
```

测试覆盖无风、适宜风速、切出风速、负荷与出力不平衡、柴油机上下限/爬坡、场景插值与 CSV 往返、综合界面离屏构造、参数写权限、UTC 曲线、数据库历史、命令去重/越序、TCP 半帧与粘包。测试不等同于真实硬件联调。
