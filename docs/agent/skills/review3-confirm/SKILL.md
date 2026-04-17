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

## 参数要求

**必须**由用户显式提供文档路径。若未提供，提示用户：
```
请提供 review 文档路径，例如：review3-confirm .temp/review-250416-1430-persona.md
```

## 步骤

1. 调用脚本读取文档到上下文
2. 检查前置条件：确认文档中存在 `Reply` 子块
3. 逐条评估 Defender 回复，按以下分支处理：

   **分支 A — 正常回复（采纳/部分采纳/不采纳）**
   - Reviewer 可自行裁定时：
     - Defender 采纳/部分采纳 → `已共识·实施`
     - Defender 不采纳且理由成立 → `已共识·存档`（无需代码改动）
     - Defender 不采纳但理由不足 → 进入分支 B
   - Reviewer 无法自行裁定时 → 进入分支 B

   **分支 B — 真实分歧，当场向用户发起仲裁**，不得跳过：
   1. 向用户展示：Reviewer 立场 / Defender 立场 / 分歧焦点（一句话）
   2. 请用户表态：支持 Reviewer / 支持 Defender / 自定义方案
   3. 等待用户回复，记入 `裁决记录`，按用户决定标 `已共识·实施` 或 `已共识·存档`

   **分支 C — Reply 评估为 `待澄清`**
   - Defender 表示看不懂该 Review，需要更多背景
   - 在 Confirm 块中提供澄清内容，标 `需补充回复`
   - **不做最终裁定**——等 Defender 看到澄清后重新回复

4. 所有 Rn 处理完毕后，在上下文中构造完整 JSON payload
5. **一次性写入**：将 JSON payload 写入临时文件，然后**仅调用一次** `batch-update --file`：
   ```bash
   python .claude/skills/review1-raise/review_record.py batch-update <filename> --file <json_file>
   ```
6. 写入完成后，检查是否存在 `需补充回复` 条目：
   - **有** → 向用户明确报告哪些 Rn 需 Defender 补充回复，提示运行 `review2-reply`，**本轮到此为止**
   - **无** → 提示下一步：`review4-execute <文件名>`

## Confirm 子块格式

```markdown
**Confirm**
- 评估: 接受 / 补充 / 用户裁决 / 澄清中
- 补充意见: ...（可选）
- 澄清内容: ...（仅 待澄清 时：针对 Defender 困惑点的解答，供其重新回复）
- 裁决记录: ...（仅用户裁决时：用户决定及简要理由）
- 共识状态: 已共识·实施 / 已共识·存档 / 需补充回复
```

## 约束

- 只修改 review 文档，不改业务代码
- 本阶段输出的 `共识状态` 只允许三种值：`已共识·实施` / `已共识·存档` / `需补充回复`，禁止输出旧的 `已共识` 或 `待裁决`
- `需补充回复` 条目不得进入 review4-execute，必须通知用户让 Defender 先补充回复
- 分歧仲裁必须当场闭环，不得遗留

## 输出

若存在 `需补充回复` 条目，输出：

```
以下条目 Reviewer 已提供澄清，需 Defender 补充回复：
- Rn: [澄清内容摘要]
请 Defender 运行 review2-reply 针对上述条目补充回复，完成后再重新运行 review3-confirm。
```

若无 `需补充回复`，提示下一步：`review4-execute <文件名>`
