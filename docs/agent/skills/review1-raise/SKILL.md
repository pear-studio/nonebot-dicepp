---
name: review1-raise
description: "[Reviewer] Analyze local git diff (with optional user-supplied extra scope) and produce a structured review document with numbered findings (R1, R2...). Part of a 5-stage adversarial ping-pong review: raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)."
---

# review1-raise — 产出评审报告

分析代码改动，生成结构化 review 文档，为后续 `review2-reply` / `review3-confirm` / `review4-execute` / `review5-accept` 提供输入。

## 角色

**Reviewer Agent** — 本阶段由 Reviewer 执行，是五阶段对抗性 ping-pong 流程的第一棒：

```
raise(R) → reply(D) → confirm(R) → execute(D) → accept(R)
```

**对抗假设**：代码中存在作者未察觉的缺陷、设计短视或一致性问题。积极挖掘，不因代码"能跑"就放水。严重程度要客观标定——风格偏好可以提，但不得标为"严重"。预设 Defender 在 `review2-reply` 中会反驳弱势 Review，因此每条 Review 必须有充分依据，禁止模糊措辞。

## 分析范围

- **前提**：本工作流基于**未提交的改动**（staged + unstaged）
- **默认**：`git diff HEAD`（已暂存 + 未暂存）
- **补充**：用户可在调用时追加范围，如特定路径、提交区间、额外文件等
- 最终执行：`git diff HEAD [补充范围]` 及 `git diff --stat HEAD [补充范围]`

## 步骤

1. 收集 diff（默认 + 用户补充）
2. 若无改动，直接退出
3. **同时**启动两个并行子 Agent 评审——不等其中一个完成再启动另一个；让 Agent 自行读取代码与 diff，**不要把 diff 文本直接传给 Agent**；任一 Agent 启动失败时，不得单独继续，向用户确认后再决定下一步：

   **Agent A — 实现质量**，聚焦：
   - 正确性：逻辑是否有 bug，边界条件是否处理
   - 健壮性：错误处理、空值、异常分支覆盖
   - 测试：改动是否有对应测试，现有测试是否需要更新
   - 代码风格：是否符合项目已有规范（命名、注释、结构）
   - **对抗视角**：假设代码有隐藏 bug，主动构造反例和边界情况验证，不因代码"能跑"就放过

   **Agent B — 设计质量**，聚焦：
   - 过度设计识别：不必要的抽象层、提前泛化、"以防万一"的冗余代码
   - 短视识别：硬编码常量、缺少迁移路径的 breaking change、未来明显会痛的耦合
   - 一致性：是否与项目已有模式保持一致，还是引入了孤立的新范式
   - 长远可维护性：改动是在清偿技术债，还是在累积新的技术债
   - **对抗视角**：假设实现者知道自己在做什么——挑战的是架构层面的合理性，不是代码细节

   每个 Agent 的输出须包含：
   - 每条问题按严重程度标注（严重 / 警告 / 建议）
   - **每条严重或警告问题必须附修改建议**：具体到文件 / 函数 / 改法，有多种改法时简述取舍
   - 无问题时明确写「此方面未发现问题」，禁止留空

4. 收集两个 Agent 的完整输出，将其作为**参考线索而非最终结论**——最终报告由本 Agent 独立负责，不得直接照搬任一 Agent 的原文：
   - **逐条回到代码重新核查**，形成独立判断后再写入报告
   - 两个 Agent 结论互相矛盾时，重新审查代码后**统一意见**，不得将分歧直接暴露在报告中
   - 仅一个 Agent 提出的条目，独立核查后决定是否成立，不因"只有一个提到"而自动降级或升级
   - 类似问题合并后**重新描述**，不得压缩成仅含标题的清单，修改建议必须具体可执行

5. 整理问题列表，按 `R1, R2...` 编号
6. **一次性写入**：将完整文档内容写入 `.temp/review_draft_tmp.txt`，然后调用脚本创建文档（**仅允许这一次写入操作**，脚本执行后会自动清理临时文件）：
   ```bash
   python .claude/skills/review1-raise/review_record.py create <主题slug> --file .temp/review_draft_tmp.txt
   ```
   脚本自动生成时间戳，输出完整文件路径（如 `.temp/review-260420-1530-<主题>.md`），后续步骤使用此路径。

## 文档格式

```markdown
## 本地改动与分支 Review

**评审范围**
- 默认范围: git diff HEAD
- 补充范围: (若有)
- 当前分支: <branch>

---

### R1 — <标题>

**Review**
- 严重程度: 严重/警告/建议
- 问题描述: ...
- 修改建议: ...

### R2 — <标题>

**Review**
...
```

## 输出

向用户报告生成的文件路径（位于 `.temp/`），并提示下一步：
`review2-reply <文件名>`
