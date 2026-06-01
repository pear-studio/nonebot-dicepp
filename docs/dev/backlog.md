# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **优先级**：P0(阻塞)/P1(应该修)/P2(可修可不修)
- **类型**：bug / feature / refactor
- **改动量**：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

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
- 工作计划:
  实现 .ai key config 命令，允许用户配置自己的 API Key：
  - 实现 command.py 中的 key config 子命令（设置/查看/删除）
  - 加密存储用户 API Key 到数据库（复用 data/models.py 已有字段）
  - LLM 路由中优先使用用户自有 key（若已配置），回退到全局 provider
  - 影响面：command.py、data/store.py、llm/router.py
  - 风险点：用户 key 的安全存储与传输，key 校验机制

### [B-260601-fb1b04] LLM 模型/provider 增加手动开关
- 创建: 2026-06-01
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
  - 当前无单模型或单 provider 的手动 disable 机制，禁用只能删除配置
  - 熔断器是自动机制（连续失败才触发），管理员无法主动干预路由策略
  - 临时切换模型（如某 provider 维护中）需要改配置文件重启，不灵活
- 工作计划:
  - ProviderConfig 增加 enabled: bool = True 字段，ModelConfig 同理
  - LLMRouter 三步筛选前先过滤 disabled 的 provider/model
  - 可选：增加 NoneBot 命令 /persona model toggle 支持运行时切换（需持久化到配置）
  - 影响面：pydantic_models.py、router.py、selection.py
  - 风险点：运行时切换需考虑正在进行的会话如何处理

### [B-260529-ed2000] SQL 行映射使用位置索引 — store.py 统一重构
- 创建: 2026-05-29
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
  - store.py 的 get_llm_traces() 使用 row[4], row[5], ..., row[25] 进行列映射，新增列后所有后续索引需手动偏移，易出错
  - store.py 所有方法（add_message_stream、get_recent_messages 等）都用 row[N]，仅改一处造成风格不一致
- 工作计划:
  - 作为独立重构 PR，统一将 store.py 所有查询方法改为 sqlite3.Row 或 dict 解包，消除位置依赖
  - 影响面: module/persona/data/store.py 所有查询方法

