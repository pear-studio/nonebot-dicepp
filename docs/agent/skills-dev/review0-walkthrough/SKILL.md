---
name: review0-walkthrough
description: "[User-guided] Walk through a diff or commit range one logical unit at a time, explaining what changed and why. Discuss concerns on the spot, record agreed decisions immediately with review_record.py. Produces a standard Rn document (with optional 用户明确 blocks) compatible with review2-reply through review5-accept. Can be used standalone or as an alternative to review1-raise."
---

# review0-walkthrough — 交互式代码走查

逐逻辑单元讲解代码改动，用户随时提问，当场讨论出方案并立即写入文档。

## 角色

**Walkthrough Agent** — 本阶段由讲解方执行，是五阶段 ping-pong 流程的入口替代：

```
walkthrough(可选) → review1-raise(可选) → reply(D) → confirm(R) → execute(D) → accept(R)
```

两者平行，不互相依赖。walkthrough 产出的文档格式与 review1-raise 完全兼容。

## 输入

```
review0-walkthrough                      # 默认：git diff HEAD
review0-walkthrough HEAD~3..HEAD         # 提交区间
review0-walkthrough HEAD~1 src/foo.cpp   # 指定文件范围
```

用户未提供参数时，使用默认范围，无需询问。

## 步骤

### 阶段一：准备

1. 运行 `git diff HEAD [补充范围]` 及 `git diff --stat HEAD [补充范围]` 获取完整 diff
2. 若无改动，直接退出
3. **静默**分析 diff，在上下文中规划逻辑单元列表（**不向用户展示此列表**）：
   - 按设计决策分组，而非按文件逐一列举
   - 每个单元有一个简短标题和内部序号
4. 用一句话告知用户本次改动概要和单元总数，然后创建文档：
   ```bash
   python .claude/skills/review1-raise/review_record.py create <topic-slug> --file .temp/walkthrough_header_tmp.txt
   ```
   文档初始内容（写入临时文件）：
   ```markdown
   ## 代码走查
   ```
   记录脚本输出的完整文件路径，全程使用此路径。

### 阶段二：逐单元讲解

对每个逻辑单元，输出以下格式，**不多不少**：

```
【X / N】<标题>

改到: <具体函数/文件，不泛化>
设计意图: <为什么这样做，1-2 句>
实现要点: <关键代码路径、兼容性处理、边界情况，2-3 句>
风险与取舍: <tradeoff、临时方案、已知缺陷，1-2 句，无则省略>

---
有问题可以提问，或回复"继续"。
```

每单元讲解不超过 **10 句**。

然后**等待用户回复，不得自动推进**。

**单元锁定**：当前单元在以下任一条件达成前视为**未完结**，禁止展示下一单元：
- 用户明确回复"继续"（或同义词）
- 讨论已得出结论、已写入文档，且用户再次确认"继续"

**用户回复的处理分支**：

- **"继续" / 无异议** → 进入下一单元，不写文档
- **提问 / 提出疑虑** → 进入讨论环节（见下方）。即使问题已被解答或请求已被执行，回复末尾必须再次明确等待用户表态（如"已处理，是否继续？"），方可决定是否进入下一单元
- **"停" / "展开"** → 对当前单元深入解释，解释完后再等待回复

**讨论环节**：

与用户来回讨论，直到对当前单元达成明确结论：
- **改**：明确改什么、改哪里、怎么改
- **不改**：明确理由

结论确定后，**立刻写入文档**（见阶段三），再进入下一单元。讨论时忠实记录双方达成的结论，不代入个人倾向。

> **行动后收尾**：若讨论中产生了代码修改、文件编辑等实际行动，行动完成后**必须回到当前单元的等待状态**，重新输出等待提示（如"已处理，是否继续？"），不得自动推进到下一单元。

### 阶段三：写入文档

方案确定后，构造完整 Rn 块，写入临时文件，然后 append：

```bash
python .claude/skills/review1-raise/review_record.py append <filename> --file .temp/walkthrough_rn_tmp.txt
```

Rn 编号按**写入顺序**递增（R1, R2...），与逻辑单元序号无关（大多数单元无需写入）。

**Rn 块格式**：

```markdown
### R1 — <标题>

**Review**
- 严重程度: 建议
- 问题描述: <讨论中发现的问题，或需要记录的背景>
- 修改建议: <商定的改动方案，或"确认无需修改，原因：...">

**用户明确**
- 决策: 实施 / 延后 / 否决
- 原因: <用户给出的背景、约束或决策理由>
- 开发备忘: <仅 延后 必填，说明后续做什么、影响面、何时拉起>
```

`**用户明确**` 块记录用户在走查中亲自参与并给出的决策依据，供下游 review2/3 了解背景，不改变流程逻辑。

### 阶段四：结束

所有单元讲完后：

- **若有 Rn 记录**：
  ```
  走查完成，共记录 N 条待处理项。
  文档：<filepath>
  下一步：review2-reply <filename>
  ```
- **若无 Rn 记录**：
  ```
  走查完成，本次改动无待处理项。
  ```
  删除空文档：`python .claude/skills/review1-raise/review_record.py` 无 delete 命令，直接用 `os.remove` 或提示用户手动删除。

## 约束

- 每次只展示**一个**逻辑单元，禁止提前透露后续内容
- 每单元讲解不超过 **10 句**，避免信息过载
- 讨论时不主动推荐修改倾向，忠实记录双方结论
- 方案未明确前禁止写文档，确定后**立刻**写，不攒到最后
- 严重程度统一填 `建议`（走查产出的是用户主动讨论项，非对抗审查）
- 文档路径确定后全程不变，每次 append 使用相同路径

## 写作风格

实现要点的写法直接影响走查的可读性，遵循以下原则：

1. **用自然语言描述流程和结构**，代码标识名只在"改到"行集中列出，正文不堆叠字段名和函数名
2. **解释现象和机制，不堆字段名** — 说清楚"是什么、为什么、怎么处理"，用中文描述数据结构和处理逻辑
3. **保持具体** — "列表"比"另一个字段"好，"拼接"比"处理"好，不要为了可读性牺牲准确性
4. **多层传递用"上游→下游"或"从...到..."的自然叙述**，不逐层罗列类名和字段名

反例：
> 从 `message.reasoning_content` 提取，通过 `LLMResponse.reasoning_content` 透传到 `LLMGatewayResult.reasoning_content`，在 `loop.py` 的 `assistant_msg` 中写入 `reasoning_content`

正例：
> 思考链文本从 API 响应中提取，经 Gateway 透传后写入 agent 的 assistant 消息。关键字段是 `reasoning_content`，各层统一用这一个名字。

反例：
> 无 `reasoning_content` 时回退到 `reasoning_details`，拼接 `d["text"]`

正例：
> 部分兼容接口的思考内容不是一整段文本，而是一个列表，每个元素里有一段文本。代码把这些文本用换行拼接起来，作为完整的思考链。
