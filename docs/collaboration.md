# DicePP 协作开发流程

本文档描述 DicePP 项目的 Git 协作工作流、分支规范和发版流程。

## 工作流模型

采用 **GitHub Flow**：

- `master` 是唯一长期分支，始终可部署
- 所有开发通过 `feature/xxx` 或 `hotfix/xxx` 分支进行
- 功能完成后通过 Pull Request 合并到 `master`
- 没有 `dev` 分支

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

### 1. 开始开发

```bash
# 确保本地 master 最新
git checkout master
git pull origin master

# 创建功能分支
git checkout -b feature/xxx-master
```

### 2. 开发与提交

在功能分支上开发，按 [DicePP 开发规范](../.claude/CLAUDE.md) 提交代码。

```bash
git add .
git commit -m "feat: xxx"
```

### 3. 同步上游（如需要）

如果 `master` 有更新，先同步：

```bash
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
git push origin feature/xxx-master
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

合并后删除功能分支：

```bash
git branch -d feature/xxx-master
git push origin --delete feature/xxx-master
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
| `create-pr` | `/create-pr` | 从当前 feature 分支创建 PR |
| `review-pr` | `/review-pr <number>` | Review PR diff 并执行 approve/merge |
| `bump-version` | `/bump-version` | 递增版本号、打 tag、推送 |

## 注意事项

1. **不要在 master 上直接写代码**。master 受保护，直接 push 会被拒绝。
2. **Reviewer 不应审核自己的 PR**。可开另一个 Claude 会话或找他人 review。
3. **feature 分支命名要清晰**。让别人一眼知道这是做什么的。
4. **合并前确保 CI 通过**。虽然保护规则未强制要求，但应作为自觉。
5. **发版前跑测试**。`uv run pytest` 通过后再 bump version。
