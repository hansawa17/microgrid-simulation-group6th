# v1.0.2 验收交付索引

本目录是 `v1.0.2` 现场验收的唯一入口，核对依据为 `南极考察站微电网_任务验收标准与评分表_0910.docx`。课程原文、`docs/requirements.md` 与本目录有冲突时，以课程原文为准。

| 文件 | 用途 |
|---|---|
| [01-source-and-run.md](01-source-and-run.md) | 完整源码范围、Python 3.11 环境、A/B/C 启动与测试 |
| [02-version-management.md](02-version-management.md) | Git 仓库、标签、校验、回退和保密边界 |
| [03-system-and-interfaces.md](03-system-and-interfaces.md) | 系统职责、TCP/串口、数据库和时序 |
| [04-hardware-and-deployment.md](04-hardware-and-deployment.md) | STM32/ESP8266/三机部署、烧录、重连验证 |
| [05-acceptance-checklist.md](05-acceptance-checklist.md) | 50 个基础评分项的状态和证据入口 |
| [06-onsite-demo-runbook.md](06-onsite-demo-runbook.md) | 下午现场演示顺序、故障注入和快速恢复 |
| [07-test-record.md](07-test-record.md) | 自动化结果与现场实物签字表 |
| [08-personal-briefing.md](08-personal-briefing.md) | 个人分工、接口解释和问题处理话术 |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | v1.0.2 发布摘要、兼容性和已知边界 |

验收前先查看 `git show --no-patch --decorate v1.0.2`，再把现场结果填入 `07-test-record.md` 的实物表。不得把 PC mock 或静态源码检查填成硬件通过。
