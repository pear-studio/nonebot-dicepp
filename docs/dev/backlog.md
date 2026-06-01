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

### [B-260529-a1b2c3] admin.py trace 展示新增 token 维度
- 创建: 2026-05-29
- 优先级: P2
- 类型: feature
- 改动量: S
- 问题表现: .ai admin trace 不显示 cache_read/cache_creation/reasoning_tokens
- 工作计划: 在 full_mode 下增加 token 维度摘要行

### [B-260529-ea64d0] 启动时汇报 persona 相关信息
- 创建: 2026-05-29
- 优先级: P2
- 类型: feature
- 改动量: S
- 问题表现:
  - 启动时 persona 模块只输出零散的 logger.info，没有结构化汇总
  - 管理员无法快速确认：加载了哪个角色卡、有哪些 LLM 模型可用、probe 结果如何
  - 排查配置问题需要翻阅多行日志，效率低
- 工作计划:
  - 在 create_persona() 初始化完成后，输出一段结构化汇总日志
  - 包含：角色卡名称+描述、已配置的 provider/model 列表、probe 成功/失败状态
  - 影响面：module/persona/agent/factory.py
  - 可选：在 NoneBot 启动事件中发送一条管理员消息

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

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-28
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现: `SelectionPolicy.CHAT`、`.SCORING` 等类属性在 `frozen=True` 的 dataclass 定义之后通过赋值添加。功能正常但静态类型检查器（mypy/pyright）无法识别这些属性，影响 IDE 补全和类型推导。
- 工作计划: 改用 `__init_subclass__` 或将预定义策略改为模块级常量而非类属性。或改用 `frozen=False` 并在 `__post_init__` 中手动实现不可变性。影响面: module/persona/llm/selection.py。

### [B-260529-f99d2c] reasoning_tokens 按 provider 分支处理
- 创建: 2026-05-29
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现:
    - openai.py _extract_usage 中 reasoning_tokens 减法逻辑对所有 provider 无差别执行
    - DeepSeek/MiMo 的 completion_tokens 包含 reasoning_tokens，需减去
    - OpenAI o1/o3 的 completion_tokens 不含 reasoning_tokens，直接使用，当前全局减法会错误扣减
- 工作计划:
    - 按 provider 类型（base_url 或 provider 元数据）选择不同的 token 计算分支
    - 影响面: module/persona/llm/providers/openai.py _extract_usage
    - 风险点: 当前三家 API（DeepSeek/MiMo/MiniMax）行为一致，暂无生产风险；引入 OpenAI o-series 时需优先处理

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

