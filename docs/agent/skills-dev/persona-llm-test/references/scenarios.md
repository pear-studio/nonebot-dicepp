# 固定场景

每次都向用户列出全部场景并标注推荐项，由用户选择。选择多个场景时共用一个全新 session，并按 `一天连续 warp → 私聊多轮 → 群聊多人上下文` 执行。固定场景覆盖不足时可提出附加场景；是否加入本文档，由 agent 与用户在测试后决定。

## 一天连续 warp

用途：验证从 Runtime 冷启动时间连续推进 24 小时的 Persona 生活与日程链路。

操作：

1. 对新 session 执行一天 warp。
2. 检查生活事件、角色反应、日记、SA 以及 morning、18:00、evening proactive。
3. proactive 目标使用群聊场景的同一测试群。

验收：

- warp job 完整结束；job 自身成功不等于场景自动通过。
- 随机生活槽位与边界时间在角色卡允许范围内，没有丢失或重复。
- 预期的生活事件、反应、日记、SA 和三类 proactive 有对应持久化记录或明确可解释的结果。
- Agent Run、trace 和 completion code 没有异常；fallback 需作为警告说明。

## 私聊多轮

用户：`llm_test_private_user`，昵称“小岚”。

操作：

两条消息都使用 DicePP Shell 的 `--private`。Shell 会按生产私聊消息语义自动设置 `to_me=true`：

1. 发送：“我刚从旧书店买了一本关于星图的书，准备周末读。”
2. 继续发送：“我刚才说周末准备做什么？”

验收：

- 两次消息正常完成并产生非空回复。
- 第二次回复明确知道用户准备周末读那本书或表达等价事实。
- Conversation 中保留两条用户消息和对应回复。
- Agent Run、trace 和 completion code 正常。

## 群聊多人上下文

群：`llm_test_group`。

操作：

1. 用户 `llm_test_xiaolin`、昵称“小林”发送普通群消息，不 `--to-me`：“我把纪念册放在会议室的蓝色柜子里了。”
2. 用户 `llm_test_xiaozhou`、昵称“小周”随后使用 `--to-me` 询问：“小林刚才说把纪念册放在哪里？”

验收：

- 第一条普通群消息不产生 Persona 回复。
- 第二条消息产生非空回复，并指出“会议室的蓝色柜子”或等价位置。
- 回复没有混淆小林、小周的身份。
- Conversation/message stream 中的 user、nickname、group scope 正确。
- Agent Run、trace 和 completion code 正常。
- 查看实际 LLM prompt，确认没有混入其他 scope 的对话上下文。
