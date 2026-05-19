# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## tests

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

### [B-260519-71060e] 测试风格统一：unittest→pytest 迁移 + 超大文件拆分
- 创建: 2026-05-19
- 问题表现: 17 个文件仍使用 unittest.TestCase/IsolatedAsyncioTestCase 风格（含 module/roll, module/deck, utils/, core/data/, core/command/ 等），与项目中大量 pytest 函数/类风格不一致。5 个测试文件 >500 行（最大 666 行）：test_llm_call_coordinator.py(666)、test_context_builder.py(637)、test_data_store.py(608)、test_scheduler.py(605)、test_command.py(585)。pyproject.toml 的 python_classes 已清理 MyTestCase。
- 工作计划: 逐步迁移 unittest.TestCase → pytest 函数/类，IsolatedAsyncioTestCase 保留（提供事件循环隔离有价值）。按目录分批：utils/ → core/ → module/roll → module/deck → module/character → module/common。迁移后删除 import unittest。超大文件按职责拆分：test_data_store.py 按 CRUD 域拆分（Message/Relationship/LLMTrace/Diary+Event），test_context_builder.py 按格式化/截断/结构拆分，test_scheduler.py 按 tick/round/schedule 拆分。每批独立 PR，不破坏测试逻辑。

