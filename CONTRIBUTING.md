# 三人及各自 Agent 的协作流程

三人使用同一个仓库，各自克隆到自己的电脑。Agent 操作各自本地副本，并不会自动看见别人尚未 push 的代码。

首次由仓库所有者在 GitHub 设置中邀请另外两人为协作者。本初始化不修改成员权限或分支保护。

## 各自模块直接更新

三名成员直接在 `main` 中更新各自负责的项目目录，不再为模块内日常开发创建功能分支：

- A 只更新 `A_simulator/` 及对应的 `tests/test_a_*.py`。
- B 只更新 `B_dispatch/` 及对应的 `tests/test_b_*.py`。
- C 只更新 `C_controller/` 及对应的 `tests/test_c_*.py`。
- 不修改、移动或删除其他成员目录中的文件；发现必须跨目录修改时，先与相关成员确认。

提交前先同步最新 `main`，完成后只暂存自己负责的目录和测试：

```bash
git switch main
git pull --ff-only origin main
# 修改并测试后，以 A 为例：
git add A_simulator/ tests/test_a_*.py
git commit -m "feat(a): add scenario loader"
git push origin main
```

若推送因远端已有新提交而被拒绝，先获取远端更新并确认没有覆盖他人文件，再继续；存在重叠修改时停止推送并与对应成员处理。不得强推、重置或覆盖他人提交。每次提交说明测试结果、mock/硬件验证范围和未完成事项。

## 公共接口修改

`common/`、`docs/` 和根目录文件不属于任一成员的独占目录。公共接口修改应先更新 common/protocol.md 和 docs/decisions.md，说明生产方、消费方、单位、兼容影响；同步样例与测试，并在 PR 中由相关成员确认后再合并。

## 给 Agent 的任务示例

“先阅读 AGENTS.md 和 A_simulator/README.md，仅在 A_simulator/ 和对应的 tests/test_a_*.py 中实现 A 的场景加载器；不要修改 B/C；未确认的物理参数保持配置项；补测试，提交前说明验证结果。”

真实姓名已用于任务分工，不猜测其他成员的 GitHub 用户名，不自动设置 CODEOWNERS。
