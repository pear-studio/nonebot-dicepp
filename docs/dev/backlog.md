# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## tests

### [B-260515-4e9762] 测试用例速度优化：消除真实等待、减少冗余、统一风格
- 创建: 2026-05-15
- 问题表现:
    - 1705 条用例总耗时 131s，其中 3 条最慢的占 58.7s（45% 总耗时）：
      test_registration_failure_without_explicit_key_stays_standalone（34s，跑完整 bot lifespan）
      test_retry_exhausted_raises（14s，未 mock asyncio.sleep，真实等待重试退避）
      test_percentile_deviation 3 条参数化（18.3s，蒙特卡洛采样量过大）
    - 31 处 sleep/asyncio.sleep 调用，319 处 @patch 引用仅 unit/persona
    - unit/persona 46 文件 10358 行，6 个文件超 500 行（最大 680 行）
    - 风格不统一：MyTestCase(unittest.TestCase) 与 pytest 函数混用
    - 无 marker 体系区分慢速/集成/E2E 测试，CI 一次性全跑
- 工作计划:
    - 快速止血：test_retry_exhausted_raises mock asyncio.sleep（预计省 12s+）
    - 快速止血：test_percentile_deviation 降低采样量或标注 slow marker 默认跳过
    - 评估 test_registration_failure（34s）是否应从单元测试层移入集成/E2E 层
    - 建立 pytest marker 体系（slow / integration / e2e），CI 分阶段运行
    - 逐步统一测试风格，清理 MyTestCase 旧命名，拆分超大文件
    - 影响面：pytest.ini / pyproject.toml marker 配置、CI 脚本、各测试文件

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

