---
name: branch-tidy
description: 对用户指定的连续 commit 区间做可迭代的历史重构，使整理后的历史接近从范围起点按最终方案重新实现。先建立目标历史图，再分领域执行 split、merge 或 reorder/reword；适用于消除中间修补与废弃路线、收敛实现和测试文档、拆分混合提交、调整依赖顺序，同时保持最终代码 tree 不变。
---

# Branch Tidy

把用户指定的连续 commit 范围逐步整理成意图单一、规模可审阅、依赖清楚的历史。目标不是机械减少 commit 数量，而是让历史接近从范围起点按照最终设计重新实现。只重构 commit 边界、顺序和 message；不得改变最终代码 tree。需要修改最终代码时，改用 `branch-polish` 或正常开发流程。

判断一个 commit 是否应独立保留时，始终询问：如果只知道范围起点、最终设计和最终代码，重新实现时是否仍会主动创建这个 commit？如果不会，就应把它合入最终实现、通过净变化消除，或重新拆分归类。

## 目标历史图

开始第一次 pass 前先设计目标历史，不从遇到的第一条 `fix` 开始局部整理：

- 阅读完整范围内每个 commit 的实际 diff，并比较 `<base>..HEAD` 的最终净变化；不得只看标题或 `--stat`。
- 按最终功能边界划分整理单元，列出每个单元最终应留下的提交意图、依赖顺序和规模预期。
- 把当前 commit 映射为最终实现、后续补完、测试与文档、临时路线或独立缺陷；一个 commit 可以向多个最终意图提供 patch。
- 当前提交分散或存在加入、撤销、重做的循环时，优先 reorder 成连续的领域块，再在领域内部 merge 或 split。
- 每轮汇报完整总范围、目标历史图、已整理领域、当前领域、待整理领域和 tree 等价结果，避免只保留局部上下文。

默认收敛规则：

- 新功能及其后续兼容修复、边界修复和回归测试属于同一最终实现，除非修复本身是可独立解释的既有缺陷。
- 针对性测试和随实现变化的文档通常跟实现放在一起，不因开发时序单独保留。
- backlog 的登记、完成和清理过程不独立保留；最终仍有效的信息随对应实现或最终文档归档。
- 被后续方案完全替代的实现，其建立、修复和删除过程应从最终历史消失。
- 与当前架构改造无关、能够独立说明和验收的真实缺陷保留为单独 commit。

## 范围与现场

用户必须明确指定完整总范围，使用 Git 原生的左开右闭语义：

```text
<base>..HEAD
```

左侧 `<base>` 不包含，右侧包含。历史改写会改变范围内 commit 的 hash，因此优先使用范围外稳定 commit 作为左侧锚点，右侧通常使用 `HEAD`。

每一轮都把同一个完整总范围传给辅助脚本；不得把本轮准备处理的几个 commit 冒充总范围。具体处理目标只写在本轮方案中，不传给脚本。

开始前：

- 当前必须处于具名分支，工作区、index 和未跟踪文件均干净。
- 完整范围必须可解析且为线性历史。
- 若范围已发布，提示用户后续需要改写远端历史；不得自动 push 或 force-push。
- 运行技能目录旁的脚本创建 backup 和 work 分支：

```text
python docs/agent/skills-dev/branch-tidy/branch_tidy.py start \
  --range <base>..HEAD
```

脚本自动从当前分支创建并切换到类似下面的 work 分支：

```text
branch-tidy/master-ba059304-001-work
```

同时保留对应的 `-backup` 分支。脚本对 `master`、`main` 和 feature 分支一视同仁。

## 每轮选择一个 pass

从目标历史图选择当前最高价值的一种 pass，识别实际意图、前后依赖、后续推翻、文件重命名、测试关系和机械生成内容：

- **split**：拆分巨大提交、混合意图或无法用一个 message 准确概括的提交。
- **merge**：合并相邻且属于同一意图的碎片或连续修补；也可先形成下一轮 split 所需的连续净 patch。
- **reorder/reword**：commit 边界合理，只需调整顺序、规范 message，或同时完成两者；换序时原 patch 必须能无歧义应用。

每轮只执行一种 pass。一次可批量处理多个互相独立的同类目标，以减少机械迭代；批次仍须有界且容易整体审阅。若操作需要另一种历史变换作为前置条件，留到下一轮。

## 语义约束

每轮始终满足：

1. 最终 `HEAD` tree 与本轮开始前完全一致。
2. 不属于本轮目标的 commit 边界、顺序、message 和语义 patch 不变；允许因祖先变化产生新 hash。
3. 不手写两个端点都不存在的代码，不创造临时兼容层。
4. 无法证明等价或依赖正确时停止，保留 backup 和 work 现场。
5. 不跨越目标历史图中未声明的领域扩大本轮批次。

### Split

只重新分配源 commit 自身 `parent → commit` 的净 patch，不吸收其他 commit 内容。按功能意图拆分，而不是机械按文件或 hunk 拆分；实现、针对性测试和随代码变化的文档通常放在一起。

每个新 commit 目标不超过约 400 个有效增删行、15 个有效文件。超过约 1000 行或 30 个文件时必须说明无法继续拆分的原因并取得用户确认。自动生成文件、锁文件、机械快照和纯移动单独统计。

若多个 commit 必须共同完成且不能在不创造过渡代码的前提下保持测试全绿，可组成连续编号系列：

```text
feat(manager): 重构归档事务 [1/3]：建立状态模型
feat(manager): 重构归档事务 [2/3]：迁移执行流程
feat(manager): 重构归档事务 [3/3]：完成恢复与验证
```

系列中不得夹入其他主题；中间节点至少保持语法、import、build 和配置可加载，最后节点通过相关验证。最后一个拆分 commit 的 tree 必须与源 commit tree 相同。

### Merge

只合并相邻 commit，以首个 commit 的 parent 为新 parent、末个 commit 的 tree 为新 tree。新 message 概括组合后的最终净效果；引入又撤销的中间内容自然消失。

桥接 merge 只能覆盖目标历史图中一个已经定义的功能领域，并且执行前必须给出下一轮 split 方案。完成桥接 merge 后，下一轮优先拆分该领域，不得转去处理其他领域。不得把多个独立领域或完整总范围合成一个桥接 commit，除非用户明确授权。

### Reorder / Reword

保持 commit 边界和语义 patch 不变，可在同一轮调整顺序、规范 message，或同时完成两者；纯 reword 不要求实际换序。换序时检查文件存在性、定义与调用、schema、测试及构建依赖；patch 冲突或需要改写内容时停止，并改在后续 split/merge pass 解决。所有新 message 遵循 `git-commit-brief`。

## 方案与执行

执行前向用户展示一份完整方案，至少包括：总范围、目标历史图中的当前领域、本轮 pass、选择理由、所有目标 commit 的 hash 与标题、拟生成或调整的 commit、文件和有效规模、依赖、验证方式，以及明确不在本轮处理的内容。

用户已明确授权连续整理且不要求逐轮确认时，可按已确认的总目标连续迭代；仍须在内部固定本轮 pass 和批次，不得执行途中扩大范围。

在当前 `branch-tidy/*-work` 分支手工构造新历史：

- 所有新增或重写的 message 使用 `git-commit-brief`。
- 保留原 author；committer 和 hash 可以变化。
- 未处理的 commit 应保留原 tree、message、author 和语义 patch。
- 根据 pass 风险做语义验收；用户明确允许历史整理阶段不跑业务测试时，至少检查中间 tree/patch 与最终 tree 等价。

## 检查并替换原分支

Agent 完整审阅新历史，确认本轮只有计划中的同类变换后运行：

```text
python docs/agent/skills-dev/branch-tidy/branch_tidy.py finish
```

`finish` 只检查客观 Git 不变量：

- 当前是本轮 `branch-tidy/*-work` 分支，现场 clean。
- manifest、backup、work 和原分支互相匹配。
- backup 与原分支仍指向本轮开始时的旧 HEAD。
- `<base>..work HEAD` 保持线性，且 base 是候选 HEAD 的祖先。
- 候选最终 tree 与 backup 最终 tree 完全一致。

全部通过后，脚本使用 expected-old 校验原子移动原分支，切回原分支，复查 HEAD、tree、clean 状态和 backup，然后安全删除 work 分支及临时 manifest。backup 永久保留；脚本不产生 merge commit、不运行项目测试、不访问远端、不 push，也不删除 backup。

若任何步骤失败，脚本直接报错且不做额外回滚。失败发生在替换前时原分支不会移动；失败发生在替换后的清理阶段时，保留尚存的 work/manifest 供诊断，不使用强制删除。

完成后汇报本轮 pass、处理摘要、tree 验证、工作区状态、backup 分支，以及下一轮建议或“总范围已整理完成”。下一轮重新从原分支运行 `start --range <同一 base>..HEAD`，不要依赖对话中的旧 hash。

## 完成条件

只有同时满足以下条件，才能判断总范围已整理完成：

- 每个剩余 commit 都能回答为什么从零实现最终方案时需要主动创建它。
- 不存在仅为修补前一 commit 的不完整实现、记录临时路线或开关 backlog 而独立保留的 commit。
- 功能领域、依赖顺序、实现与测试文档关系清楚，规模符合本技能约束或已经用户确认。
- 最终 tree、线性历史和工作区状态均通过验证。

commit 数量减少是结果，不是单独的完成标准。

## 辅助脚本边界

`branch_tidy.py` 只有两个命令：

```text
branch_tidy.py start --range LEFT..RIGHT
branch_tidy.py finish
```

它只管理当前 Git 分支、backup/work 分支、临时 manifest、客观不变量和原子 ref 更新，不决定或记录 pass，不分析 commit 语义，不自动整理历史。
