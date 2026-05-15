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

- **默认**：`git diff HEAD`（已暂存 + 未暂存）
- **补充**：用户可在调用时追加范围，如特定路径、提交区间、额外文件等
- 最终执行：`git diff HEAD [补充范围]` 及 `git diff --stat HEAD [补充范围]`
- **审阅 OpenSpec 文档时**：OpenSpec 文件位于 `openspec/changes/` 目录，通常被 `.gitignore` 排除，`git diff` 无法追踪。此时跳过 diff 收集，直接读取 `openspec/changes/` 下的目标目录/文件作为评审对象。评审产物仍走 `.temp/review-*.md` 流程不变。

## 步骤

1. **检查历史 review 记录**（由主 Agent 执行）：
   - 执行 `ls -t .temp/review-*.md` 获取历史文档列表
   - 逐个用 `head -n 15 .temp/review-xxx.md` 读文档开头，查看"评审范围"中涉及的文件路径
   - 若和当前 `git diff --stat HEAD` 涉及的文件有重叠，视为相关文档；用 `read` 读取完整内容
   - 从相关文档中提取精简信息：各 Rn 的共识状态（`已共识·实施` / `已共识·延后` / `已共识·否决` / `已共识·存档`(旧) / `验收退回` 等）及核心问题描述
   - 这些精简信息作为约束提供给子 Agent，避免子 Agent 上下文被大量历史内容占满
2. 收集 diff（默认 + 用户补充）
3. 若无改动，直接退出
4. **判定评审对象类型**：非代码文档（如 `openspec/changes/` 下的设计文档）走设计文档评审分支，跳过步骤 5（档位判定），直接进入步骤 6。
   - 识别方式：用户指定路径在 `openspec/changes/` 下，或 diff 为空但用户明确提供了文档目标。不确定时凭文件内容自行判断（设计文档 vs 代码改动）
   - 设计文档评审**不分档**，固定使用 2 个 Agent（见步骤 6 的 Design 行）
5. **判定评审档位**（代码改动专用，设计文档跳过）：基于 `git diff HEAD --numstat` 统计改动规模（排除 `tests/`、`docs/` 目录及 `*.md` 文件）：

   | 档位 | 条件 | Agent 数 |
   |------|------|---------|
   | light | < 100 行 **且** < 5 非 test/doc 文件 | 1 |
   | normal | 兜底（不满足 light 也不满足 deep） | 2 |
   | deep | ≥ 1000 行 **或** ≥ 8 非 test/doc 文件 | 3 |

   统计命令参考：
   ```bash
   git diff HEAD --numstat | awk '
   $3 !~ /^(tests\/|docs\/)/ && $3 !~ /\.md$/ { files++; added+=$1; deleted+=$2 }
   END { printf "files=%d, added=%d, deleted=%d\n", files, added, deleted }'
   ```

6. 根据档位/类型，**同时**启动对应数量的子 Agent 评审——不等其中一个完成再启动另一个；代码评审时让 Agent 自行读取代码与 diff，设计文档评审时让 Agent 自行读取文档，**不要把评审对象文本直接传给 Agent**；任一 Agent 启动失败时，不得单独继续，向用户确认后再决定下一步：

   **子 Agent 通用约束**（所有 Agent 适用）：
   - 子 Agent 只负责分析并输出评审意见，**不需要运行 pytest 测试**，测试由主 Agent 在后续步骤统一执行
   - 若子 Agent 运行超过 15 分钟，说明可能陷入无限循环，主 Agent 应终止该子 Agent 并重新启动调查
   - 如有历史 review 精简信息，子 Agent 应先阅读，**避免重复提出之前已闭环（已共识·存档 或 已共识·实施）的同类问题**

   **子 Agent 启动方式**：使用 `Agent` 工具，`subagent_type` 固定为 `general-purpose`，`description` 字段写明 Agent 名称与职责，`prompt` 字段开头注明 `## 审查任务` 和要审查的目标范围，然后 `## 审查要求` 引用对应的 prompt 文件内容。prompt 文件路径（相对于本 SKILL.md 所在目录）：

   ### Design（2 Agent，设计文档）

   | Agent | prompt 文件 | 粒度 |
   |-------|------------|------|
   | A — 需求与方案 | `prompt_design_a.md` | 问题定义、替代思路、过度与不足 |
   | B — 落地与影响 | `prompt_design_b.md` | 技术可行性、架构一致性、遗漏点 |

   A 聚焦"想得对吗？还有更好的吗？"，B 聚焦"能做吗？搭吗？还有什么没想到？"。各 Agent 输出分为「问题」和「提议」两部分。

   ### Light（1 Agent）

   | Agent | prompt 文件 |
   |-------|------------|
   | 综合审查 | `prompt_light.md` |

   覆盖微观正确性、中观设计、宏观彻底性三个维度，深度精简。

   ### Normal（2 Agent）

   | Agent | prompt 文件 | 粒度 |
   |-------|------------|------|
   | A — 实现质量 | `prompt_normal_a.md` | 行/函数级 |
   | B — 设计质量 | `prompt_normal_b.md` | 模块/文件级 |

   ### Deep（3 Agent）

   | Agent | prompt 文件 | 粒度 |
   |-------|------------|------|
   | A — 微观正确性 | `prompt_deep_a.md` | 行/函数级 |
   | B — 中观设计 | `prompt_deep_b.md` | 模块/文件级 |
   | C — 宏观审视 | `prompt_deep_c.md` | 项目/架构级 |

   ABC 三者视角互不重叠：A 只看"这行代码对吗"，B 只看"这几个文件搭得好吗"，C 只看"整体方向对吗 / 改全了吗"。

7. 收集所有子 Agent 的完整输出，将其作为**参考线索而非最终结论**——最终报告由本 Agent 独立负责，不得直接照搬任一 Agent 的原文：
   - **逐条回到代码/文档重新核查**，形成独立判断后再写入报告
   - 多 Agent 结论互相矛盾时，重新审查后**统一意见**，不得将分歧直接暴露在报告中
   - 仅一个 Agent 提出的条目，独立核查后决定是否成立，不因"只有一个提到"而自动降级或升级
   - 类似问题合并后**重新描述**，不得压缩成仅含标题的清单，修改建议必须具体可执行
   - **对照历史 review**：若某条问题在之前的 review 中已被标记为 `已共识·否决` 或旧版 `已共识·存档`，本轮不再重复提出；若之前标记为 `已共识·实施` 但当前代码仍未修复，可再次提出并注明"历史遗留"；若之前标记为 `已共识·延后`，本轮不重复提出，但可在新问题中引用 backlog ID

8. **用户体验改动门禁**：检查所有问题中是否存在**影响功能设计或用户体验**的建议（即不是代码 bug 修复或内部实现优化的条目）。若有，**逐一和用户确认**，用户明确同意保留的建议方可写入文档，未确认的直接剔除
9. 整理问题列表，按 `R1, R2...` 编号
10. **一次性写入**：调用脚本创建文档，通过 heredoc 直接传入完整内容（**仅允许这一次写入操作**）：
   ```bash
   python .claude/skills/review1-raise/review_record.py create <主题slug> <<'EOF'
   ## 本地改动与分支 Review
   ...
   EOF
   ```
   脚本自动生成时间戳，输出完整文件路径（如 `.temp/review-260420-1530-<主题>.md`），后续步骤使用此路径。

## 文档格式

评审代码改动时：

```markdown
## 本地改动与分支 Review

**阶段状态**
- [x] 1. 评审发起 (review1-raise)
- [ ] 2. 作者回复 (review2-reply)
- [ ] 3. 审阅者确认 (review3-confirm)
- [ ] 4. 实施 (review4-execute)
- [ ] 5. 验收 (review5-accept)

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

评审设计文档时，标题改为 `## 设计文档评审 — <文档名>`，评审范围写明文档路径。

## Worktree 路径规范

当评审对象是 worktree 中的未提交改动时，所有操作必须在**目标 worktree** 中执行，不可写入 dev 目录：

- **历史 review 检查**：步骤 1 的 `ls -t .temp/review-*.md` 必须在目标 worktree 根目录下执行，读取目标 worktree 中的历史 review 记录。
- **diff 收集**：`git diff HEAD` 必须在目标 worktree 目录下执行，确保 diff 来自目标 worktree 的未提交改动。
- **评审报告生成**：步骤 10 的 `review_record.py create` 必须在目标 worktree 目录下执行，生成的 `.temp/review-*.md` 位于目标 worktree 根目录，随 worktree 一起清理/提交。
- **脚本路径**：`review_record.py` 等脚本的调用必须在目标 worktree 目录下执行，确保相对路径解析正确。

## 输出

向用户报告生成的文件路径（位于 `.temp/`），并提示下一步：
`review2-reply <文件名>`
