---
name: branch-tidy
description: 开发分支提交重组——分析、分组、walkthrough 确认、reset-soft 重提交、验证无内容丢失。不修改代码。
---

# branch-tidy — 开发分支提交重组

将零散的开发草稿提交重新组织为干净、可读的历史，每个 commit 独立可理解、可编译、可测试。只重组 commit 边界和 message，**不修改代码内容**。完成后可用 /branch-polish 清理代码遗留项。

## 输入

```
/branch-tidy              # 当前分支
/branch-tidy <branch>     # 指定目标分支
```

## 步骤

### 阶段一：准备

1. **确认工作区干净**：`git status --short` 必须无输出。有未提交改动则**停止**。

2. **确认 worktree 隔离**：`git worktree list` 检查是否已在 worktree 中。不在 → 调 `EnterWorktree` 创建。

3. **确认目标分支**：必须是 feature 分支（`feature/` 前缀），非 master/main。

4. **建 backup 分支**：`git branch feature/xxx-backup`，若已存在则追加 `-backup-2`、`-backup-3`。

### 阶段二：分析

1. **列出提交**：`git log --oneline master..HEAD`

2. **逐 commit 精读**：对每个 commit **必须完整阅读** `git show <hash>`（不得只看 `--stat`），理解实际改了什么逻辑、与前后 commit 的关系、是否被后续推翻。超过 15 个 commit 可分批，但不得跳过任何一个。

3. **规划分组**：

   **squash**：
   - 同一文件、同一话题的多次迭代修复 → squash，只保留最终实现
   - 针对性测试随功能 squash；大面积跨模块测试补强独立为一组
   - 只改 `docs/dev/backlog.md` 的 chore 全合成一个
   - 标题含"登记""收窄""收尾"且无实质逻辑改动的 process chore → 吸收进相关功能组
   - 因代码变更更新的文档随代码 squash

   **独立保留**：
   - 跨 scope 重构（如 shell 分支上的 `refactor(core)`）
   - **预存 bug fix**：本分支引入前就存在的 bug 必须独立，便于 cherry-pick 和 revert

   **拆分**——原始 commit 混了多个不相关改动时**必须拆分**，不得因"原来就在一个 commit 里"而保留混合状态。触发信号：message 用"与""和""同时""顺便"连接多主题、改了分属不同模块的文件、diff 内存在逻辑上无关联的改动块。此规则优先于迭代 squash。

   **何时停手**——以下任一触发说明 group 太大，必须进一步拆：
   - **一句话原则**：message 首行需要连接词串联多个主题才能说清
   - 组内同时存在 feat 和预存 bug fix

   **重排时处理依赖**：如需调整 commit 顺序（如把独立重构移到最前），先诊断目标 commit 依赖的文件是否在目标位置已存在。若不存在：
   - 把创建该文件的 commit 一起前移
   - 或拆分目标 commit，将依赖新文件的部分留在原位
   不要不诊断直接尝试重排——cherry-pick/rebase 撞文件缺失冲突不可自动解决。

   **分组验真**：方案完成后启动 subagent 做对抗性验真——独立阅读全部 commit diff，挑战归组合理性、遗漏拆分、遗漏 commit、边界规则触发。主 agent 逐条审查反馈后更新方案，争议项在 walkthrough 中标注。

### 阶段三：逐组 Walkthrough

逐组展示，**每次只展示一组，等待用户回复**。

```
【X / N】<组标题>

涉及提交:
  squash: 1452487 feat(dev): 新增 dicepp-shell serve...
  squash: 739a705 fix(dev): lease 改 os.link...          ← 废弃方案，最终改 filelock
  keep:   4ce2829 fix(dev): lease 改用 filelock...        ← 最终方案
  squash: 8288e3b test(dev): lease 并发测试...

→ 新 commit type/scope: feat(dev)
→ 新 commit message:
  feat(dev): 新增 dicepp-shell serve 常驻 HTTP runtime 与 session lease 文件锁

  - serve 子命令启动常驻 HTTP server，send 子命令收发指令
  - session lease 基于 filelock OS 级文件锁，消除并发双 lease 竞态

→ 涉及文件: shell/main.py, shell/server.py, shell/session.py, ...
→ 拆分需求: 无
---
有问题可以提问、调整分组、重写 message，或回复"继续"。
```

**标注规范**：
- `squash:` — squash 进本组；`keep:` — 独立保留（说明原因）
- `← 废弃方案` — 被后续推翻的尝试；`← 需拆分：A 归本组，B 归第 Y 组`

**交互**："继续"→ 下一组；"这个拆开"/"xxx 独立保留"→ 调整后重新展示；修改 message → 重写后重新展示；"停"→ 深入解释。

### 阶段四：执行

1. **最终确认**：展示完整分组摘要（各组标题 + message 首行），等待确认。

2. **动态获取 hash**：引用 commit 时必须从当前 HEAD 动态读取：

   ```bash
   git log --oneline --format='%H' master..HEAD
   ```

   不得使用阶段二记录的 hash——cherry-pick/rebase 会改变 hash 值，过期 hash 导致错误。

3. **reset 到 merge-base**：`git reset --soft $(git merge-base HEAD master)`

4. **逐组提交**：每组 `git reset HEAD` 清空暂存区，`git add` 只暂存本组文件。

   拆分一个 commit 到多个组时，用非交互方式提取目标文件的指定 diff：
   ```bash
   git diff <source-commit> -- <target-files> | git apply --cached
   ```
   这替代了 `git add -p`（交互式，不可自动化）。确认提取范围正确后 `git commit`，剩余改动留在工作区归入后续组。

   若使用 fixup + autosquash 合并时遇到冲突：逐个冲突块判断哪一侧是最终想保留的状态，不预设 `--theirs` 或 `--ours`。无法判断时 `git rebase --abort` 后调整策略，不要强行解决。

5. **验证无内容丢失**：`git diff feature/xxx-backup` 必须无输出。若有差异回退到 backup 排查。

   通过后报告：

   ```
   提交重组完成。

   原分支: feature/xxx (N 个提交)
   整理后: M 个提交
   git diff feature/xxx-backup 无差异 ✓

   确认无误后可删除 backup：git branch -D feature/xxx-backup
   ```

## 约束

- **不得修改代码内容**，只重组 commit 边界和 message
- 阶段三逐组等待确认；阶段四后必须 diff 验证
- commit message 遵守 git-commit-brief 规范
