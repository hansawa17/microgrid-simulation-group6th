# v1.0.2 Git 仓库与版本管理

- 仓库：<https://github.com/hansawa17/microgrid-simulation-group6th>
- 集成分支：`main`
- 验收标签：`v1.0.2`
- B 本次远端基线：`2175ac5 B: relax supply-demand balance scoring thresholds`

验收机器使用标签而不是随后变动的 `main`：

```powershell
git fetch origin --tags
git checkout v1.0.2
git show --no-patch --decorate v1.0.2
git rev-parse 'v1.0.2^{commit}'
git status --short
```

最后一条应无输出。如需回到开发分支，在无未保存变更时执行 `git switch main`。不移动或覆盖已发布标签；后续修复发新修订号。

发布校验：

```powershell
git ls-remote --tags origin v1.0.2
git diff --exit-code v1.0.2 -- .
git archive --format=zip --output microgrid-v1.0.2-source.zip v1.0.2
Get-FileHash microgrid-v1.0.2-source.zip -Algorithm SHA256
```

仓库不接收运行数据库、虚拟环境、真实 SSID/密码、令牌、日志和 `.hex/.bin/.elf`。需要保留的现场证据单独存放，记录标签 SHA 和时间。
