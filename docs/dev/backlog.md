# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **优先级**：P0(阻塞)/P1(应该修)/P2(可修可不修)
- **类型**：bug / feature / refactor
- **改动量**：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**：症状、错误日志、量化指标、复现路径
- **开发备忘**：历史背景、相关线索、可能方向（仅供参考，agent 应独立诊断，允许推翻）

---

## persona

### [B-260602-4263c4] user_stat/group_stat 的 read-modify-write 写竞争
- 创建: 2026-06-02
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现: 多路径对 user_stat/group_stat 做全量覆写，tick_daily 的 daily_update() 可能被 stale 数据覆盖，update_group_info_all 修改 meta 字段同理。涉及路径：process_message（dicebot.py:577-585,735）、tick_daily（line 282-310）、update_group_info_all（line 856-866）、record_roll_stat（roll_dice_command.py:527-556）。meta_stat 同类竞争已修复（单一写者），这些路径仍有窗口。
- 开发备忘: 修复方向同 meta_stat 单写者模式或 Repository 原子更新：将 user_stat/group_stat 的读-改-写收敛到单一路径，或用原子 upsert 替代全量覆写。需先梳理各写路径的字段修改交集。

### [B-260601-ef9e5a] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-06-01
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
  当前 .ai key config 命令返回"升级中，暂不可用"，用户无法配置自己的 API Key。
  - command.py:436 硬编码了占位回复
  - errors.py:163 已提示用户使用 .ai key config 配置 API Key 可解除限制，但功能未实现
  - data/models.py 已有 primary_api_key / auxiliary_api_key 字段，但缺少命令入口和路由集成
  - 所有对话只能使用全局 provider 配置，用户无法配置自有 key 来解除限流或使用自己的额度
- 开发备忘:
  实现 .ai key config 命令，允许用户配置自己的 API Key：
  - 实现 command.py 中的 key config 子命令（设置/查看/删除）
  - 加密存储用户 API Key 到数据库（复用 data/models.py 已有字段）
  - LLM 路由中优先使用用户自有 key（若已配置），回退到全局 provider
  - 影响面：command.py、data/store.py、llm/router.py
  - 风险点：用户 key 的安全存储与传输，key 校验机制

### [B-260604-270f2a] LLM 历史对话查询工具
- 创建: 2026-06-04
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现: Session 归档或压缩后 LLM 完全丢失历史上下文，只能依赖压缩摘要（200-300字）。用户追问历史细节（如上次我说的那个地方叫什么）时 LLM 无法查阅。message_stream 中存有完整历史记录，但没有工具让 LLM 主动查询。
- 开发备忘:
  新增 search_history 工具，LLM 可主动搜索 message_stream 中的历史对话。
  方案方向：
    - 定义 tool schema：关键词、时间范围、用户ID、返回条数上限
    - executor 调用现有 PersonaDataStore.search_messages()，返回格式化后的历史消息摘要
    - 注册到 ToolDomain.CHAT，在 _chat_with_tools 路径中可用
  影响面：tools/ 新增工具文件、tool_registry 注册、可能需调整 search_messages 返回格式以适应 LLM 上下文窗口
  风险：搜索结果可能很长（需截断）；搜索精度依赖 message_stream 的内容质量

