---
name: branch-new
description: 从 master 创建新的 feature 分支，基于 git worktree 实现环境隔离。
license: MIT
metadata:
  author: DicePP
  version: "2.0"
---

从最新 master 创建功能分支，使用 git worktree 实现独立工作区，自动共享 `.venv` 环境。

**Input**: 用户请求创建功能分支，如 "开新功能"、"/branch-new"、或提供分支名如 "feature/roll-refactor"。

**Steps**

1. **检查当前工作区状态**

   运行：
   ```bash
   git status --short
   ```

   - 有未提交更改：
     - 拒绝执行
     - 汇报未提交文件列表
     - 提示：`存在未提交更改，请先 commit 或 stash 后再开新分支`
     - **停止**
   - 干净：继续

2. **获取功能分支名称**

   如果用户已提供分支名，直接使用。

   如果未提供，根据上下文猜测——从用户最近讨论的功能、需求中推断合理的分支名。目标明确时直接创建，不需确认；模糊时简单汇报拟用名称即可。

   分支名规范处理：
   - 如果不以 `feature/` 或 `hotfix/` 开头，自动补全 `feature/` 前缀
   - 去除空格和特殊字符

3. **创建 worktree**

   调用 `EnterWorktree` 工具，传入校验后的分支名作为 `name` 参数。

   `EnterWorktree` 默认 `baseRef: fresh`，自动基于 `origin/master` 创建，无需手动 fetch/pull。

4. **验证 .venv 符号链接**

   ```bash
   test -L .venv && echo ".venv symlink ok" || ln -sf /home/ubuntu/dicepp/dev/.venv .venv
   ```

   如果符号链接创建失败，汇报原因并**停止**。

5. **验证 .claude/ agent 配置**

   ```bash
   python docs/agent/sync.py apply claude --env dev
   ```

6. **验证环境**

   ```bash
   uv run python -c "import sys; print(sys.executable)"
   ```

7. **输出结果**

   ```
   功能分支已创建

   分支: <branch_name>
   基于: origin/master
   worktree 路径: .claude/worktrees/<name>/

   可以开始开发了。提交完成后用 /branch-tidy 整理提交历史，用 /branch-polish 清理代码，最后 /pr-create 创建 Pull Request。
   ```

8. **路径硬约束**

   EnterWorktree 已将 CWD 切换到 worktree 目录。**从现在起，所有文件操作（Edit/Read/Write/Bash/git）必须以 worktree 根目录为绝对路径前缀，禁止使用原始仓库路径。** worktree 根目录可从 CWD 或 `git worktree list` 获取。

   > 反例：`/home/ubuntu/dicepp/dev/src/.../file.py`（原始仓库路径）
   > 正例：`/home/ubuntu/dicepp/dev/.claude/worktrees/<name>/src/.../file.py`

**清理 worktree**

开发完成、分支合并后，使用 `ExitWorktree` 退出并清理：

- `action: "remove"` — 删除 worktree 目录和关联分支
- `action: "keep"` — 保留 worktree，后续继续使用

**Important Notes**

- worktree 提供独立工作区，`config/`、`data/` 等文件互不干扰
- `.venv` 通过符号链接共享，所有 worktree 使用同一套 Python 环境
- `.claude/CLAUDE.md` 与 `.claude/skills/` 由 `docs/agent/sync.py` 基于当前环境生成，使 worktree 可直接使用项目技能和规范
- `EnterWorktree` 的 Claude/Linux 平台配置位于 `docs/agent/platforms/claude-linux/`
- Agent 配置同步、检查与状态汇报统一使用 `docs/agent/sync.py`
- 分支名建议用 `feature/简述` 或 `hotfix/简述` 格式
