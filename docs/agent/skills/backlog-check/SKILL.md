---
name: backlog-check
description: "核实和清理所有 backlog 条目，删除过时项，按时间重排序，并向用户汇报结果。"
---

# backlog-check — Backlog 核实与清理

定期或按需运行，清理 `docs/dev/backlog.md` 中过时的条目，重排序，保持 backlog 健康。

## 角色

**Maintainer Agent** — 负责 backlog 的日常维护，不修改业务代码，只操作 `docs/dev/backlog.md`。

## 参数要求

无必须参数，直接调用：
```
/backlog-check
```

## 步骤

1. **一致性校验**：
   ```bash
   python scripts/tools/backlog.py validate
   ```
   若校验失败（如缺少 `问题表现` / `工作计划`），先向用户报告错误列表，停止清理，建议手动修复后再跑。

2. **拉取全表**：
   ```bash
   python scripts/tools/backlog.py list
   ```

3. **逐条评估过时性**：
   对每条 backlog，使用 `show` 查看完整内容，根据 `问题表现` 描述实际去代码里核实：
   - 涉及的文件 / 函数 / 配置项是否仍然存在？被删除/重命名了？
   - 描述的问题症状是否在当前代码里仍然可能复现？
   - 是否有近期 commit（`git log --since=...`）已经修改过相关代码，使问题不再成立？
   - 创建时间是否超过 6 个月且无任何后续动作？

   **评估结论只有两种**：`建议保留` / `建议删除`。
   不得因为"记不清"或"看起来旧"就建议删除；不确定的标 `建议保留` 并附理由。

4. **与用户确认**：
   将所有 `建议删除` 的条目汇总展示给用户：
   ```
   建议删除以下 N 条 backlog：
   - [B-...] <标题> (<模块>) — 理由：<在 commit XXX 中修复 / 文件已删除 / ...>
   是否确认删除？(是 / 否，逐条审)
   ```
   等待用户回复。用户要求逐条审时，逐条展示并确认。

5. **执行清理（dry-run 先）**：
   用户确认后：
   ```bash
   python scripts/tools/backlog.py prune <id1> <id2> ... --dry-run
   ```
   展示将要删除的内容，再次请用户确认。

6. **正式删除**：
   ```bash
   python scripts/tools/backlog.py prune <id1> <id2> ...
   ```

7. **重排序**：
   ```bash
   python scripts/tools/backlog.py sort
   ```
   （按 模块→优先级→类型→改动量 自动排序，写入时已自动执行，此处为显式确认）

8. **汇报**：
   ```
   Backlog 清理完成
   - 清理前总数: N
   - 删除: M 条
   - 保留: K 条
   - 文件: docs/dev/backlog.md
   ```

## 约束

- 删除前必须经过用户确认，禁止自动删除
- 不确定的条目一律保留
- 每次操作后跑 `validate` 确认文件格式仍然合法
- 不修改除 `docs/dev/backlog.md` 外的任何文件
