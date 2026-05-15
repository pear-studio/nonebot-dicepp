---
name: review2-reply
description: "[Defender] Read a review document and write/update the Reply section for each Rn in-place. Part of a 5-stage adversarial ping-pong review: raise(R) → reply(D) → confirm(R) → execute(D) → accept(R). Requires the user to explicitly provide the document path."
---

# review2-reply — 作者回复

读取由 `review1-raise` 产出的文档，对每条 `Rn` 原地写入 **Reply** 子块。

## 角色

**Defender Agent** — 本阶段由 Defender 执行，是五阶段对抗性 ping-pong 流程的第二棒：

```
raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)
```

**对抗假设**：Reviewer 的部分 Review 可能是误判、过度设计，或因不了解具体约束而产生。对弱势 Review **有理有据地反驳**，不盲目采纳——沉默式接受（"好的我会改"）视为无效回复。承认真实问题，给出具体到文件/函数/改法的执行方案。预设 Reviewer 在 `review3-confirm` 会审视回复是否有实质依据，因此反驳需有可验证的技术理由。

## 参数要求

**必须**由用户显式提供文档路径。若未提供，提示用户：
```
请提供 review 文档路径，例如：review2-reply .temp/review-250416-1430-persona.md
```

## 步骤

0. **门禁 — 读取文档并核验阶段状态**：
   - 先读取文档完整内容，逐项检查「阶段状态」checklist
   - 文档存在即表示阶段 1 已完成
   - 若阶段 3 已勾选 → 说明本阶段已有回复被确认过，当前可能是补充回复；正常继续
   - **禁止凭跨会话记忆判断前置条件**——以文件内容为唯一依据
   - `batch-update` 执行时会自动校验前置阶段，门禁不通过直接 exit 1
1. 调用脚本读取文档到上下文：
   ```bash
   python .claude/skills/review1-raise/review_record.py read <filename>
   ```
2. 扫描所有 `Rn`，识别哪些已有 `Reply`、哪些还没有（包括上一轮标为 `需补充回复` 的条目）
3. 对**未回复或需补充回复**的条目逐条评估，在上下文中构造完整 payload
4. **一次性写入**：通过 heredoc 直接传入 payload，**仅调用一次** `batch-update --format plain`（禁止逐条多次调用 `update`）：
   ```bash
   python .claude/skills/review1-raise/review_record.py batch-update <filename> --format plain <<'EOF'
   Rn: R1
   Section: Reply
   Content:
   - 评估: 采纳
   - 理由: ...
   - 拟执行改动: ...
   <<<END>>>
   Rn: R2
   Section: Reply
   Content:
   - 评估: 不采纳
   - 理由: ...
   <<<END>>>
   EOF
   ```

## Reply 子块格式

```markdown
**Reply**
- 评估: 采纳 / 部分采纳 / 不采纳 / 待澄清
- 理由: ...
- 拟执行改动: 具体到文件/函数/改法（若有）
```

## 约束

- 只修改 review 文档，不改业务代码
- `待澄清` 表示"真正看不懂这条 Review 的依据或背景"，使用前自问：是真的不理解，还是不同意？**不同意应用 `不采纳` 并给出反驳理由**，`待澄清` 不是规避对抗的出口
- 标为 `待澄清` 的条目会触发 Reviewer 在 confirm 阶段提供澄清，之后本 skill 须再次运行，对这些条目补充正式回复

5. `batch-update` 写入完成后自动标记阶段 2 完成，无需手动更新 checklist

## 输出

完成后提示下一步：`review3-confirm <文件名>`

> **注意**：若存在 `待澄清` 条目，本次回复可能不完整——阶段 2 仍可打勾（已做了能力范围内的回复），待 review3-confirm 提供澄清后，会再次触发 review2-reply 补充回复。
