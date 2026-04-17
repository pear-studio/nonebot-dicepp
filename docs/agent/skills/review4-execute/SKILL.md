---
name: review4-execute
description: "[Defender] Read a finalized review document, implement 已共识·实施 items after explicit user confirmation, and skip 已共识·存档 items. Refuse to proceed if any 需补充回复 or 待裁决 items exist. Part of a 5-stage adversarial ping-pong review: raise(R) → reply(D) → confirm(R) → execute(D) → accept(R). Requires the user to explicitly provide the document path."
---

# review4-execute — 达成共识后实施

消费 review 文档，只实施 `已共识·实施` 的条目，跳过 `已共识·存档`，拒绝任何未闭环条目。本阶段是纯粹的实施阶段，不做任何仲裁——分歧和澄清均已在 review3-confirm 闭环。

## 角色

**Defender Agent** — 本阶段由 Defender 执行，是五阶段对抗性 ping-pong 流程的第四棒：

```
raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)
```

**对抗假设**：Reviewer 将在 `review5-accept` 逐条精确对照实施结果。改动要清晰、可追溯，严格在共识范围内——**不扩大**（不借机重构无关代码）、**不缩小**（不遗漏约定的改点）。预设 Reviewer 会发现偏差并退回，因此实施前先在上下文中逐条列出每个 Rn 的具体改点，再动手。

## 参数要求

**必须**由用户显式提供文档路径。若未提供，提示用户：
```
请提供 review 文档路径，例如：review4-execute .temp/review-250416-1430-persona.md
```

## 步骤

1. 调用脚本读取文档到上下文（**本 skill 禁止修改 review 文档，只读**）：
   ```bash
   python .claude/skills/review1-raise/review_record.py read <filename>
   ```
2. 解析所有 `Rn` 的 `共识状态`，生成实施清单：
   - `已共识·实施`：列出 Confirm/Reply 中约定的具体改点，纳入实施
   - `已共识·存档`：Defender 不采纳且已被接受，**无需代码改动，跳过**
   - `需补充回复`：**拒绝实施**，提示用户先完成 review2-reply → review3-confirm
   - `待裁决`（异常）：**拒绝实施**，提示用户先完成 review3-confirm
3. 若存在拒绝条目，停止并告知用户；若全部可处理，与用户确认实施范围（逐条或批量均可）
4. **门禁**：仅当用户明确授权后才动手
5. 修改代码
6. 跑项目配套的测试命令验证（如构建验证、单元测试等，依项目实际情况而定）
7. 向用户汇报完成情况和测试结果

## 约束

- 未获明确授权前禁止改代码
- 实施范围不得超过用户确认的子集
- **实施完成后禁止提交代码**，必须等待 review5-accept 验收通过后再 commit——提前提交会导致改动脱离 `git diff HEAD` 的追踪范围，review5-accept 将无法正确验收
- 修改后必须跑测试
