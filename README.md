# microgrid-simulation-group6th

南极考察站微电网智能调控课程项目，第六组。

> 当前为协作骨架，不是已完成或可启动的系统。已核对《软硬件编程综合》课程大作业原文；课程硬性要求与小组协议草案分开记录。协议及模型中标为草案的部分须三人确认。

## 分工与入口

| 成员 | 模块 | 代码位置 | 核心交付 |
|---|---|---|---|
| A 刘雨杭 | 电网模拟器 | `A_simulator/` | 场景、风柴实际出力仿真、TCP 服务端、grid.db、Qt 界面 |
| B 李佳霖 | EMS 主站 | `B_dispatch/` | 状态采集、调度、开闭环模式、ems.db、Qt 界面 |
| C 陈信甫 | 风电子站 | `C_controller/` | STM32 控制、Wi-Fi TCP 客户端、串口上位机、wind.db |

运行关系：B ↔ A ↔ STM32；STM32 ↔ C 上位机（串口）。手机热点只承载局域网，不承担调度。A 是 TCP 服务端，B 和 STM32 主动连接 A。

## 开始协作

1. 阅读 [需求与边界](docs/requirements.md)、[接口草案](common/protocol.md)、[待确认事项](docs/decisions.md)。
2. 克隆仓库：`git clone https://github.com/hansawa17/microgrid-simulation-group6th.git`。
3. 从最新 main 创建个人功能分支，按 [协作说明](CONTRIBUTING.md) 开发；Agent 先读 [AGENTS.md](AGENTS.md)。
4. 将 `common/config.example.json` 复制为根目录 `config.local.json`，修改本地 IP。此文件不入库。
5. 当前可执行的基础检查：`python -m unittest discover -s tests -v`。仅检查配置和样例一致性，不代表系统联调成功。

## 文档索引

- [课程方法与需求](docs/requirements.md)
- [通信字段、方向和帧边界](common/protocol.md)
- [热点联调](docs/network.md)
- [数据库职责](docs/database.md)
- [验收清单](docs/acceptance.md)
- [实施顺序与待确认项](docs/decisions.md)

不提交密码、令牌、数据库实测内容、构建输出及无授权课程附件。各模块完成后补齐依赖、启动命令、参数与测试说明。

---
原仓库备注（保留）：王鸡的彬巴
