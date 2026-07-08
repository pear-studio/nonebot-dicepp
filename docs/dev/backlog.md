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

### [B-260630-38ec7f] share_desire 概念重新设计及 share_threshold 死配置清理
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: L
- 问题表现:
    - Phase 2+3 已将 share_desire 从数据模型/CRUD/协议/DB 列全量清理
    - schedule_share 已移除 (Phase 4 R3)，proactive_event_share_threshold 配置（proactive_config.py / simulator.py / pydantic_models.py）变为死配置
    - "角色对事件有分享欲望→触发主动分享"概念本身有价值，当前无替代机制
    - 主动分享仅靠 tick 时间窗口触发，缺少事件级筛选
- 开发备忘:
    - share_desire 重新设计暂不启动，等 SA 条目化 (B-260629-5a1f2c) 落地后系统性设计
    - share_threshold 配置清理: 移除 proactive_config.py 属性及 from_persona_config 映射，移除 simulator.py 传递，清理或标记 DEPRECATED pydantic_models.py 字段
    - 新方案应与 SA 叙事产出和角色状态结合，避免回到独立数值字段旧模式
    - 影响面: proactive_config.py、simulator.py、pydantic_models.py

## release

### [B-260617-1cc4a4] 改进 PyInstaller 打包结构以减少 hiddenimports 补丁
- 创建: 2026-06-17
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: Windows rc1 包可生成并通过 --smoke-check，但普通启动时 NoneBot 加载 DicePP 插件失败：ModuleNotFoundError: No module named 'cryptography.fernet'。现场包内只有 cryptography/hazmat/bindings/_rust.pyd 和 dist-info，缺少 cryptography/fernet.py；原因是插件源码主要作为 datas 复制，PyInstaller 没有完整分析 DicePP 插件 import 链。短期可用 collect_submodules('cryptography') 修复，但类似动态依赖仍可能再次漏包。
- 开发备忘: 长期方向：重新梳理 Windows 打包结构，让 DicePP 插件代码尽量作为 PyInstaller 可静态分析的 Python 模块进入 Analysis，而不是主要依赖 datas 复制源码和手写 hiddenimports。需先验证 adapter/module/utils 等当前顶层导入路径是否能迁移或兼容；影响面包括 scripts/build/dicepp.spec、bot.py 的 frozen 路径、插件导入方式、release smoke test。风险点是改动可能影响开发环境插件加载和现有 NoneBot load_plugin 行为，适合在 RC 后续单独处理。

## runtime

### [B-260616-5f74ec] 重新设计 Standalone 无 QQ 服务入口
- 创建: 2026-06-16
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现: 当前 standalone_bot.py 是历史实验入口，混合了无 QQ 服务入口、DiceHub 注册、WebChat 启动和 /dpp runtime 绑定等职责。该模式目前不作为发布路径或新手路径使用，但未来网站可能需要单独部署 DicePP，绕过 QQ / NoneBot 直接提供服务。继续保留根目录入口和旧测试会让它看起来像现役能力，也会把未来重写约束在旧实现上。
- 开发备忘: 近期先将旧 standalone 入口归档为 docs/dev/archive/standalone_bot_legacy.py，删除围绕旧行为的测试，避免维护半成品运行模式。后续实现时重新设计无 QQ / 无 NoneBot 服务入口：明确 CLI/配置入口、HTTP/WebChat 边界、与 DiceHub 的显式启用策略、与 bot.py/NoneBot 入口的职责分离，并考虑是否提供 dicepp-standalone console script 或包内 runtime 模块。

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

