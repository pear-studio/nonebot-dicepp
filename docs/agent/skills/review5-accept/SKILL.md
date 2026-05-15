---
name: review5-accept
description: "[Reviewer] Re-examine actual code changes against the finalized review document and accept or return each 已共识·实施 item. On retry, carry forward 退回记录 history rather than overwriting. Returned items loop directly back to review4-execute. Part of a 5-stage adversarial ping-pong review: raise(R) → reply(D) → confirm(R) → execute(D) → accept(R). Requires the user to explicitly provide the document path."
---

# review5-accept — 验收

对照 review 文档与实际代码改动，逐条验收 Defender 的实施结果。本阶段是五阶段对抗性 ping-pong 流程的最后一棒，也是流程的闭环门禁。

## 角色

**Reviewer Agent** — 本阶段由 Reviewer 执行：

```
raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)
```

**对抗假设**：Defender 的实施可能有遗漏、偏差或悄悄超出约定范围。逐条精确对照，以文档中的 Confirm/Reply 约定为唯一基准——**不因"大体上对"就通过**，不引入新的评审意见（新问题留给下一轮 raise）。退回时必须指出具体差异，禁止模糊描述（"感觉不对"不是有效退回理由）。

## 参数要求

**必须**由用户显式提供文档路径。若未提供，提示用户：
```
请提供 review 文档路径，例如：review5-accept .temp/review-250416-1430-persona.md
```

## 步骤

0. **门禁 — 重新读取文档并核验阶段状态**：
   - **必须先调用 Read 工具重新读取文档**，禁止依赖上下文中已有的旧版本——文档可能已被其他 Agent/会话更新
   - 逐项检查「阶段状态」checklist
   - 阶段 4 未勾选 → **禁止继续**，提示用户先完成 `review4-execute`
   - 阶段 5 已勾选（验收退回后重新验收）→ 正常继续
   - **禁止凭跨会话记忆判断前置条件**——以本次 Read 返回的文件内容为唯一依据
   - `batch-update` 执行时会自动校验前置阶段（stage 4），门禁不通过直接 exit 1
1. 调用脚本读取文档到上下文：
   ```bash
   python .claude/skills/review1-raise/review_record.py read <filename>
   ```
2. 拉取实际改动：
   ```bash
   git diff HEAD
   ```
3. 筛选需验收的 Rn：`共识状态` 为 `已共识·实施` 的条目（`已共识·延后` / `已共识·否决` / `已共识·存档` 均无需验收）
4. **Backlog 写入校验**（在逐条比对之前）：
   - 检查所有 `共识状态: 已共识·延后` 的 Rn 是否都有 `已归档: B-...` 字段
   - 对每个 ID 运行 `python scripts/tools/backlog.py show <id>` 确认在 `docs/dev/backlog.md` 中存在
   - 任一缺失视为验收失败，列出差异常项并退回
5. 逐条比对：实际 diff 是否覆盖了约定的具体改点？是否有超出约定的额外改动？
5. 构造 payload 时，对**已有 Accept 块**的 Rn（即重新验收），须先读取原有 `退回记录` 字段，将本次退回理由**追加**进去，而不是清空重写；验收通过的条目也同样携带历史退回记录（作为过程留档）
6. 在上下文中构造完整 payload，**一次性写入**。`batch-update` 自动处理阶段状态：
   - 全部通过 → 自动标记阶段 5 完成
   - 存在 `验收退回` → 自动回退阶段 4 和阶段 5
   - Agent 无需手动更新 checklist
   ```bash
   python .claude/skills/review1-raise/review_record.py batch-update <filename> --format plain <<'EOF'
   Rn: R1
   Section: Accept
   Content:
   - 验收结论: 验收通过
   - 说明: ...
   <<<END>>>
   Rn: R2
   Section: Accept
   Content:
   - 验收结论: 验收退回
   - 说明: ...
   - 退回记录: ...
   <<<END>>>
   EOF
   ```

## Accept 子块格式

```markdown
**Accept**
- 验收结论: 验收通过 / 验收退回
- 说明: 对照约定改点逐一确认（通过时简要确认，退回时指出具体差异）
- 退回记录: [#1] 原因; [#2] 原因（仅有退回历史时填写，追加不覆盖）
```

## 退回处理

若存在 `验收退回` 条目：
1. 向用户报告退回的 Rn 列表及每条的具体差异
2. 等待用户确认后，直接重走 `review4-execute`（**无需重走 reply/confirm**，共识已达成）
3. review4 修复实施后，再次运行本阶段重新验收，携带原有退回记录追加本次结果
4. **验收退回时**：`batch-update` 自动回退阶段 4 和阶段 5（无需手动操作）

## 约束

- 只追加 Accept 块，不修改 Review / Reply / Confirm 内容
- 重新验收时**必须携带原有退回记录**，不得清空历史
- 对照基准为文档约定，不引入新的评审意见（新问题留给下一轮 `review1-raise`）
- 退回必须给出具体差异，禁止模糊描述

## 输出

验收全部通过时，向用户报告 **评审闭环完成** 即可，**无需再修改 review 文档**，直接打印最终统计：

```
评审闭环完成
- 总条目数: N（实施 M 条，延后 K 条，否决 L 条）
- 本轮验收通过: M
- 累计退回次数: N（跨所有 Rn 汇总）
- 本次归档至 backlog: K 条
- 文档: <filename>
```
