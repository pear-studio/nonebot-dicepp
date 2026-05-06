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
   若校验失败，先向用户报告错误列表，停止清理，建议手动修复后再跑。

2. **拉取全表**：
   ```bash
   python scripts/tools/backlog.py list
   ```

3. **逐条评估过时性**：
   对每条 backlog，Agent 检查以下信号（仅作初判，最终由用户确认）：
   - 触发条件是否已满足或已失效
   - 涉及的模块/文件是否已被删除或重构
   - 原始问题描述是否已不再成立（如相关代码已完全重写）
   - 创建时间是否超过 6 个月且无任何后续动作

   **评估结论只有两种**：`建议保留` / `建议删除`。
   不得因为"记不清"就建议删除；不确定的标 `建议保留`。

4. **与用户确认**：
   将所有 `建议删除` 的条目汇总展示给用户，格式：
   ```
   建议删除以下 N 条 backlog：
   - [B-...] <标题> (<模块>) — 理由：...
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
