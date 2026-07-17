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

## agent

### [B-260716-bae952] persona-inspect trace 与当前数据库 schema 不兼容
- 创建: 2026-07-16
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - 对当前 Persona 数据库执行 persona_inspect.py trace 时，脚本查询不存在的 session_id 列并抛出 sqlite3.OperationalError: no such column: session_id。
    - state 和 llm-health 可正常使用，trace 子命令完全不可用；本次回归只能改用只读 sqlite3 手工检查 persona_llm_traces 和 persona_agent_runs。
    - 当前 persona_llm_traces 使用 interaction_id、run_id、selected_provider、selected_model 等字段，已与 inspection 查询假设漂移。
- 开发备忘:
    - 按当前 DDL 更新 trace 查询和格式化字段，并检查是否还有 completion_code/provider 等同类列名漂移。
    - 为当前 schema 增加临时 SQLite fixture 测试；如仍需兼容旧数据库，先通过 PRAGMA table_info 检测列集合再选择查询。
    - 影响面：docs/agent/skills-common/persona-inspect/scripts/persona_inspect.py 及其测试。
    - 风险点：不要为了兼容单一版本而破坏旧生产数据库的只读检查能力。

## persona

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

### [B-260630-46af37] compact_conversation 改为 LLM 摘要压缩
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - 当前 compact_conversation 仅做 clear() 清空
    - 日终前的所有对话记忆被丢弃而非提炼为叙事线索
    - 跨事件累积的叙事上下文（人物/地点/事件/线索）完全丢失
- 开发备忘:
    - 将 compact 从 clear 改为 LLM summary 压缩
    - 调用一次轻量 LLM 将 _messages 压缩为叙事摘要，保留关键信息
    - 需评估压缩 LLM 的 token 消耗和延时
    - 影响面: life/agent.py compact_conversation()

## release

### [B-260617-1cc4a4] 改进 PyInstaller 打包结构以减少 hiddenimports 补丁
- 创建: 2026-06-17
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: Windows rc1 包可生成并通过 --smoke-check，但普通启动时 NoneBot 加载 DicePP 插件失败：ModuleNotFoundError: No module named 'cryptography.fernet'。现场包内只有 cryptography/hazmat/bindings/_rust.pyd 和 dist-info，缺少 cryptography/fernet.py；原因是插件源码主要作为 datas 复制，PyInstaller 没有完整分析 DicePP 插件 import 链。短期可用 collect_submodules('cryptography') 修复，但类似动态依赖仍可能再次漏包。
- 开发备忘: 长期方向：重新梳理 Windows 打包结构，让 DicePP 插件代码尽量作为 PyInstaller 可静态分析的 Python 模块进入 Analysis，而不是主要依赖 datas 复制源码和手写 hiddenimports。需先验证 adapter/module/utils 等当前顶层导入路径是否能迁移或兼容；影响面包括 scripts/build/dicepp.spec、bot.py 的 frozen 路径、插件导入方式、release smoke test。风险点是改动可能影响开发环境插件加载和现有 NoneBot load_plugin 行为，适合在 RC 后续单独处理。

## statistics

### [B-260622-d85176] StatManager 规模化运维
- 创建: 2026-06-22
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - (a) tick_daily 逐行 get+upsert 替代 batch upsert_many，O(1)→O(N) commit，万级用户时 daily tick DB 写入次数显著增加
    - (b) StatManager._user_locks / _group_locks 字典无上限无清理，每个新 key 创建一个 asyncio.Lock 永不删除，百万级历史 ID 时内存持续增长
- 开发备忘:
    - 正确性优先于性能，R2 per-row 异常保护已减轻逐行失败影响
    - 优化方向：(1) StatManager 增加批量更新方法（update_user_stat_batch / update_group_stat_batch），单次事务内顺序获取 per-key 锁但合并 commit，需注意多锁获取顺序避免死锁
    - (2) 锁池增加 LRU 清理或 weakref 防护，需记录最后使用时间戳
    - 触发条件：实测 daily tick 耗时超阈值，或锁池 dict 大小超过 100K keys

