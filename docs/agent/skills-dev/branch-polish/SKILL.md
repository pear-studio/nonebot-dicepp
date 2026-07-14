---
name: branch-polish
description: 代码审视清理——扫描分支改动中的遗留调试代码、废弃注释、中间版本 schema 等，一次确认后批量修改，跑通全量测试。
---

# branch-polish — 代码审视与清理

扫描当前分支改动中的可清理项，一次性列出全部建议，用户确认后批量修改，最后跑全量测试。

不管理 commit——清理后的 commit 边界用 /branch-tidy 整理。

## 输入

```
/branch-polish              # 当前分支
/branch-polish <branch>     # 指定目标分支
```

## 步骤

### 阶段一：准备

1. **确认工作区干净**：`git status --short` 必须无输出。
2. **确认 worktree 隔离**：已在 worktree → 继续；不在 → `EnterWorktree` 创建。
3. **确认目标分支**：必须是 feature 分支。

### 阶段二：扫描

逐 commit 审视（`git show <hash>`），按以下维度收集清理项：

- 遗留调试代码：`print`、临时 `logger.debug`、`breakpoint()` / `pdb.set_trace()`
- 被注释掉的旧代码块
- 多余/重复的 import
- 过时或与代码现状不一致的注释
- 冗余变量或中间赋值
- 硬编码临时值（magic number、临时端口号等）
- TODO/FIXME 注释（标注是否应保留）
- 类型标注缺失或不一致
- 命名不当（`tmp`、`foo`、拼写错误等）
- **中间版本 schema/迁移**：本分支内迭代多次的 schema 或迁移脚本，只保留起点到终点的单次迁移

### 阶段三：确认

一次性列出全部清理项：

```
【清理 1】src/plugins/DicePP/shell/session.py:42
  类型: 遗留调试代码
  内容: print(f"DEBUG: lease acquired: {path}")
  → 建议: 删除

【清理 2】src/plugins/DicePP/shell/session.py:78-84
  类型: 注释掉的旧代码块
  内容: # old_lock = os.link(tempfile, lockfile)
  → 建议: 删除注释掉的代码

【清理 3】...

---
共发现 N 条清理项。回复"全部处理"/"处理 1,3,5"/"跳过"。
```

**交互**：
- "全部处理" → 进入阶段四，执行所有建议
- "处理 x,y,z" → 只执行选中的
- "跳过 x" → 跳过某项，其余全部处理
- "跳过全部" → 退出

### 阶段四：执行

一次性应用所有确认的清理改动，直接编辑代码。改动完成后逐文件 `git add`。

### 阶段五：全量测试

调用 `auto-test-run` 运行完整测试套件。测试失败则排查是否由清理改动引入，修复后重跑。

```
代码清理完成。

分支: feature/xxx
处理: K 处 / 跳过: J 处
全量测试: ✓ 通过

如需整理清理产生的 commit，运行 /branch-tidy。
```

## 约束

- 阶段三一次性展示全部建议，用户确认后方可修改代码
- 不管理 commit 边界——清理后的 commit 整理交给 /branch-tidy
- commit message 遵守 git-commit-brief 规范
