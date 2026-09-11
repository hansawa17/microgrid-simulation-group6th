# v1.0.0 验收交付索引

本目录是南极考察站微电网智能调控系统 `v1.0.0` 的验收入口。发布标签所指提交包含
A 电网仿真、B EMS 调度、C STM32 固件、C 串口上位机、公共协议、自动化测试和验收辅助脚本。

## 交付文件

| 验收材料 | 文档 | 覆盖内容 |
|---|---|---|
| 完整源码与运行说明 | [01-source-and-run.md](01-source-and-run.md) | 获取固定版本、安装依赖、A/B/C 启动、测试和故障注入 |
| Git 仓库与版本管理 | [02-version-management.md](02-version-management.md) | 仓库、分支、标签、发布、校验、回退和保密规则 |
| 系统与接口说明 | [03-system-and-interfaces.md](03-system-and-interfaces.md) | 架构、职责、数据流、TCP/串口协议、单位、时序和数据库 |
| 硬件工程与部署说明 | [04-hardware-and-deployment.md](04-hardware-and-deployment.md) | STM32 工程、ESP8266、接线、编译烧录、部署和现场验收 |

底层设计依据仍以课程原文和仓库中的
[`docs/requirements.md`](../requirements.md)、[`common/protocol.md`](../../common/protocol.md)、
[`docs/parameter-ownership.md`](../parameter-ownership.md) 为准。若整理文档与课程原文冲突，按课程原文处理并记录变更。

## 发布时的验证边界

- 本次发布在 CPython 3.11.14 / PyQt6 6.11.0 环境执行 153 项自动化测试，结果全部通过；正式标签指向的提交 SHA 以 `git rev-parse 'v1.0.0^{commit}'` 输出为准。
- A/B/C 的 TCP 协议、数据库、GUI 逻辑可由 PC 自动化测试覆盖；本地模拟器结果必须标注为 mock。
- STM32 固件源码已纳入版本管理，但 `v1.0.0` 发布前若没有可用交叉工具链，则不能把源码检查写成“固件编译通过”。
- STM32、ESP8266、UART、热点断线、上电恢复等项目必须以现场实物记录为准，不能由单元测试替代。

## 验收证据建议

每个案例保存：日期、`v1.0.0` 标签解析出的提交 SHA、设备和固件版本、配置（不含密码）、输入场景、预期、实际、截图/日志路径、执行人及结论。推荐至少覆盖无风、适宜风速、高风禁运、低负荷、高负荷、柴发上下限、开/闭环、断线重连、超时、重复或乱序报文、TCP 分包/粘包及参数回读。
