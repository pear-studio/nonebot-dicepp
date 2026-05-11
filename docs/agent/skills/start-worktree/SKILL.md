---
name: start-worktree
description: 为 feature 分支创建独立的 git worktree，并自动共享 dev 目录的 .venv 环境。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

为 feature 分支创建独立的 git worktree，开发完成后可直接在 worktree 中创建 PR。自动共享 dev 目录的 `.venv`，避免重复安装依赖。

**Input**: 用户请求创建 feature worktree，如 "开新功能"、"/start-worktree"、或提供分支名如 "feature/roll-refactor"。

**重要**: 本项目使用 bare repo + worktree 架构，Claude Code 内置的 `EnterWorktree` 工具无法识别此结构。**禁止使用 EnterWorktree 工具**，必须直接使用 `git worktree add` 命令。

**Prerequisites**

- Git 仓库为 bare repo + worktree 模式
- `dev/` worktree 存在且已配置 `.venv`
- `origin/master` 可达

**Steps**

1. **获取功能分支名称**

   如果用户没有提供分支名，询问：
   > 请输入功能分支名称（如 `feature/roll-refactor`）：

   分支名规范检查：
   - 如果不以 `feature/` 或 `hotfix/` 开头，自动补全 `feature/` 前缀
   - 去除空格和特殊字符

2. **检查 dev .venv 存在**

   确认共享环境：
   ```bash
   test -d /home/ubuntu/dicepp/dev/.venv && echo "ok" || echo "missing"
   ```

   如果不存在：
   - 汇报：`dev/.venv 不存在，请先运行 uv sync`
   - **停止**

3. **创建 worktree（同时创建分支）**

   目标路径：`/home/ubuntu/dicepp/worktrees/<branch_name>`

   ```bash
   git fetch origin master
   git worktree add -b <branch_name> /home/ubuntu/dicepp/worktrees/<branch_name> origin/master
   ```

   `-b` 会基于 `origin/master` 创建新分支并同时 checkout 到新 worktree。如果分支已存在则去掉 `-b`：

   ```bash
   git worktree add /home/ubuntu/dicepp/worktrees/<branch_name> <branch_name>
   ```

   如果 worktree 已存在：
   - 汇报：`worktree 已存在: /home/ubuntu/dicepp/worktrees/<branch_name>`
   - 询问是否切换到该 worktree 继续开发
   - **停止**

5. **共享 .venv**

   在新 worktree 中创建符号链接：
   ```bash
   cd /home/ubuntu/dicepp/worktrees/<branch_name>
   ln -s /home/ubuntu/dicepp/dev/.venv .venv
   ```

6. **验证环境**

   运行：
   ```bash
   cd /home/ubuntu/dicepp/worktrees/<branch_name>
   uv run python -c "import sys; print(sys.executable)"
   ```

   验证 Python 解释器路径指向 dev/.venv 中的解释器。

7. **输出结果**

   成功创建后输出：
   ```
   ✅ Feature worktree 已创建

   分支:    <branch_name>
   目录:    /home/ubuntu/dicepp/worktrees/<branch_name>
   基于:    origin/master
   .venv:   共享自 /home/ubuntu/dicepp/dev/.venv

   进入工作目录:
     cd /home/ubuntu/dicepp/worktrees/<branch_name>

   开发完成后:
     /pr-create   (创建 Pull Request)
   ```

**清理 worktree**

功能合并后，删除 worktree：

```bash
cd /home/ubuntu/dicepp/worktrees/<branch_name>
git checkout master  # 切走分支（必须先离开当前分支）
cd /home/ubuntu/dicepp/dev
git worktree remove /home/ubuntu/dicepp/worktrees/<branch_name>
git branch -d <branch_name>
```

**Important Notes**

- worktree 路径不能位于已有 worktree 内部，因此统一放在 `/home/ubuntu/dicepp/worktrees/` 下。
- `.venv` 通过符号链接共享，所有 worktree 使用同一套 Python 环境。如果某个 feature 需要新增依赖，在任意 worktree（包括 dev）中 `uv sync` 即可同步到全部 worktree。
- 各 worktree 的 `config/`、`data/` 等文件相互独立，不会互相影响。
- worktree 数量建议控制在合理范围，合并后及时清理。
