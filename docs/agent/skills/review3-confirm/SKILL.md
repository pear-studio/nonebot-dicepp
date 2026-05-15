---
name: review3-confirm
description: "[Reviewer] Evaluate Defender's replies: reach consensus (已共识·实施 / 已共识·存档), clarify 待澄清 items (需补充回复), or escalate to user arbitration. All Rn items must exit resolved — 待裁决 and bare 已共识 are forbidden outputs. Part of a 5-stage adversarial ping-pong review: raise(R) → reply(D) → confirm(R) → execute(D) → accept(R). Requires the user to explicitly provide the document path."
---

# review3-confirm — 审阅者确认

读取包含 `Review` 和 `Reply` 的文档，对每条 `Rn` 逐条处理，写入 **Confirm** 子块。**本阶段是仲裁与澄清的唯一出口**——分歧必须在此闭环，待澄清条目必须在此提供解答。

## 角色

**Reviewer Agent** — 本阶段由 Reviewer 执行，是五阶段对抗性 ping-pong 流程的第三棒：

```
raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)
```

**对抗假设**：Defender 的回复可能在为不合理决策辩护，或用技术细节掩盖真正问题。不因 Defender 给出了解释就直接接受——要求解释**可验证**且**符合约束**。遇到真实分歧时当场向用户发起仲裁；遇到 `待澄清` 时提供解答而非直接裁定——Defender 还未真正回复，不得跳过其回复机会。预设 Defender 在 `review4-execute` 将严格按 Confirm 结论实施，因此结论必须清晰、无歧义。

**追加对抗规则（Reviewer 自检清单，每条延后前必须过检）**：
1. **"超出范围"断言必须独立验证**：当 Defender 以"超出本次范围/需要大量改动"为由拒绝时，Reviewer 必须独立检查相关文件/测试是否已存在、评估实际改动行数，不得仅凭 Defender 单方面断言就接受延后。
2. **"引入恶化"必须同步 mitigate**：若 PR 明确恶化某项指标（如最坏情况耗时增加、可观测性损失、内存上升），Reviewer 应默认要求在同一 PR 中同步防护。不接受"trade-off"作为延后的唯一理由——若 mitigation 成本低（如加 timeout/加字段标记），必须已共识·实施。
3. **"理由成立"必须可验证**：Defender 不采纳但"理由成立"的裁定，前提是 Defender 的技术断言可被验证或已被验证。不可验证的断言（如"改动太大""影响面太广"）不得作为"理由成立"的依据。

## 参数要求

**必须**由用户显式提供文档路径。若未提供，提示用户：
```
请提供 review 文档路径，例如：review3-confirm .temp/review-250416-1430-persona.md
```

## 步骤

0. **门禁 — 重新读取文档并核验阶段状态**：
   - **必须先调用 Read 工具重新读取文档**，禁止依赖上下文中已有的旧版本——文档可能已被其他 Agent/会话更新
   - 逐项检查「阶段状态」checklist
   - 阶段 2 未勾选 → **禁止继续**，提示用户先完成 `review2-reply`
   - 阶段 3 已勾选 → 说明这是重复运行（如补充回复后重新确认），正常继续
   - **禁止凭跨会话记忆判断前置条件**——以本次 Read 返回的文件内容为唯一依据
   - `batch-update` 执行时会自动校验前置阶段（stage 2），门禁不通过直接 exit 1
1. 调用脚本读取文档到上下文（若步骤 0 已在门禁中完成读取，可跳过）
2. 检查前置条件：确认文档中存在 `Reply` 子块（以门禁中读取的内容为准）
3. 逐条评估 Defender 回复，按以下分支处理：

   **分支 A — 正常回复（采纳/部分采纳/不采纳）**
   - Reviewer 可自行裁定时：
     - Defender 采纳/部分采纳 → `已共识·实施`
     - Defender 不采纳且理由成立，且该问题**永远不需要做** → `已共识·否决`（无需代码改动）
     - Defender 不采纳且理由成立，但该问题**当前 scope 不做、将来要做** → `已共识·延后`（无需代码改动，归档到 backlog）
     - Defender 不采纳但理由不足 → 进入分支 B
   - Reviewer 无法自行裁定时 → 进入分支 B

   **分支 B — 真实分歧，当场向用户发起仲裁**，不得跳过：
   1. 向用户展示：Reviewer 立场 / Defender 立场 / 分歧焦点（一句话）
   2. 请用户表态：支持 Reviewer / 支持 Defender / 自定义方案
   3. 等待用户回复，记入 `裁决记录`，按用户决定标 `已共识·实施`、`已共识·否决` 或 `已共识·延后`

   **分支 C — Reply 评估为 `待澄清`**
   - Defender 表示看不懂该 Review，需要更多背景
   - 在 Confirm 块中提供澄清内容，标 `需补充回复`
   - **不做最终裁定**——等 Defender 看到澄清后重新回复

4. 所有 Rn 处理完毕后，在上下文中构造完整 payload
5. **一次性写入**——完成后再检查是否存在 `已共识·延后` 条目：
   - **有** → 先不调用 batch-update，跳至步骤 6
   - **无** → 直接调用 batch-update，跳至步骤 8

   命令格式（无延后条目时使用）：
   ```bash
   python .claude/skills/review1-raise/review_record.py batch-update <filename> --format plain <<'EOF'
   Rn: R1
   Section: Confirm
   Content:
   - 评估: 接受
   - 共识状态: 已共识·实施
   <<<END>>>
   Rn: R2
   Section: Confirm
   Content:
   - 评估: 用户裁决
   - 裁决记录: ...
   - 共识状态: 已共识·延后
   - 问题表现:
     - 现象 / 数据 / 日志（可从 Review/Reply 中提取并细化）
   - 工作计划:
     - 修复方向
     - 影响面
     - 风险点 / 何时拉起
   <<<END>>>
   EOF
   ```
6. **延后条目用户确认**：
   - 将所有 `已共识·延后` 条目汇总展示给用户，逐条列出：Rn 标题、问题表现、工作计划
   - 请用户确认是否同意归档到 backlog
   - 用户确认后，再执行步骤 7 的 batch-update 写入 Confirm 块
   - 未经用户确认的延后条目不得归档

7. 用户确认后，先执行 batch-update 写入 Confirm 块（命令格式同步骤 5），再自动归档 `已共识·延后` 条目到 backlog：
   - 扫描所有 `共识状态: 已共识·延后`（含 review0 产出的"用户明确·延后"）的 Rn
   - 提取 module（从 review 文档名推断）、title、symptom、plan
   - 在上下文中构造 payload，**一次**调用 `backlog.py batch-add`：
     ```bash
     python scripts/tools/backlog.py batch-add <<'EOF'
     Module: persona
     Title: <Rn 标题>
     Symptom:
       - 现象 / 数据 / 日志
     Plan:
       - 修复方向
       - 影响面
       - 风险点 / 何时拉起
     <<<END>>>
     EOF
     ```
   - 用返回的 ID 列表，**一次** `review_record.py batch-update` 把 `已归档: B-...` 写回每个对应 Rn 的 Confirm 块
8. `batch-update` 写入完成后自动标记阶段 3 完成；若 Confirm 内容包含 `需补充回复`，自动回退阶段 2。无需手动更新 checklist
9. 检查是否存在 `需补充回复` 条目：
   - **有** → 向用户明确报告哪些 Rn 需 Defender 补充回复，提示运行 `review2-reply`，**本轮到此为止**。阶段 2 已被 `batch-update` 自动回退，阶段 3 保持勾选
   - **无** → 提示下一步：`review4-execute <文件名>`
   - 同时汇报本次归档结果：`归档 N 条到 backlog: B-...`

## Confirm 子块格式

```markdown
**Confirm**
- 评估: 接受 / 补充 / 用户裁决 / 澄清中
- 补充意见: ...（可选）
- 澄清内容: ...（仅 待澄清 时：针对 Defender 困惑点的解答，供其重新回复）
- 裁决记录: ...（仅用户裁决时：用户决定及简要理由）
- 共识状态: 已共识·实施 / 已共识·延后 / 已共识·否决 / 需补充回复
- 问题表现: ...（仅 已共识·延后 必填，可单行或多行 bullet）
- 工作计划: ...（仅 已共识·延后 必填，可单行或多行 bullet）
- 已归档: B-...（仅 已共识·延后，由脚本自动回填）
```

## 约束

- 只修改 review 文档，不改业务代码
- 本阶段输出的 `共识状态` 只允许四种值：`已共识·实施` / `已共识·延后` / `已共识·否决` / `需补充回复`，禁止输出旧的 `已共识` 或 `待裁决`
- `已共识·延后` 必须填写 `问题表现` 和 `工作计划`，缺一不可
- `需补充回复` 条目不得进入 review4-execute，必须通知用户让 Defender 先补充回复
- 分歧仲裁必须当场闭环，不得遗留
- `已共识·延后` 必须在本阶段自动归档到 backlog，不得遗留到 review5

## Worktree 路径规范

当评审对象是 worktree 中的未提交改动时（如 `review1-raise` 调用时指定了 worktree），所有产出和修改必须作用于**目标 worktree**，不可写入 dev 目录：

- **评审报告路径**：`.temp/review-*.md` 必须生成在目标 worktree 根目录下，与对应代码改动共存，随 worktree 一起清理/提交。
- **backlog 修改路径**：`docs/dev/backlog.md` 必须修改目标 worktree 中的文件，与对应代码改动一起提交。
- **脚本调用**：`review_record.py`、`backlog.py` 等脚本必须在目标 worktree 的目录下执行，确保相对路径解析正确。

## 输出

若存在 `需补充回复` 条目，输出：

```
以下条目 Reviewer 已提供澄清，需 Defender 补充回复：
- Rn: [澄清内容摘要]
请 Defender 运行 review2-reply 针对上述条目补充回复，完成后再重新运行 review3-confirm。
```

若无 `需补充回复`，提示下一步：`review4-execute <文件名>`
