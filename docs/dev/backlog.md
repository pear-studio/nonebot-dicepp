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

## adapter

### [B-260520-de976f] Adapter 命令分发深化 — 消除 isinstance 级联与模块反向导入
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: M
- 问题表现:
    - NoneBotClientProxy.process_bot_command() 用 isinstance 级联检查 6 种 BotCommandBase 子类
    - StandaloneClientProxy 和 WebChatProxy 部分复制了相同分支
    - 新增 BotCommand 子类需在三个 proxy 中追加入口
    - nonebot_adapter.py 直接从 module.common.log_command 导入 append_log_record，破坏依赖方向
- 工作计划:
    - 在 BotCommandBase 上引入自描述 dispatch 方法，命令自身声明发送行为
    - adapter 只做平台格式翻译，不再用 isinstance 分发
    - 日志记录改用 Bot._post_send_hooks 统一路径，消除 adapter -> module 反向导入
    - 影响面: adapter/nonebot_adapter.py, adapter/standalone_proxy.py, adapter/web_chat_proxy.py, core/command/user_cmd.py
    - 风险: dispatch 接口需同时满足三种 adapter (NoneBot/Standalone/WebChat) 的差异化需求

## bot

### [B-260520-b6f605] 从 Bot 中提取 TaskScheduler 模块，消除 todo_tasks 耦合
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: M
- 问题表现:
  Bot 持有 todo_tasks: Dict 并直接操作其内部结构，命令层可随意篡改（master_command 直接 todo_tasks={} 清空）
  process_async_task 原地修改可变 bot_commands 列表，接口副作用不透明
  register_task / process_async_task 无法独立测试，必须构造完整 Bot 实例
  shell/bot_runner 直接调用 process_async_task，绕过 tick_loop 的正常路径
- 工作计划:
  新建 core/bot/task_scheduler.py: TaskScheduler 类
    - schedule(task, is_async, timeout, timeout_callback) 注册任务
    - async process(free_time) -> List[BotCommandBase] 纯返回值，消除副作用
    - clear_all() 替代直接清空 dict
    - error_handler 通过构造函数注入，dice_log 直接 import
  修改 core/bot/dicebot.py: 移除 todo_tasks/register_task/process_async_task，Bot 持有 self._scheduler
  迁移 4 个命令调用方: register_task -> bot._scheduler.schedule
  迁移 shell/bot_runner.py: process_async_task -> bot._scheduler.process
  影响面: core/bot/dicebot.py, master_command.py, persona/command.py, roll_dice_command.py, shell/bot_runner.py
  风险: process() 返回值合并语义需与 tick_loop 的 bot_commands 累积逻辑对齐

## character

### [B-260520-368eba] 角色卡模型统一 — 删除旧 JsonObject 体系，全量迁移到 Pydantic
- 创建: 2026-05-20
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - 相同领域概念 (HPInfo, AbilityInfo, DNDCharacter 等) 存在两套模型
    - 旧体系: module/character/base/ 用自定义 JsonObject + serialize()/deserialize()
    - 新体系: core/data/models/character.py 用 Pydantic BaseModel
    - module/character/dnd5e/services.py 同时从两套导入
    - 理解角色卡数据模型需要跨三个目录阅读
- 工作计划:
    - 将 module/character/dnd5e/ 所有消费者迁移到 core/data/models/ 的 Pydantic 模型
    - 删除 module/character/base/ 和 core/data/json_object.py
    - 影响面: module/character/dnd5e/, module/character/base/, core/data/json_object.py
    - 风险: 旧 JsonObject 的 serialize/deserialize 语义可能与 Pydantic model_dump/model_validate 有细微差异

## command

### [B-260520-fe4aaa] 命令注册机制深化 — 用 CommandRegistry 替代全局字典注册
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: M
- 问题表现:
    - 命令注册依赖 import 副作用: @custom_user_command 装饰器写入全局 USER_COMMAND_CLS_DICT
    - module/__init__.py 必须按精确顺序 import 所有模块
    - 测试无法只注册需要的命令子集，必须编排完整模块导入链
    - 全局 dict 是 hypothetical seam (只有一个 adapter: 全量生产注册)
- 工作计划:
    - 引入 CommandRegistry 类，支持 registry.register(MyCommand) 和 registry.register_all([...])
    - Bot.register_command() 接收 registry 作为参数而非读取全局状态
    - 测试可创建独立 registry，只注入需要的命令
    - 影响面: core/command/user_cmd.py, core/bot/dicebot.py, module/__init__.py, 所有命令类
    - 风险: 命令装饰器注册是隐式全局约定，改显式后需保证所有命令都被注册（遗漏检测）

## persona

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 优先级: P2
- 类型: feature
- 改动量: L
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

## query

### [B-260520-88576f] QueryStore 统一 — 删除旧 query_database.py，合并常量与工具
- 创建: 2026-05-20
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - query_database.py (同步 sqlite3) 和 query_store.py (异步 aiosqlite) 定义相同字段常量，需手动同步
    - 旧代码用全局字典 CONNECTED_QUERY_DATABASES 管理连接
    - module/persona/tools/search_query.py 跨模块依赖 query_utils.command_split()
    - 旧 sqlite3 路径无法用 :memory: 连接测试
- 工作计划:
    - 所有调用方迁移到 QueryStore (core/data/query_store.py)
    - 删除 module/query/query_database.py
    - command_split() 作为共享工具移入 core/
    - 影响面: module/query/, module/persona/tools/search_query.py, core/data/query_store.py
    - 风险: 同步 sqlite3 -> 异步 aiosqlite 迁移需保证所有调用路径已支持 async

## roll

### [B-260520-be2315] 掷骰引擎统一 — 删除 Legacy 引擎，全量迁移到 AST 引擎
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现:
    - 两套完整掷骰引擎共存: expression.py (870行，正则) 与 ast_engine/ (10文件，Lark AST)
    - ast_engine/adapter.py (327行) 作为运行时 demux 桥接层
    - legacy/ 目录为空，legacy_adapter.py 中 _LEGACY_ENABLED=False (正常路径死代码)
    - 5个 module 子目录通过 module.roll 重导出使用，无法确定实际走哪个引擎
- 工作计划:
    - 将所有调用方迁移到 from module.roll.ast_engine.adapter import exec_roll_exp_unified
    - 删除 module/roll/expression.py
    - 删除 module/roll/legacy/ 和 ast_engine/legacy_adapter.py
    - 影响面: module/deck, module/character/base, module/character/dnd5e, module/initiative, module/persona/tools, module/roll/roll_dice_command.py
    - 风险: 旧引擎某些边界表达式行为可能与 AST 引擎有差异，迁移前需全量对比测试

