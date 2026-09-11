# v1.0.0 Git 仓库与版本管理

## 1. 仓库与正式版本

- GitHub：<https://github.com/hansawa17/microgrid-simulation-group6th>
- 集成分支：`main`
- 正式版本标签：`v1.0.0`
- 历史预发布标签：`beta0.4`

`v1.0.0` 使用带说明的 Git 标签固定发布提交，并通过 GitHub Release 对外展示。标签发布后不移动、不覆盖；后续修复使用新提交和新版本号。

## 2. 发布内容核对

正式版发布前必须满足：

1. 拉取并核对 `origin/main`，确认本地没有遗漏的 A/B/C 更新。
2. 检查 B 最新变更：GUI 自持 TCP；每 1 s 检查、默认每 5 s 决策；仅 YK/YT 变化时发送调度。
3. 检查公共协议、参数所有权、单位和端口没有未记录冲突。
4. 在 Python 3.11 环境运行完整自动化测试。
5. 检查 `git status`，只提交本次发布文件，不夹带个人运行数据或其他成员未完成文件。
6. 记录固件是否完成交叉编译和实机复测；未完成时必须在 Release 中保留限制说明。

## 3. 日常协作流程

```powershell
git fetch origin --tags
git status --short --branch
git log --oneline --decorate -10
git pull --ff-only origin main
```

合入前查看差异和测试；提交应短小并说明模块，例如 `fix(B): ...`、`docs: ...`、`test(C): ...`。禁止强推、重写他人提交或用目标值冒充实际出力。涉及公共协议、字段单位、控制权或跨模块行为时，同步更新 `common/protocol.md`、`docs/decisions.md` 和相关测试。

## 4. 版本号和标签规则

- `v主版本.次版本.修订号`：可验收正式版，如 `v1.0.0`。
- `betaX.Y`：预发布联调快照，如 `beta0.4`。
- 主版本：不兼容的协议或职责变化。
- 次版本：向后兼容的新功能。
- 修订号：向后兼容的修复和文档勘误。

正式标签创建和推送示例：

```powershell
git tag -a v1.0.0 -m "南极考察站微电网智能调控系统 v1.0.0"
git push origin main
git push origin v1.0.0
```

发布前必须确认 `main` 推送成功，且标签指向同一个已测试提交。

## 5. 验证与复现

```powershell
git fetch origin --tags
git rev-parse 'v1.0.0^{commit}'
git show --stat v1.0.0
git diff --exit-code v1.0.0 -- .
```

GitHub Release 应列出发布提交 SHA、测试环境和结果、功能摘要、已知限制，并附带平台自动生成的 Source code 归档。需要交付离线包时，从标签生成并记录 SHA-256：

```powershell
git archive --format=zip --output microgrid-v1.0.0-source.zip v1.0.0
Get-FileHash microgrid-v1.0.0-source.zip -Algorithm SHA256
```

## 6. 回退与修复

- 复现旧版：在独立目录检出 `v1.0.0`，不要在有未提交修改的工作区强制切换。
- 撤销已发布错误：在 `main` 上创建反向提交，再发布新修订版；不要移动 `v1.0.0` 标签。
- 数据库 schema 升级前备份本机运行库；禁止用 Git 管理或跨电脑共享正在写入的 SQLite 文件。

## 7. 不得进入仓库的内容

`.gitignore` 已排除 `.venv`、SQLite、日志、运行目录、MCU Debug/Release 和二进制产物。仍需人工确认不提交：

- `config.local.json`、`.env`、真实 SSID/密码、令牌和公网穿透凭据；
- `grid.db`、`ems.db`、`wind.db` 及其 WAL/备份；
- `.hex`、`.bin`、`.elf`、对象文件和临时 IDE 输出；
- Word 临时锁文件、个人截图和无关测试产物。
