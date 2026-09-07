# 三人及各自 Agent 的协作流程

三人使用同一个仓库，各自克隆到自己的电脑。Agent 操作各自本地副本，并不会自动看见别人尚未 push 的代码。

首次由仓库所有者在 GitHub 设置中邀请另外两人为协作者。本初始化不修改成员权限或分支保护。

## 每次开发

```bash
git switch main
git pull --ff-only origin main
git switch -c feat/a-scenario-loader
# 修改、测试后，仅添加本次任务文件
git add A_simulator/
git commit -m "feat(a): add scenario loader"
git push -u origin feat/a-scenario-loader
```

示例分支前缀：`feat/a-*`、`feat/b-*`、`feat/c-*`、`docs/*`。这些是命名建议，不是已创建的远端分支。
有未提交改动时先处理自己的工作，不要强行切换或覆盖。提交后打开 Pull Request，说明测试；由另一位成员检查再合并。不要三个人同时直接向 main 推送。

## 公共接口修改

先更新 common/protocol.md 和 docs/decisions.md，说明生产方、消费方、单位、兼容影响；同步样例与测试。双方确认后再改实现。

## 给 Agent 的任务示例

“先阅读 AGENTS.md 和 A_simulator/README.md，在新分支实现 A 的场景加载器；不要修改 B/C；未确认的物理参数保持配置项；补测试，提交前说明验证结果。”

真实姓名已用于任务分工，不猜测其他成员的 GitHub 用户名，不自动设置 CODEOWNERS。
