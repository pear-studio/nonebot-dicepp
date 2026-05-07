---
name: backlog-implement
description: "开始实现某一个 backlog 条目。展示条目详情，引导实现流程，完成后协助从 backlog 中移除。"
---

# backlog-implement — 实施 backlog 条目

从 backlog 中选取一个条目，展示上下文，引导用户完成实现，最终协助清理 backlog。

## 角色

**Implementer Agent** — 负责把 backlog 中的延后项落地为代码改动。

## 参数要求

用户可直接提供 backlog ID，或仅调用技能让 Agent 展示列表供选择：
```
/backlog-implement B-260506-a3f9c1
/backlog-implement              # 无参数，展示列表供选择
```

## 步骤

1. **获取条目**：
   - 若用户提供了 ID：
     ```bash
     python scripts/tools/backlog.py show <id>
     ```
   - 若无参数：
     ```bash
     python scripts/tools/backlog.py list
     ```
     展示列表，请用户选择一条（回复 ID 或序号）。

2. **展示上下文**：
   完整展示该 backlog 条目的内容字段：
   ```
   即将实现 backlog 条目：
   - ID: B-...
   - 模块: ...
   - 标题: ...
   - 创建: ...
   - 问题表现:
     - ...
   - 工作计划:
     - ...
   ```
   询问用户是否基于此条目开始实现。

3. **核实问题表现**：
   实现前先按 `问题表现` 描述去代码里核实——文件 / 函数 / 配置项是否仍然存在？症状是否还可能复现？若发现条目已过时（例如对应代码早已被重构掉），向用户确认后改走 `backlog-check` 删除流程，不强行实施。

4. **实现引导**：
   - **简单改动**（单文件、局部逻辑）：直接编辑代码，跑测试验证。
   - **复杂改动**（涉及多模块、需设计决策）：建议用户先走 `opsx:new` 或 `review0-walkthrough` 创建独立变更流程，本技能等待其结果。
   - 实现思路以 `工作计划` 字段为起点，但不必照搬——计划字段只是落地建议，落地时若发现更优方案应优先采用并向用户说明。

5. **测试与验证**：
   修改完成后必须跑项目配套测试（`uv run pytest` 或相关模块测试），确认不破坏现有功能。

6. **清理 backlog**：
   实现并测试通过后，向用户确认是否从 backlog 移除该条目：
   ```bash
   python scripts/tools/backlog.py close <id> --dry-run
   ```
   展示预览，用户确认后：
   ```bash
   python scripts/tools/backlog.py close <id>
   ```

7. **汇报**：
   ```
   Backlog 条目实施完成
   - ID: B-...
   - 标题: ...
   - 改动范围: ...（文件/函数摘要）
   - 已移除 backlog: 是
   ```

## 约束

- 不自动 commit 或 push（受全局规则约束）
- 实现范围以 backlog 条目描述为边界，如需扩大 scope 必须经用户同意
- 复杂改动必须走独立设计流程，不得在本技能内直接开搞大重构
- 清理 backlog 前必须完成测试验证
