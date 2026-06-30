# CharacterAgent Conversation 统一 — diary 迁移

ID: B-260630-789272
日期: 2026-06-30
状态: 待实施

## 背景

当前仅 `react()` 使用 Conversation 跨调用上下文积累，`diary()` 是单轮独立调用，
直接拼接 `[system, user]` 后调 LLM，不经过 Conversation。

去掉 `share()`（将单独重构）和 `opening()`（Conversation 在日终已被 `compact_conversation()` 清空，
迁移无意义），本设计仅涉及 `diary()`。

## 设计决策（经 review 确认）

### 关键发现

1. **系统提示词分层**：当前四种模式的 `_build_*_prompt()` 把人设 + 任务指令绑死在 system prompt 中。
   角色身份（你是谁）与任务指令（现在做什么）应解耦——参考 Character.AI、Thane Agent、CPDC 2025
   竞赛方案等业界实践，共识是"身份入 system prompt，任务指令入 user message"。

2. **同一角色、同一视角不存在任务干扰**：LLM Task Interference (EMNLP 2024) 研究主要针对跨认知域
   切换（情感分类→数学推理），DACS 针对多 agent 身份污染。diary 和 reaction 都是同一角色第一人称的
   情感表达，Conversation 中的 reaction 历史对 diary 是素材而非噪声。

3. **状态不应进 system prompt**：当前 `_format_state_prompt()` 在 system prompt 中，每次状态变更
   （一天 5-10 轮事件链）都导致 system prompt 字节变化 → 缓存全清。应迁移到 user message。

### 1. System prompt 分层

```
System Prompt (固定，缓存友好，不区分 mode):
  ├─ 角色身份: "你是{name}。\n角色设定:\n{description}"
  └─ 核心边界: 第一人称、性格一致性，不编造内容

User Message (per-mode 动态):
  ├─ 当前状态: 体力/心情/健康 (临时简单拼接，延后项)
  ├─ 任务指令: reaction/日记 各自的输出要求、字数限制
  ├─ 工具指定: "请通过 {tool_name} 工具输出"
  └─ 事件/上下文数据
```

- `build_system_prompt()` 不再依赖 `context["mode"]`，对所有 mode 返回相同内容
- `_ensure_conversation()` **不传** `system_prompt_override`——首次调用即设为人设层，
  后续 diary 复用同一 Conversation 时拿到正确的 system prompt
- 各 `_build_*_prompt()` 中的任务指令部分移入对应的 `_build_*_user_prompt()`

### 2. 工具加载与 required_tool

当前问题：`_run_life_collect_loop` 从 `tools[0]` 自动推导 `required_tools`，调用者无法显式指定。

改为：`_run_life_collect_loop` 新增 `required_tool: Optional[str]` 参数，
替换 `tools[0]["function"]["name"]` 推导逻辑。默认取 `tools[0]` 保持向后兼容。

工具加载策略：保持 **per-mode 单工具**，不改全量加载。
原因：`required_tool` 机制基于单工具设计；`SAY_TOOL_CHARACTER` 与
`RECORD_DIARY_ENTRY_TOOL` 输出 schema 不同，全量加载无实际收益。

### 3. 提取 `Agent._run_with_conv()`

消除 agent.py (DM run) 与 character_agent.py (react) 两处重复的 Conversation 集成代码：

```python
async def _run_with_conv(
    self,
    context: dict,
    system_prompt: str,
    user_prompt: str,
    tools: list,
    temperature: float,
    selection: SelectionPolicy,
    extra_registry: Optional[Any] = None,
    required_tool: Optional[str] = None,
) -> tuple[list, Conversation]:
    conv = await self._ensure_conversation(context, system_prompt_override=system_prompt)
    conv.add_user(user_prompt)
    prev_len = conv.length
    collected, final_msgs = await _run_life_collect_loop(
        router=self.router, store=self.store,
        messages=conv.render(self._system_prompt),
        tools=tools, temperature=temperature, selection=selection,
        bg_timeout=self._bg_timeout, max_rounds=self._max_rounds,
        extra_registry=extra_registry,
        required_tool=required_tool,
    )
    conv.extend(final_msgs[prev_len + 1:])
    return collected, conv
```

参数说明：
- `extra_registry` — DM Agent 传递只读查询工具（read_events 等），CharacterAgent 传 None
- `required_tool` — 覆盖默认的 tools[0] 推导，显式指定本轮必调工具

### 4. diary() 迁移

```diff
- messages=[{"role": "system", ...}, {"role": "user", ...}]
+ // 通过 _run_with_conv() 复用 Conversation
+ collected, conv = await self._run_with_conv(
+     context, system_prompt, user_prompt,
+     tools=[RECORD_DIARY_ENTRY_TOOL.to_openai_format()],
+     temperature=0.85,
+     selection=DIARY,
+     required_tool="record_diary_entry",
+ )
```

### 5. opening() 不动

Conversation 在日终已被 `compact_conversation()` 清空，opening 在次日早晨调用时无可用上下文。
保持独立 `AgentRuntime.run()` 路径。

### 6. temperature 与 selection 统一

- `temperature` — reaction (0.9) 与 diary (0.85) 差异微小，统一为 0.9
- `selection` — EVENT_GEN、DIARY、SUMMARIZE 当前配置完全等价（同 `SelectionPolicy`），统一安全

## 改动范围

| 文件 | 变更 |
|------|------|
| `character_agent.py` | `build_system_prompt()` 简化为纯人设；`_build_diary_prompt()` 任务指令移入 `_build_diary_user_prompt()`；状态从 system prompt 移入 user prompt；`diary()` 改用 `_run_with_conv()`；`react()` 改用 `_run_with_conv()` |
| `_llm_utils.py` | `_run_life_collect_loop` 新增 `required_tool` 参数 |
| `agent.py` | 新增 `_run_with_conv()`；DM `run()` 改用该方法；删除现有内联 Conversation 代码 |

## 不变更项

- `share()` — 将单独重构（B-260630-789272 原始范围去掉 share）
- `opening()` — 无需迁移（Conversation 已清空）
- SAAgent — 不使用 Conversation，不涉及
- `conversation.py` — 无需改动

## 延后项（本次不实施）

| 编号 | 条目 | 说明 |
|------|------|------|
| B-260630-05739c | **状态变更通知 message** | 状态变化时通过独立通知 message 注入 Conversation（事件溯源模式），替换当前 per-message 状态拼接。渲染时可通过扫数据库记录重建上下文 |
| B-260630-9f3fa0 | **Conversation 事务性保护** | `conv.add_user()` 后 LLM 调用失败时，Conversation 中留下悬挂的 user 消息（无 assistant 响应）。需要在 add_user/LLM 调用/extend 之间加事务性边界 |
| B-260630-1f9286 | **工具定义与传入规则梳理** | 当前 `required_tools` 从 `tools[0]` 隐式推导；`SAY_TOOL_DM` 与 `SAY_TOOL_CHARACTER` 共享 name="say" 但通过不同路径传递；工具传递三路并行（`_run_life_collect_loop` / `AgentRuntime.run()` / `run_structured_collect`）；opening() 走了独立的 `AgentRuntime` 路径。需要统一工具注册、传递与 required 语义 |
