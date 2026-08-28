---
name: backlog-add
description: "根据汇报文件或用户给出的信息，新增 backlog 条目到 docs/dev/backlog.md。"
---

# backlog-add — 新增 backlog 条目

将外部输入（review 文档、会议纪要、用户口述）转化为规范的 backlog 条目并录入。

## 角色

**Recorder Agent** — 负责把非结构化的延后需求转化为结构化的 backlog 条目。

## 字段约束

每条 backlog 包含五项必填内容字段（外加 ID/模块/标题/创建日期）：

- **优先级**（必填）：P0(阻塞，卡住开发/测试流程或核心功能不可用) / P1(应该修，持续在痛) / P2(可修可不修，改善项)
- **类型**（必填）：bug / feature / refactor
- **改动量**（必填）：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**（必填）：症状、错误日志、量化指标、复现路径。要写得让一个月后没上下文的人也能看懂在说什么。
  - 写作 checklist：症状 / 现场数据 / 错误日志 / 量化指标 / 影响后果。能贴日志原文就贴。
- **开发备忘**（必填）：可能的修复方向、需要先验证的假设、影响范围、风险点。
  - 写作 checklist：修复方向（可多个候选方案） / 需先验证的假设 / 影响面（文件、模块、配置） / 已知风险点。

不再使用 来源 / 触发条件 / 暂缓原因 三个字段——经验上它们要么循环定义，要么后续被消费的概率极低。

## 参数要求

支持三种输入方式：
```
/backlog-add                          # 对话式录入，逐字段问答
/backlog-add <file-path>              # 从文件解析录入
/backlog-add --module M --title T ... # 直接参数录入
```

## 步骤

### 模式 A — 对话式录入（无参数）

1. 依次向用户询问必填字段：
   - `module`（模块名）
   - `title`（标题，一句话概括）
   - `priority`（优先级：P0/P1/P2）
   - `type`（类型：bug/feature/refactor）
   - `effort`（改动量：S/M/L/XL）
   - `symptom`（问题表现，按 checklist 展开）
   - `plan`（开发备忘，按 checklist 展开）

2. 构造候选条目展示给用户确认：
   ```
   即将录入以下 backlog：
   - 模块: ...
   - 标题: ...
   - 优先级: P1
   - 类型: bug
   - 改动量: M
   - 问题表现:
     - ...
     - ...
   - 开发备忘:
     - ...
     - ...
   确认录入？(是 / 否 / 修改某字段)
   ```

3. 用户确认后执行：
   ```bash
   python docs/agent/tools/backlog.py add \
     --module <M> --title <T> \
     --priority <P> --type <T> --effort <E> \
     --symptom "$(cat symptom.txt)" \
     --plan "$(cat plan.txt)"
   ```
   多行内容用 `$()` 命令替换或在调用前写入临时文件。

4. 输出新 ID。

### 模式 B — 文件解析录入

1. 读取用户提供的文件（如 review 文档、会议纪要、自由文本）。
2. Agent 解析文件内容，识别其中标记为"延后"、"TODO"、"后续处理"的条目。
3. 每条按 checklist 整理 `symptom` 和 `plan`，构造候选列表。
4. 逐条展示给用户确认（支持批量确认或逐条审）。
5. 对确认的条目构造 batch-add payload（见下文格式），写入临时文件后执行：
   ```bash
   python docs/agent/tools/backlog.py batch-add --payload-file <tmp>
   ```
6. 输出所有新 ID。

### 模式 C — 直接参数录入

如果用户一次性给了所有字段，直接构造 add 命令执行，跳过问答。仍需在执行前展示 preview。

## batch-add payload 格式

条目之间用 `<<<END>>>` 分隔。每条内部 7 个 Key（`Module`/`Title`/`Priority`/`Type`/`Effort`/`Symptom`/`Plan`），冒号后跟单行值，或 Symptom/Plan 可空冒号后跟多行续行直到下一个 Key 或分隔符：

```
Module: persona
Title: Persona 生活事件记录
Priority: P1
Type: bug
Effort: M
Symptom:
  - 生活事件完成后未稳定写入 daily events
  - 日终摘要缺少当天已完成事件
Plan:
  - 补齐生活事件与日终摘要的持久化检查
  - 复核事件写入与摘要读取的边界
  - 影响面: persona/life、data/store.py
<<<END>>>
Module: persona
Title: ...
Priority: P2
Type: feature
Effort: S
Symptom: 单行也可以这样写
Plan: 单行计划
<<<END>>>
```

注意：Key 名固定为 `Module`/`Title`/`Priority`/`Type`/`Effort`/`Symptom`/`Plan`（首字母大写），其他变体不识别。

## 约束

- `priority`/`type`/`effort`/`symptom`/`plan` 必填，不得为空（脚本 validate 会拦截）
- `priority` 只能为 P0/P1/P2，`type` 只能为 bug/feature/refactor，`effort` 只能为 S/M/L/XL
- 录入前必须经过用户确认（哪怕模式 C 也要展示 preview）
- 一次调用可录入多条，但每条独立生成 ID
- 插入后自动按 模块→优先级→类型→改动量 排序
- 不修改 review 文档或业务代码，只操作 backlog
