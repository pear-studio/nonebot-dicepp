# DicePP 协作开发流程

本文档描述 DicePP 项目的 Git 协作工作流、分支规范和发版流程。

## 工作流模型

采用 **GitHub Flow**：

- `master` 是唯一长期分支，始终可部署
- 所有开发通过 `feature/xxx` 或 `hotfix/xxx` 分支进行
- 功能完成后通过 Pull Request 合并到 `master`
- 没有 `dev` 分支

## 本地环境结构

项目采用 **bare repo + worktree** 的本地部署模式：

```
/home/ubuntu/dicepp/
├── .bare/                  # bare 仓库（所有 worktree 共享）
├── dev/                    # master 分支，基础工作区 + .venv
├── prod/                   # prod 本地分支（跟踪 origin/master），生产环境
└── worktrees/              # feature worktree 目录
    ├── feature-roll/       # feature/roll 分支
    └── ...
```

| 目录 | 分支 | 用途 | 操作原则 |
|------|------|------|---------|
| `dev/` | `master` | 基础工作区 | 存放共享的 `.venv`，不直接开发 |
| `prod/` | `prod`（跟踪 `origin/master`）| 生产环境 | 只 pull 更新，不直接开发 |
| `worktrees/*/` | `feature/xxx` | 功能开发 | 每个功能独立的 worktree，共享 dev 的 `.venv` |

**为什么选择 worktree？**

- dev 和 prod 共享同一个 git 数据库，不重复克隆
- 每个 feature 有独立的目录，互不干扰
- `.venv` 符号链接共享，避免重复安装依赖
- prod 本地分支独立命名，避免与 master 冲突

### .venv 共享机制

所有 feature worktree 通过符号链接共享 dev 的 `.venv`：

```
worktrees/feature-xxx/.venv -> /home/ubuntu/dicepp/dev/.venv
```

新增依赖时，在任意 worktree（包括 dev）中运行 `uv sync` 即可同步到全部 worktree。

## 分支规范

| 分支类型 | 命名 | 说明 |
|---------|------|------|
| 主分支 | `master` | 受保护，禁止直接 push，只能 PR 合并 |
| 功能分支 | `feature/简述` | 从 master 切出，开发单一功能 |
| 修复分支 | `hotfix/简述` | 紧急修复，同样走 PR 流程 |

示例：
- `feature/roll-refactor`
- `feature/persona-event-delta`
- `hotfix/unicode-crash`

## 开发流程

### 1. 创建功能 worktree

使用 Claude Code 的 `start-worktree` skill：

```
/start-worktree
```

或手动：

```bash
cd /home/ubuntu/dicepp/dev
git fetch origin master
git branch feature/xxx origin/master
git worktree add /home/ubuntu/dicepp/worktrees/feature-xxx feature/xxx
cd /home/ubuntu/dicepp/worktrees/feature-xxx
ln -s /home/ubuntu/dicepp/dev/.venv .venv
```

### 2. 开发与提交

在 feature worktree 中开发：

```bash
cd /home/ubuntu/dicepp/worktrees/feature-xxx
git add .
git commit -m "feat: xxx"
```

### 3. 同步上游（如需要）

如果 `master` 有更新，先同步：

```bash
cd /home/ubuntu/dicepp/worktrees/feature-xxx
git fetch origin
git rebase origin/master
git push --force-with-lease
```

### 4. 创建 Pull Request

使用 Claude Code 的 `create-pr` skill：

```
/create-pr
```

或手动：

```bash
git push origin feature/xxx
gh pr create --title "feat: xxx" --body "xxx" --base master
```

### 5. Code Review

使用 Claude Code 的 `review-pr` skill：

```
/review-pr <pr_number>
```

Review 维度：
- 代码逻辑正确性
- 边界情况处理
- 命名规范与代码风格
- 测试覆盖
- 安全风险

### 6. 合并

Review 通过后，选择合并方式：

- **Squash and merge**（默认推荐）：压缩为一个 commit，master 历史简洁
- **Rebase and merge**：保留每个 commit，线性历史
- 不推荐使用普通 merge commit

```bash
gh pr merge <pr_number> --squash
```

### 7. 清理 worktree

合并后删除 feature worktree：

```bash
# 先切走当前分支
cd /home/ubuntu/dicepp/worktrees/feature-xxx
git checkout master

# 删除 worktree 和分支
cd /home/ubuntu/dicepp/dev
git worktree remove /home/ubuntu/dicepp/worktrees/feature-xxx
git branch -d feature/xxx
git push origin --delete feature/xxx
```

## 发版流程

功能合并到 `master` 后，使用 `bump-version` skill 发版：

```
/bump-version
```

流程：
1. 确认在 `master` 分支
2. 确认工作区干净
3. 选择递增级别：patch / minor / major
4. 执行 `bump-my-version`，自动 commit + tag
5. 自动 push `master` 和 tag 到远端

发版后更新生产环境：

```bash
cd /home/ubuntu/dicepp/prod
git pull origin master
```

## 分支保护规则

`master` 分支已配置以下保护：

- [x] 必须通过 Pull Request 合并
- [x] 禁止 force push
- [x] 禁止删除分支
- [ ] 不要求 review approval（单人项目，多人协作时可开启）
- [ ] 不要求 status checks 通过

如需调整，访问 https://github.com/pear-studio/nonebot-dicepp/settings/branches

## Claude Code Skill 速查

| Skill | 触发方式 | 用途 |
|-------|---------|------|
| `start-worktree` | `/start-worktree` | 创建 feature worktree 并共享 .venv |
| `create-pr` | `/create-pr` | 从当前 feature 分支创建 PR |
| `review-pr` | `/review-pr <number>` | Review PR diff 并执行 approve/merge |
| `bump-version` | `/bump-version` | 递增版本号、打 tag、推送 |

## 注意事项

1. **不要在 master 上直接写代码**。master 受保护，直接 push 会被拒绝。
2. **Reviewer 不应审核自己的 PR**。可开另一个 Claude 会话或找他人 review。
3. **feature 分支命名要清晰**。让别人一眼知道这是做什么的。
4. **合并前确保 CI 通过**。虽然保护规则未强制要求，但应作为自觉。
5. **发版前跑测试**。`uv run pytest` 通过后再 bump version。
6. **及时清理已合并的 worktree**。避免磁盘堆积。
