---
name: backlog-add
description: "根据汇报文件或用户给出的信息，新增 backlog 条目到 docs/dev/backlog.md。"
---

# backlog-add — 新增 backlog 条目

将外部输入（review 文档、会议纪要、用户口述）转化为规范的 backlog 条目并录入。

## 角色

**Recorder Agent** — 负责把非结构化的延后需求转化为结构化的 backlog 条目。

## 参数要求

支持两种输入方式：
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
   - `problem`（原始问题/背景）
   - `trigger`（触发条件，什么时候应该处理）
   - `reason`（暂缓原因，为什么现在不做）
   - `source`（来源，可选，如 "chat 2026-05-06"）

2. 构造候选条目展示给用户确认：
   ```
   即将录入以下 backlog：
   - 模块: ...
   - 标题: ...
   - 原始问题: ...
   - 触发条件: ...
   - 暂缓原因: ...
   确认录入？(是 / 否 / 修改某字段)
   ```

3. 用户确认后执行：
   ```bash
   python scripts/tools/backlog.py add \
     --module <M> --title <T> --source <S> \
     --problem <P> --trigger <TR> --reason <R>
   ```

4. 输出新 ID。

### 模式 B — 文件解析录入

1. 读取用户提供的文件（如 `.temp/review-*.md`、会议纪要、自由文本）。
2. Agent 解析文件内容，识别其中标记为"延后"、"TODO"、"后续处理"的条目。
3. 每条提取 module、title、problem、trigger、reason，构造候选列表。
4. 逐条展示给用户确认（支持批量确认或逐条审）。
5. 对确认的条目构造 batch-add payload：
   ```bash
   python scripts/tools/backlog.py batch-add --payload-file <tmp>
   ```
6. 输出所有新 ID。

### 模式 C — 直接参数录入

如果用户一次性给了所有字段，直接构造 add 命令执行，跳过问答。

## 约束

- `trigger` 和 `reason` 必填，不得为空
- 录入前必须经过用户确认（哪怕模式 C 也要展示 preview）
- 来源字段自由填写，不做格式校验
- 一次调用可录入多条，但每条独立生成 ID
- 不修改 review 文档或业务代码，只操作 backlog
