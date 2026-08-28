---
name: branch-polish
description: 代码审视清理——扫描变更中的遗留调试代码、废弃注释、无 backlog 引用的 TODO 等，一次确认后批量修改，跑通全量测试。
---

# branch-polish — 代码审视与清理

扫描当前分支或工作区变更中的可清理项，一次性列出全部建议，用户确认后批量修改，最后跑全量测试。

分支类型不限（feature/hotfix/release/master 均可），也不限定是否在 worktree 中。

## 输入

```
/branch-polish                  # 自动判断模式
/branch-polish <ref>            # 指定 commit 范围（如 HEAD~5、main）
```

## 步骤

### 阶段一：判断变更来源

1. `git status --short` → **有输出**：Dirty 模式，扫 `git diff HEAD`。
2. 无输出 → 当前分支 ≠ master：
   - 扫 `git log master..HEAD`。
3. 无输出 → 当前分支 = master：
   - 用户必须指定范围（`/branch-polish <ref>`），否则停止并提示"当前在 master 分支且工作区干净，请指定变更范围，如 /branch-polish HEAD~5"。

不创建新 worktree，不创建新分支。在当前工作目录原地操作。

### 阶段二：扫描

Dirty 模式扫 `git diff HEAD`，Commit 模式逐 commit `git show <hash>`。按以下维度收集清理项：

- 遗留调试代码：`print`、临时 `logger.debug`、`breakpoint()` / `pdb.set_trace()`
- 被注释掉的旧代码块
- 多余/重复的 import
- 过时或与代码现状不一致的注释
- 冗余变量或中间赋值
- 硬编码临时值（magic number、临时端口号等）
- **TODO/待办类注释**：逐行判断是否为"写了 TODO 但没 backlog 引用"的待办项。TODO 注释必须引用 `docs/dev/backlog.md` 中当前有效的 backlog ID（格式 `B-YYMMDD-xxxxxx`），引用写在 TODO 旁边：
  - 规范写法：`# TODO(<有效 backlog ID>): 描述待办事项`
  - 无 backlog 引用 → 清理项，建议"添加 backlog 引用或移除此 TODO"
  - 有 backlog 引用 → 可保留，标注"有 backlog 引用"
- 类型标注缺失或不一致
- 命名不当（`tmp`、`foo`、拼写错误等）
- **中间版本 schema/迁移**：迭代多次的 schema 或迁移脚本，只保留起点到终点的单次迁移（Commit 模式才能识别；Dirty 模式下 agent 自行判断）

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

【清理 3】src/.../some_file.py:15
  类型: TODO 缺少 backlog 引用
  内容: # TODO: 以后改成异步
  → 建议: 添加 backlog 引用（如 B-YYMMDD-xxxxxx）或移除此 TODO

---

共发现 N 条清理项。回复"全部处理"/"处理 1,3,5"/"跳过"。
```

**交互**：
- "全部处理" → 进入阶段四，执行所有建议
- "处理 x,y,z" → 只执行选中的
- "跳过 x" → 跳过某项，其余全部处理
- "跳过全部" → 退出

### 阶段四：执行

一次性应用所有确认的清理改动，直接编辑代码。

**Dirty 模式**：改动直接覆盖到工作区已有变更中，不操作 git。

**Commit 模式**：
1. 展示 diff 摘要
2. 按 git-commit-brief 规范拟一条 commit message：
   ```
   建议 commit:
     chore(<scope>): 清理 <简述清理内容>
   ```
3. 用户确认后 `git add` + `git commit`。用户可修改 message 后再确认。

### 阶段五：全量测试

调用 `auto-test-run` 运行完整测试套件。测试失败则排查是否由清理改动引入，修复后重跑。

**Dirty 模式收尾：**
```
代码清理完成。

模式: 工作区清理
处理: K 处 / 跳过: J 处
全量测试: ✓ 通过
清理改动已应用到工作区，请 review 后提交。
```

**Commit 模式收尾：**
```
代码清理完成。

模式: commit 扫描
分支: <当前分支>
处理: K 处 / 跳过: J 处
全量测试: ✓ 通过

如需整理清理产生的 commit，运行 /branch-tidy。
```

## 约束

- 阶段三一次性展示全部建议，用户确认后方可修改代码
- 不创建新 worktree 或分支
- commit message 遵守 git-commit-brief 规范
